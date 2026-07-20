import SwiftUI

/// 单门课程的编辑表单。手动添加、导入草稿修改、已存课程编辑三处共用:
/// 操作 ParsedCourse 草稿值,保存动作由调用方决定写入去向。
struct CourseEditorView: View {
    let title: String
    @State var draft: ParsedCourse
    let onSave: (ParsedCourse) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var weeksText: String

    init(title: String, draft: ParsedCourse, onSave: @escaping (ParsedCourse) -> Void) {
        self.title = title
        self._draft = State(initialValue: draft)
        self.onSave = onSave
        self._weeksText = State(initialValue: WeeksExpression.format(draft.weeks))
    }

    private var parsedWeeks: [Int]? {
        WeeksExpression.parse(weeksText)
    }

    private var canSave: Bool {
        !draft.name.trimmingCharacters(in: .whitespaces).isEmpty && parsedWeeks != nil
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("课程") {
                    TextField("课程名称(必填)", text: $draft.name)
                    TextField("任课老师", text: $draft.teacher)
                    TextField("上课地点", text: $draft.location)
                }

                Section("时间") {
                    Picker("周几", selection: $draft.weekday) {
                        ForEach(1...7, id: \.self) { weekday in
                            Text(CourseEntry.weekdayNames[weekday - 1]).tag(weekday)
                        }
                    }
                    Picker("开始节次", selection: $draft.startSection) {
                        ForEach(1...SectionTimetable.sectionCount, id: \.self) { section in
                            Text(sectionLabel(section)).tag(section)
                        }
                    }
                    Picker("结束节次", selection: $draft.endSection) {
                        ForEach(draft.startSection...SectionTimetable.sectionCount, id: \.self) { section in
                            Text(sectionLabel(section)).tag(section)
                        }
                    }
                }

                Section {
                    TextField("如 1-16 或 1-8,10-16 或 2-16双周", text: $weeksText)
                        .autocorrectionDisabled()
                    HStack(spacing: 8) {
                        weeksShortcut("1-16周", "1-16")
                        weeksShortcut("单周", "1-16单周")
                        weeksShortcut("双周", "2-16双周")
                    }
                } header: {
                    Text("上课周次")
                } footer: {
                    if let weeks = parsedWeeks {
                        Text("将在 \(WeeksExpression.format(weeks)) 上课并提醒。")
                    } else {
                        Text("无法识别周次,请检查格式,例如 1-16。")
                            .foregroundStyle(.orange)
                    }
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存", action: save)
                        .disabled(!canSave)
                }
            }
            .onChange(of: draft.startSection) { _, newStart in
                if draft.endSection < newStart {
                    draft.endSection = newStart
                }
            }
        }
    }

    private func sectionLabel(_ section: Int) -> String {
        let table = SectionTimetable.current()
        if let start = table.startMinute(ofSection: section) {
            return "第\(section)节(\(SectionTimetable.clockText(start)))"
        }
        return "第\(section)节"
    }

    private func weeksShortcut(_ label: String, _ expression: String) -> some View {
        Button(label) {
            weeksText = expression
        }
        .font(.caption.weight(.semibold))
        .buttonStyle(.bordered)
        .buttonBorderShape(.capsule)
        .controlSize(.small)
    }

    private func save() {
        guard let weeks = parsedWeeks else { return }
        var result = draft
        result.weeks = weeks
        result.name = result.name.trimmingCharacters(in: .whitespacesAndNewlines)
        result.teacher = result.teacher.trimmingCharacters(in: .whitespacesAndNewlines)
        result.location = result.location.trimmingCharacters(in: .whitespacesAndNewlines)
        onSave(result)
        dismiss()
    }
}
