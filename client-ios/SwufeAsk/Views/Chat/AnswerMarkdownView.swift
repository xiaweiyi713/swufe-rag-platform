import SwiftUI

/// answer_md 的完整渲染。系统的 inline Markdown 解析不支持表格和标题，
/// 这里按行分段，并把常见培养方案表格转换成适合手机阅读的原生行布局。
struct AnswerMarkdownView: View {
    let source: String
    var isStreaming = false

    private enum Segment {
        case heading(String)
        case table(MarkdownTable)
        case text(String)
        case sourceFile(text: String, fileURL: URL)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
                switch segment {
                case .heading(let title):
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 2)
                case .table(let table):
                    AnswerTableView(table: table)
                case .text(let text):
                    Text(inlineMarkdown: text)
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                case .sourceFile(let text, let fileURL):
                    SourceFileReference(text: text, fileURL: fileURL)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var segments: [Segment] {
        var result: [Segment] = []
        var textBuffer: [String] = []
        var tableBuffer: [String] = []

        func flushText() {
            let joined = textBuffer.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { result.append(.text(joined)) }
            textBuffer = []
        }
        func flushTable() {
            if let table = MarkdownTable(lines: tableBuffer) {
                result.append(.table(table))
            } else if !isStreaming && !tableBuffer.isEmpty {
                result.append(.text(tableBuffer.joined(separator: "\n")))
            }
            tableBuffer = []
        }

        for line in source.split(separator: "\n", omittingEmptySubsequences: false) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("|") {
                flushText()
                tableBuffer.append(String(line))
            } else if trimmed.hasPrefix("#") {
                flushText()
                flushTable()
                let title = trimmed.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                if !title.isEmpty { result.append(.heading(title)) }
            } else if let reference = sourceFileReference(from: String(line)) {
                flushText()
                flushTable()
                result.append(
                    .sourceFile(text: reference.text, fileURL: reference.fileURL)
                )
            } else {
                flushTable()
                let displayLine = isStreaming
                    ? streamingSafeText(from: String(line))
                    : String(line)
                textBuffer.append(displayLine)
            }
        }
        flushText()
        flushTable()
        return result
    }

    /// Never expose a half-written Markdown link while an SSE delta is still
    /// arriving. Page-link labels remain readable; download links are withheld
    /// until the final response can turn them into real buttons.
    private func streamingSafeText(from line: String) -> String {
        var value = line
        if let downloadStart = value.range(of: "[下载原文件")?.lowerBound {
            value = String(value[..<downloadStart])
                .replacingOccurrences(
                    of: #"\s*·\s*$"#,
                    with: "",
                    options: .regularExpression
                )
        }
        value = value.replacingOccurrences(
            of: #"\[([^\]]+)\]\([^\r\n)]*\)"#,
            with: "$1",
            options: .regularExpression
        )
        value = value.replacingOccurrences(
            of: #"\[([^\]]+)\]\([^\r\n]*$"#,
            with: "$1",
            options: .regularExpression
        )
        return value
    }

    private func sourceFileReference(from line: String) -> (text: String, fileURL: URL)? {
        let marker = "[下载原文件]("
        guard let markerRange = line.range(of: marker),
              let closingParenthesis = line[markerRange.upperBound...].firstIndex(of: ")") else {
            return nil
        }

        let rawURL = String(line[markerRange.upperBound..<closingParenthesis])
        guard let fileURL = URL(string: rawURL) else { return nil }

        let suffixStart = line.index(after: closingParenthesis)
        var displayText = String(line[..<markerRange.lowerBound]) + String(line[suffixStart...])
        displayText = displayText.trimmingCharacters(in: .whitespacesAndNewlines)
        if displayText.last == "·" {
            displayText.removeLast()
            displayText = displayText.trimmingCharacters(in: .whitespaces)
        }
        return (displayText, fileURL)
    }
}

private struct MarkdownTable {
    let headers: [String]
    let rows: [[String]]

    init?(lines: [String]) {
        let parsed = lines.compactMap(Self.cells(from:))
        guard let header = parsed.first,
              !header.isEmpty,
              let separatorIndex = parsed.firstIndex(where: Self.isSeparator) else {
            return nil
        }
        headers = header
        rows = Array(parsed.dropFirst(separatorIndex + 1))
    }

    private static func cells(from line: String) -> [String]? {
        var value = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.hasPrefix("|") else { return nil }
        value.removeFirst()
        if value.hasSuffix("|") {
            value.removeLast()
        }
        return value.split(separator: "|", omittingEmptySubsequences: false).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    private static func isSeparator(_ row: [String]) -> Bool {
        !row.isEmpty && row.allSatisfy { cell in
            let value = cell.replacingOccurrences(of: ":", with: "")
            return value.count >= 3 && value.allSatisfy { $0 == "-" }
        }
    }
}

private struct AnswerTableView: View {
    let table: MarkdownTable

    private var isCreditComposition: Bool {
        table.headers.contains("类别")
            && table.headers.contains("必修")
            && table.headers.contains("选修")
            && table.headers.contains("合计")
    }

    private var isRequirementTable: Bool {
        table.headers.contains("模块") && table.headers.contains("最低学分")
    }

    private var isDetailedCreditComposition: Bool {
        table.headers.contains("板块")
            && table.headers.contains("模块")
            && table.headers.contains("必修")
            && table.headers.contains("选修")
            && table.headers.contains("合计")
    }

    @ViewBuilder
    var body: some View {
        if isDetailedCreditComposition {
            detailedCreditComposition
        } else if isCreditComposition {
            creditComposition
        } else if isRequirementTable {
            requirementRows
        } else {
            genericTable
        }
    }

