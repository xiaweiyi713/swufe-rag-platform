import Foundation

/// 厂商预设里的一个可选模型。
struct LLMModelOption: Identifiable, Hashable, Codable {
    var id: String { name }
    let name: String
    let caption: String
}

/// LLM 厂商预设:端点、推荐模型与文案。
/// swufe-rag 后端使用 OpenAI-compatible 客户端(X-LLM-Base-URL / X-LLM-Model
/// 随请求覆盖),因此这里全部是 OpenAI 兼容端点;Claude 可经 OpenRouter 使用。
struct LLMProviderPreset: Identifiable, Hashable {
    let id: String
    let name: String
    let subtitle: String
    let icon: String
    let baseURL: String
    let endpointNote: String
    let keyPlaceholder: String
    let models: [LLMModelOption]

    var brandAssetName: String? {
        switch id {
        case "deepseek": return "BrandDeepSeek"
        case "openai": return "BrandOpenAI"
        case "qwen": return "BrandQwen"
        case "zhipu": return "BrandZhipu"
        case "moonshot": return "BrandMoonshot"
        case "volcengine": return "BrandVolcengine"
        case "gemini": return "BrandGemini"
        case "openrouter": return "BrandOpenRouter"
        case "aihubmix": return "BrandAiHubMix"
        default: return nil
        }
    }

    func isActive(config: LLMConfigStore.Config?) -> Bool {
        guard let config else { return false }
        return config.providerID == id
    }

    static let all: [LLMProviderPreset] = [
        LLMProviderPreset(
            id: "deepseek",
            name: "DeepSeek",
            subtitle: "中文理解与推理稳定,教务问答推荐默认",
            icon: "sparkle.magnifyingglass",
            baseURL: "https://api.deepseek.com",
            endpointNote: "DeepSeek 官方 OpenAI-compatible Chat Completions 端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "deepseek-v4-flash", caption: "新一代快速模型,教务问答推荐默认"),
                LLMModelOption(name: "deepseek-v4-pro", caption: "复杂学分推算、跨条款推理更强")
            ]
        ),
        LLMProviderPreset(
            id: "openai",
            name: "OpenAI",
            subtitle: "OpenAI-compatible 接口,自然语言表达质量高",
            icon: "circle.hexagongrid",
            baseURL: "https://api.openai.com/v1",
            endpointNote: "OpenAI Chat Completions 端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "gpt-5.6", caption: "当前主线通用模型,推荐默认"),
                LLMModelOption(name: "gpt-5.6-terra", caption: "能力与成本平衡"),
                LLMModelOption(name: "gpt-5.6-luna", caption: "高并发、成本敏感场景")
            ]
        ),
        LLMProviderPreset(
            id: "qwen",
            name: "通义千问",
            subtitle: "DashScope 兼容模式,中文校园场景友好",
            icon: "cloud",
            baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
            endpointNote: "阿里云 DashScope OpenAI 兼容模式端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "qwen3.7-plus", caption: "旗舰通用模型,教务问答推荐默认"),
                LLMModelOption(name: "qwen3.7-max", caption: "更强推理与长上下文"),
                LLMModelOption(name: "qwen3.6-flash", caption: "低延迟、成本友好")
            ]
        ),
        LLMProviderPreset(
            id: "zhipu",
            name: "智谱 GLM",
            subtitle: "OpenAI-compatible GLM 系列",
            icon: "brain.head.profile",
            baseURL: "https://open.bigmodel.cn/api/paas/v4",
            endpointNote: "智谱 BigModel OpenAI-compatible 端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "glm-5.2", caption: "新一代旗舰模型,推荐默认"),
                LLMModelOption(name: "glm-5-turbo", caption: "长任务与工具调用"),
                LLMModelOption(name: "glm-5", caption: "复杂问答与推理")
            ]
        ),
        LLMProviderPreset(
            id: "moonshot",
            name: "Moonshot / Kimi",
            subtitle: "长上下文中文模型,长文档条款问答",
            icon: "moon.stars",
            baseURL: "https://api.moonshot.cn/v1",
            endpointNote: "Moonshot OpenAI-compatible 端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "kimi-k2.7", caption: "新一代通用模型,推荐默认"),
                LLMModelOption(name: "kimi-k2.6", caption: "复杂推理与长上下文"),
                LLMModelOption(name: "kimi-k2.5", caption: "稳定、兼容性好")
            ]
        ),
        LLMProviderPreset(
            id: "volcengine",
            name: "Volcengine Ark",
            subtitle: "火山方舟接入点(豆包系模型)",
            icon: "flame",
            baseURL: "https://ark.cn-beijing.volces.com/api/v3",
            endpointNote: "火山方舟 OpenAI-compatible 端点;模型名通常是控制台里的 ep- 接入点 ID。",
            keyPlaceholder: "API Key",
            models: [
                LLMModelOption(name: "ep-你的模型接入点", caption: "替换成方舟控制台 Endpoint ID")
            ]
        ),
        LLMProviderPreset(
            id: "gemini",
            name: "Google Gemini",
            subtitle: "Gemini OpenAI 兼容端点",
            icon: "diamond",
            baseURL: "https://generativelanguage.googleapis.com/v1beta/openai",
            endpointNote: "Google Gemini OpenAI-compatible 端点;使用 Gemini API Key。",
            keyPlaceholder: "AIza...",
            models: [
                LLMModelOption(name: "gemini-3.5-flash", caption: "当前主线快速模型,推荐默认"),
                LLMModelOption(name: "gemini-3.1-pro-preview", caption: "更强推理与长上下文"),
                LLMModelOption(name: "gemini-3-flash-preview", caption: "最新预览模型")
            ]
        ),
        LLMProviderPreset(
            id: "openrouter",
            name: "OpenRouter",
            subtitle: "一个 Key 用多家模型(含 Claude / Gemini)",
            icon: "point.3.connected.trianglepath.dotted",
            baseURL: "https://openrouter.ai/api/v1",
            endpointNote: "OpenRouter OpenAI-compatible 端点。",
            keyPlaceholder: "sk-or-...",
            models: [
                LLMModelOption(name: "deepseek/deepseek-v4-flash", caption: "DeepSeek via OpenRouter"),
                LLMModelOption(name: "anthropic/claude-opus-4.6", caption: "Claude via OpenRouter"),
                LLMModelOption(name: "google/gemini-3.5-flash", caption: "Gemini via OpenRouter")
            ]
        ),
        LLMProviderPreset(
            id: "aihubmix",
            name: "AiHubMix",
            subtitle: "聚合模型服务,OpenAI-compatible",
            icon: "square.stack.3d.up",
            baseURL: "https://aihubmix.com/v1",
            endpointNote: "AiHubMix OpenAI-compatible 端点。",
            keyPlaceholder: "sk-...",
            models: [
                LLMModelOption(name: "deepseek-v4-flash", caption: "中文问答推荐"),
                LLMModelOption(name: "qwen3.7-plus", caption: "长文档与中文理解"),
                LLMModelOption(name: "gpt-5.6", caption: "快速通用")
            ]
        )
    ]
}
