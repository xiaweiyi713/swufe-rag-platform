import SwiftUI

/// 聊天流中的一行：用户气泡 / 助手回答（含打字机与附件）/ 恢复引导卡。
struct MessageRow: View {
    let message: ChatMessage
    let sendPrompt: (String) -> Void
    var isStreaming = false

    private var hasAttachments: Bool {
        message.mode != nil
            || !message.citations.isEmpty
            || !message.retrieved.isEmpty
            || !message.officialLinks.isEmpty
            || !message.webSources.isEmpty
    }

    var body: some View {
        switch message.role {
        case .user:
            HStack {
                Spacer(minLength: 48)
                Text(message.text)
                    .font(.callout)
                    .foregroundStyle(Theme.Color.onAccent)
                    .padding(.horizontal, 15)
                    .padding(.vertical, 11)
                    .background(Theme.Color.accent, in: .rect(cornerRadius: 20))
            }
        case .assistant:
            HStack(alignment: .top, spacing: 10) {
                SwufeLogoMark(size: 30)
                VStack(alignment: .leading, spacing: 8) {
                    if message.text.isEmpty {
                        AssistantThinkingBubble()
                    } else {
                        HStack(alignment: .bottom, spacing: 3) {
                            // 后端增量只包含可直接阅读的正文预览；这里仍走
                            // Markdown 渲染，避免通用模型输出标题或强调符号时
                            // 把语法字符直接暴露给用户。表格在结构确定后逐字
                            // 更新，来源与下载链接仍随 final 一次性落地。
                            if isStreaming {
                                AnswerMarkdownView(
                                    source: message.text,
                                    isStreaming: true
                                )
                                .transaction { transaction in
                                    transaction.animation = nil
                                }
                                StreamingCaret()
                            } else {
                                AnswerMarkdownView(source: message.text)
                                    .foregroundStyle(.primary)
                            }
                        }
                        .padding(13)
                        .liquidGlass(radius: Theme.Radius.md, elevated: false)
                        .transition(.opacity.combined(with: .scale(scale: 0.98, anchor: .leading)))
                    }
                    if !isStreaming, hasAttachments {
                        AnswerAttachments(message: message)
                    }
                }
                Spacer(minLength: 24)
            }
        case .notice:
            if let notice = message.notice {
                NoticeCard(notice: notice, sendPrompt: sendPrompt)
            }
        }
    }
}

/// 等待后端响应时的占位气泡。
struct AssistantThinkingBubble: View {
    @State private var phase = false

    var body: some View {
        Text("正在检索与作答…")
            .font(.callout.weight(.medium))
            .foregroundStyle(.secondary)
            .opacity(phase ? 1 : 0.55)
            .animation(.easeInOut(duration: 0.85).repeatForever(autoreverses: true), value: phase)
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .liquidGlass(radius: Theme.Radius.md, elevated: false)
            .onAppear {
                phase = true
            }
            .accessibilityLabel("正在检索与作答")
    }
}

/// Blinking text caret shown at the tail of a streaming assistant message.
/// Falls back to a steady bar when Reduce Motion is on.
struct StreamingCaret: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var visible = true

    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(Theme.Color.accent)
            .frame(width: 2.5, height: 16)
            .opacity(visible ? 1 : 0)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true)) {
                    visible = false
                }
            }
            .accessibilityHidden(true)
    }
}
