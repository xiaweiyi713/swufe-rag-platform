import Foundation

struct APIClient {
    static let defaultBaseURL = URL(string: "http://127.0.0.1:8000")!

    /// UserDefaults 覆盖键，「关于」页的后端地址输入框写这里。
    static let baseURLOverrideKey = "swufeask.apiBaseURL"

    /// 每次请求现读配置：在「关于」页改后端地址后即时生效，无需重启。
    var baseURL: URL {
        Self.configuredBaseURL()
    }

    private static func configuredBaseURL() -> URL {
        let candidates = [
            UserDefaults.standard.string(forKey: baseURLOverrideKey),
            Bundle.main.object(forInfoDictionaryKey: "SWUFE_ASK_API_BASE_URL") as? String
        ]

        for raw in candidates.compactMap({ $0 }) {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty,
                  let url = URL(string: trimmed),
                  url.scheme != nil,
                  url.host != nil else {
                continue
            }
            return url
        }
        return defaultBaseURL
    }

    static func validate(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard 200..<300 ~= http.statusCode else {
            let detail = (try? JSONDecoder().decode(APIErrorResponse.self, from: data))?.detailText
            throw RecoverableAPIError(
                statusCode: http.statusCode,
                message: detail?.message ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode),
                code: detail?.code
            )
        }
    }
}

struct RecoverableAPIError: LocalizedError {
    let statusCode: Int
    let message: String
    let code: String?

    var errorDescription: String? {
        message
    }
}

private struct StreamProtocolError: LocalizedError {
    let message: String
    let code: String?

    var errorDescription: String? { message }

    var allowsDeterministicFallback: Bool {
        guard let code else { return false }
        return [
            "provider_authentication_failed",
            "provider_permission_denied",
            "provider_model_not_found"
        ].contains(code)
    }
}

/// FastAPI 标准错误体 `{"detail": ...}`。Pydantic 422 时 detail 是数组，
/// 其余场景是字符串，两种都兼容。
private struct APIErrorResponse: Decodable {
    struct Detail {
        let message: String
        let code: String?
    }

    let detailText: Detail?

    enum CodingKeys: String, CodingKey {
        case detail
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let text = try? container.decode(String.self, forKey: .detail) {
            detailText = Detail(message: text, code: nil)
        } else if let value = try? container.decode(StructuredDetail.self, forKey: .detail) {
            detailText = Detail(message: value.message, code: value.code)
        } else if let items = try? container.decode([ValidationItem].self, forKey: .detail) {
            detailText = Detail(
                message: items.compactMap(\.msg).joined(separator: "；"),
                code: nil
            )
        } else {
            detailText = nil
        }
    }

    private struct ValidationItem: Decodable {
        let msg: String?
    }

    private struct StructuredDetail: Decodable {
        let code: String?
        let message: String
    }
}

enum LLMModelDiscoveryError: LocalizedError {
    case invalidEndpoint
    case noChatModels

    var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            return "模型端点地址无效。"
        case .noChatModels:
            return "Key 有效，但服务商没有返回可用于对话的模型。"
        }
    }
}

/// The common `/models` response used by OpenAI-compatible providers. A few
/// providers return `models` instead of `data`, so both shapes are accepted.
struct LLMModelDiscovery {
    private struct Response: Decodable {
        let models: [String]
    }

    static func fetch(baseURL: String, apiKey: String) async throws -> [LLMModelOption] {
        guard URL(string: baseURL)?.host != nil,
              !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw LLMModelDiscoveryError.invalidEndpoint
        }

        var request = URLRequest(url: APIClient().baseURL.appending(path: "/llm/models"))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue(apiKey.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "X-LLM-API-Key")
        request.setValue(baseURL.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "X-LLM-Base-URL")
        let (data, response) = try await URLSession.shared.data(for: request)
        try APIClient.validate(response, data: data)

        let payload = try JSONDecoder().decode(Response.self, from: data)
        let items = payload.models.compactMap { raw -> LLMModelOption? in
                let modelID = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !modelID.isEmpty, isLikelyChatModel(modelID) else { return nil }
                return LLMModelOption(name: modelID, caption: "服务商返回")
            }

        var unique = [String: LLMModelOption]()
        for item in items {
            unique[item.name] = item
        }
        let result = unique.values.sorted {
            $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
        guard !result.isEmpty else { throw LLMModelDiscoveryError.noChatModels }
        return result
    }

    private static func isLikelyChatModel(_ modelID: String) -> Bool {
        let value = modelID.lowercased()
        let nonChatMarkers = [
            "embedding", "embed-", "rerank", "moderation", "whisper",
            "transcription", "tts", "speech", "text-to-image", "image-generation",
            "image-gen", "video-generation", "-asr", "-ocr"
        ]
        return !nonChatMarkers.contains { value.contains($0) }
    }
}

struct LLMConnectionValidation {
    private struct Response: Decodable {
        let valid: Bool
        let model: String
    }

    static func validate(baseURL: String, apiKey: String, model: String) async throws {
        var request = URLRequest(url: APIClient().baseURL.appending(path: "/llm/validate"))
        request.httpMethod = "POST"
        request.timeoutInterval = 45
        request.setValue(apiKey.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "X-LLM-API-Key")
        request.setValue(baseURL.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "X-LLM-Base-URL")
        request.setValue(model.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "X-LLM-Model")
        let (data, response) = try await URLSession.shared.data(for: request)
        try APIClient.validate(response, data: data)
        let payload = try JSONDecoder().decode(Response.self, from: data)
        guard payload.valid, payload.model == model.trimmingCharacters(in: .whitespacesAndNewlines) else {
            throw URLError(.badServerResponse)
        }
    }
}

