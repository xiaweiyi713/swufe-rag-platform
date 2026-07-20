import SwiftUI

/// 语音输入进行中的悬浮横幅：实时转写预览 + 停止按钮。
struct ListeningBanner: View {
    let partialText: String
    let isVoiceLoop: Bool
    let stop: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "waveform")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.Color.accent)
                .symbolEffect(.variableColor.iterative, options: .repeating)
            VStack(alignment: .leading, spacing: 2) {
                Text(isVoiceLoop ? "正在聆听 · 语音连续对话" : "正在聆听…")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(partialText.isEmpty ? "请开始说话，我会实时转写" : partialText)
                    .font(.callout)
                    .foregroundStyle(partialText.isEmpty ? .secondary : .primary)
                    .lineLimit(2)
                    .animation(.snappy, value: partialText)
            }
            Spacer(minLength: 8)
            Button(action: stop) {
                Image(systemName: "stop.fill")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Theme.Color.onAccent)
                    .frame(width: 34, height: 34)
                    .background(Theme.Color.accent, in: .circle)
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("停止聆听")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .liquidGlass(radius: Theme.Radius.md, elevated: true)
    }
}
