import PDFKit
import UIKit
import Vision

/// OCR 识别出的一段文字及其在图中的位置(左上原点、0~1 归一化)。
struct RecognizedTextBlock {
    let text: String
    let midX: Double
    let midY: Double
    let height: Double
}

/// 课表图片/PDF 的文字识别。PDF 逐页渲染成位图后统一走 Vision OCR,
/// 这样两种来源共用同一条解析管线。
/// 这些是非隔离 async 函数,在后台执行器运行,不阻塞主线程。
enum ScheduleTextRecognizer {
    enum RecognizerError: LocalizedError {
        case unreadableImage
        case unreadablePDF

        var errorDescription: String? {
            switch self {
            case .unreadableImage: "无法读取所选图片。"
            case .unreadablePDF: "无法读取所选 PDF 文件。"
            }
        }
    }

    /// 相册图片 → 文本块。
    static func recognize(imageData: Data) async throws -> [RecognizedTextBlock] {
        guard let cgImage = UIImage(data: imageData)?.cgImage else {
            throw RecognizerError.unreadableImage
        }
        return try recognize(cgImage: cgImage)
    }

    /// PDF → 每页一组文本块(页与页分开解析,避免跨页串列)。
    static func recognize(pdfURL: URL) async throws -> [[RecognizedTextBlock]] {
        let secured = pdfURL.startAccessingSecurityScopedResource()
        defer {
            if secured { pdfURL.stopAccessingSecurityScopedResource() }
        }
        guard let document = PDFDocument(url: pdfURL), document.pageCount > 0 else {
            throw RecognizerError.unreadablePDF
        }

        var pages: [[RecognizedTextBlock]] = []
        for index in 0..<document.pageCount {
            guard let page = document.page(at: index) else { continue }
            let bounds = page.bounds(for: .mediaBox)
            // 长边渲染到 ~2200px,兼顾小字识别率与内存。
            let scale = 2200 / max(bounds.width, bounds.height)
            let size = CGSize(width: bounds.width * scale, height: bounds.height * scale)
            let image = page.thumbnail(of: size, for: .mediaBox)
            guard let cgImage = image.cgImage else { continue }
            pages.append(try recognize(cgImage: cgImage))
        }
        return pages
    }

    private static func recognize(cgImage: CGImage) throws -> [RecognizedTextBlock] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true

        try VNImageRequestHandler(cgImage: cgImage).perform([request])

        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            let box = observation.boundingBox
            // Vision 的归一化坐标以左下为原点,翻转成左上原点方便按行排序。
            return RecognizedTextBlock(
                text: text,
                midX: box.midX,
                midY: 1 - box.midY,
                height: box.height
            )
        }
    }
}
