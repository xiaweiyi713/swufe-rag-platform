import Foundation
import OSLog

private actor StreamRevealBuffer {
    struct Step: Sendable {
        let text: String
        let delayNanoseconds: UInt64
    }

    private var characters: [Character] = []
    private var readIndex = 0
    private var inputFinished = false

    func enqueue(_ fragment: String) {
        guard !fragment.isEmpty else { return }
        characters.append(contentsOf: fragment)
    }

    func finish() {
        inputFinished = true
    }

    func reset() {
        characters.removeAll(keepingCapacity: true)
        readIndex = 0
        inputFinished = false
    }

    func nextStep() -> (step: Step?, isFinished: Bool) {
        let backlog = characters.count - readIndex
        guard backlog > 0 else {
            return (nil, inputFinished)
        }

        // Even cached answers must grow like a live response. Larger batches
        // make table rows appear to pop in, so keep each visual step small and
        // only shorten the delay while catching up with a large backlog.
        let revealCount: Int
        let baseDelay: UInt64
        switch backlog {
        case 1_201...:
            revealCount = 8
            baseDelay = 8_000_000
        case 601...:
            revealCount = 4
            baseDelay = 12_000_000
        case 241...:
            revealCount = 2
            baseDelay = 18_000_000
        default:
            revealCount = 1
            baseDelay = 22_000_000
        }

        let endIndex = min(readIndex + revealCount, characters.count)
        let slice = characters[readIndex..<endIndex]
        let text = String(slice)
        readIndex = endIndex

        var delay = baseDelay
        if let last = slice.last {
            if last == "\n" {
                delay += 80_000_000
            } else if "。！？!?".contains(last) {
                delay += 64_000_000
            } else if "，,；;：:".contains(last) {
                delay += 32_000_000
            }
        }

        if readIndex > 512, readIndex * 2 >= characters.count {
            characters.removeFirst(readIndex)
            readIndex = 0
        }
        return (Step(text: text, delayNanoseconds: delay), false)
    }
}

@MainActor
@Observable
final class ChatViewModel {
    static let welcomeText = "你好，我是西财教务问答助手。培养方案、推免细则、学籍管理等问题都可以问我，回答只依据学校官方文件并附来源角标。建议先在左上角菜单里选择你的学院和入学年级。"

    var inputText = ""
    var messages: [ChatMessage] = [
        ChatMessage(role: .assistant, text: ChatViewModel.welcomeText)
    ]
    var isStreaming = false
    var errorMessage: String?
    var speechOutputText: String?
    /// Composer modes apply to the next request and can be toggled independently.
    var deepThinkingEnabled = false
    var webSearchEnabled = false

    /// 提问范围：nil 表示“不限”。随每次 /ask 发送，持久化到 UserDefaults。
    var college: String? {
        didSet {
            guard !suppressScopePersistence else { return }
            UserDefaults.standard.set(college ?? "", forKey: Self.collegeKey)
        }
    }
    var cohort: String? {
        didSet {
            guard !suppressScopePersistence else { return }
            UserDefaults.standard.set(cohort ?? "", forKey: Self.cohortKey)
        }
    }
    /// V16：专业上下文,培养方案/学分类问题按专业精确检索。
    var major: String? {
        didSet {
            guard !suppressScopePersistence else { return }
            UserDefaults.standard.set(major ?? "", forKey: Self.majorKey)
        }
    }

    /// `GET /options` 的结果：学院/年级候选、知识块数量、运行模式。
    var options: OptionsResponse?
    var isOptionsLoading = false
    var optionsError: String?

    private(set) var sessionID: String
    /// Existing history item being continued, if the current chat was restored.
    private(set) var activeConversationID: UUID? = nil
    private let service = AskAPIService()
    private let performanceLogger = Logger(
        subsystem: "com.swufe.SwufeAsk",
        category: "Performance"
    )
    private let performanceClock = ContinuousClock()
    private var streamTask: Task<Void, Never>?
    private var suppressScopePersistence = false
#if DEBUG
    private var lastAuditLLMCalled: Bool?
    private var lastAuditOutputSource: String?
    private var lastAuditFallbackReason: String?
#endif

    private static let collegeKey = "swufeask.college"
    private static let cohortKey = "swufeask.cohort"
    private static let majorKey = "swufeask.major"

