import SwiftUI

/// assistant 回答气泡下方的附加信息区：模式/耗时标签、引用角标列表、
/// 检索详情入口和官方入口链接。打字机播放结束后才会出现。
struct AnswerAttachments: View {
    let message: ChatMessage

    @State private var sourceTarget: SourceSheetTarget?
    @State private var showsRetrieved = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            metaRow

            if !message.citations.isEmpty {
                citationList
            }

            if !message.retrieved.isEmpty {
                Button {
                    showsRetrieved = true
                } label: {
                    Label("检索到 \(message.retrieved.count) 条相关条款", systemImage: "text.magnifyingglass")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }

            if !message.officialLinks.isEmpty {
                officialLinks
            }

            if !message.webSources.isEmpty {
                webSources
            }
        }
        .sheet(item: $sourceTarget) { target in
            SourceDetailSheet(chunkID: target.chunkID, quote: target.quote)
        }
        .sheet(isPresented: $showsRetrieved) {
            RetrievedDetailView(retrieved: message.retrieved)
        }
    }

    private var metaRow: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                if let mode = message.mode {
                    GlassTag(
                        text: mode == "school_rag" ? "校规检索" : "通用对话",
                        systemImage: mode == "school_rag" ? "book.closed" : "bubble.left.and.bubble.right"
                    )
                }
                if let path = executionPathLabel {
                    GlassTag(text: path, systemImage: "arrow.triangle.branch")
                }
                if message.deepThinking {
                    GlassTag(text: "深度思考", systemImage: "atom")
                }
                if message.webSearch {
                    GlassTag(text: "联网搜索", systemImage: "globe")
                }
                if message.refused || message.validationPassed == false {
                    GlassTag(text: "证据不足", systemImage: "exclamationmark.triangle")
                }
                if let latency = message.latencyMS {
                    Text("\(latency / 1000, format: .number.precision(.fractionLength(1)))s")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    /// V16 执行路径的展示文案;未知取值原样显示。
    private var executionPathLabel: String? {
        switch message.executionPath {
        case nil, "":
            nil
        case "sql":
            "课程库"
        case "rag":
            "文档检索"
        case "sql+rag", "sql_rag":
            "课程库+文档"
        case "clarify":
            "追问澄清"
        case let other?:
            other
        }
    }

    private var citationList: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("来源")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.bottom, 6)
            ForEach(citationGroups) { group in
                Button {
                    sourceTarget = SourceSheetTarget(
                        chunkID: group.primary.chunkID,
                        quote: group.primary.quote
                    )
                } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "doc.text.fill")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Theme.Color.onAccent)
                            .frame(width: 17, height: 17)
                            .background(Theme.Color.accent, in: .circle)
                            .padding(.top, 1)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(group.primary.docTitle)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(.primary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                            Text(group.summary)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.tertiary)
                            .padding(.top, 3)
                    }
                    .padding(.vertical, 7)
                    .frame(minHeight: 44)
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                if group.id != citationGroups.last?.id {
                    Divider().overlay(Theme.Color.cardStroke)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .liquidGlass(radius: Theme.Radius.sm, elevated: false)
    }

    private var citationGroups: [CitationFileGroup] {
        CitationFileGroup.group(message.citations)
    }

    private var officialLinks: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("官方入口")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(message.officialLinks) { link in
                if let url = link.linkURL {
                    Link(destination: url) {
                        Label(link.displayTitle, systemImage: "link")
                            .font(.caption.weight(.medium))
                            .lineLimit(1)
                    }
                } else {
                    Label(link.displayTitle, systemImage: "link")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .liquidGlass(radius: Theme.Radius.sm, elevated: false)
    }

    private var webSources: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label("联网来源", systemImage: "globe")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(message.webSources) { source in
                if let url = source.linkURL {
                    Link(destination: url) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(source.title)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(Theme.Color.accent)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                            if !source.snippet.isEmpty {
                                Text(source.snippet)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                    .multilineTextAlignment(.leading)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(.rect)
                    }
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .liquidGlass(radius: Theme.Radius.sm, elevated: false)
    }
}

private struct CitationFileGroup: Identifiable {
    let id: String
    var citations: [Citation]

    var primary: Citation { citations[0] }

    var summary: String {
        let markers = citations
            .map(\.marker)
            .reduce(into: [Int]()) { values, marker in
                if !values.contains(marker) { values.append(marker) }
            }
            .sorted()
            .map { "[\($0)]" }
            .joined()
        let pages = citations
            .compactMap(\.physicalPage)
            .reduce(into: [Int]()) { values, page in
                if !values.contains(page) { values.append(page) }
            }
            .sorted()
        let pageText = pages.isEmpty
            ? "页码未标注"
            : "原文件第\(pages.map(String.init).joined(separator: "、"))页"
        return "引用 \(markers) · \(pageText)"
    }

    static func group(_ citations: [Citation]) -> [CitationFileGroup] {
        var groups: [CitationFileGroup] = []
        var indexes: [String: Int] = [:]
        for citation in citations {
            let fileURL = citation.fileURL.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            let key = fileURL.isEmpty
                ? "source:\(citation.docTitle)|\(citation.pageURL)"
                : "file:\(fileURL)"
            if let index = indexes[key] {
                groups[index].citations.append(citation)
            } else {
                indexes[key] = groups.count
                groups.append(CitationFileGroup(id: key, citations: [citation]))
            }
        }
        return groups
    }
}

private struct SourceSheetTarget: Identifiable {
    var id: String { chunkID }
    let chunkID: String
    var quote: String?
}

// MARK: - 条款原文回查

/// sheet 形式的原文回查（引用角标点击进入）。
struct SourceDetailSheet: View {
    let chunkID: String
    var quote: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            SourceChunkContent(chunkID: chunkID, quote: quote)
                .navigationTitle("条款原文")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("完成") { dismiss() }
                    }
                }
        }
    }
}

