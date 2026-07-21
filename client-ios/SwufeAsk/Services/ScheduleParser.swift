import Foundation

/// OCR 解析出的一门课(导入确认页的可编辑草稿,确认后转为 CourseEntry)。
struct ParsedCourse: Identifiable, Hashable {
    let id = UUID()
    var name: String
    var teacher: String
    var location: String
    var weekday: Int
    var startSection: Int
    var endSection: Int
    var weeks: [Int]
    /// OCR 没有识别出节次时为 true,确认页高亮提示用户补填。
    var sectionsUncertain = false
}

/// 课表 OCR 文本的启发式解析:
/// 1. 定位表头「周一…周日」得到 7 列的 x 中心;
/// 2. 表头以下的文本块按 x 归入最近列,列内按 y 间隙聚簇成单元格;
/// 3. 单元格内以「周次」行为锚点,拆出课程名/老师/节次/地点。
/// 教务课表格式繁杂,解析结果一律进确认页人工校对后再入库。
enum ScheduleParser {
    static func parse(blocks: [RecognizedTextBlock]) -> [ParsedCourse] {
        // 预过滤:左侧节次栏的纯数字、页脚“打印时间”、底部“其他课程”
        // (无固定上课时间的挂名课)等与课程格无关的块。
        let content = blocks.filter { block in
            if block.text.wholeMatch(of: /\d{1,2}/) != nil { return false }
            if block.text.contains("打印时间") || block.text.contains("其他课程") { return false }
            return true
        }
        guard let header = detectWeekdayColumns(in: content) else { return [] }

        var courses: [ParsedCourse] = []
        for (weekday, columnX) in header.columns {
            let columnBlocks = content
                .filter { $0.midY > header.headerY + 0.005 }
                .filter { abs($0.midX - columnX) < header.columnTolerance }
                .sorted { $0.midY < $1.midY }
            for cell in clusterIntoCells(columnBlocks) {
                courses.append(contentsOf: parseCell(cell, weekday: weekday))
            }
        }
        return courses
    }

    // MARK: - 表头与列

    private struct HeaderLayout {
        /// [(weekday 1~7, 列 x 中心)]
        let columns: [(Int, Double)]
        let headerY: Double
        let columnTolerance: Double
    }

    private static let weekdayPatterns: [(Int, [String])] = [
        (1, ["星期一", "周一"]), (2, ["星期二", "周二"]), (3, ["星期三", "周三"]),
        (4, ["星期四", "周四"]), (5, ["星期五", "周五"]), (6, ["星期六", "周六"]),
        (7, ["星期日", "星期天", "周日", "周天"])
    ]

    private static func detectWeekdayColumns(in blocks: [RecognizedTextBlock]) -> HeaderLayout? {
        var found: [Int: RecognizedTextBlock] = [:]
        for block in blocks {
            for (weekday, patterns) in weekdayPatterns where patterns.contains(where: { block.text.contains($0) }) {
                // 同一天取最靠上的匹配(表头),忽略正文里偶然出现的“周三”等字样。
                if let existing = found[weekday], existing.midY <= block.midY { continue }
                found[weekday] = block
            }
        }
        // 至少认出 3 天才认为找到了课表表头。
        guard found.count >= 3 else { return nil }

        let columns = found
            .map { ($0.key, $0.value.midX) }
            .sorted { $0.1 < $1.1 }
        let xs = columns.map(\.1)
        let gaps = zip(xs, xs.dropFirst()).map { $1 - $0 }
        let averageGap = gaps.isEmpty ? 0.14 : gaps.reduce(0, +) / Double(gaps.count)
        let headerY = found.values.map(\.midY).max() ?? 0

        return HeaderLayout(
            columns: columns,
            headerY: headerY,
            columnTolerance: max(averageGap * 0.55, 0.05)
        )
    }

    // MARK: - 单元格聚簇

