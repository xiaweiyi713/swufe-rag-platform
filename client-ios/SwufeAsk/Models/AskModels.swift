import Foundation

// MARK: - POST /ask

enum AskStreamEvent {
    case metadata(mode: String?, executionPath: String?)
    case status(stage: String?, message: String?)
    case delta(String)
    case final(AskResponse)
}

struct AskStreamEnvelope: Decodable {
    let type: String
    let text: String?
    let stage: String?
    let message: String?
    let mode: String?
    let executionPath: String?
    let response: AskResponse?
    let errorType: String?
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case type
        case text
        case stage
        case message
        case mode
        case executionPath = "execution_path"
        case response
        case errorType = "error_type"
        case errorCode = "error_code"
    }
}

/// 正式混合问答接口 `POST /ask` 的完整响应。
/// 兼容 API_REFERENCE.md（冻结契约 1.0）与 FRONTEND_API_V16.md：
/// V16 增补 execution_path、validation、planner/presenter 等调试字段，
/// 客户端只解析展示需要的部分,其余忽略。
struct AskResponse: Decodable, Hashable {
    /// `school_rag` 或 `general_chat`，表示实际回答分支。
    let mode: String
    let answerMD: String
    let citations: [Citation]
    let retrieved: [RetrievedSummary]
    let officialLinks: [OfficialLink]
    /// 学校证据不足为 true；澄清问题为 false。
    let refused: Bool
    let latencyMS: Double?
    /// V16：sql / rag / sql+rag / clarify 等实际执行路径。
    let executionPath: String?
    /// V16：事实校验结果；false 时按证据不足展示。
    let validationPassed: Bool?
    let webSources: [WebSource]
    let llmCalled: Bool?
    let finalOutputSource: String?
    let fallbackReason: String?

    enum CodingKeys: String, CodingKey {
        case mode
        case answerMD = "answer_md"
        case citations
        case retrieved
        case officialLinks = "official_links"
        case refused
        case latencyMS = "latency_ms"
        case executionPath = "execution_path"
        case validation
        case webSources = "web_sources"
        case llmCalled = "llm_called"
        case finalOutputSource = "final_output_source"
        case fallbackReason = "fallback_reason"
    }

    private struct ValidationPayload: Decodable {
        let passed: Bool?
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "general_chat"
        answerMD = try container.decode(String.self, forKey: .answerMD)
        citations = try container.decodeIfPresent([Citation].self, forKey: .citations) ?? []
        retrieved = try container.decodeIfPresent([RetrievedSummary].self, forKey: .retrieved) ?? []
        officialLinks = try container.decodeIfPresent([OfficialLink].self, forKey: .officialLinks) ?? []
        refused = try container.decodeIfPresent(Bool.self, forKey: .refused) ?? false
        latencyMS = try container.decodeIfPresent(Double.self, forKey: .latencyMS)
        executionPath = try container.decodeIfPresent(String.self, forKey: .executionPath)
        validationPassed = (try? container.decodeIfPresent(ValidationPayload.self, forKey: .validation))??.passed
        webSources = try container.decodeIfPresent([WebSource].self, forKey: .webSources) ?? []
        llmCalled = try container.decodeIfPresent(Bool.self, forKey: .llmCalled)
        finalOutputSource = try container.decodeIfPresent(String.self, forKey: .finalOutputSource)
        fallbackReason = try container.decodeIfPresent(String.self, forKey: .fallbackReason)
    }
}

/// Public-web snippets returned by explicit search or automatic KB-miss fallback.
struct WebSource: Codable, Hashable, Identifiable {
    let title: String
    let url: String
    let snippet: String

    var id: String { url }

    var linkURL: URL? { URL(string: url) }
}

/// 回答正文中 `[n]` 角标对应的可信引用。标题、条款和 URL 由后端按
/// chunk_id 从可信存储重建，quote 是数据库原文子串。
/// 除 marker/chunk_id 外全部宽松解码,兼容 V16 的字段增补与缺省。
struct Citation: Codable, Hashable, Identifiable {
    var id: Int { marker }
    let marker: Int
    let chunkID: String
    let docTitle: String
    let article: String
    let quote: String
    let pageURL: String
    let fileURL: String
    /// V16：原文件物理页码。
    let physicalPage: Int?

