import Foundation

/// 周次表达式的解析与格式化。
/// 支持 "1-16"、"1-16周"、"1,3,5-9"、"2-16双周"、"单周" 等教务课表里的常见写法。
enum WeeksExpression {
    /// 解析失败(没有任何数字且不含单双周标记)返回 nil。
    static func parse(_ raw: String, maxWeek: Int = 25) -> [Int]? {
        let text = raw
            .replacing("，", with: ",")
            .replacing("、", with: ",")
            .replacing("～", with: "-")
            .replacing("—", with: "-")
            .replacing("至", with: "-")
            .replacing("周", with: "")
            .replacing("第", with: "")
            .replacing(" ", with: "")

        let wantsOdd = text.contains("单")
        let wantsEven = text.contains("双")
        let digitsOnly = text.replacing(/[单双()（）]/, with: "")

        var weeks = Set<Int>()
        for segment in digitsOnly.split(separator: ",") {
            if let match = segment.wholeMatch(of: /(\d+)-(\d+)/),
               let low = Int(match.1), let high = Int(match.2), low <= high {
                weeks.formUnion(low...min(high, maxWeek))
            } else if let single = Int(segment) {
                weeks.insert(single)
            }
        }

        // "单周"/"双周" 未带范围时默认整学期。
        if weeks.isEmpty, wantsOdd || wantsEven {
            weeks.formUnion(1...maxWeek)
        }
        if wantsOdd { weeks = weeks.filter { $0.isMultiple(of: 2) == false } }
        if wantsEven { weeks = weeks.filter { $0.isMultiple(of: 2) } }

        let sorted = weeks.filter { $0 >= 1 && $0 <= maxWeek }.sorted()
        return sorted.isEmpty ? nil : sorted
    }

    /// 把周次数组压缩成可读文案:[1,2,3,5,7] -> "1-3,5,7周";纯单/双周会标注。
    static func format(_ weeks: [Int]) -> String {
        let sorted = Array(Set(weeks)).sorted()
        guard !sorted.isEmpty else { return "未设置" }

        if sorted.count > 2 {
            let allOdd = sorted.allSatisfy { !$0.isMultiple(of: 2) }
            let allEven = sorted.allSatisfy { $0.isMultiple(of: 2) }
            let stride2 = zip(sorted, sorted.dropFirst()).allSatisfy { $1 - $0 == 2 }
            if stride2, allOdd || allEven {
                return "\(sorted.first!)-\(sorted.last!)周(\(allOdd ? "单" : "双"))"
            }
        }

        var parts: [String] = []
        var runStart = sorted[0]
        var previous = sorted[0]
        for week in sorted.dropFirst() {
            if week == previous + 1 {
                previous = week
                continue
            }
            parts.append(runStart == previous ? "\(runStart)" : "\(runStart)-\(previous)")
            runStart = week
            previous = week
        }
        parts.append(runStart == previous ? "\(runStart)" : "\(runStart)-\(previous)")
        return parts.joined(separator: ",") + "周"
    }
}