/// `GET /source/{chunk_id}` 的展示主体，可嵌在 sheet 或 push 进导航栈。
struct SourceChunkContent: View {
    let chunkID: String
    var quote: String?

    @State private var chunk: KnowledgeChunk?
    @State private var loadError: String?
    private let service = AskAPIService()

    var body: some View {
        Group {
            if let chunk {
                detail(chunk)
            } else if let loadError {
                errorView(loadError)
            } else {
                ProgressView("正在回查可信原文…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(LiquidBackdrop())
        .task(id: chunkID) {
            await load()
        }
    }

    private func detail(_ chunk: KnowledgeChunk) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.Spacing.md) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(chunk.docTitle)
                        .font(.title3.weight(.semibold))
                    Text(chunk.article)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 6) { tags(chunk) }
                    VStack(alignment: .leading, spacing: 6) { tags(chunk) }
                }

                if let quote, !quote.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("回答引用的原句")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Text(quote)
                            .font(.callout.weight(.medium))
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Theme.Color.accent.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(Theme.Color.accent)
                                    .frame(width: 3)
                                    .padding(.vertical, 4)
                            }
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("条款全文")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(chunk.text)
                        .font(chunk.isTable ? .caption.monospaced() : .callout)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .liquidGlass(radius: Theme.Radius.sm, elevated: false)
                }

                VStack(alignment: .leading, spacing: 8) {
                    if let pageURL = URL(string: chunk.pageURL) {
                        Link(destination: pageURL) {
                            Label("查看官网通知页", systemImage: "safari")
                                .font(.footnote.weight(.semibold))
                        }
                    }
                    if let fileURL = URL(string: chunk.fileURL) {
                        Link(destination: fileURL) {
                            Label("打开原始文件", systemImage: "doc")
                                .font(.footnote.weight(.semibold))
                        }
                    }
                }

                Text(chunk.chunkID)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
            }
            .padding(Theme.Spacing.md)
        }
        .scrollIndicators(.hidden)
    }

    @ViewBuilder
    private func tags(_ chunk: KnowledgeChunk) -> some View {
        // V16 后端不保证返回全部元数据字段,空值不显示标签。
        if !chunk.level.isEmpty {
            GlassTag(text: chunk.level)
        }
        if !chunk.college.isEmpty {
            GlassTag(text: chunk.college)
        }
        if !chunk.cohort.isEmpty {
            GlassTag(text: chunk.cohort == "不限" ? "年级不限" : "\(chunk.cohort)级")
        }
        if !chunk.status.isEmpty {
            GlassTag(text: chunk.status, systemImage: chunk.status == "现行" ? "checkmark.seal" : "clock.arrow.circlepath")
        }
        if chunk.isTable {
            GlassTag(text: "表格", systemImage: "tablecells")
        }
    }

    private func errorView(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "doc.questionmark")
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(.secondary)
            Text(message)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("重试") {
                loadError = nil
                Task { await load() }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(Theme.Spacing.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @MainActor
    private func load() async {
        guard chunk == nil else { return }
        do {
            chunk = try await service.source(chunkID: chunkID)
        } catch {
            loadError = "原文回查失败：\(error.localizedDescription)"
        }
    }
}

// MARK: - 检索详情

/// 本次回答实际参与的检索候选（含融合排序分数），点击任一行可回查原文。
struct RetrievedDetailView: View {
    let retrieved: [RetrievedSummary]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                Color(uiColor: .systemBackground)
                    .ignoresSafeArea()

                List(retrieved) { item in
                    NavigationLink(value: item.chunkID) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(item.docTitle)
                                .font(.subheadline.weight(.medium))
                                .lineLimit(2)
                            Text(item.article)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                            HStack(spacing: 8) {
                                if !item.college.isEmpty {
                                    Text(item.college)
                                }
                                if !item.cohort.isEmpty {
                                    Text(item.cohort == "不限" ? "年级不限" : "\(item.cohort)级")
                                }
                                if item.isTable {
                                    Label("表格", systemImage: "tablecells")
                                }
                                Spacer()
                                Text("相似度 \(item.score, format: .number.precision(.fractionLength(3)))")
                            }
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                            if !item.summary.isEmpty {
                                Text(item.summary)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .background(.clear)
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                retrievedHeader
            }
            .navigationDestination(for: String.self) { chunkID in
                SourceChunkContent(chunkID: chunkID)
                    .navigationTitle("条款原文")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar(.visible, for: .navigationBar)
                    .toolbarBackground(
                        Color(uiColor: .systemBackground),
                        for: .navigationBar
                    )
                    .toolbarBackground(.visible, for: .navigationBar)
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .presentationBackground(Color(uiColor: .systemBackground))
    }

    private var retrievedHeader: some View {
        ZStack {
            Text("检索详情")
                .font(.headline)
                .accessibilityAddTraits(.isHeader)

            HStack {
                Spacer()
                Button("完成") { dismiss() }
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: .capsule)
                    .overlay {
                        Capsule()
                            .strokeBorder(Theme.Color.cardStroke, lineWidth: 1)
                    }
            }
        }
        .frame(height: 54)
        .padding(.horizontal, Theme.Spacing.md)
    }
}