    private static func clusterIntoCells(_ blocks: [RecognizedTextBlock]) -> [[RecognizedTextBlock]] {
        var cells: [[RecognizedTextBlock]] = []
        var current: [RecognizedTextBlock] = []
        for block in blocks {
            if let last = current.last {
                let gap = block.midY - last.midY
                let threshold = max(max(last.height, block.height) * 1.9, 0.022)
                if gap > threshold {
                    cells.append(current)
                    current = []
                }
            }
            current.append(block)
        }
        if !current.isEmpty { cells.append(current) }
        return cells
    }

    // MARK: - 单元格 → 课程

    // 兼容 “1-16周”“1,3,5周”“2-16双周”“单周” 等连写形式。
    private static let weeksAnchor = /(\d+(?:[\-~～]\d+)?周?(?:[,，、]\d+(?:[\-~～]\d+)?周?)*[单双]?周|[单双]周)/
    private static let sectionRange = /(\d{1,2})[\-~～](\d{1,2})节/
    private static let sectionSingle = /第?(\d{1,2})节/

    /// 一个视觉单元格里可能压着上下两门课(网格紧凑时被聚进同一簇),
    /// 按“周次”锚点行切分:每个锚点上方连续的“干净行”(无键值分隔符)是
    /// 该课的名称,锚点下方到下一门名称区之前的行是键值详情。
    private static func parseCell(_ cell: [RecognizedTextBlock], weekday: Int) -> [ParsedCourse] {
        let lines = cell.map(\.text)
        let anchors = lines.indices.filter { isAnchorLine(lines, $0) }
        guard !anchors.isEmpty else { return [] }

        var courses: [ParsedCourse] = []
        for (order, anchor) in anchors.enumerated() {
            var nameStart = anchor
            let lowerBound = order > 0 ? anchors[order - 1] : -1
            while nameStart - 1 > lowerBound, isCleanNameLine(lines[nameStart - 1]) {
                nameStart -= 1
            }
            var detailEnd = order + 1 < anchors.count ? anchors[order + 1] : lines.count
            if order + 1 < anchors.count {
                var probe = anchors[order + 1] - 1
                while probe > anchor, isCleanNameLine(lines[probe]) {
                    detailEnd = probe
                    probe -= 1
                }
            }
            let slice = Array(lines[nameStart..<detailEnd])
            if let course = parseCourseLines(slice, anchorIndex: anchor - nameStart, weekday: weekday) {
                courses.append(course)
            }
        }
        return courses
    }

    /// 不含键值分隔符与周次标记的行,视为课程名的组成部分。
    private static func isCleanNameLine(_ line: String) -> Bool {
        !line.contains("/") && !line.contains(":") && !line.contains("：")
            && !line.contains(weeksAnchor)
    }

    /// 周次锚点行判定。窄单元格里 “1-17周” 可能恰好在数字与“周”之间被
    /// 换行切断,所以额外检查与下一行的拼接处是否跨缝出现周次。
    private static func isAnchorLine(_ lines: [String], _ index: Int) -> Bool {
        if lines[index].contains(weeksAnchor) { return true }
        guard index + 1 < lines.count else { return false }
        let seam = lines[index] + lines[index + 1]
        guard let match = seam.firstMatch(of: weeksAnchor) else { return false }
        // 匹配必须真正跨越接缝,否则那是下一行自己的周次。
        let seamIndex = seam.index(seam.startIndex, offsetBy: lines[index].count)
        return match.range.lowerBound < seamIndex && match.range.upperBound > seamIndex
    }

