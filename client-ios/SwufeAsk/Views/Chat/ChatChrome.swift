import SwiftUI

struct WelcomeAction: Identifiable {
    var id: String { prompt }
    let title: String
    let subtitle: String
    let icon: String
    let prompt: String
}

struct WelcomeActionPanel: View {
    let actions: [WelcomeAction]
    let sendPrompt: (String) -> Void

    private let columns = [
        GridItem(.flexible(minimum: 116), spacing: 10),
        GridItem(.flexible(minimum: 116), spacing: 10)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                SwufeLogoMark(size: 32)
                VStack(alignment: .leading, spacing: 3) {
                    Text("西财教务问答")
                        .font(.headline)
                    Text("官方文件 · 条款溯源 · 证据不足即拒答")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }

            LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
                ForEach(actions) { action in
                    Button {
                        sendPrompt(action.prompt)
                    } label: {
                        HStack(spacing: 9) {
                            Image(systemName: action.icon)
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(Theme.Color.accent)
                                .frame(width: 28, height: 28)
                                .background(Theme.Color.accent.opacity(0.12), in: Circle())
                            VStack(alignment: .leading, spacing: 2) {
                                Text(action.title)
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(.primary)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.8)
                                Text(action.subtitle)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.75)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                        .background(Color(.tertiarySystemBackground), in: RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous)
                                .stroke(Theme.Color.cardStroke, lineWidth: 1)
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(14)
        .cardSurface(radius: Theme.Radius.lg)
    }
}

struct QuickPromptBar: View {
    let prompts: [String]
    let action: (String) -> Void

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(prompts, id: \.self) { prompt in
                    Button {
                        action(prompt)
                    } label: {
                        // 视觉仍是小胶囊，把样式放进 label 并撑到 44pt
                        // 最小命中高度，满足系统可点击区域要求。
                        Text(prompt)
                            .font(.footnote.weight(.medium))
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .frame(maxWidth: 220, alignment: .leading)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 7)
                            .cleanGlassCapsule(interactive: true)
                            .frame(minHeight: 44, alignment: .bottom)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 12)
        }
        .scrollIndicators(.hidden)
        .scrollClipDisabled()
    }
}

/// DeepSeek-style request mode pills shown directly above the composer.
struct ComposerModeBar: View {
    @Binding var deepThinking: Bool
    @Binding var webSearch: Bool
    let llmConfig: LLMConfigStore.Config?
    let modelOptions: [LLMModelOption]
    let isDisabled: Bool
    let selectModel: (String) -> Void

    @State private var modelSelectionFeedback = 0

    var body: some View {
        HStack(spacing: 8) {
            modelControl
            modeButton(
                title: "深度思考",
                icon: "atom",
                isOn: $deepThinking,
                accessibilityLabel: "深度思考模式"
            )
            modeButton(
                title: "智能搜索",
                icon: "globe",
                isOn: $webSearch,
                accessibilityLabel: "联网搜索模式"
            )
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 4)
        .frame(minHeight: 42)
        .opacity(isDisabled ? 0.6 : 1)
        .sensoryFeedback(.selection, trigger: modelSelectionFeedback)
    }

    @ViewBuilder
    private var modelControl: some View {
        if let config = llmConfig {
            Menu {
                Section(config.providerName) {
                    ForEach(modelOptions) { option in
                        Button {
                            modelSelectionFeedback &+= 1
                            selectModel(option.name)
                        } label: {
                            if option.name == config.model {
                                Label(option.name, systemImage: "checkmark")
                            } else {
                                Text(option.name)
                            }
                        }
                    }
                }
            } label: {
                modelPill(title: compactModelName(config.model), isConfigured: true)
            }
            .buttonStyle(.plain)
            .disabled(isDisabled)
            .accessibilityLabel("切换对话模型")
            .accessibilityValue("\(config.providerName)，\(config.model)")
        } else {
            Menu {
                Text("请先在设置中接入 API Key")
            } label: {
                modelPill(title: "选择模型", isConfigured: false)
            }
            .buttonStyle(.plain)
            .disabled(isDisabled)
            .accessibilityLabel("选择对话模型")
            .accessibilityHint("请先在设置中接入 API Key")
        }
    }

