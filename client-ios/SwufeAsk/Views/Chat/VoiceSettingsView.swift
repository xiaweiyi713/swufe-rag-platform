import AVFoundation
import SwiftUI

/// 语音设置：朗读语速、音色与“语音连续对话”开关。
struct VoiceSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var rate: Double
    @Binding var voiceId: String
    @Binding var loopEnabled: Bool
    let previewVoice: () -> Void

    private let voices = SpeechOutputController.availableChineseVoices()

    var body: some View {
        NavigationStack {
            Form {
                Section("朗读语速") {
                    Slider(value: $rate, in: 0.5...1.5, step: 0.02) {
                        Text("语速")
                    } minimumValueLabel: {
                        Image(systemName: "tortoise")
                    } maximumValueLabel: {
                        Image(systemName: "hare")
                    }
                    Text("当前：\(rate, format: .number.precision(.fractionLength(2)))×")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("朗读音色") {
                    Picker("音色", selection: $voiceId) {
                        Text("系统默认（中文）").tag("")
                        ForEach(voices, id: \.identifier) { voice in
                            Text(voiceLabel(voice)).tag(voice.identifier)
                        }
                    }
                    if voices.isEmpty {
                        Text("未检测到额外中文语音，可在「设置 › 辅助功能 › 朗读内容 › 声音」中下载更多音色。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    Toggle("语音连续对话", isOn: $loopEnabled)
                    Text("开启后，说完会自动发送并朗读回复，全程免手动操作。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section {
                    Button(action: previewVoice) {
                        Label("试听当前语速与音色", systemImage: "play.circle.fill")
                    }
                }
            }
            .navigationTitle("语音设置")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
    }

    private func voiceLabel(_ voice: AVSpeechSynthesisVoice) -> String {
        let quality = switch voice.quality {
        case .premium: "（高级）"
        case .enhanced: "（增强）"
        default: ""
        }
        return "\(voice.name)\(quality)"
    }
}
