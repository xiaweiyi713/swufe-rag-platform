import Foundation

/// 对话模型(BYOK)配置的本地存取。
/// 厂商/端点/模型名存 UserDefaults;API Key 存系统钥匙串。
/// 每次 `/ask` 请求把三者放进 `X-LLM-API-Key / X-LLM-Base-URL / X-LLM-Model`
/// 请求头发给后端,后端按请求构建对应厂商的 OpenAI-compatible 客户端。
enum LLMConfigStore {
    struct Config: Hashable {
        let providerID: String
        let providerName: String
        let baseURL: String
        let model: String
    }

    private static let providerIDKey = "swufeask.llm.providerID"
    private static let providerNameKey = "swufeask.llm.providerName"
    private static let baseURLKey = "swufeask.llm.baseURL"
    private static let modelKey = "swufeask.llm.model"
    private static let modelOptionsKey = "swufeask.llm.modelOptions"
    private static let modelOptionsProviderKey = "swufeask.llm.modelOptionsProvider"

    /// 当前配置;未配置(或缺 Key)时为 nil,后端走确定性降级模式。
    static func current() -> Config? {
        let defaults = UserDefaults.standard
        guard let providerID = defaults.string(forKey: providerIDKey), !providerID.isEmpty,
              let baseURL = defaults.string(forKey: baseURLKey), !baseURL.isEmpty,
              let model = defaults.string(forKey: modelKey), !model.isEmpty,
              hasAPIKey else {
            return nil
        }
        return Config(
            providerID: providerID,
            providerName: defaults.string(forKey: providerNameKey) ?? providerID,
            baseURL: baseURL,
            model: model
        )
    }

    static var hasAPIKey: Bool {
        !(KeychainStore.read(KeychainStore.llmAPIKeyAccount) ?? "").isEmpty
    }

    static func apiKey() -> String? {
        KeychainStore.read(KeychainStore.llmAPIKeyAccount)
    }

    static func save(
        providerID: String,
        providerName: String,
        baseURL: String,
        model: String,
        apiKey: String,
        availableModels: [LLMModelOption] = []
    ) {
        let defaults = UserDefaults.standard
        let selectedModel = model.trimmingCharacters(in: .whitespacesAndNewlines)
        defaults.set(providerID, forKey: providerIDKey)
        defaults.set(providerName, forKey: providerNameKey)
        defaults.set(baseURL.trimmingCharacters(in: .whitespacesAndNewlines), forKey: baseURLKey)
        defaults.set(selectedModel, forKey: modelKey)
        let normalizedModels = normalizedModelOptions(
            availableModels,
            selectedModel: selectedModel
        )
        if let encoded = try? JSONEncoder().encode(normalizedModels) {
            defaults.set(encoded, forKey: modelOptionsKey)
            defaults.set(providerID, forKey: modelOptionsProviderKey)
        }
        KeychainStore.write(apiKey, account: KeychainStore.llmAPIKeyAccount)
    }

    /// Models available to the active provider. Newly saved configurations use
    /// the API-discovered list; existing installs fall back to provider presets.
    static func availableModels() -> [LLMModelOption] {
        guard let config = current() else { return [] }
        let defaults = UserDefaults.standard
        var models: [LLMModelOption] = []
        if defaults.string(forKey: modelOptionsProviderKey) == config.providerID,
           let data = defaults.data(forKey: modelOptionsKey),
           let decoded = try? JSONDecoder().decode([LLMModelOption].self, from: data) {
            models = decoded
        }
        if models.isEmpty {
            models = LLMProviderPreset.all
                .first(where: { $0.id == config.providerID })?
                .models ?? []
        }
        return normalizedModelOptions(models, selectedModel: config.model)
    }

    /// Switch the model without touching the provider, endpoint or Key. The
    /// request client reads this value immediately before every new turn.
    @discardableResult
    static func selectModel(_ model: String) -> Config? {
        let value = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, current() != nil else { return current() }
        UserDefaults.standard.set(value, forKey: modelKey)
        return current()
    }

    /// 清除配置与 Key,回到后端降级模式。
    static func clear() {
        let defaults = UserDefaults.standard
        defaults.removeObject(forKey: providerIDKey)
        defaults.removeObject(forKey: providerNameKey)
        defaults.removeObject(forKey: baseURLKey)
        defaults.removeObject(forKey: modelKey)
        defaults.removeObject(forKey: modelOptionsKey)
        defaults.removeObject(forKey: modelOptionsProviderKey)
        KeychainStore.delete(KeychainStore.llmAPIKeyAccount)
    }

    /// 侧栏/设置页展示用摘要,如 "DeepSeek · deepseek-chat"。
    static var summary: String {
        if let config = current() {
            return "\(config.providerName) · \(config.model)"
        }
        return "未配置 · 确定性降级模式"
    }

    private static func normalizedModelOptions(
        _ options: [LLMModelOption],
        selectedModel: String
    ) -> [LLMModelOption] {
        var seen = Set<String>()
        var result: [LLMModelOption] = []
        for option in options {
            let name = option.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty, seen.insert(name).inserted else { continue }
            result.append(LLMModelOption(name: name, caption: option.caption))
        }
        if !selectedModel.isEmpty, seen.insert(selectedModel).inserted {
            result.insert(
                LLMModelOption(name: selectedModel, caption: "当前已配置模型"),
                at: 0
            )
        }
        return result
    }
}
