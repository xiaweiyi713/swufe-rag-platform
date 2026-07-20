import PhotosUI
import SwiftData
import SwiftUI

/// 我的课表:按周几分组展示已保存课程,支持截图/PDF 导入与手动添加。
/// 课表变动后自动重排「课前 10 分钟」本地提醒。
struct ScheduleView: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @Query(sort: [
        SortDescriptor(\CourseEntry.weekday),
        SortDescriptor(\CourseEntry.startSection)
    ]) private var courses: [CourseEntry]

    @State private var importModel = ScheduleImportModel()
    @State private var photoItem: PhotosPickerItem?
    @State private var showsPhotoPicker = false
    @State private var showsFileImporter = false
    @State private var showsSettings = false
    @State private var editingCourse: CourseEntry?
    @State private var showsManualAdd = false

    private var currentWeek: Int? { SemesterCalendar.teachingWeek() }
    private var todayWeekday: Int { SemesterCalendar.courseWeekday(of: .now) }

    var body: some View {
        NavigationStack {
            Group {
                if courses.isEmpty {
                    emptyState
                } else {
                    courseList
                }
            }
            .navigationTitle("我的课表")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("课表设置", systemImage: "gearshape", action: openSettings)
                        .labelStyle(.iconOnly)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    addMenu
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .overlay {
                if importModel.isParsing {
                    parsingOverlay
                }
            }
            .photosPicker(isPresented: $showsPhotoPicker, selection: $photoItem, matching: .images)
            .fileImporter(isPresented: $showsFileImporter, allowedContentTypes: [.pdf]) { result in
                if case .success(let url) = result {
                    importModel.importPDF(at: url)
                }
            }
            .onChange(of: photoItem) { _, item in
                guard let item else { return }
                photoItem = nil
                importModel.importImage(item)
            }
            .sheet(item: $importModel.batch) { batch in
                ScheduleImportReviewView(drafts: batch.courses, onConfirm: saveImported)
            }
            .sheet(item: $editingCourse) { course in
                CourseEditorView(
                    title: "编辑课程",
                    draft: ParsedCourse(
                        name: course.name,
                        teacher: course.teacher,
                        location: course.location,
                        weekday: course.weekday,
                        startSection: course.startSection,
                        endSection: course.endSection,
                        weeks: course.weeks
                    )
                ) { draft in
                    apply(draft, to: course)
                }
            }
            .sheet(isPresented: $showsManualAdd) {
                CourseEditorView(title: "添加课程", draft: ParsedCourse(
                    name: "",
                    teacher: "",
                    location: "",
                    weekday: todayWeekday,
                    startSection: 1,
                    endSection: 2,
                    weeks: Array(1...16)
                )) { draft in
                    insert(draft)
                }
            }
            .sheet(isPresented: $showsSettings) {
                ScheduleSettingsView()
            }
            .alert("导入失败", isPresented: importErrorBinding) {
            } message: {
                Text(importModel.errorMessage ?? "")
            }
        }
    }

    // MARK: - 列表

    private var courseList: some View {
        List {
            Section {
                LabeledContent("当前教学周", value: currentWeek.map { "第 \($0) 周" } ?? "学期未开始")
                LabeledContent(
                    "上课提醒",
                    value: CourseReminderScheduler.remindersEnabled ? "开课前 10 分钟" : "已关闭"
                )
            }
            ForEach(1...7, id: \.self) { weekday in
                let dayCourses = courses.filter { $0.weekday == weekday }
                if !dayCourses.isEmpty {
                    Section {
                        ForEach(dayCourses) { course in
                            Button {
                                editingCourse = course
                            } label: {
                                CourseRow(
                                    course: course,
                                    isToday: weekday == todayWeekday,
                                    isThisWeek: currentWeek.map { course.weeks.contains($0) } ?? true
                                )
                            }
                            .buttonStyle(.plain)
                        }
                        .onDelete { offsets in
                            delete(dayCourses, at: offsets)
                        }
                    } header: {
                        Text(weekday == todayWeekday
                             ? "\(CourseEntry.weekdayNames[weekday - 1]) · 今天"
                             : CourseEntry.weekdayNames[weekday - 1])
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("还没有课表", systemImage: "calendar.badge.plus")
        } description: {
            Text("导入教务系统的课表截图或 PDF,自动识别课程并在每节课开课前 10 分钟提醒你。识别结果可以逐条修改。")
        } actions: {
            Button {
                showsPhotoPicker = true
            } label: {
                Label("从相册导入截图", systemImage: "photo")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.Color.onAccent)
                    .frame(minWidth: 210)
                    .padding(.horizontal, Theme.Spacing.md)
                    .padding(.vertical, 11)
                    .background(Theme.Gradient.brand, in: .capsule)
                    .overlay {
                        Capsule()
                            .strokeBorder(Theme.Color.glassHighlight, lineWidth: 1)
                    }
            }
            .buttonStyle(.plain)
            Button("导入 PDF 课表", systemImage: "doc.text", action: openFileImporter)
            Button("手动添加课程", systemImage: "square.and.pencil", action: openManualAdd)
        }
    }

    private var addMenu: some View {
        Menu("添加课程", systemImage: "plus") {
            Button("从相册导入截图", systemImage: "photo") {
                showsPhotoPicker = true
            }
            Button("导入 PDF 课表", systemImage: "doc.text", action: openFileImporter)
            Divider()
            Button("手动添加课程", systemImage: "square.and.pencil", action: openManualAdd)
        }
        .labelStyle(.iconOnly)
    }

    private var parsingOverlay: some View {
        VStack(spacing: 10) {
            ProgressView()
            Text("正在识别课表…")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(24)
        .background(.regularMaterial, in: .rect(cornerRadius: Theme.Radius.md))
    }

    private var importErrorBinding: Binding<Bool> {
        Binding(
            get: { importModel.errorMessage != nil },
            set: { isPresented in
                if !isPresented {
                    importModel.errorMessage = nil
                }
            }
        )
    }

    // MARK: - 动作

    private func openSettings() { showsSettings = true }
    private func openFileImporter() { showsFileImporter = true }
    private func openManualAdd() { showsManualAdd = true }

    private func saveImported(_ drafts: [ParsedCourse]) {
        for draft in drafts {
            context.insert(makeEntry(from: draft))
        }
        persistAndReschedule()
    }

    private func insert(_ draft: ParsedCourse) {
        context.insert(makeEntry(from: draft))
        persistAndReschedule()
    }

    private func apply(_ draft: ParsedCourse, to course: CourseEntry) {
        course.name = draft.name
        course.teacher = draft.teacher
        course.location = draft.location
        course.weekday = draft.weekday
        course.startSection = draft.startSection
        course.endSection = draft.endSection
        course.weeks = draft.weeks
        persistAndReschedule()
    }

    private func delete(_ dayCourses: [CourseEntry], at offsets: IndexSet) {
        for index in offsets {
            context.delete(dayCourses[index])
        }
        persistAndReschedule()
    }

    private func makeEntry(from draft: ParsedCourse) -> CourseEntry {
        CourseEntry(
            name: draft.name,
            teacher: draft.teacher,
            location: draft.location,
            weekday: draft.weekday,
            startSection: draft.startSection,
            endSection: draft.endSection,
            weeks: draft.weeks
        )
    }

    private func persistAndReschedule() {
        try? context.save()
        CourseReminderScheduler.refresh(using: context)
    }
}

/// 课表列表行:节次时间 + 课程信息;非本教学周的课灰显。
private struct CourseRow: View {
    let course: CourseEntry
    let isToday: Bool
    let isThisWeek: Bool

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .center, spacing: 2) {
                Text(course.sectionRangeText)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(isToday ? Theme.Color.accent : .secondary)
                Text(course.timeRangeText)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(width: 74)

            VStack(alignment: .leading, spacing: 3) {
                Text(course.name)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                HStack(spacing: 6) {
                    if !course.teacher.isEmpty {
                        Label(course.teacher, systemImage: "person")
                    }
                    if !course.location.isEmpty {
                        Label(course.location, systemImage: "mappin.and.ellipse")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                Text(course.weeksSummary)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
        .opacity(isThisWeek ? 1 : 0.45)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(course.weekdayName)\(course.sectionRangeText),\(course.name),\(course.teacher),\(course.location)"
        )
    }
}
