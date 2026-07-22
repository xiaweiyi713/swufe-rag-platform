import SwiftUI

/// 客户端本地构造的错误/引导卡片（网络失败、后端 503 等），
/// 带一组可点击的恢复建议按钮。
struct NoticeCard: View {
    let notice: FallbackNotice
    let sendPrompt: (String) -> Void

    // Monochrome brand: severity is conveyed by the icon glyph + copy, not hue.
    private var tint: Color { Theme.Color.accent }

    private var icon: String {
        switch notice.code {
        case "network_failed":
            "wifi.exclamationmark"
        case "service_unavailable":
            "server.rack"
        case "request_failed":
            "exclamationmark.bubble"
        case "model_content_restricted":
            "hand.raised.fill"
        case "model_request_rejected", "model_configuration_failed":
            "exclamationmark.bubble"
        case "model_rate_limited", "model_timeout":
            "clock.badge.exclamationmark"
        default:
            "arrow.triangle.2.circlepath"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(Theme.Color.onAccent)
                .frame(width: 30, height: 30)
                .background(Theme.Color.accent, in: .circle)

            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(notice.title)
                        .font(.subheadline.weight(.semibold))
                    Text(notice.message)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if !notice.actions.isEmpty {
                    FlowActionButtons(actions: notice.actions, tint: tint, sendPrompt: sendPrompt)
                }
            }
            .padding(13)
            .liquidGlass(radius: Theme.Radius.md, elevated: false)

            Spacer(minLength: 18)
        }
    }
}

/// 恢复建议按钮组：一行放不下时退化为纵向排列。
struct FlowActionButtons: View {
    let actions: [RecoveryAction]
    let tint: Color
    let sendPrompt: (String) -> Void

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) {
                buttons
            }
            VStack(alignment: .leading, spacing: 8) {
                buttons
            }
        }
    }

    @ViewBuilder
    private var buttons: some View {
        ForEach(actions.prefix(3)) { action in
            Button {
                sendPrompt(action.prompt)
            } label: {
                Text(action.label)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 7)
                    .background(tint.opacity(0.12), in: Capsule())
                    .overlay(Capsule().stroke(tint.opacity(0.25), lineWidth: 0.8))
                    .frame(minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
        }
    }
}
