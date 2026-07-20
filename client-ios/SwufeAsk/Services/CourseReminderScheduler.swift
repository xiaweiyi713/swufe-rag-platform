import Foundation
import SwiftData
import UserNotifications

/// 上课提醒调度。
///
/// iOS 对每个 App 只保留 64 条待触发的本地通知,没法一次排完整学期,
/// 所以采用滚动窗口:每次 App 激活/课表变动时,重排「未来 7 天」内
/// 每节课开课前 10 分钟的提醒(按教学周精确匹配,单双周不误报)。
/// 只要一周内打开过一次 App,提醒就会持续续期。
enum CourseReminderScheduler {
    static let enabledKey = "schedule.reminders.enabled.v1"
    private static let identifierPrefix = "course-"
    private static let leadMinutes = 10
    private static let windowDays = 7

    static var remindersEnabled: Bool {
        UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true
    }

    /// 请求通知权限;已拒绝时返回 false,由设置页引导去系统设置开启。
    static func requestAuthorization() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        case .denied:
            return false
        default:
            return true
        }
    }

    /// 从 SwiftData 主上下文读出全部课程并重排提醒。App 激活与课表变动时调用。
    @MainActor
    static func refresh(using context: ModelContext) {
        let courses = (try? context.fetch(FetchDescriptor<CourseEntry>())) ?? []
        Task {
            await reschedule(courses: courses.map(ReminderCourse.init))
        }
    }

    /// 设置页展示用:当前已排定的提醒条数。
    static func pendingReminderCount() async -> Int {
        await UNUserNotificationCenter.current()
            .pendingNotificationRequests()
            .count { $0.identifier.hasPrefix(identifierPrefix) }
    }

    // MARK: - 内部实现

    /// CourseEntry 的值快照,避免把 @Model 对象带出主线程。
    private struct ReminderCourse {
        let id: UUID
        let name: String
        let teacher: String
        let location: String
        let weekday: Int
        let startSection: Int
        let weeks: Set<Int>

        init(_ entry: CourseEntry) {
            id = entry.id
            name = entry.name
            teacher = entry.teacher
            location = entry.location
            weekday = entry.weekday
            startSection = entry.startSection
            weeks = Set(entry.weeks)
        }
    }

    private static func reschedule(courses: [ReminderCourse]) async {
        let center = UNUserNotificationCenter.current()

        // 只清自己前缀的通知,不动其他来源。
        let stale = await center.pendingNotificationRequests()
            .map(\.identifier)
            .filter { $0.hasPrefix(identifierPrefix) }
        center.removePendingNotificationRequests(withIdentifiers: stale)

        guard remindersEnabled, !courses.isEmpty else { return }
        guard await requestAuthorization() else { return }

        let timetable = SectionTimetable.current()
        let calendar = Calendar.current
        let now = Date.now
        let dayStamp = Date.FormatStyle().year().month(.twoDigits).day(.twoDigits)

        for dayOffset in 0..<windowDays {
            guard let day = calendar.date(byAdding: .day, value: dayOffset, to: calendar.startOfDay(for: now)),
                  let week = SemesterCalendar.teachingWeek(of: day) else { continue }
            let weekday = SemesterCalendar.courseWeekday(of: day)

            for course in courses where course.weekday == weekday && course.weeks.contains(week) {
                guard let startMinute = timetable.startMinute(ofSection: course.startSection),
                      let classTime = calendar.date(byAdding: .minute, value: startMinute, to: day) else { continue }
                let fireTime = classTime.addingTimeInterval(TimeInterval(-leadMinutes * 60))
                guard fireTime > now else { continue }

                let content = UNMutableNotificationContent()
                content.title = "即将上课:\(course.name)"
                var details = ["\(SectionTimetable.clockText(startMinute)) 开始"]
                if !course.teacher.isEmpty { details.append(course.teacher) }
                if !course.location.isEmpty { details.append(course.location) }
                content.body = details.joined(separator: " · ")
                content.sound = .default

                let components = calendar.dateComponents(
                    [.year, .month, .day, .hour, .minute],
                    from: fireTime
                )
                let request = UNNotificationRequest(
                    identifier: "\(identifierPrefix)\(course.id.uuidString)-\(day.formatted(dayStamp))",
                    content: content,
                    trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                )
                try? await center.add(request)
            }
        }
    }
}

/// 让提醒在 App 处于前台时也以横幅+声音展示(默认前台不显示)。
final class NotificationForegroundPresenter: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }
}