    enum CodingKeys: String, CodingKey {
        case marker
        case chunkID = "chunk_id"
        case docTitle = "doc_title"
        case article
        case quote
        case pageURL = "page_url"
        case fileURL = "file_url"
        case physicalPage = "physical_page"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        marker = try container.decode(Int.self, forKey: .marker)
        chunkID = try container.decode(String.self, forKey: .chunkID)
        docTitle = try container.decodeIfPresent(String.self, forKey: .docTitle) ?? "未命名来源"
        article = try container.decodeIfPresent(String.self, forKey: .article) ?? ""
        quote = try container.decodeIfPresent(String.self, forKey: .quote) ?? ""
        pageURL = try container.decodeIfPresent(String.self, forKey: .pageURL) ?? ""
        fileURL = try container.decodeIfPresent(String.self, forKey: .fileURL) ?? ""
        physicalPage = try container.decodeIfPresent(Int.self, forKey: .physicalPage)
    }
}

/// `/ask` 响应里的检索摘要条目。
/// 摘要字段兼容 `summary`（API_REFERENCE.md）与 `snippet`（仓库 README D-4）两种命名。
struct RetrievedSummary: Codable, Hashable, Identifiable {
    var id: String { chunkID }
    let chunkID: String
    let docTitle: String
    let article: String
    let college: String
    let cohort: String
    let score: Double
    let isTable: Bool
    let summary: String

    enum CodingKeys: String, CodingKey {
        case chunkID = "chunk_id"
        case docTitle = "doc_title"
        case article
        case college
        case cohort
        case score
        case isTable = "is_table"
        case summary
        case snippet
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        chunkID = try container.decode(String.self, forKey: .chunkID)
        docTitle = try container.decode(String.self, forKey: .docTitle)
        article = try container.decodeIfPresent(String.self, forKey: .article) ?? ""
        college = try container.decodeIfPresent(String.self, forKey: .college) ?? ""
        cohort = try container.decodeIfPresent(String.self, forKey: .cohort) ?? ""
        score = try container.decodeIfPresent(Double.self, forKey: .score) ?? 0
        if let value = try? container.decode(Bool.self, forKey: .isTable) {
            isTable = value
        } else if let value = try? container.decode(Int.self, forKey: .isTable) {
            isTable = value != 0
        } else {
            isTable = false
        }
        summary = try container.decodeIfPresent(String.self, forKey: .summary)
            ?? container.decodeIfPresent(String.self, forKey: .snippet)
            ?? ""
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(chunkID, forKey: .chunkID)
        try container.encode(docTitle, forKey: .docTitle)
        try container.encode(article, forKey: .article)
        try container.encode(college, forKey: .college)
        try container.encode(cohort, forKey: .cohort)
        try container.encode(score, forKey: .score)
        try container.encode(isTable, forKey: .isTable)
        try container.encode(summary, forKey: .summary)
    }
}

/// 官方入口链接。后端尚未冻结该结构的字段名，这里做全可选宽松解码，
/// 展示时按优先级取第一个非空值。
struct OfficialLink: Codable, Hashable, Identifiable {
    let title: String?
    let label: String?
    let docTitle: String?
    let topic: String?
    let college: String?
    let url: String?
    let pageURL: String?
    let fileURL: String?

    enum CodingKeys: String, CodingKey {
        case title
        case label
        case docTitle = "doc_title"
        case topic
        case college
        case url
        case pageURL = "page_url"
        case fileURL = "file_url"
    }

    var id: String { linkString ?? displayTitle }

    var displayTitle: String {
        [title, label, docTitle].compactMap { $0 }.first { !$0.isEmpty } ?? "官方入口"
    }

    var linkString: String? {
        [url, pageURL, fileURL].compactMap { $0 }.first { !$0.isEmpty }
    }

    var linkURL: URL? {
        linkString.flatMap(URL.init(string:))
    }
}

// MARK: - GET /options

/// `GET /options`：可选学院、年级、专业、知识块数量与运行模式。
/// V16 的专业按年级分组，并携带“年级 + 专业 → 学院”的可信归属映射。
struct OptionsResponse: Decodable, Hashable {
    let mode: String
    let colleges: [String]
    let cohorts: [String]
    let majorsByCohort: [String: [String]]
    let majorCollegesByCohort: [String: [String: String]]
    let chunkCount: Int
    let defaultTopK: Int

