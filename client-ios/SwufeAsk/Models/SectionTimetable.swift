import Foundation

/// 节次作息表:第 n 节课的上下课时间(自午夜起的分钟数)。
/// 默认值取常见高校作息,可在「课表设置」里按学校实际作息逐节调整,
/// 修改持久化到 UserDefaults 并立即影响课表显示与提醒时间。
struct SectionTimetable {
    static let sectionCount = 12
    static let storageKey = "schedule.sectionTimes.v1"

    /// [(开始分钟, 结束分钟)],下标 0 对应第 1 节。
    var times: [(start: Int, end: Int)]

    // 12 节制:上午 1-4、下午 5-9、晚上 10-12(与教务课表的时间段分区一致)。
    static let defaultTimes: [(start: Int, end: Int)] = [
        (8 * 60 + 30, 9 * 60 + 15),    // 1  08:30-09:15
        (9 * 60 + 20, 10 * 60 + 5),    // 2  09:20-10:05
        (10 * 60 + 25, 11 * 60 + 10),  // 3  10:25-11:10
        (11 * 60 + 15, 12 * 60 + 0),   // 4  11:15-12:00
        (14 * 60 + 0, 14 * 60 + 45),   // 5  14:00-14:45
        (14 * 60 + 50, 15 * 60 + 35),  // 6  14:50-15:35
        (15 * 60 + 55, 16 * 60 + 40),  // 7  15:55-16:40
        (16 * 60 + 45, 17 * 60 + 30),  // 8  16:45-17:30
        (17 * 60 + 35, 18 * 60 + 20),  // 9  17:35-18:20
        (19 * 60 + 30, 20 * 60 + 15),  // 10 19:30-20:15
        (20 * 60 + 20, 21 * 60 + 5),   // 11 20:20-21:05
        (21 * 60 + 10, 21 * 60 + 55)   // 12 21:10-21:55
    ]

    static func current() -> SectionTimetable {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let stored = try? JSONDecoder().decode([[Int]].self, from: data),
              stored.count == sectionCount,
              stored.allSatisfy({ $0.count == 2 }) else {
            return SectionTimetable(times: defaultTimes)
        }
        return SectionTimetable(times: stored.map { (start: $0[0], end: $0[1]) })
    }

    func save() {
        let encodable = times.map { [$0.start, $0.end] }
        UserDefaults.standard.set(try? JSONEncoder().encode(encodable), forKey: Self.storageKey)
    }

    static func resetToDefault() {
        UserDefaults.standard.removeObject(forKey: storageKey)
    }

    func startMinute(ofSection section: Int) -> Int? {
        guard (1...times.count).contains(section) else { return nil }
        return times[section - 1].start
    }

    func endMinute(ofSection section: Int) -> Int? {
        guard (1...times.count).contains(section) else { return nil }
        return times[section - 1].end
    }

    static func clockText(_ minute: Int) -> String {
        let hour = minute / 60
        let min = minute % 60
        return "\(hour):" + (min < 10 ? "0\(min)" : "\(min)")
    }
}

/// 学期周历:以「第一周的周一」为锚点,把日历日期换算成教学周与周几。
enum SemesterCalendar {
    static let startDateKey = "schedule.semesterStart.v1"

    /// 学期第一周的周一。未设置时默认取本周周一(便于导入后立即演示)。
    static func semesterStart() -> Date {
        let stored = UserDefaults.standard.double(forKey: startDateKey)
        if stored > 0 {
            return Date(timeIntervalSince1970: stored)
        }
        return mondayOfWeek(containing: .now)
    }

    static func saveSemesterStart(_ date: Date) {
        UserDefaults.standard.set(
            mondayOfWeek(containing: date).timeIntervalSince1970,
            forKey: startDateKey
        )
    }

    /// date 所在教学周(第 1 周起);早于学期开始返回 nil。
    static func teachingWeek(of date: Date = .now) -> Int? {
        let start = mondayOfWeek(containing: semesterStart())
        let days = Calendar.current.dateComponents(
            [.day],
            from: Calendar.current.startOfDay(for: start),
            to: Calendar.current.startOfDay(for: date)
        ).day ?? 0
        guard days >= 0 else { return nil }
        return days / 7 + 1
    }

    /// 1=周一 … 7=周日(与 CourseEntry.weekday 一致)。
    static func courseWeekday(of date: Date) -> Int {
        let sundayFirst = Calendar.current.component(.weekday, from: date)
        return sundayFirst == 1 ? 7 : sundayFirst - 1
    }

    static func mondayOfWeek(containing date: Date) -> Date {
        var calendar = Calendar.current
        calendar.firstWeekday = 2
        let components = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        return calendar.date(from: components) ?? calendar.startOfDay(for: date)
    }
}
