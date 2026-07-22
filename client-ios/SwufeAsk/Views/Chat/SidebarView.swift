import SwiftData
import SwiftUI

/// Slide-in navigation drawer: quick entries, persisted chat history, and a
/// pinned account/settings footer. New chats archive the current one.
struct SidebarView: View {
    @Bindable var model: ChatViewModel
    @Binding var isOpen: Bool
    let topInset: CGFloat
    var openScope: () -> Void
    var openSchedule: () -> Void
    var openGrades: () -> Void
    var openSettings: () -> Void

    @Environment(\.modelContext) private var context
    @Query(sort: \StoredConversation.createdAt, order: .reverse) private var conversations: [StoredConversation]
    @State private var pendingDeletion: StoredConversation?
    @State private var showsLoginPlaceholder = false
    @State private var isSearchingHistory = false
    @State private var historySearchText = ""

    private var filteredConversations: [StoredConversation] {
        let query = historySearchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return conversations }
        return conversations.filter { conversation in
            conversation.title.localizedStandardContains(query)
                || conversation.preview.localizedStandardContains(query)
                || conversation.messages.contains { message in
                    message.text.localizedStandardContains(query)
                }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.Spacing.md) {
            header

            VStack(spacing: 2) {
                SidebarRow(title: "提问范围", subtitle: model.scopeSummary, systemImage: "person.crop.rectangle", action: openScope)
                SidebarRow(title: "我的课表", subtitle: "导入课表 · 上课提醒", systemImage: "calendar", action: openSchedule)
                SidebarRow(title: "我的成绩", subtitle: "学校 WebVPN", systemImage: "graduationcap", action: openGrades)
            }

            Divider().overlay(Theme.Color.cardStroke)

            if isSearchingHistory {
                historySearchField
            }

            Text("历史对话")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            history

            Spacer(minLength: 0)

            footer
        }
        .padding(.horizontal, Theme.Spacing.md)
        .padding(.top, topInset + Theme.Spacing.md)
        .padding(.bottom, Theme.Spacing.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .alert("删除历史对话？", isPresented: deleteAlertBinding) {
            Button("删除", role: .destructive) {
                if let pendingDeletion {
                    deleteConversation(pendingDeletion)
                }
                pendingDeletion = nil
            }
            Button("取消", role: .cancel) {
                pendingDeletion = nil
            }
        } message: {
            Text("删除后无法恢复。")
        }
        .alert("账号功能即将开放", isPresented: $showsLoginPlaceholder) {
            Button("知道了", role: .cancel) { }
        } message: {
            Text("登录后将可以同步对话记录和个人偏好,当前版本先以本机存储为主。")
        }
    }

    private var header: some View {
        HStack {
            HStack(spacing: 8) {
                SwufeLogoMark(size: 27)
                Text("西财教务问答")
                    .font(.headline)
            }
            Spacer()
            HStack(spacing: 8) {
                Button(action: startNewChat) {
                    Image(systemName: "square.and.pencil")
                        .font(.headline)
                        .foregroundStyle(.primary)
                        .frame(width: 40, height: 40)
                        .background(.ultraThinMaterial, in: .circle)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("新对话")

                Button {
                    withAnimation(Theme.Motion.spring) {
                        isSearchingHistory.toggle()
                        if !isSearchingHistory {
                            historySearchText = ""
                        }
                    }
                } label: {
                    Image(systemName: "magnifyingglass")
                        .font(.headline)
                        .foregroundStyle(.primary)
                        .frame(width: 40, height: 40)
                        .background(.ultraThinMaterial, in: .circle)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(isSearchingHistory ? "关闭搜索对话" : "搜索对话")
            }
        }
    }

    private var footer: some View {
        HStack(spacing: Theme.Spacing.sm) {
            Button {
                showsLoginPlaceholder = true
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "person.crop.circle.fill")
                        .font(.system(size: 31, weight: .medium))
                        .foregroundStyle(Theme.Color.accent)

                    VStack(alignment: .leading, spacing: 2) {
                        Text("登录账号")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.primary)
                        Text("登录后同步对话")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("登录账号")
            .accessibilityHint("账号功能即将开放")

            Button(action: openSettings) {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.primary)
                    .frame(width: 44, height: 44)
                    .background(.ultraThinMaterial, in: .circle)
                    .overlay(Circle().stroke(Theme.Color.cardStroke, lineWidth: 0.8))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("设置")
        }
        .padding(.horizontal, Theme.Spacing.sm)
        .padding(.vertical, Theme.Spacing.xs)
        .background(.ultraThinMaterial, in: .rect(cornerRadius: Theme.Radius.md))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous)
                .stroke(Theme.Color.cardStroke, lineWidth: 0.8)
        )
    }

