import SwiftUI

/// 对话模型设置:选择厂商 → 填 API Key、选模型 → 测试/保存启用。
/// 结构复刻自“字节AI全栈挑战赛”客户端的 ModelBrain 设置页;
/// 保存后 Key 存本机钥匙串,随每次提问以请求头发给后端(BYOK)。
struct LLMSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var searchText = ""
    @State private var activeConfig = LLMConfigStore.stored()
    @State private var statusMessage: String?

    private var filteredProviders: [LLMProviderPreset] {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return LLMProviderPreset.all }
        return LLMProviderPreset.all.filter { preset in
            preset.name.localizedStandardContains(query)
                || preset.subtitle.localizedStandardContains(query)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    TextField("搜索提供商…", text: $searchText)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section {
                    ForEach(filteredProviders) { preset in
                        NavigationLink(value: preset) {
                            ProviderListRow(preset: preset, isActive: preset.isActive(config: activeConfig))
                        }
                    }
                } header: {
                    Text("提供商")
                } footer: {
                    Text("选择提供商后,下一页会自动填好 API 端点和推荐模型,只需粘贴你的 API Key。回答仍由教务后端检索与校验,模型只负责理解与表达。")
                }

                Section {
                    LabeledContent("当前", value: LLMConfigStore.summary)
                        .font(.footnote)
                    Button("恢复降级模式(清除 Key)", role: .destructive, action: clearConfig)
                        .disabled(activeConfig == nil)
                    if let statusMessage {
                        ResultBanner(message: statusMessage)
                            .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
                    }
                }
            }
            .navigationTitle("对话模型")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: LLMProviderPreset.self) { preset in
                ProviderDetailView(preset: preset, activeConfig: $activeConfig, statusMessage: $statusMessage)
            }
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
    }

    private func clearConfig() {
        LLMConfigStore.clear()
        activeConfig = nil
        statusMessage = "已恢复降级模式,后续提问不携带 Key。"
    }
}

// MARK: - 厂商详情

private struct ProviderDetailView: View {
    let preset: LLMProviderPreset
    @Binding var activeConfig: LLMConfigStore.Config?
    @Binding var statusMessage: String?

    @Environment(\.dismiss) private var dismiss
    @State private var baseURL = ""
    @State private var apiKey = ""
    @State private var model = ""
    @State private var modelOptions: [LLMModelOption] = []
    @State private var modelSearchText = ""
    @State private var usesDiscoveredModels = false
    @State private var isDiscoveringModels = false
    @State private var modelDiscoveryMessage: String?
    @State private var discoveryTask: Task<Void, Never>?
    @State private var revealsKey = false
    @State private var isTesting = false
    @State private var testMessage: String?
    @State private var validatedSignature: String?
    @FocusState private var keyFieldFocused: Bool

    private var canSubmit: Bool {
        !apiKey.trimmingCharacters(in: .whitespaces).isEmpty
            && !model.trimmingCharacters(in: .whitespaces).isEmpty
            && URL(string: baseURL)?.host != nil
    }

    private var configurationSignature: String {
        [baseURL, model, apiKey]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .joined(separator: "\u{0}")
    }