/// swufe-rag 正式 HTTP 接口客户端：`POST /ask`、`GET /options`、`GET /source/{chunk_id}`。
struct AskAPIService {
    private let client = APIClient()
    private let decoder = JSONDecoder()

    /// 路由优先的统一问答。后端一次性返回完整 JSON（无流式）。
    /// 学校分支可能包含 SQL/RAG 检索 + LLM 生成，超时留足余量。
    /// V16 BYOK：钥匙串里有 LLM API Key 时随请求头发送(仅本次请求,不落日志),
    /// 无 Key 时后端走确定性降级链路。
    func ask(
        question: String,
        college: String?,
        cohort: String?,
        major: String?,
        sessionID: String,
        deepThinking: Bool = false,
        webSearch: Bool = false
    ) async throws -> AskResponse {
        let request = try askRequest(
            path: "/ask",
            question: question,
            college: college,
            cohort: cohort,
            major: major,
            sessionID: sessionID,
            deepThinking: deepThinking,
            webSearch: webSearch
        )
        let (data, response) = try await URLSession.shared.data(for: request)
        try APIClient.validate(response, data: data)
        return try decoder.decode(AskResponse.self, from: data)
    }

    func askStream(
        question: String,
        college: String?,
        cohort: String?,
        major: String?,
        sessionID: String,
        deepThinking: Bool = false,
        webSearch: Bool = false
    ) throws -> AsyncThrowingStream<AskStreamEvent, Error> {
        let request = try askRequest(
            path: "/ask/stream",
            question: question,
            college: college,
            cohort: cohort,
            major: major,
            sessionID: sessionID,
            deepThinking: deepThinking,
            webSearch: webSearch
        )
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse,
                          200..<300 ~= http.statusCode else {
                        var data = Data()
                        for try await byte in bytes {
                            data.append(byte)
                        }
                        try APIClient.validate(response, data: data)
                        throw URLError(.badServerResponse)
                    }

                    var receivedFinal = false
                    for try await line in bytes.lines {
                        guard !line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                            continue
                        }
                        let envelope = try decoder.decode(
                            AskStreamEnvelope.self,
                            from: Data(line.utf8)
                        )
                        switch envelope.type {
                        case "meta":
                            continuation.yield(
                                .metadata(
                                    mode: envelope.mode,
                                    executionPath: envelope.executionPath
                                )
                            )
                        case "status":
                            continuation.yield(
                                .status(
                                    stage: envelope.stage,
                                    message: envelope.message
                                )
                            )
                        case "delta":
                            if let text = envelope.text, !text.isEmpty {
                                continuation.yield(.delta(text))
                            }
                        case "final":
                            guard let response = envelope.response else {
                                throw StreamProtocolError(
                                    message: "流式回答缺少最终响应。",
                                    code: "stream_missing_final"
                                )
                            }
                            receivedFinal = true
                            continuation.yield(.final(response))
                        case "error":
                            throw StreamProtocolError(
                                message: envelope.message ?? "流式回答暂时不可用。",
                                code: envelope.errorCode
                            )
                        default:
                            continue
                        }
                    }
                    if !receivedFinal {
                        throw StreamProtocolError(
                            message: "流式连接提前结束。",
                            code: "stream_ended_early"
                        )
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    private func askRequest(
        path: String,
        question: String,
        college: String?,
        cohort: String?,
        major: String?,
        sessionID: String,
        deepThinking: Bool,
        webSearch: Bool
    ) throws -> URLRequest {
        var request = URLRequest(url: client.baseURL.appending(path: path))
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
        if let config = LLMConfigStore.current(), let apiKey = LLMConfigStore.apiKey() {
            request.setValue(apiKey, forHTTPHeaderField: "X-LLM-API-Key")
            request.setValue(config.baseURL, forHTTPHeaderField: "X-LLM-Base-URL")
            request.setValue(config.model, forHTTPHeaderField: "X-LLM-Model")
        }
        request.httpBody = try JSONEncoder().encode(
            AskPayload(
                question: question,
                college: college,
                cohort: cohort,
                major: major,
                sessionID: sessionID,
                deepThinking: deepThinking,
                webSearch: webSearch
            )
        )
        return request
    }

    func options() async throws -> OptionsResponse {
        var request = URLRequest(url: client.baseURL.appending(path: "/options"))
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        try APIClient.validate(response, data: data)
        return try decoder.decode(OptionsResponse.self, from: data)
    }

    func source(chunkID: String) async throws -> KnowledgeChunk {
        var request = URLRequest(url: client.baseURL.appending(path: "/source/\(chunkID)"))
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        try APIClient.validate(response, data: data)
        return try decoder.decode(KnowledgeChunk.self, from: data)
    }
}

/// V16 后端的 AskRequest 是 extra=forbid 严格模式,只发送契约允许的键;
/// college/cohort/major 为 nil 时省略而不是发 null。
private struct AskPayload: Encodable {
    let question: String
    let college: String?
    let cohort: String?
    let major: String?
    let sessionID: String
    let deepThinking: Bool
    let webSearch: Bool

    enum CodingKeys: String, CodingKey {
        case question
        case college
        case cohort
        case major
        case sessionID = "session_id"
        case deepThinking = "deep_thinking"
        case webSearch = "web_search"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(question, forKey: .question)
        try container.encodeIfPresent(college, forKey: .college)
        try container.encodeIfPresent(cohort, forKey: .cohort)
        try container.encodeIfPresent(major, forKey: .major)
        try container.encode(sessionID, forKey: .sessionID)
        try container.encode(deepThinking, forKey: .deepThinking)
        try container.encode(webSearch, forKey: .webSearch)
    }
}
