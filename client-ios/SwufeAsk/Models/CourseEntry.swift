import Foundation
import SwiftData

/// 个人课表中的一门课。由课表导入(OCR 解析确认后)或手动添加产生,
/// 存 SwiftData 本地库;上课提醒按 weekday + 节次 + 周次滚动调度。
@Model
final class CourseEntry {
    var id: UUID = UUID()
    /// 课程名称,如"高等数学"。
    var name: String = ""
    /// 任课老师,可为空。
    var teacher: String = ""
    /// 上课地点,如"通博楼B201"。
    var location: String = ""
    /// 周几上课:1=周一 … 7=周日。
    var weekday: Int = 1
    /// 起止节次(1~12),对应 SectionTimetable 的作息时间。
    var startSection: Int = 1
    var endSection: Int = 2
    /// 上课周次,如 [1,2,…,16];单双周课存对应奇偶周。
    var weeks: [Int] = []
    var createdAt: Date = Date.now

    init(
        name: String,
        teacher: String = "",
        location: String = "",
        weekday: Int = 1,
        startSection: Int = 1,
        endSection: Int = 2,
        weeks: [Int] = Array(1...16)
    ) {
        self.id = UUID()
        self.name = name
        self.teacher = teacher
        self.location = location
        self.weekday = weekday
        self.startSection = startSection
        self.endSection = endSection
        self.weeks = weeks
        self.createdAt = .now
    }
}

extension CourseEntry {
    static let weekdayNames = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    var weekdayName: String {
        Self.weekdayNames[max(1, min(weekday, 7)) - 1]
    }

    var sectionRangeText: String {
        startSection == endSection ? "第\(startSection)节" : "\(startSection)-\(endSection)节"
    }

    var weeksSummary: String {
        WeeksExpression.format(weeks)
    }

    /// 依据当前作息表得到的上课时间段文案,如 "10:25-12:00"。
    var timeRangeText: String {
        let table = SectionTimetable.current()
        guard let start = table.startMinute(ofSection: startSection),
              let end = table.endMinute(ofSection: endSection) else {
            return sectionRangeText
        }
        return "\(SectionTimetable.clockText(start))-\(SectionTimetable.clockText(end))"
    }
}
