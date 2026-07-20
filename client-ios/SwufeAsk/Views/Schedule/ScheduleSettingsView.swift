import SwiftData
import SwiftUI

/// 课表设置:学期第一周、提醒开关与状态、节次作息表调整。
struct ScheduleSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var context

    @AppStorage(CourseReminderScheduler.enabledKey) private var remindersEnabled = true
    @State private var semesterStart = SemesterCalendar.semesterStart()
    @State private var sectionStarts: [Int] = []
    @State private var sectionEnds: [Int] = []
    @State private var pendingCount: Int?
    @State private var authorizationDenied = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    DatePicker("第一周的周一", selection: $semesterStart, displayedComponents: .date)
                    LabeledContent(
                        "当前教学周",
                        value: SemesterCalendar.teachingWeek().map { "第 \($0) 周" } ?? "学期未开始"
                    )
                } header: {
                    Text("学期")
                } footer: {
                    Text("周次(单双周)按这里设置的第一周计算,选任意一天会自动对齐到该周周一。")
                }

                Section {
                    Toggle("开课前 10 分钟提醒", isOn: $remindersEnabled)
                    if remindersEnabled {
                        LabeledContent("未来 7 天已排提醒", value: pendingCount.map { "\($0) 条" } ?? "统计中…")
                    }
                    if authorizationDenied {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("通知权限已被关闭,提醒无法送达。")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                            Button("去系统设置开启", action: openSystemSettings)
                                .font(.footnote.weight(.semibold))
                        }
                    }
                } header: {
                    Text("上课提醒")
                } footer: {
                    Text("提醒按未来 7 天滚动安排,平时正常打开 App 即会自动续期;内容包含课程名、老师和上课地点。")
                }

                Section {
                    ForEach(sectionStarts.indices, id: \.self) { index in
                        SectionTimeRow(
                            section: index + 1,
                            startMinute: $sectionStarts[index],
                            endMinute: $sectionEnds[index]
                        )
                    }
                    Button("恢复默认作息", role: .destructive, action: resetTimetable)
                } header: {
                    Text("节次作息")
                } footer: {
                    Text("如与学校实际作息不符可逐节调整,课表显示与提醒时间都会随之更新。")
                }
            }
            .navigationTitle("课表设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .task {
                loadTimetable()
                await refreshStatus()
            }
            .onChange(of: semesterStart) { _, newValue in
                SemesterCalendar.saveSemesterStart(newValue)
                rescheduleAndRecount()
            }
            .onChange(of: remindersEnabled) {
                rescheduleAndRecount()
            }
            .onChange(of: sectionStarts) {
                saveTimetable()
            }
            .onChange(of: sectionEnds) {
                saveTimetable()
            }
        }
    }

    private func loadTimetable() {
        let table = SectionTimetable.current()
        sectionStarts = table.times.map(\.start)
        sectionEnds = table.times.map(\.end)
    }

    private func saveTimetable() {
        guard sectionStarts.count == SectionTimetable.sectionCount,
              sectionEnds.count == SectionTimetable.sectionCount else { return }
        SectionTimetable(times: zip(sectionStarts, sectionEnds).map { (start: $0, end: $1) }).save()
        rescheduleAndRecount()
    }

    private func resetTimetable() {
        SectionTimetable.resetToDefault()
        loadTimetable()
        rescheduleAndRecount()
    }

    private func rescheduleAndRecount() {
        CourseReminderScheduler.refresh(using: context)
        Task {
            // 稍等调度落盘再统计,数字才准确。
            try? await Task.sleep(for: .milliseconds(600))
            await refreshStatus()
        }
    }

    private func refreshStatus() async {
        pendingCount = await CourseReminderScheduler.pendingReminderCount()
        if remindersEnabled {
            authorizationDenied = await !CourseReminderScheduler.requestAuthorization()
        } else {
            authorizationDenied = false
        }
    }

    private func openSystemSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}

/// 一节课的起止时间编辑行(以分钟数存储,DatePicker 编辑)。
private struct SectionTimeRow: View {
    let section: Int
    @Binding var startMinute: Int
    @Binding var endMinute: Int

    var body: some View {
        HStack {
            Text("第 \(section) 节")
            Spacer()
            DatePicker("第 \(section) 节开始时间", selection: minuteBinding($startMinute), displayedComponents: .hourAndMinute)
                .labelsHidden()
            Text("–")
                .foregroundStyle(.secondary)
            DatePicker("第 \(section) 节结束时间", selection: minuteBinding($endMinute), displayedComponents: .hourAndMinute)
                .labelsHidden()
        }
    }

    /// 分钟数 ↔ 当天时刻的换算,DatePicker 只关心时分。
    private func minuteBinding(_ minute: Binding<Int>) -> Binding<Date> {
        Binding(
            get: {
                Calendar.current.date(
                    bySettingHour: minute.wrappedValue / 60,
                    minute: minute.wrappedValue % 60,
                    second: 0,
                    of: Calendar.current.startOfDay(for: .now)
                ) ?? .now
            },
            set: { newValue in
                let components = Calendar.current.dateComponents([.hour, .minute], from: newValue)
                minute.wrappedValue = (components.hour ?? 0) * 60 + (components.minute ?? 0)
            }
        )
    }
}