    @ViewBuilder private var history: some View {
        if conversations.isEmpty {
            Text("还没有历史对话。点右上角“新对话”会把当前会话归档到这里。")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } else if filteredConversations.isEmpty {
            Text("没有找到匹配的历史对话。")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            ScrollView {
                LazyVStack(spacing: 4) {
                    ForEach(filteredConversations) { conversation in
                        HistoryRow(
                            conversation: conversation,
                            open: {
                                restore(conversation)
                            },
                            delete: {
                                pendingDeletion = conversation
                            }
                        )
                        .contextMenu {
                            Button("删除", systemImage: "trash", role: .destructive) {
                                pendingDeletion = conversation
                            }
                        }
                    }
                }
            }
            .scrollIndicators(.hidden)
        }
    }

    private var historySearchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundStyle(.secondary)
            TextField("搜索历史对话…", text: $historySearchText)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
            if !historySearchText.isEmpty {
                Button {
                    historySearchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("清除搜索")
            }
        }
        .padding(.horizontal, Theme.Spacing.sm)
        .frame(minHeight: 42)
        .background(.ultraThinMaterial, in: .rect(cornerRadius: Theme.Radius.sm))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.sm, style: .continuous)
                .stroke(Theme.Color.cardStroke, lineWidth: 0.8)
        )
    }

    private func startNewChat() {
        archiveCurrentConversation()
        model.startNewConversation()
        withAnimation(Theme.Motion.spring) { isOpen = false }
    }

    private func restore(_ conversation: StoredConversation) {
        if model.activeConversationID != conversation.id {
            archiveCurrentConversation()
        }
        model.restoreConversation(conversation)
        withAnimation(Theme.Motion.spring) { isOpen = false }
    }

    private func archiveCurrentConversation() {
        guard model.hasUserMessages else { return }

        if let activeID = model.activeConversationID,
           let existing = conversations.first(where: { $0.id == activeID }) {
            existing.update(
                title: model.conversationTitle,
                messages: model.archivedMessages,
                sessionID: model.sessionID,
                college: model.college,
                cohort: model.cohort,
                major: model.major
            )
        } else {
            context.insert(
                StoredConversation(
                    title: model.conversationTitle,
                    messages: model.archivedMessages,
                    sessionID: model.sessionID,
                    college: model.college,
                    cohort: model.cohort,
                    major: model.major
                )
            )
        }
        try? context.save()
    }

    private var deleteAlertBinding: Binding<Bool> {
        Binding(
            get: { pendingDeletion != nil },
            set: { isPresented in
                if !isPresented {
                    pendingDeletion = nil
                }
            }
        )
    }

    private func deleteConversation(_ conversation: StoredConversation) {
        context.delete(conversation)
        try? context.save()
    }
}

struct SidebarSettingsView: View {
    @Bindable var model: ChatViewModel
    var openLLMSettings: () -> Void
    var openAbout: () -> Void

    @Environment(\.dismiss) private var dismiss
    @AppStorage(AppearanceMode.storageKey) private var appearanceRaw = AppearanceMode.dark.rawValue

    private var appearance: AppearanceMode {
        AppearanceMode(rawValue: appearanceRaw) ?? .system
    }

    var body: some View {
        NavigationStack {
            List {
                Section("外观") {
                    Picker("外观", selection: $appearanceRaw) {
                        ForEach(AppearanceMode.allCases) { mode in
                            Text(mode.label).tag(mode.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 16))
                }

                Section("应用设置") {
                    Button(action: openLLMSettings) {
                        Label("对话模型", systemImage: "cpu")
                    }
                    Button(action: openAbout) {
                        Label("关于与数据说明", systemImage: "info.circle")
                    }
                }

                Section("知识库状态") {
                    if let options = model.options {
                        LabeledContent("知识块数量", value: "\(options.chunkCount)")
                        if !options.mode.isEmpty {
                            LabeledContent("运行模式", value: options.mode)
                        }
                    } else if model.isOptionsLoading {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("正在读取后端状态…")
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(model.optionsError ?? "暂时无法读取知识库状态。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            Button("重新加载", systemImage: "arrow.clockwise") {
                                model.reloadOptions()
                            }
                        }
                    }
                }
            }
            .navigationTitle("设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .onAppear {
                model.loadOptionsIfNeeded()
            }
        }
        .preferredColorScheme(appearance.colorScheme)
    }
}

private struct SidebarRow: View {
    let title: String
    var subtitle: String?
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: systemImage)
                    .frame(width: 24)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.body)
                        .foregroundStyle(.primary)
                    if let subtitle {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }
}

private struct HistoryRow: View {
    let conversation: StoredConversation
    let open: () -> Void
    let delete: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: 2) {
                Text(conversation.title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Text(conversation.preview)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .padding(.trailing, 48)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint("打开并继续对话")
        .padding(.horizontal, Theme.Spacing.sm)
        .padding(.vertical, Theme.Spacing.xs)
        .background(.ultraThinMaterial, in: .rect(cornerRadius: Theme.Radius.sm))
        .contentShape(.rect)
        .overlay(alignment: .trailing) {
            Button(action: delete) {
                Image(systemName: "trash")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .frame(width: 34, height: 34)
                    .background(.ultraThinMaterial, in: .circle)
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("删除历史对话 \(conversation.title)")
            .padding(.trailing, Theme.Spacing.xxs)
        }
    }
}
