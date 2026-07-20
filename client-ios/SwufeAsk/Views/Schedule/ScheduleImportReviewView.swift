import SwiftUI

/// 导入确认页:展示 OCR 解析出的课程草稿,支持逐条修改、删除,
/// 确认后才写入课表。节次没识别出来的条目会高亮提醒补填。
struct ScheduleImportReviewView: View {
    @State var drafts: [ParsedCourse]
    let onConfirm: ([ParsedCourse]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var editingDraft: ParsedCourse?

    private var hasUncertain: Bool {
        drafts.contains(where: \.sectionsUncertain)
    }

    var body: some View {
        NavigationStack {
            List {
                if hasUncertain {
                    Section {
                        Label("带感叹号的课程没识别出节次,请点击补填。", systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    ForEach(drafts) { draft in
                        Button {
                            editingDraft = draft
                        } label: {
                            DraftRow(draft: draft)
                        }
                        .buttonStyle(.plain)
                    }
                    .onDelete { offsets in
                        drafts.remove(atOffsets: offsets)
                    }
                } header: {
                    Text("识别出 \(drafts.count) 门课")
                } footer: {
                    Text("左滑可删除识别错误的条目;点击任意条目可修改课程信息。确认无误后保存,提醒会自动按周次排好。")
                }
            }
            .navigationTitle("确认课表")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存 \(drafts.count) 门课", action: confirm)
                        .disabled(drafts.isEmpty)
                }
            }
            .sheet(item: $editingDraft) { draft in
                CourseEditorView(title: "修改课程", draft: draft) { updated in
                    if let index = drafts.firstIndex(where: { $0.id == draft.id }) {
                        drafts[index] = updated
                        drafts[index].sectionsUncertain = false
                    }
                }
            }
        }
    }

    private func confirm() {
        onConfirm(drafts)
        dismiss()
    }
}

private struct DraftRow: View {
    let draft: ParsedCourse

    var body: some View {
        HStack(spacing: 10) {
            if draft.sectionsUncertain {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(draft.name)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                Text(summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
        }
        .contentShape(.rect)
        .frame(minHeight: 44)
    }

    private var summary: String {
        var parts = [
            "\(CourseEntry.weekdayNames[max(1, min(draft.weekday, 7)) - 1]) " +
            (draft.startSection == draft.endSection
             ? "第\(draft.startSection)节"
             : "\(draft.startSection)-\(draft.endSection)节")
        ]
        if !draft.teacher.isEmpty { parts.append(draft.teacher) }
        if !draft.location.isEmpty { parts.append(draft.location) }
        parts.append(WeeksExpression.format(draft.weeks))
        return parts.joined(separator: " · ")
    }
}