    enum CodingKeys: String, CodingKey {
        case mode
        case colleges
        case cohorts
        case majorsByCohort = "majors_by_cohort"
        case majorCollegesByCohort = "major_colleges_by_cohort"
        case chunkCount = "chunk_count"
        case defaultTopK = "default_top_k"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? ""
        colleges = try container.decodeIfPresent([String].self, forKey: .colleges) ?? []
        cohorts = try container.decodeIfPresent([String].self, forKey: .cohorts) ?? []
        majorsByCohort = try container.decodeIfPresent([String: [String]].self, forKey: .majorsByCohort) ?? [:]
        majorCollegesByCohort = try container.decodeIfPresent(
            [String: [String: String]].self,
            forKey: .majorCollegesByCohort
        ) ?? [:]
        chunkCount = try container.decodeIfPresent(Int.self, forKey: .chunkCount) ?? 0
        defaultTopK = try container.decodeIfPresent(Int.self, forKey: .defaultTopK) ?? 8
    }

    /// 指定年级和学院的专业候选；未选年级时合并全部年级去重。
    func majors(for cohort: String?, college: String? = nil) -> [String] {
        let candidates: [String]
        if let cohort, let scoped = majorsByCohort[cohort] {
            candidates = scoped
        } else {
            candidates = Array(Set(majorsByCohort.values.flatMap { $0 })).sorted()
        }
        guard let college, !majorCollegesByCohort.isEmpty else {
            return candidates
        }
        return candidates.filter {
            belongsToCollege($0, college: college, cohort: cohort)
        }
    }

    func belongsToCollege(
        _ major: String,
        college: String,
        cohort: String?
    ) -> Bool {
        if let cohort {
            return majorCollegesByCohort[cohort]?[major] == college
        }
        return majorCollegesByCohort.values.contains {
            $0[major] == college
        }
    }

    /// 专业归属随培养方案年级变化；未选年级时采用最新年级的归属。
    func college(for major: String, cohort: String?) -> String? {
        if let cohort {
            return majorCollegesByCohort[cohort]?[major]
        }
        for key in majorCollegesByCohort.keys.sorted(by: >) {
            if let college = majorCollegesByCohort[key]?[major] {
                return college
            }
        }
        return nil
    }
}

// MARK: - GET /source/{chunk_id}

/// 可信知识块原文，`GET /source/{chunk_id}` 的展示模型。
/// V16 后端返回 text/article/doc_title/page_url/file_url 等,不保证
/// 冻结契约的全部 12 字段,因此除 text 外全部宽松解码。
struct KnowledgeChunk: Decodable, Hashable, Identifiable {
    var id: String { chunkID }
    let chunkID: String
    let text: String
    let docTitle: String
    let article: String
    let level: String
    let college: String
    let cohort: String
    let year: Int?
    let status: String
    let pageURL: String
    let fileURL: String
    let isTable: Bool

    enum CodingKeys: String, CodingKey {
        case chunkID = "chunk_id"
        case text
        case docTitle = "doc_title"
        case article
        case level
        case college
        case cohort
        case year
        case status
        case pageURL = "page_url"
        case fileURL = "file_url"
        case isTable = "is_table"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        chunkID = try container.decodeIfPresent(String.self, forKey: .chunkID) ?? ""
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        docTitle = try container.decodeIfPresent(String.self, forKey: .docTitle) ?? "未命名来源"
        article = try container.decodeIfPresent(String.self, forKey: .article) ?? ""
        level = try container.decodeIfPresent(String.self, forKey: .level) ?? ""
        college = try container.decodeIfPresent(String.self, forKey: .college) ?? ""
        cohort = try container.decodeIfPresent(String.self, forKey: .cohort) ?? ""
        year = try container.decodeIfPresent(Int.self, forKey: .year)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? ""
        pageURL = try container.decodeIfPresent(String.self, forKey: .pageURL) ?? ""
        fileURL = try container.decodeIfPresent(String.self, forKey: .fileURL) ?? ""
        if let value = try? container.decode(Bool.self, forKey: .isTable) {
            isTable = value
        } else if let value = try? container.decode(Int.self, forKey: .isTable) {
            isTable = value != 0
        } else {
            isTable = false
        }
    }
}

// MARK: - 本地恢复提示

struct RecoveryAction: Hashable, Identifiable {
    var id: String { "\(label)-\(prompt)" }
    let label: String
    let prompt: String
}

/// 客户端本地构造的错误/引导卡片（网络失败、后端 503 等），
/// 与后端契约无关，仅用于聊天流里的恢复引导。
struct FallbackNotice: Hashable {
    let code: String
    let title: String
    let message: String
    let actions: [RecoveryAction]
    let severity: String

    init(
        code: String = "general",
        title: String,
        message: String,
        actions: [RecoveryAction] = [],
        severity: String = "info"
    ) {
        self.code = code
        self.title = title
        self.message = message
        self.actions = actions
        self.severity = severity
    }
}