    init() {
        sessionID = Self.makeSessionID()
        let storedCollege = UserDefaults.standard.string(forKey: Self.collegeKey) ?? ""
        let storedCohort = UserDefaults.standard.string(forKey: Self.cohortKey) ?? ""
        let storedMajor = UserDefaults.standard.string(forKey: Self.majorKey) ?? ""
        college = storedCollege.isEmpty ? nil : storedCollege
        cohort = storedCohort.isEmpty ? nil : storedCohort
        major = storedMajor.isEmpty ? nil : storedMajor
    }

    var scopeSummary: String {
        // 专业比学院更具体,顶栏摘要优先展示专业。
        let identity = major ?? college
        switch (identity, cohort) {
        case (nil, nil):
            return "全校 · 不限年级"
        case (let identity?, nil):
            return identity
        case (nil, let cohort?):
            return "\(cohort)级"
        case (let identity?, let cohort?):
            return "\(identity) · \(cohort)级"
        }
    }

    // MARK: - Conversations / history

    var hasUserMessages: Bool {
        messages.contains { $0.role == .user }
    }

    /// Short title for the history list, derived from the first user message.
    var conversationTitle: String {
        let firstUser = messages.first { $0.role == .user }?.text
        let raw = (firstUser ?? messages.first?.text ?? "新对话").trimmingCharacters(in: .whitespacesAndNewlines)
        return raw.isEmpty ? "新对话" : String(raw.prefix(24))
    }

    /// Turns to archive into history (notice cards skipped), including the
    /// response metadata needed to keep citations visible after restoration.
    var archivedMessages: [ArchivedMessage] {
        messages.compactMap { message in
            guard message.role == .user || message.role == .assistant else { return nil }
            let text = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            return ArchivedMessage(
                id: message.id,
                role: message.role.rawValue,
                text: message.text,
                citations: message.citations,
                retrieved: message.retrieved,
                officialLinks: message.officialLinks,
                refused: message.refused,
                mode: message.mode,
                latencyMS: message.latencyMS,
                executionPath: message.executionPath,
                validationPassed: message.validationPassed,
                webSources: message.webSources,
                deepThinking: message.deepThinking,
                webSearch: message.webSearch
            )
        }
    }

    /// Restores an archived conversation into the live chat surface so the
    /// composer and the next question continue in the same dialogue context.
    func restoreConversation(_ conversation: StoredConversation) {
        streamTask?.cancel()
        streamTask = nil
        isStreaming = false
        inputText = ""
        errorMessage = nil
        speechOutputText = nil

        sessionID = conversation.sessionID.isEmpty
            ? Self.makeSessionID()
            : conversation.sessionID
        activeConversationID = conversation.id
        college = conversation.college
        cohort = conversation.cohort
        major = conversation.major

        let restored = conversation.messages.compactMap { archived -> ChatMessage? in
            guard let role = ChatRole(rawValue: archived.role) else { return nil }
            return ChatMessage(
                id: archived.id,
                role: role,
                text: archived.text,
                citations: archived.citations,
                retrieved: archived.retrieved,
                officialLinks: archived.officialLinks,
                refused: archived.refused,
                mode: archived.mode,
                latencyMS: archived.latencyMS,
                executionPath: archived.executionPath,
                validationPassed: archived.validationPassed,
                webSources: archived.webSources,
                deepThinking: archived.deepThinking,
                webSearch: archived.webSearch
            )
        }
        messages = restored.isEmpty
            ? [ChatMessage(role: .assistant, text: Self.welcomeText)]
            : restored
    }

    /// Starts a fresh conversation. A new `sessionID` gives the backend a clean
    /// session, so连续追问的范围记忆（上一轮学院/年级/主题）也随之重置。
    func startNewConversation() {
        streamTask?.cancel()
        streamTask = nil
        sessionID = Self.makeSessionID()
        activeConversationID = nil
        inputText = ""
        messages = [ChatMessage(role: .assistant, text: Self.welcomeText)]
        speechOutputText = nil
        isStreaming = false
    }

    private static func makeSessionID() -> String {
        "ios-\(UUID().uuidString.prefix(8))"
    }

    // MARK: - Options

    func loadOptionsIfNeeded() {
        guard options == nil, !isOptionsLoading else { return }
        reloadOptions()
    }

    func reloadOptions() {
        guard !isOptionsLoading else { return }
        isOptionsLoading = true
        optionsError = nil
        Task {
            do {
                let loaded = try await service.options()
                options = loaded
                reconcileScope(using: loaded)
            } catch {
                optionsError = "无法读取学院/年级选项：\(error.localizedDescription)"
            }
            isOptionsLoading = false
        }
    }

    func selectCollege(_ value: String?) {
        college = value
        guard let options, let major else { return }
        guard value != nil else {
            self.major = nil
            return
        }
        if !options.majors(for: cohort, college: value).contains(major) {
            self.major = nil
        }
    }

