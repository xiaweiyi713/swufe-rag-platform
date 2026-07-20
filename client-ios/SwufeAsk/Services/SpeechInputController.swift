import AVFoundation
import Speech

/// 本地语音输入（设备端 SFSpeechRecognizer，无云端回退）。
final class SpeechInputController: NSObject {
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var transcriptEmitted = false
    private var userStoppedRecognition = false

    func start(
        onTranscript: @escaping (String) -> Void,
        onError: @escaping (String) -> Void,
        onFinish: @escaping () -> Void
    ) {
        Task {
            let speechStatus = await requestSpeechAuthorization()
            guard speechStatus == .authorized else {
                await MainActor.run {
                    onError("语音识别权限未开启，请在系统设置中允许西财教务问答使用语音识别。")
                }
                return
            }
            let microphoneAllowed = await AVAudioApplication.requestRecordPermission()
            guard microphoneAllowed else {
                await MainActor.run {
                    onError("麦克风权限未开启，请在系统设置中允许西财教务问答使用麦克风。")
                }
                return
            }
            do {
                try await MainActor.run {
                    guard let recognizer = self.preferredRecognizer() else {
                        throw SpeechInputError.recognizerUnavailable
                    }
                    try self.startRecording(
                        recognizer: recognizer,
                        onTranscript: onTranscript,
                        onError: onError,
                        onFinish: onFinish
                    )
                }
            } catch {
                await MainActor.run {
                    onError("语音输入启动失败：\(error.localizedDescription)")
                }
            }
        }
    }

    @MainActor
    func stop() {
        userStoppedRecognition = true
        stopRecognition()
    }

    @MainActor
    private func startRecording(
        recognizer: SFSpeechRecognizer,
        onTranscript: @escaping (String) -> Void,
        onError: @escaping (String) -> Void,
        onFinish: @escaping () -> Void
    ) throws {
        stop()
        userStoppedRecognition = false
        transcriptEmitted = false
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.record, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        self.request = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak request] buffer, _ in
            request?.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let transcript = result?.bestTranscription.formattedString, !transcript.isEmpty {
                Task { @MainActor in
                    self.transcriptEmitted = true
                    onTranscript(transcript)
                }
            }
            if error != nil {
                Task { @MainActor in
                    guard !self.userStoppedRecognition else { return }
                    self.stopRecognition()
                    if self.transcriptEmitted {
                        onFinish()
                    } else {
                        onError("没有识别到语音内容，可以再试一次或改用文字输入。")
                    }
                }
                return
            }
            if result?.isFinal == true {
                Task { @MainActor in
                    self.stopRecognition()
                    onFinish()
                }
            }
        }
    }

    @MainActor
    private func stopRecognition() {
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func preferredRecognizer() -> SFSpeechRecognizer? {
        let identifiers = [
            "zh-Hans-CN",
            "zh_CN",
            "zh-Hans",
            Locale.current.identifier,
            "en-US",
            "en_US"
        ]
        var seen = Set<String>()
        for identifier in identifiers where seen.insert(identifier).inserted {
            if let recognizer = SFSpeechRecognizer(locale: Locale(identifier: identifier)) {
                return recognizer
            }
        }
        return nil
    }

    private func requestSpeechAuthorization() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
    }

    private enum SpeechInputError: LocalizedError {
        case recognizerUnavailable

        var errorDescription: String? {
            switch self {
            case .recognizerUnavailable:
                "当前设备没有可用的语音识别器。"
            }
        }
    }
}
