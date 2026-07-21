import SwiftData
import SwiftUI

struct ChatView: View {
    @Bindable var model: ChatViewModel
    @Environment(\.modelContext) private var modelContext
    @State private var showsScope = false
    @State private var showsAbout = false
    @State private var showsSchedule = false
    @State private var showsGrades = false
    @State private var showsSidebarSettings = false
    @State private var showsLLMSettings = false
    @State private var speechInput = SpeechInputController()
    @State private var speechOutput = SpeechOutputController()
    @State private var isListening = false
    @State private var isSpeechOutputEnabled = false
    @State private var showsSidebar = false
    @State private var showsVoiceSettings = false
    @State private var activeLLMConfig = LLMConfigStore.current()
    @State private var availableLLMModels = LLMConfigStore.availableModels()
    @AppStorage("voice.rate.v1") private var speechRate = 0.92
    @AppStorage("voice.voiceId.v1") private var speechVoiceId = ""
    @AppStorage("voice.loop.v1") private var voiceLoopEnabled = false

    private let prompts = [
        "毕业需要修满多少学分？",
        "挂科后还能申请推免吗？",
        "转专业需要满足什么条件？",
        "推免综合测评怎么计算？",
        "学业预警的标准是什么？",
        "辅修学位怎么申请？"
    ]

    private let welcomeActions = [
        WelcomeAction(title: "培养方案", subtitle: "学分与课程要求", icon: "list.bullet.rectangle", prompt: "我的专业毕业需要修满多少学分？"),
        WelcomeAction(title: "推免细则", subtitle: "资格与综合测评", icon: "medal", prompt: "推免资格有哪些基本条件？"),
        WelcomeAction(title: "学籍管理", subtitle: "转专业 · 休学", icon: "person.text.rectangle", prompt: "转专业需要满足什么条件？"),
        WelcomeAction(title: "课程考核", subtitle: "补考与重修", icon: "checkmark.seal", prompt: "挂科后能补考吗？")
    ]