    func selectCohort(_ value: String?) {
        cohort = value
        guard let options, let major else { return }
        guard options.majors(for: value).contains(major) else {
            self.major = nil
            return
        }
        if let owner = options.college(for: major, cohort: value) {
            college = owner
        }
    }

    func selectMajor(_ value: String?) {
        major = value
        guard let value, let options else {
            return
        }
        if let college,
           options.belongsToCollege(value, college: college, cohort: cohort) {
            return
        }
        guard let owner = options.college(for: value, cohort: cohort) else { return }
        college = owner
    }

    func reconcileScopeSelection() {
        guard let options else { return }
        reconcileScope(using: options)
    }

    private func reconcileScope(using options: OptionsResponse) {
        if let cohort, !options.cohorts.contains(cohort) {
            self.cohort = nil
        }
        if let college, !options.colleges.contains(college) {
            self.college = nil
        }
        guard let major else { return }
        guard options.majors(for: cohort).contains(major) else {
            self.major = nil
            return
        }
        if let owner = options.college(for: major, cohort: cohort) {
            college = owner
        }
    }

    // MARK: - Ask

    func send() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        inputText = ""
        messages.append(ChatMessage(role: .user, text: text))
        let assistant = ChatMessage(
            role: .assistant,
            deepThinking: deepThinkingEnabled,
            webSearch: webSearchEnabled
        )
        let assistantID = assistant.id
        messages.append(assistant)
        isStreaming = true
        let deepThinking = deepThinkingEnabled
        let webSearch = webSearchEnabled
        let traceID = String(UUID().uuidString.prefix(8))
        let requestStartedAt = performanceClock.now
#if DEBUG
        lastAuditLLMCalled = nil
        lastAuditOutputSource = nil
        lastAuditFallbackReason = nil
#endif

