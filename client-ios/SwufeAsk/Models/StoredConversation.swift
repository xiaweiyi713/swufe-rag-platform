import Foundation
import SwiftData

/// A single archived chat turn. The response metadata is persisted as well so
/// restored conversations keep their source cards and can continue in context.
struct ArchivedMessage: Codable, Identifiable, Hashable {
    var id = UUID()
    var role: String   // "user" / "assistant"
    var text: String
    var citations: [Citation] = []
    var retrieved: [RetrievedSummary] = []
    var officialLinks: [OfficialLink] = []
    var refused: Bool = false
    var mode: String?
    var latencyMS: Double?
    var executionPath: String?
    var validationPassed: Bool?
    var webSources: [WebSource] = []
    var deepThinking: Bool = false
    var webSearch: Bool = false

    private enum CodingKeys: String, CodingKey {
        case id
        case role
        case text
        case citations
        case retrieved
        case officialLinks
        case refused
        case mode
        case latencyMS
        case executionPath
        case validationPassed
        case webSources
        case deepThinking
        case webSearch
    }

    init(
        id: UUID = UUID(),
        role: String,
        text: String,
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
        webSearch: Bool = false
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
    }

    /// Older history blobs only contain id/role/text; missing metadata falls
    /// back to empty values so the schema remains backward compatible.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        role = try container.decode(String.self, forKey: .role)
        text = try container.decode(String.self, forKey: .text)
        citations = try container.decodeIfPresent([Citation].self, forKey: .citations) ?? []
        retrieved = try container.decodeIfPresent([RetrievedSummary].self, forKey: .retrieved) ?? []
        officialLinks = try container.decodeIfPresent([OfficialLink].self, forKey: .officialLinks) ?? []
        refused = try container.decodeIfPresent(Bool.self, forKey: .refused) ?? false
        mode = try container.decodeIfPresent(String.self, forKey: .mode)
        latencyMS = try container.decodeIfPresent(Double.self, forKey: .latencyMS)
        executionPath = try container.decodeIfPresent(String.self, forKey: .executionPath)
        validationPassed = try container.decodeIfPresent(Bool.self, forKey: .validationPassed)
        webSources = try container.decodeIfPresent([WebSource].self, forKey: .webSources) ?? []
        deepThinking = try container.decodeIfPresent(Bool.self, forKey: .deepThinking) ?? false
        webSearch = try container.decodeIfPresent(Bool.self, forKey: .webSearch) ?? false
    }
}

/// A persisted past conversation. SwiftData stores these in a local SQLite
/// database inside the app's private sandbox (Application Support), so history
/// survives relaunches without any third-party dependency.
@Model
final class StoredConversation {
    var id: UUID = UUID()
    var title: String = ""
    var createdAt: Date = Date.now
    /// 列表行预览。归档时算好存储，避免侧栏每行渲染都解码整个 JSON blob。
    var previewText: String = ""
    /// JSON-encoded `[ArchivedMessage]`; kept as a blob to avoid a relationship.
    var messagesData: Data = Data()
    /// Reuse the backend's dialogue context when a history item is reopened.
    var sessionID: String = ""
    /// Scope used by the archived conversation; restored alongside its messages.
    var college: String?
    var cohort: String?
    var major: String?

    init(
        title: String,
        messages: [ArchivedMessage],
        createdAt: Date = .now,
        sessionID: String = "",
        college: String? = nil,
        cohort: String? = nil,
        major: String? = nil
    ) {
        self.id = UUID()
        self.title = title
        self.createdAt = createdAt
        self.previewText = Self.makePreview(from: messages)
        self.messagesData = (try? JSONEncoder().encode(messages)) ?? Data()
        self.sessionID = sessionID
        self.college = college
        self.cohort = cohort
        self.major = major
    }

    var messages: [ArchivedMessage] {
        (try? JSONDecoder().decode([ArchivedMessage].self, from: messagesData)) ?? []
    }

    /// 旧记录没有缓存预览，回退到解码消息现算。
    var preview: String {
        previewText.isEmpty ? Self.makePreview(from: messages) : previewText
    }

    func update(
        title: String,
        messages: [ArchivedMessage],
        sessionID: String,
        college: String?,
        cohort: String?,
        major: String?
    ) {
        self.title = title
        self.previewText = Self.makePreview(from: messages)
        self.messagesData = (try? JSONEncoder().encode(messages)) ?? Data()
        self.sessionID = sessionID
        self.college = college
        self.cohort = cohort
        self.major = major
    }

    private static func makePreview(from messages: [ArchivedMessage]) -> String {
        messages.first(where: { $0.role == "assistant" && !$0.text.isEmpty })?.text
            ?? messages.first?.text
            ?? ""
    }
}