    private func modelPill(title: String, isConfigured: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "cpu")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(isConfigured ? Theme.Color.accent : .primary)
            Text(title)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .frame(maxWidth: 82)
            Image(systemName: "chevron.up.chevron.down")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.secondary)
        }
        .foregroundStyle(.primary)
        .padding(.horizontal, 11)
        .frame(minHeight: 38)
        .cleanGlassCapsule(interactive: true)
        .contentShape(Capsule())
    }

    private func compactModelName(_ rawName: String) -> String {
        var name = rawName.split(separator: "/").last.map(String.init) ?? rawName
        if name.lowercased().hasPrefix("deepseek-") {
            name = String(name.dropFirst("deepseek-".count))
        }
        let words = name.split(separator: "-").map { word -> String in
            let value = String(word)
            let lower = value.lowercased()
            if lower.hasPrefix("v") && lower.dropFirst().first?.isNumber == true {
                return lower.uppercased()
            }
            if ["gpt", "glm", "qwen", "kimi"].contains(lower) {
                return lower.uppercased()
            }
            return value.prefix(1).uppercased() + value.dropFirst()
        }
        let displayName = words.joined(separator: " ")
        guard displayName.count > 16 else { return displayName }
        return String(displayName.prefix(15)) + "…"
    }

    @ViewBuilder
    private func modeButton(
        title: String,
        icon: String,
        isOn: Binding<Bool>,
        accessibilityLabel: String
    ) -> some View {
        Button {
            guard !isDisabled else { return }
            isOn.wrappedValue.toggle()
        } label: {
            Label(title, systemImage: icon)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(isOn.wrappedValue ? Color.white : .primary)
                .padding(.horizontal, 13)
                .frame(minHeight: 38)
                .actionBlueGlassCapsule(isActive: isOn.wrappedValue)
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityValue(isOn.wrappedValue ? "已开启" : "已关闭")
    }
}

struct ComposerView: View {
    @Binding var text: String
    let isStreaming: Bool
    let isListening: Bool
    let toggleSpeech: () -> Void
    let send: () -> Void

    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isStreaming
    }

    var body: some View {
        HStack(spacing: 8) {
            TextField("输入你的教务问题...", text: $text, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.plain)
                .padding(.horizontal, 12)
                .padding(.vertical, 11)
                .cleanGlassRounded(radius: 18)
                .accessibilityLabel("教务问题输入框")
                .accessibilityIdentifier("chat.composer.text")

            Button(action: toggleSpeech) {
                glyph(isListening ? "mic.circle.fill" : "mic")
            }
            .buttonStyle(.borderless)
            .disabled(isStreaming)
            .accessibilityLabel(isListening ? "停止语音输入" : "开始语音输入")

            Button(action: send) {
                Image(systemName: sendIcon)
                    .font(.system(size: 17, weight: .heavy))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(sendForeground)
                    .frame(width: 42, height: 42)
                    .background(sendBackground, in: .circle)
                    .overlay(Circle().strokeBorder(sendStroke, lineWidth: 1))
                    .contentTransition(.symbolEffect(.replace))
                    .frame(width: 44, height: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("发送")
        }
        .padding(10)
        .cleanGlassRounded(radius: 28)
    }

    /// Monochrome glass icon button face used for the mic control.
    /// 图标视觉保持 38pt 圆面，外层撑到 44pt 最小命中区域。
    private func glyph(_ name: String) -> some View {
        Image(systemName: name)
            .font(.system(size: 16, weight: .semibold))
            .foregroundStyle(.primary)
            .frame(width: 38, height: 38)
            .cleanGlassCircle(interactive: true)
            .frame(width: 44, height: 44)
            .contentShape(.rect)
    }

    private var sendIcon: String {
        isStreaming ? "hourglass" : "arrow.up"
    }

    private var sendForeground: Color {
        canSend ? Theme.Color.onAccent : .secondary
    }

    private var sendBackground: some ShapeStyle {
        canSend ? AnyShapeStyle(Theme.Color.accent) : AnyShapeStyle(.ultraThinMaterial)
    }

    private var sendStroke: Color {
        canSend ? Theme.Color.glassHighlight : Theme.Color.cardStroke
    }
}