        streamTask = Task {
            let revealBuffer = StreamRevealBuffer()
            let revealTask = Task {
                try await self.revealBufferedText(
                    from: revealBuffer,
                    assistantID: assistantID
                )
            }
            defer { revealTask.cancel() }

            do {
                let stream = try service.askStream(
                    question: text,
                    college: college,
                    cohort: cohort,
                    major: major,
                    sessionID: sessionID,
                    deepThinking: deepThinking,
                    webSearch: webSearch
                )
                var finalResponse: AskResponse?
                var networkCompletedAt: ContinuousClock.Instant?
                var receivedDelta = false
                for try await event in stream {
                    try Task.checkCancellation()
                    guard let index = messages.firstIndex(where: { $0.id == assistantID }) else {
                        return
                    }
                    switch event {
                    case let .metadata(mode, executionPath):
                        messages[index].mode = mode
                        messages[index].executionPath = executionPath
                    case .status:
                        continue
                    case let .delta(fragment):
                        receivedDelta = receivedDelta || !fragment.isEmpty
                        await revealBuffer.enqueue(fragment)
                    case let .reset(replacement):
                        await revealBuffer.reset()
                        messages[index].text = replacement
                        receivedDelta = !replacement.isEmpty
                    case let .final(response):
                        finalResponse = response
                        networkCompletedAt = performanceClock.now
                        performanceLogger.notice(
                            "ask_response trace=\(traceID, privacy: .public) path=\((response.executionPath ?? "unknown"), privacy: .public) request_ms=\(self.elapsedMilliseconds(from: requestStartedAt, to: networkCompletedAt ?? self.performanceClock.now), privacy: .public) server_ms=\((response.latencyMS ?? -1), privacy: .public)"
                        )
                    }
                }
                guard let response = finalResponse else {
                    throw URLError(.networkConnectionLost)
                }
                await revealBuffer.finish()
                try Task.checkCancellation()
                let firstVisibleAt = try await withTaskCancellationHandler {
                    try await revealTask.value
                } onCancel: {
                    revealTask.cancel()
                }
                let renderCompletedAt = performanceClock.now
                guard let index = messages.firstIndex(where: { $0.id == assistantID }) else {
                    return
                }
                if messages[index].text != response.answerMD {
                    messages[index].text = response.answerMD
                }
                finalize(response, at: index)
                logRenderingMetrics(
                    traceID: traceID,
                    characterCount: response.answerMD.count,
                    requestStartedAt: requestStartedAt,
                    firstVisibleAt: firstVisibleAt
                        ?? networkCompletedAt
                        ?? renderCompletedAt,
                    completedAt: renderCompletedAt
                )
            } catch {
                if error is CancellationError {
                    return
                }
                performanceLogger.error(
                    "ask_error trace=\(traceID, privacy: .public) request_ms=\(self.elapsedMilliseconds(from: requestStartedAt, to: self.performanceClock.now), privacy: .public) error=\(String(describing: type(of: error)), privacy: .public)"
                )
                messages.removeAll { $0.id == assistantID }
                appendRecoveryNotice(.from(error, retrying: text))
                isStreaming = false
                streamTask = nil
            }
        }
    }

    private func revealBufferedText(
        from buffer: StreamRevealBuffer,
        assistantID: UUID
    ) async throws -> ContinuousClock.Instant? {
        var firstVisibleAt: ContinuousClock.Instant?
        while true {
            try Task.checkCancellation()
            let next = await buffer.nextStep()
            if let step = next.step {
                guard let index = messages.firstIndex(where: { $0.id == assistantID }) else {
                    throw CancellationError()
                }
                if firstVisibleAt == nil {
                    firstVisibleAt = performanceClock.now
                }
                messages[index].text.append(step.text)
                try await Task<Never, Never>.sleep(
                    nanoseconds: step.delayNanoseconds
                )
            } else if next.isFinished {
                return firstVisibleAt
            } else {
                try await Task<Never, Never>.sleep(nanoseconds: 8_000_000)
            }
        }
    }

    func sendQuickPrompt(_ prompt: String) {
        // 回答生成中直接忽略，避免覆盖用户正在输入的内容。
        guard !isStreaming else { return }
        inputText = prompt
        send()
    }

    private func logRenderingMetrics(
        traceID: String,
        characterCount: Int,
        requestStartedAt: ContinuousClock.Instant,
        firstVisibleAt: ContinuousClock.Instant,
        completedAt: ContinuousClock.Instant
    ) {
        performanceLogger.notice(
            "ask_render trace=\(traceID, privacy: .public) chars=\(characterCount, privacy: .public) first_visible_ms=\(self.elapsedMilliseconds(from: requestStartedAt, to: firstVisibleAt), privacy: .public) complete_ms=\(self.elapsedMilliseconds(from: requestStartedAt, to: completedAt), privacy: .public) stream_visible_ms=\(self.elapsedMilliseconds(from: firstVisibleAt, to: completedAt), privacy: .public)"
        )
    }

    private func elapsedMilliseconds(
        from start: ContinuousClock.Instant,
        to end: ContinuousClock.Instant
    ) -> Double {
        let components = start.duration(to: end).components
        return Double(components.seconds) * 1_000
            + Double(components.attoseconds) / 1_000_000_000_000_000
    }

    private func finalize(_ response: AskResponse, at index: Int) {
        messages[index].citations = response.citations
        messages[index].retrieved = response.retrieved
        messages[index].officialLinks = response.officialLinks
        messages[index].refused = response.refused
        messages[index].mode = response.mode
        messages[index].latencyMS = response.latencyMS
        messages[index].executionPath = response.executionPath
        messages[index].validationPassed = response.validationPassed
        messages[index].webSources = response.webSources
#if DEBUG
        lastAuditLLMCalled = response.llmCalled
        lastAuditOutputSource = response.finalOutputSource
        lastAuditFallbackReason = response.fallbackReason
#endif
        isStreaming = false
        streamTask = nil
        publishSpeechOutput(response.answerMD)
    }

    private func publishSpeechOutput(_ markdown: String) {
        // 朗读时去掉引用角标 [1] 之类的标记。
        let text = markdown
            .replacing(/\[\d+\]/, with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        speechOutputText = text
    }

    func appendRecoveryNotice(_ notice: FallbackNotice) {
        messages.append(ChatMessage(role: .notice, notice: notice))
    }

#if DEBUG
    private struct SimulatorAuditScenario: Decodable {
        let id: String
        let question: String
        let scopeMode: String?
        let college: String?
        let cohort: String?
        let major: String?
        let newSession: Bool?
        let deepThinking: Bool?
        let webSearch: Bool?
    }

    private struct SimulatorAuditResult: Encodable {
        let id: String
        let question: String
        let college: String?
        let cohort: String?
        let major: String?
        let answer: String
        let mode: String?
        let executionPath: String?
        let refused: Bool?
        let validationPassed: Bool?
        let citationTitles: [String]
        let citationPages: [Int]
        let webSourceTitles: [String]
        let latencyMS: Double?
        let llmCalled: Bool?
        let finalOutputSource: String?
        let fallbackReason: String?
        let error: String?
    }

    /// Runs real, streamed App conversations only when explicitly launched with
    /// `SWUFE_SIMULATOR_AUDIT_BASE64`. Release builds contain no audit runner.
    func runSimulatorAuditIfConfigured() async -> Bool {
        let environment = ProcessInfo.processInfo.environment
        guard let encoded = environment["SWUFE_SIMULATOR_AUDIT_BASE64"],
              let data = Data(base64Encoded: encoded),
              let scenarios = try? JSONDecoder().decode(
                  [SimulatorAuditScenario].self,
                  from: data
              ),
              !scenarios.isEmpty else {
            return false
        }

        let storedScope = (college, cohort, major)
        suppressScopePersistence = true
        defer {
            college = storedScope.0
            cohort = storedScope.1
            major = storedScope.2
            suppressScopePersistence = false
        }

        var results: [SimulatorAuditResult] = []
        for (index, scenario) in scenarios.enumerated() {
            if scenario.newSession ?? (index > 0) {
                startNewConversation()
            }
            switch scenario.scopeMode {
            case "none":
                college = nil
                cohort = nil
                major = nil
            case "explicit":
                college = scenario.college
                cohort = scenario.cohort
                major = scenario.major
            case "stored":
                college = storedScope.0
                cohort = storedScope.1
                major = storedScope.2
            default:
                break
            }
            deepThinkingEnabled = scenario.deepThinking ?? false
            webSearchEnabled = scenario.webSearch ?? false

            let messageStart = messages.count
            sendQuickPrompt(scenario.question)
            let deadline = Date().addingTimeInterval(150)
            while isStreaming, Date() < deadline {
                try? await Task<Never, Never>.sleep(nanoseconds: 100_000_000)
            }

            var failure: String?
            if isStreaming {
                let timedOutTask = streamTask
                timedOutTask?.cancel()
                await timedOutTask?.value
                streamTask = nil
                isStreaming = false
                failure = "timeout"
            }
            let newMessages = messages.dropFirst(messageStart)
            let response = newMessages.last { $0.role == .assistant }
            let notice = newMessages.last { $0.role == .notice }?.notice
            if failure == nil, let notice {
                failure = "\(notice.title)：\(notice.message)"
            }
            results.append(
                SimulatorAuditResult(
                    id: scenario.id,
                    question: scenario.question,
                    college: college,
                    cohort: cohort,
                    major: major,
                    answer: response?.text ?? "",
                    mode: response?.mode,
                    executionPath: response?.executionPath,
                    refused: response?.refused,
                    validationPassed: response?.validationPassed,
                    citationTitles: response?.citations.map(\.docTitle) ?? [],
                    citationPages: response?.citations.compactMap(\.physicalPage) ?? [],
                    webSourceTitles: response?.webSources.map(\.title) ?? [],
                    latencyMS: response?.latencyMS,
                    llmCalled: lastAuditLLMCalled,
                    finalOutputSource: lastAuditOutputSource,
                    fallbackReason: lastAuditFallbackReason,
                    error: failure
                )
            )
        }

        guard let output = try? JSONEncoder().encode(results),
              let documents = FileManager.default.urls(
                  for: .documentDirectory,
                  in: .userDomainMask
              ).first else {
            return true
        }
        try? output.write(
            to: documents.appendingPathComponent("simulator-chat-audit.json"),
            options: .atomic
        )
        return true
    }
#endif
}

extension FallbackNotice {
    static func from(_ error: Error, retrying question: String) -> FallbackNotice {
        if let apiError = error as? RecoverableAPIError {
            if apiError.statusCode == 503 {
                return FallbackNotice(
                    code: "service_unavailable",
                    title: "服务暂时不可用",
                    message: apiError.message.isEmpty
                        ? "知识库、索引或生成模型未就绪，请稍后再试。"
                        : apiError.message,
                    actions: [
                        RecoveryAction(label: "再试一次", prompt: question),
                        RecoveryAction(label: "换个问题", prompt: "本科生转专业需要什么条件？")
                    ],
                    severity: "warning"
                )
            }
            return FallbackNotice(
                code: "request_failed",
                title: "这次提问没有成功",
                message: apiError.message,
                actions: [
                    RecoveryAction(label: "再试一次", prompt: question)
                ],
                severity: "warning"
            )
        }
        return FallbackNotice(
            code: "network_failed",
            title: "网络连接失败",
            message: "没有连上教务问答后端：\(error.localizedDescription)",
            actions: [
                RecoveryAction(label: "重试提问", prompt: question)
            ],
            severity: "error"
        )
    }
}