    var body: some View {
        NavigationStack {
            ZStack {
                LiquidBackdrop()

                GeometryReader { proxy in
                    let windowInsets = currentWindowSafeAreaInsets
                    let safeTop = proxy.safeAreaInsets.top > 0 ? proxy.safeAreaInsets.top : windowInsets.top
                    let safeBottom = proxy.safeAreaInsets.bottom > 0 ? proxy.safeAreaInsets.bottom : windowInsets.bottom
                    let drawerWidth = min(max(proxy.size.width * 0.76, 280), 340)
                    let exposedWidth = max(proxy.size.width - drawerWidth, 0)
                    let drawerHeight = proxy.size.height + safeTop + safeBottom
                    let drawerYOffset = -safeTop

                    ZStack(alignment: .leading) {
                        chatMainLayer(topInset: safeTop, bottomInset: safeBottom)
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .offset(x: showsSidebar ? drawerWidth : 0)
                            .brightness(showsSidebar ? -0.08 : 0)
                            .scaleEffect(showsSidebar ? 0.985 : 1, anchor: .leading)
                            .overlay {
                                if showsSidebar {
                                    Rectangle()
                                        .fill(.black.opacity(0.12))
                                        .allowsHitTesting(false)
                                }
                            }
                            .allowsHitTesting(!showsSidebar)

                        ZStack(alignment: .topLeading) {
                            Rectangle()
                                .fill(.ultraThinMaterial)
                                .frame(width: drawerWidth, height: drawerHeight)
                                .offset(y: drawerYOffset)

                            SidebarView(
                                model: model,
                                isOpen: $showsSidebar,
                                topInset: safeTop,
                                openScope: { showsSidebar = false; showsScope = true },
                                openSchedule: { showsSidebar = false; showsSchedule = true },
                                openGrades: { showsSidebar = false; showsGrades = true },
                                openSettings: { showsSidebar = false; showsSidebarSettings = true }
                            )
                            .frame(width: drawerWidth, height: proxy.size.height, alignment: .topLeading)
                        }
                        .frame(width: drawerWidth, height: proxy.size.height, alignment: .topLeading)
                        .overlay(alignment: .trailing) {
                            Rectangle()
                                .fill(Theme.Color.cardStroke)
                                .frame(width: 1, height: drawerHeight)
                                .offset(y: drawerYOffset)
                        }
                        .offset(x: showsSidebar ? 0 : -drawerWidth)

                        if showsSidebar {
                            Button(action: closeSidebar) {
                                Color.clear
                                    .frame(width: exposedWidth, height: drawerHeight)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .offset(x: drawerWidth, y: drawerYOffset)
                            .accessibilityLabel("返回聊天")
                        }
                    }
                    .frame(width: proxy.size.width, height: proxy.size.height)
                }
            }
            .ignoresSafeArea()
            .animation(.interactiveSpring(response: 0.52, dampingFraction: 0.9, blendDuration: 0.12), value: showsSidebar)
            .toolbar(.hidden, for: .navigationBar)
            .sensoryFeedback(.impact(weight: .light), trigger: model.isStreaming)
            .sensoryFeedback(.warning, trigger: model.errorMessage) { _, newValue in
                newValue != nil
            }
            .sheet(isPresented: $showsScope) {
                ScopeSettingsView(model: model)
            }
            .sheet(isPresented: $showsAbout) {
                AboutView()
            }
            .sheet(isPresented: $showsSchedule) {
                ScheduleView()
            }
            .sheet(isPresented: $showsGrades) {
                GradeView()
            }
            .sheet(isPresented: $showsSidebarSettings) {
                SidebarSettingsView(
                    model: model,
                    openLLMSettings: {
                        showsSidebarSettings = false
                        showsLLMSettings = true
                    },
                    openAbout: {
                        showsSidebarSettings = false
                        showsAbout = true
                    }
                )
            }
            .sheet(isPresented: $showsLLMSettings, onDismiss: refreshLLMSelection) {
                LLMSettingsView()
            }
            .sheet(isPresented: $showsVoiceSettings) {
                VoiceSettingsView(
                    rate: $speechRate,
                    voiceId: $speechVoiceId,
                    loopEnabled: $voiceLoopEnabled,
                    previewVoice: previewVoice
                )
                .presentationDetents([.medium, .large])
            }
            .alert("请求失败", isPresented: errorAlertBinding) {
            } message: {
                Text(model.errorMessage ?? "")
            }
            .onChange(of: model.speechOutputText) { _, text in
                guard isSpeechOutputEnabled, let text else { return }
                speakReply(text)
            }
            .onChange(of: model.isStreaming) { _, isStreaming in
                guard !isStreaming else { return }
                syncRestoredConversation()
            }
            .task {
                refreshLLMSelection()
#if DEBUG
                if await model.runSimulatorAuditIfConfigured() {
                    return
                }
#endif
                model.loadOptionsIfNeeded()
            }
        }
    }

    /// 系统关闭 alert 时会写回 false，这里同步清空错误信息，
    /// 避免 `.constant` 绑定造成的状态失步。
    private var errorAlertBinding: Binding<Bool> {
        Binding(
            get: { model.errorMessage != nil },
            set: { isPresented in
                if !isPresented {
                    model.errorMessage = nil
                }
            }
        )
    }

    private var currentWindowSafeAreaInsets: UIEdgeInsets {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first { $0.isKeyWindow }?
            .safeAreaInsets ?? .zero
    }

    /// Keep a restored history item current after a follow-up answer finishes.
    private func syncRestoredConversation() {
        guard let activeID = model.activeConversationID, model.hasUserMessages else { return }
        let targetID = activeID
        let descriptor = FetchDescriptor<StoredConversation>(
            predicate: #Predicate { $0.id == targetID }
        )
        guard let conversation = (try? modelContext.fetch(descriptor))?.first else { return }
        conversation.update(
            title: model.conversationTitle,
            messages: model.archivedMessages,
            sessionID: model.sessionID,
            college: model.college,
            cohort: model.cohort,
            major: model.major
        )
        try? modelContext.save()
    }

    private func chatMainLayer(topInset: CGFloat, bottomInset: CGFloat) -> some View {
        ZStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(model.messages) { message in
                            MessageRow(
                                message: message,
                                sendPrompt: { prompt in
                                    model.sendQuickPrompt(prompt)
                                },
                                isStreaming: model.isStreaming
                                    && message.role == .assistant
                                    && message.id == model.messages.last?.id
                            )
                            .id(message.id)
                        }
                        if model.messages.count <= 1 {
                            WelcomeActionPanel(actions: welcomeActions) { prompt in
                                model.sendQuickPrompt(prompt)
                            }
                            .id("welcome-actions")
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 18)
                    .padding(.bottom, 12)
                }
                .scrollContentBackground(.hidden)
                .scrollIndicators(.hidden)
                .contentMargins(.top, topInset + 74, for: .scrollContent)
                .contentMargins(.bottom, bottomInset + 214, for: .scrollContent)
                .onChange(of: model.messages.count) {
                    scrollToBottom(proxy, animated: true)
                }
                .onChange(of: model.messages.last?.text) {
                    // 打字机播放时正文不断长高，持续贴底跟随（无动画避免抖动）。
                    guard model.isStreaming else { return }
                    scrollToBottom(proxy, animated: false)
                }
            }
        }
        .overlay(alignment: .top) {
            VStack(spacing: 0) {
                ChatTopBar(
                    model: model,
                    isSpeechOn: isSpeechOutputEnabled,
                    openSidebar: { withAnimation(Theme.Motion.spring) { showsSidebar = true } },
                    toggleSpeech: toggleSpeechOutput,
                    openVoiceSettings: { showsVoiceSettings = true }
                )
                .padding(.top, topInset)
                .padding(.bottom, 10)
            }
            .frame(maxWidth: .infinity)
        }
        .overlay(alignment: .bottom) {
            VStack(spacing: 8) {
                if isListening {
                    ListeningBanner(
                        partialText: model.inputText,
                        isVoiceLoop: voiceLoopEnabled,
                        stop: { toggleSpeechInput() }
                    )
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                QuickPromptBar(prompts: prompts) { prompt in
                    model.sendQuickPrompt(prompt)
                }
                ComposerModeBar(
                    deepThinking: $model.deepThinkingEnabled,
                    webSearch: $model.webSearchEnabled,
                    llmConfig: activeLLMConfig,
                    modelOptions: availableLLMModels,
                    isDisabled: model.isStreaming,
                    selectModel: selectLLMModel
                )
                ComposerView(
                    text: $model.inputText,
                    isStreaming: model.isStreaming,
                    isListening: isListening,
                    toggleSpeech: {
                        toggleSpeechInput()
                    }
                ) {
                    model.send()
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, bottomInset + 4)
            .animation(.snappy(duration: 0.24), value: isListening)
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy, animated: Bool) {
        guard let id = model.messages.last?.id else { return }
        if animated {
            withAnimation(.snappy) {
                proxy.scrollTo(id, anchor: .bottom)
            }
        } else {
            proxy.scrollTo(id, anchor: .bottom)
        }
    }

    private func refreshLLMSelection() {
        activeLLMConfig = LLMConfigStore.current()
        availableLLMModels = LLMConfigStore.availableModels()
    }

    private func selectLLMModel(_ modelName: String) {
        guard !model.isStreaming else { return }
        LLMConfigStore.selectModel(modelName)
        refreshLLMSelection()
    }

    private func closeSidebar() {
        withAnimation(.interactiveSpring(response: 0.52, dampingFraction: 0.9, blendDuration: 0.12)) {
            showsSidebar = false
        }
    }

    private func toggleSpeechOutput() {
        isSpeechOutputEnabled.toggle()
        if !isSpeechOutputEnabled {
            speechOutput.stop()
        }
    }

    private func speakReply(_ text: String) {
        speechOutput.rateMultiplier = Float(speechRate)
        speechOutput.voiceIdentifier = speechVoiceId.isEmpty ? nil : speechVoiceId
        speechOutput.speak(text)
    }

    private func previewVoice() {
        speakReply("你好，我是西财教务问答助手，这是当前的语速和音色效果。")
    }

    private func toggleSpeechInput() {
        if isListening {
            speechInput.stop()
            isListening = false
            return
        }
        speechInput.start(
            onTranscript: { transcript in
                model.inputText = transcript
            },
            onError: { message in
                model.errorMessage = message
                isListening = false
            },
            onFinish: {
                isListening = false
                // Hands-free voice loop: once recognition settles, auto-send the
                // recognised text and read the reply aloud so the user can keep
                // the whole exchange voice-only.
                guard voiceLoopEnabled else { return }
                let recognised = model.inputText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !recognised.isEmpty, !model.isStreaming else { return }
                isSpeechOutputEnabled = true
                model.send()
            }
        )
        isListening = true
    }
}
