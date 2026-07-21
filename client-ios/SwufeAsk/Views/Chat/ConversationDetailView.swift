import SwiftUI

/// Read-only view of an archived conversation, opened from the sidebar history.
struct ConversationDetailView: View {
    let conversation: StoredConversation
    var delete: (() -> Void)?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                LiquidBackdrop()

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Theme.Spacing.md) {
                        ForEach(conversation.messages) { message in
                            ArchivedBubble(message: message)
                        }
                    }
                    .padding(.horizontal, Theme.Spacing.md)
                    .padding(.vertical, Theme.Spacing.lg)
                }
                .scrollContentBackground(.hidden)
                .scrollIndicators(.hidden)
                .background(.clear)
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                historyHeader
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .presentationBackground {
            LiquidBackdrop()
        }
    }

    private var historyHeader: some View {
        ZStack {
            Text(conversation.title)
                .font(.headline)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
                .padding(.horizontal, 76)
                .accessibilityAddTraits(.isHeader)

            HStack {
                if let delete {
                    Button(role: .destructive) {
                        delete()
                    } label: {
                        Image(systemName: "trash")
                            .font(.system(size: 17, weight: .semibold))
                            .frame(width: 42, height: 42)
                            .background(.ultraThinMaterial, in: .circle)
                            .overlay {
                                Circle()
                                    .strokeBorder(Theme.Color.cardStroke, lineWidth: 1)
                            }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("删除历史对话")
                }

                Spacer()

                Button("完成") { dismiss() }
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 14)
                    .frame(height: 42)
                    .background(.ultraThinMaterial, in: .capsule)
                    .overlay {
                        Capsule()
                            .strokeBorder(Theme.Color.cardStroke, lineWidth: 1)
                    }
            }
        }
        .frame(height: 54)
        .padding(.horizontal, Theme.Spacing.md)
    }
}

private struct ArchivedBubble: View {
    let message: ArchivedMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack {
            if isUser {
                Spacer(minLength: 48)
                Text(message.text)
                    .font(.callout)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 15)
                    .padding(.vertical, 11)
                    .actionBlueGlassBubble(radius: 20)
            } else {
                AnswerMarkdownView(source: message.text)
                    .foregroundStyle(.primary)
                    .padding(13)
                    .liquidGlass(radius: Theme.Radius.md, elevated: false)
                Spacer(minLength: 48)
            }
        }
    }
}