    private var detailedCreditComposition: some View {
        VStack(alignment: .leading, spacing: 0) {
            tableHeader(leading: "课程模块", trailing: "计入学分")
            Divider()
            ForEach(Array(table.rows.enumerated()), id: \.offset) { index, row in
                let section = cell(row, named: "板块")
                let module = cell(row, named: "模块")
                let note = cell(row, named: "说明")
                let isTotal = module == "合计"
                let previousSection = index > 0
                    ? cell(table.rows[index - 1], named: "板块")
                    : ""
                if !section.isEmpty && section != previousSection {
                    Text(section)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.Color.accent)
                        .padding(.top, index == 0 ? 9 : 13)
                        .padding(.bottom, 3)
                }
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(inlineMarkdown: module.isEmpty ? " " : module)
                            .font(.subheadline.weight(isTotal ? .semibold : .medium))
                            .frame(minHeight: 20, alignment: .leading)
                        Spacer(minLength: 8)
                        let total = cell(row, named: "合计")
                        Text(inlineMarkdown: total.isEmpty ? "000 学分" : creditWithUnit(total))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(isTotal ? Theme.Color.accent : .primary)
                            .opacity(total.isEmpty ? 0 : 1)
                            .frame(minWidth: 76, alignment: .trailing)
                    }
                    let detail = detailedCreditDetail(row)
                    if !detail.isEmpty {
                        Text(inlineMarkdown: detail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if !note.isEmpty && note != "—" {
                        Text(inlineMarkdown: note)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.vertical, 8)
                if index < table.rows.count - 1 {
                    Divider()
                }
            }
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var creditComposition: some View {
        VStack(alignment: .leading, spacing: 0) {
            tableHeader(leading: "类别", trailing: "合计")
            Divider()
            ForEach(Array(table.rows.enumerated()), id: \.offset) { index, row in
                let title = cell(row, named: "类别")
                let isTotal = title == "合计"
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(inlineMarkdown: title)
                            .font(.subheadline.weight(isTotal ? .semibold : .medium))
                        Spacer(minLength: 8)
                        Text(inlineMarkdown: creditWithUnit(cell(row, named: "合计")))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(isTotal ? Theme.Color.accent : .primary)
                    }
                    Text(inlineMarkdown: compositionDetail(row))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 9)
                if index < table.rows.count - 1 {
                    Divider()
                }
            }
        }
        .textSelection(.enabled)
    }

    private var requirementRows: some View {
        VStack(alignment: .leading, spacing: 0) {
            tableHeader(leading: "模块", trailing: "最低要求")
            Divider()
            ForEach(Array(table.rows.enumerated()), id: \.offset) { index, row in
                let note = cell(row, named: "说明")
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(inlineMarkdown: cell(row, named: "模块"))
                            .font(.subheadline.weight(.medium))
                        Spacer(minLength: 8)
                        Text(inlineMarkdown: creditLabel(cell(row, named: "最低学分")))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.Color.accent)
                    }
                    if !note.isEmpty && note != "—" {
                        Text(inlineMarkdown: note)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 9)
                if index < table.rows.count - 1 {
                    Divider()
                }
            }
        }
        .textSelection(.enabled)
    }

    private var genericTable: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 8) {
                GridRow {
                    ForEach(Array(table.headers.enumerated()), id: \.offset) { _, header in
                        Text(inlineMarkdown: header)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                }
                Divider().gridCellColumns(max(table.headers.count, 1))
                ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(0..<table.headers.count, id: \.self) { index in
                            Text(inlineMarkdown: cell(row, at: index))
                                .font(.caption)
                        }
                    }
                }
            }
            .textSelection(.enabled)
        }
        .scrollIndicators(.hidden)
    }

    private func tableHeader(leading: String, trailing: String) -> some View {
        HStack {
            Text(leading)
            Spacer()
            Text(trailing)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(.secondary)
        .padding(.bottom, 7)
        .frame(maxWidth: .infinity)
    }

    private func compositionDetail(_ row: [String]) -> String {
        let required = cell(row, named: "必修")
        let elective = cell(row, named: "选修")
        let ratio = cell(row, named: "占比")
        return "必修 \(required) · 选修 \(elective) · 占比 \(ratio)"
    }

    private func detailedCreditDetail(_ row: [String]) -> String {
        let required = cell(row, named: "必修")
        let elective = cell(row, named: "选修")
        var values: [String] = []
        if required != "0" && !required.isEmpty {
            values.append("必修 \(required)")
        }
        if elective != "0" && !elective.isEmpty {
            values.append("选修 \(elective)")
        }
        return values.joined(separator: " · ")
    }

    private func creditLabel(_ value: String) -> String {
        value == "未明确提取" ? "待核对" : "\(value) 学分"
    }

    private func creditWithUnit(_ value: String) -> String {
        guard let markerRange = value.range(
            of: #"\[\d+\]$"#,
            options: .regularExpression
        ) else {
            return "\(value) 学分"
        }
        let number = value[..<markerRange.lowerBound]
        let marker = value[markerRange]
        return "\(number) 学分\(marker)"
    }

    private func cell(_ row: [String], named header: String) -> String {
        guard let index = table.headers.firstIndex(of: header) else { return "" }
        return cell(row, at: index)
    }

    private func cell(_ row: [String], at index: Int) -> String {
        row.indices.contains(index) ? row[index] : ""
    }
}

private struct SourceFileReference: View {
    let text: String
    let fileURL: URL

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(inlineMarkdown: text)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)

            Link(destination: fileURL) {
                Label("下载原文件", systemImage: "arrow.down.circle.fill")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14)
                    .frame(height: 40)
                    .actionBlueGlassCapsule()
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44, alignment: .leading)
            .accessibilityHint("打开学校官方原始文件")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 2)
    }
}