    private static func parseCourseLines(_ lines: [String], anchorIndex: Int, weekday: Int) -> ParsedCourse? {

        // 无分隔拼接,还原被单元格宽度截断换行的长文本
        // (如“…场地:经世楼”+“B108/教师:邢容/…”)。
        let joined = lines.joined()

        // 周次:优先取锚点行里的周次片段,行内没有完整周次时用全文匹配。
        let anchorLine = lines[anchorIndex]
        let weeksText = anchorLine.firstMatch(of: weeksAnchor).map { String($0.1) }
            ?? joined.firstMatch(of: weeksAnchor).map { String($0.1) }
            ?? anchorLine
        let weeks = WeeksExpression.parse(weeksText) ?? Array(1...16)

        // 节次:全单元格文本里找 “m-n节”,退化到 “第m节”。
        var startSection = 1
        var endSection = 2
        var sectionsUncertain = true
        if let match = joined.firstMatch(of: sectionRange),
           let low = Int(match.1), let high = Int(match.2),
           low >= 1, low <= high, high <= 12 {
            startSection = low
            endSection = high
            sectionsUncertain = false
        } else if let match = joined.firstMatch(of: sectionSingle),
                  let section = Int(match.1), section >= 1, section <= 12 {
            startSection = section
            endSection = section
            sectionsUncertain = false
        }

        let beforeAnchor = Array(lines[..<anchorIndex])

        // 键值串格式(正方教务导出):…/场地:经世楼B108/教师:邢容/课程性质简称:…
        // 命中时直接取字段;锚点行剩余与后续行都属于键值串,不再拼进名称。
        let kvTeacher = joined.firstMatch(of: /教师[:：]([^\/:：]+)/).map { String($0.1) }
        let kvLocation = joined.firstMatch(of: /场地[:：]([^\/:：]+)/).map { String($0.1) }
        if kvTeacher != nil || kvLocation != nil || joined.contains("课程性质") {
            var name = beforeAnchor.joined()
            if name.isEmpty {
                // 课程名与键值串被 OCR 合成一行:取锚点行左括号前的部分。
                name = String(anchorLine.prefix(while: { $0 != "(" && $0 != "（" }))
            }
            name = name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }

            // OCR 偶发漏识别“场地:/教师:”标签,用楼栋号模式与宽容教师正则兜底。
            var location = kvLocation?.trimmingCharacters(in: .whitespaces) ?? ""
            if location.isEmpty {
                location = joined
                    .firstMatch(of: /[\u{4E00}-\u{9FFF}]{1,6}[楼馆场][A-Za-z]?\d{0,4}/)
                    .map { String($0.0) } ?? ""
            }
            var teacher = kvTeacher?.trimmingCharacters(in: .whitespaces) ?? ""
            if teacher.isEmpty {
                // “教”字偶发识别丢失,退化到 “师:姓名” 模式。
                teacher = joined
                    .firstMatch(of: /师[:：]\s*([\u{4E00}-\u{9FFF}]{2,4})/)
                    .map { String($0.1) } ?? ""
            }

            return ParsedCourse(
                name: name,
                teacher: teacher,
                location: location,
                weekday: weekday,
                startSection: startSection,
                endSection: endSection,
                weeks: weeks,
                sectionsUncertain: sectionsUncertain
            )
        }

        // 通用启发式:课表单元格惯例是课程名在前、老师紧邻周次行,
        // 所以只有当锚点前至少有两行、且最后一行像人名(2~4 个纯汉字)时,
        // 才把最后一行当老师,避免“高等数学”这类短课程名被误判。
        var nameParts: [String] = []
        var teacher = ""
        if beforeAnchor.count >= 2, let last = beforeAnchor.last, isLikelyTeacher(last) {
            teacher = last
            nameParts = beforeAnchor.dropLast()
        } else {
            nameParts = beforeAnchor
        }