    private var filteredModelOptions: [LLMModelOption] {
        let query = modelSearchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return modelOptions }
        return modelOptions.filter {
            $0.name.localizedStandardContains(query)
                || $0.caption.localizedStandardContains(query)
        }
    }

    private var visibleModelOptions: ArraySlice<LLMModelOption> {
        filteredModelOptions.prefix(120)
    }

    var body: some View {
        Form {
            Section {
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 8) {
                        TextField(preset.baseURL, text: $baseURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                            .font(.callout.monospaced())
                        Text(preset.endpointNote)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } label: {
                    Label("API 端点(高级)", systemImage: "server.rack")
                }

                VStack(alignment: .leading, spacing: 10) {
                    Label("API Key", systemImage: "key")
                        .font(.subheadline.weight(.semibold))
                    APIKeyInputBox(
                        apiKey: $apiKey,
                        revealsKey: $revealsKey,
                        placeholder: preset.keyPlaceholder,
                        focus: $keyFieldFocused
                    )
                    Button {
                        Task { await discoverModels() }
                    } label: {
                        Label(
                            isDiscoveringModels ? "正在识别可用模型…" : "自动识别可用模型",
                            systemImage: isDiscoveringModels ? "hourglass" : "wand.and.stars"
                        )
                    }
                    .buttonStyle(.bordered)
                    .disabled(
                        apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || isDiscoveringModels
                            || isTesting
                    )
                    Text("Key 保存在本机钥匙串,仅随每次提问通过请求头发送,不写日志。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if let modelDiscoveryMessage {
                        Text(modelDiscoveryMessage)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text(preset.subtitle)
            }

            Section {
                TextField("自定义模型名", text: $model)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.callout.monospaced())
                if usesDiscoveredModels && modelOptions.count > 1 {
                    TextField("搜索已识别的模型", text: $modelSearchText)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.callout.monospaced())
                }
                ForEach(visibleModelOptions) { option in
                    Button {
                        model = option.name
                    } label: {
                        ModelOptionRow(option: option, isSelected: model == option.name)
                    }
                    .buttonStyle(.plain)
                }
                if filteredModelOptions.count > visibleModelOptions.count {
                    Text("已显示前 \(visibleModelOptions.count) 个结果,可用上方搜索继续筛选。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("模型")
            } footer: {
                Text(usesDiscoveredModels
                    ? "列表来自当前 API Key 的模型权限,只展示看起来可用于对话的模型。模型名仍可手动修改。"
                    : "先填入 API Key,系统会自动读取服务商返回的可用模型;也可以直接填写自定义模型名。")
            }

            Section {
                if isTesting {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("正在连接模型服务…")
                            .foregroundStyle(.secondary)
                    }
                }
                if let testMessage {
                    ResultBanner(message: testMessage)
                        .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
                }
            } footer: {
                Text("模型可以替换,但检索、引用溯源与事实校验始终由教务后端控制。")
            }
        }
        .navigationTitle(preset.name)
        .navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom) {
            ActionBar(
                canSubmit: canSubmit,
                canSave: canSubmit && validatedSignature == configurationSignature,
                isTesting: isTesting,
                test: { Task { await testConnection() } },
                save: save
            )
        }
        .onAppear(perform: prefill)
        .onChange(of: apiKey) { _, _ in
            scheduleModelDiscovery()
        }
        .onDisappear {
            discoveryTask?.cancel()
        }
        .task {
            // 进页后自动聚焦 Key 输入框,粘贴即可,免去点小输入框。
            try? await Task.sleep(for: .milliseconds(700))
            keyFieldFocused = true
        }
    }

    private func prefill() {
        baseURL = preset.baseURL
        modelOptions = preset.models
        model = preset.models.first?.name ?? ""
        usesDiscoveredModels = false
        modelDiscoveryMessage = nil
        if let config = activeConfig, config.providerID == preset.id {
            baseURL = config.baseURL
            model = config.model
            apiKey = LLMConfigStore.apiKey() ?? ""
            let savedModels = LLMConfigStore.availableModels()
            if !savedModels.isEmpty {
                modelOptions = savedModels
                usesDiscoveredModels = savedModels != preset.models
            }
        }
        validatedSignature = LLMConfigStore.isValidated ? configurationSignature : nil
    }

    private func scheduleModelDiscovery() {
        discoveryTask?.cancel()
        let candidate = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard candidate.count >= 12 else {
            modelOptions = preset.models
            usesDiscoveredModels = false
            modelDiscoveryMessage = nil
            return
        }

        modelOptions = preset.models
        usesDiscoveredModels = false
        modelDiscoveryMessage = "已检测到 Key,正在准备识别模型…"
        discoveryTask = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(850))
            guard !Task.isCancelled,
                  candidate == apiKey.trimmingCharacters(in: .whitespacesAndNewlines) else { return }
            await discoverModels()
        }
    }

    private func discoverModels() async {
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { return }
        isDiscoveringModels = true
        defer { isDiscoveringModels = false }

        do {
            let discovered = try await LLMModelDiscovery.fetch(baseURL: baseURL, apiKey: key)
            modelOptions = discovered
            usesDiscoveredModels = true
            modelSearchText = ""
            if !discovered.contains(where: { $0.name == model }) {
                model = discovered.first?.name ?? model
            }
            modelDiscoveryMessage = "已读取 \(discovered.count) 个候选模型；保存前仍需验证所选模型能实际回答。"
        } catch {
            modelDiscoveryMessage = "暂时无法读取模型列表：\(error.localizedDescription) 仍可手动填写模型名。"
        }
    }

    /// 通过后端发起最小真实生成，并同步服务商返回的模型列表。
    private func testConnection() async {
        isTesting = true
        defer { isTesting = false }
        validatedSignature = nil
        do {
            try await LLMConnectionValidation.validate(
                baseURL: baseURL,
                apiKey: apiKey,
                model: model
            )
            do {
                let discovered = try await LLMModelDiscovery.fetch(
                    baseURL: baseURL,
                    apiKey: apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
                )
                modelOptions = discovered
                usesDiscoveredModels = true
                modelDiscoveryMessage = "已读取 \(discovered.count) 个候选模型。"
            } catch {
                modelDiscoveryMessage = "真实回答已通过，但服务商没有提供可读的模型列表。"
            }
            validatedSignature = configurationSignature
            testMessage = "真实回答验证成功，当前 Key、端点和模型可以启用。"
        } catch let error as RecoverableAPIError {
            testMessage = "服务返回 \(error.statusCode):\(error.message)"
        } catch {
            testMessage = "连接失败:\(error.localizedDescription)"
        }
    }

    private func save() {
        guard validatedSignature == configurationSignature else {
            testMessage = "请先点“验证并刷新”，确认所选模型能实际回答。"
            return
        }
        LLMConfigStore.save(
            providerID: preset.id,
            providerName: preset.name,
            baseURL: baseURL,
            model: model,
            apiKey: apiKey,
            availableModels: modelOptions,
            validated: true
        )
        activeConfig = LLMConfigStore.stored()
        // 钥匙串写入可能失败(如构建未签名),回读校验后再报成功。
        if activeConfig != nil {
            statusMessage = "已启用 \(preset.name) · \(model),下次提问生效。"
        } else {
            statusMessage = "Key 保存失败:钥匙串不可用,请重试或检查构建签名。"
        }
        dismiss()
    }
}

