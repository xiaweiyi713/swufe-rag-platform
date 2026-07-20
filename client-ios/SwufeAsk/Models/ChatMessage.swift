import Foundation

enum ChatRole: String, Hashable {
    case user
    case assistant
    case notice
}

struct ChatMessage: Identifiable, Hashable {
    let id: UUID
    var role: ChatRole
    var text: String
    /// 以下字段仅 assistant 消息使用，在 `/ask` 响应就绪后一次性填入。
    var citations: [Citation]
    var retrieved: [RetrievedSummary]
    var officialLinks: [OfficialLink]
    var refused: Bool
    var mode: String?
    var latencyMS: Double?
    /// V16：sql / rag 等实际执行路径。
    var executionPath: String?
    /// V16：事实校验结果,false 时按证据不足展示。
    var validationPassed: Bool?
    var webSources: [WebSource]
    var deepThinking: Bool
    var webSearch: Bool
    var notice: FallbackNotice?

    init(
        id: UUID = UUID(),
        role: ChatRole,
        text: String = "",
        citations: [Citation] = [],
        retrieved: [RetrievedSummary] = [],
        officialLinks: [OfficialLink] = [],
        refused: Bool = false,
        mode: String? = nil,
        latencyMS: Double? = nil,
        executionPath: String? = nil,
        validationPassed: Bool? = nil,
        webSources: [WebSource] = [],
        deepThinking: Bool = false,
        webSearch: Bool = false,
        notice: FallbackNotice? = nil
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.citations = citations
        self.retrieved = retrieved
        self.officialLinks = officialLinks
        self.refused = refused
        self.mode = mode
        self.latencyMS = latencyMS
        self.executionPath = executionPath
        self.validationPassed = validationPassed
        self.webSources = webSources
        self.deepThinking = deepThinking
        self.webSearch = webSearch
        self.notice = notice
    }
}