        // 锚点行剥掉周次/节次后剩余的文字,可能是名称或地点的一部分。
        var anchorRemainder = anchorLine
            .replacing(weeksAnchor, with: " ")
            .replacing(sectionRange, with: " ")
            .replacing(sectionSingle, with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        anchorRemainder = anchorRemainder
            .trimmingCharacters(in: CharacterSet(charactersIn: "()（）[]【】"))

        // 锚点之后的行:优先取带教学楼特征的作为地点,其余并入名称备选。
        var location = ""
        var trailingParts: [String] = []
        for line in lines[(anchorIndex + 1)...] {
            if location.isEmpty, isLikelyLocation(line) {
                location = line
            } else if isLikelyTeacher(line), teacher.isEmpty {
                teacher = line
            } else {
                trailingParts.append(line)
            }
        }
        if location.isEmpty, isLikelyLocation(anchorRemainder) {
            location = anchorRemainder
            anchorRemainder = ""
        }
        if location.isEmpty, let last = trailingParts.last {
            location = last
            trailingParts.removeLast()
        }

        if !anchorRemainder.isEmpty { nameParts.append(anchorRemainder) }
        nameParts.append(contentsOf: trailingParts)
        let name = nameParts.joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard !name.isEmpty else { return nil }
        return ParsedCourse(
            name: name,
            teacher: teacher,
            location: location,
            weekday: weekday,
            startSection: startSection,
            endSection: endSection,
            weeks: weeks,
            sectionsUncertain: sectionsUncertain
        )
    }

    private static func isLikelyTeacher(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        // 2~4 个纯汉字视为人名。
        return trimmed.wholeMatch(of: /[\u{4E00}-\u{9FFF}]{2,4}/) != nil
    }

    private static func isLikelyLocation(_ text: String) -> Bool {
        guard !text.isEmpty else { return false }
        if text.contains(/[楼馆场室区厅]/) { return true }
        // “通博B201”“H203”这类楼栋+房间号。
        return text.contains(/[A-Za-z]\d{2,}/) || text.contains(/\d{3,}教?室?$/)
    }

    /// PDF 文字层提供精确的课程名、节次与周次；Vision 仍负责星期列、
    /// 教师和教室定位。按课程名与已有周次重叠度匹配，避免同名分段课串位。
    static func reconcile(
        _ courses: [ParsedCourse],
        with hints: [ScheduleCourseHint]
    ) -> [ParsedCourse] {
        guard !courses.isEmpty, !hints.isEmpty else { return courses }
        var available = Set(hints.indices)
        return courses.map { original in
            let courseName = chineseSignature(original.name)
            let match = available.max { lhs, rhs in
                hintScore(hints[lhs], course: original, signature: courseName)
                    < hintScore(hints[rhs], course: original, signature: courseName)
            }
            guard let match,
                  hintScore(hints[match], course: original, signature: courseName) >= 80 else {
                return cleaned(original)
            }
            available.remove(match)
            let hint = hints[match]
            var course = cleaned(original)
            course.name = hint.name
            course.startSection = hint.startSection
            course.endSection = hint.endSection
            course.weeks = hint.weeks
            course.sectionsUncertain = false
            return course
        }
    }

    private static func hintScore(
        _ hint: ScheduleCourseHint,
        course: ParsedCourse,
        signature: String
    ) -> Int {
        let target = chineseSignature(hint.name)
        var score = 0
        if !signature.isEmpty, signature == target {
            score += 100
        } else if !signature.isEmpty,
                  (signature.contains(target) || target.contains(signature)) {
            score += 80
        }
        if course.startSection == hint.startSection,
           course.endSection == hint.endSection {
            score += 12
        }
        score += Set(course.weeks).intersection(hint.weeks).count
        return score
    }

    private static func chineseSignature(_ value: String) -> String {
        String(value.unicodeScalars.filter {
            (0x4E00...0x9FFF).contains(Int($0.value))
        })
    }

    private static func cleaned(_ original: ParsedCourse) -> ParsedCourse {
        var course = original
        course.teacher = (course.teacher.components(separatedBy: "课程").first ?? "")
            .trimmingCharacters(
            in: CharacterSet(charactersIn: " ；;:：,，/")
        )
        course.location = (course.location.components(separatedBy: "教师").first ?? "")
        .replacingOccurrences(of: " ", with: "")
        .trimmingCharacters(in: CharacterSet(charactersIn: "；;:：,，/"))
        return course
    }
}