// MARK: - 组件

private struct APIKeyInputBox: View {
    @Binding var apiKey: String
    @Binding var revealsKey: Bool
    let placeholder: String
    var focus: FocusState<Bool>.Binding

    var body: some View {
        HStack(spacing: 10) {
            Group {
                if revealsKey {
                    TextField("粘贴 API Key(\(placeholder))", text: $apiKey)
                } else {
                    SecureField("粘贴 API Key(\(placeholder))", text: $apiKey)
                }
            }
            .focused(focus)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .font(.callout.monospaced())
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(apiKey.isEmpty ? Color.primary.opacity(0.16) : Theme.Color.accent.opacity(0.65), lineWidth: 1)
            )

            Button {
                revealsKey.toggle()
            } label: {
                Image(systemName: revealsKey ? "eye.slash.fill" : "eye.fill")
                    .font(.system(size: 16, weight: .semibold))
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.bordered)
            .accessibilityLabel(revealsKey ? "隐藏 API Key" : "显示 API Key")
        }
    }
}

private struct ActionBar: View {
    let canSubmit: Bool
    let canSave: Bool
    let isTesting: Bool
    let test: () -> Void
    let save: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: test) {
                Label("验证并刷新", systemImage: "arrow.clockwise.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(!canSubmit || isTesting)

            Button(action: save) {
                Label("保存启用", systemImage: "checkmark.circle.fill")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.Color.onAccent)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(Theme.Color.accent, in: .capsule)
            }
            .buttonStyle(.plain)
            .opacity(!canSave || isTesting ? 0.4 : 1)
            .disabled(!canSave || isTesting)
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(.bar)
    }
}

private struct ProviderListRow: View {
    let preset: LLMProviderPreset
    let isActive: Bool

    var body: some View {
        HStack(spacing: 12) {
            ProviderBrandIcon(preset: preset)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(preset.name)
                        .font(.subheadline.weight(.semibold))
                    if isActive {
                        Text("使用中")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Theme.Color.accent)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(Theme.Color.accent.opacity(0.14), in: .capsule)
                    }
                }
                Text(preset.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct ProviderBrandIcon: View {
    let preset: LLMProviderPreset

    var body: some View {
        Group {
            if let brandAssetName = preset.brandAssetName {
                Image(brandAssetName)
                    .resizable()
                    .scaledToFit()
                    .padding(7)
            } else {
                Image(systemName: preset.icon)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(8)
            }
        }
        .frame(width: 42, height: 42)
        .background(Color.white.opacity(0.96), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 0.5)
        )
        .accessibilityHidden(true)
    }
}

private struct ModelOptionRow: View {
    let option: LLMModelOption
    let isSelected: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(option.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                Text(option.caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(isSelected ? Theme.Color.accent : Color.secondary.opacity(0.55))
        }
        .contentShape(.rect)
        .frame(minHeight: 44)
    }
}

private struct ResultBanner: View {
    let message: String

    private var isSuccess: Bool {
        message.contains("成功") || message.contains("启用") || message.contains("恢复")
    }

    var body: some View {
        Label(message, systemImage: isSuccess ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
            .font(.footnote.weight(.medium))
            .foregroundStyle(.primary)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.ultraThinMaterial, in: .rect(cornerRadius: 12))
    }
}
