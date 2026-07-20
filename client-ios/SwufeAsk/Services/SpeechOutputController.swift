import AVFoundation

/// 回答朗读（AVSpeechSynthesizer）。语速与音色可在语音设置里调整。
final class SpeechOutputController: NSObject {
    private let synthesizer = AVSpeechSynthesizer()

    /// Playback rate as a multiplier of the system default (0.5–1.5).
    var rateMultiplier: Float = 0.92
    /// Specific voice identifier; falls back to the default zh-CN voice when nil.
    var voiceIdentifier: String?

    func speak(_ text: String) {
        let compact = text
            .replacing("\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compact.isEmpty else { return }
        // 语音输入可能把音频会话留在 .record，先切回可播放类别，
        // 否则“语音连续对话”里的朗读可能无声或音量极小。
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, options: .duckOthers)
        try? session.setActive(true, options: .notifyOthersOnDeactivation)
        synthesizer.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: compact)
        if let voiceIdentifier, let voice = AVSpeechSynthesisVoice(identifier: voiceIdentifier) {
            utterance.voice = voice
        } else {
            utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        }
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * min(max(rateMultiplier, 0.5), 1.5)
        synthesizer.speak(utterance)
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    /// Chinese voices available on the current device, for the settings picker.
    static func availableChineseVoices() -> [AVSpeechSynthesisVoice] {
        AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language.hasPrefix("zh") }
            .sorted { $0.name < $1.name }
    }
}
