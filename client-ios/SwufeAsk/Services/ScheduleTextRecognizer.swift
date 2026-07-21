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

struct ScheduleCourseHint {
    let name: String
    let startSection: Int
    let endSection: Int
    let weeks: [Int]
}

struct RecognizedSchedulePage {
    let blocks: [RecognizedTextBlock]
    let hints: [ScheduleCourseHint]
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
        guard let image = UIImage(data: imageData),
              let cgImage = normalizedImage(image, minimumLongEdge: 2800) else {
            throw RecognizerError.unreadableImage
        }
        return try recognize(cgImage: cgImage)
    }

    /// PDF → 每页一组文本块(页与页分开解析,避免跨页串列)。
    static func recognize(pdfURL: URL) async throws -> [RecognizedSchedulePage] {
        let secured = pdfURL.startAccessingSecurityScopedResource()
        defer {
            if secured { pdfURL.stopAccessingSecurityScopedResource() }
        }
        guard let document = PDFDocument(url: pdfURL), document.pageCount > 0 else {
            throw RecognizerError.unreadablePDF
        }

        var pages: [RecognizedSchedulePage] = []
        for index in 0..<document.pageCount {
            guard let page = document.page(at: index) else { continue }
            let bounds = page.bounds(for: .mediaBox)
            // 正方课表的单元格字很小。4000px 可避免把 17 周误读为 7 周，
            // 每页识别完成后即释放位图，不同时持有整份 PDF 的所有页面。
            let scale = 4000 / max(bounds.width, bounds.height)
            let size = CGSize(width: bounds.width * scale, height: bounds.height * scale)
            let image = page.thumbnail(of: size, for: .mediaBox)
            guard let cgImage = image.cgImage else { continue }
            pages.append(RecognizedSchedulePage(
                blocks: try recognize(cgImage: cgImage),
                hints: scheduleHints(from: page.string ?? "")
            ))
        }
        return pages
    }

    /// 上传视觉模型前移除课表顶部姓名/学号区，并限制体积。
    static func redactedImageDataForVision(_ imageData: Data) -> Data? {
        guard let image = UIImage(data: imageData),
              let normalized = normalizedImage(image, minimumLongEdge: 0) else {
            return nil
        }
        let top = Int((Double(normalized.height) * 0.10).rounded())
        let bottom = Int((Double(normalized.height) * 0.98).rounded())
        let cropRect = CGRect(
            x: 0,
            y: top,
            width: normalized.width,
            height: max(1, bottom - top)
        )
        guard let cropped = normalized.cropping(to: cropRect) else { return nil }
        let output = normalizedImage(
            UIImage(cgImage: cropped),
            maximumLongEdge: 2400
        ) ?? cropped
        return UIImage(cgImage: output).jpegData(compressionQuality: 0.88)
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

    private static func normalizedImage(
        _ image: UIImage,
        minimumLongEdge: CGFloat = 0,
        maximumLongEdge: CGFloat? = nil
    ) -> CGImage? {
        let orientedSize = image.size
        guard orientedSize.width > 0, orientedSize.height > 0 else { return nil }
        let sourceLongEdge = max(orientedSize.width, orientedSize.height)
        var targetLongEdge = sourceLongEdge
        if minimumLongEdge > 0, sourceLongEdge < minimumLongEdge {
            targetLongEdge = minimumLongEdge
        }
        if let maximumLongEdge, targetLongEdge > maximumLongEdge {
            targetLongEdge = maximumLongEdge
        }
        let scale = targetLongEdge / sourceLongEdge
        let targetSize = CGSize(
            width: max(1, (orientedSize.width * scale).rounded()),
            height: max(1, (orientedSize.height * scale).rounded())
        )
        let renderer = UIGraphicsImageRenderer(size: targetSize)
        return renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: targetSize))
        }.cgImage
    }

    private static func scheduleHints(from text: String) -> [ScheduleCourseHint] {
        guard !text.isEmpty else { return [] }
        let lines = text.components(separatedBy: .newlines)
        var names: [String] = []
        for index in lines.indices {
            let line = lines[index].trimmingCharacters(in: .whitespacesAndNewlines)
            guard let marker = line.firstIndex(of: "▲") else { continue }
            var name = String(line[..<marker]).replacingOccurrences(
                of: #"^\d{1,2}\s*"#,
                with: "",
                options: .regularExpression
            )
            if name.count <= 2,
               let prefix = courseNamePrefix(before: index, in: lines) {
                name = prefix + name
            }
            name = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if !name.isEmpty { names.append(name) }
        }

        let expression = try? NSRegularExpression(
            pattern: #"\((\d{1,2})-(\d{1,2})节\)([0-9,，、\-~～]+周(?:[,，、][0-9\-~～]+周)*)"#
        )
        let nsText = text as NSString
        let matches = expression?.matches(
            in: text,
            range: NSRange(location: 0, length: nsText.length)
        ) ?? []
        let schedules = matches.compactMap { match -> (Int, Int, [Int])? in
            guard let start = Int(nsText.substring(with: match.range(at: 1))),
                  let end = Int(nsText.substring(with: match.range(at: 2))),
                  start >= 1, end <= 12, start <= end,
                  let weeks = WeeksExpression.parse(
                    nsText.substring(with: match.range(at: 3))
                  ) else {
                return nil
            }
            return (start, end, weeks)
        }
        guard names.count == schedules.count else { return [] }
        return zip(names, schedules).map { name, schedule in
            ScheduleCourseHint(
                name: name,
                startSection: schedule.0,
                endSection: schedule.1,
                weeks: schedule.2
            )
        }
    }

    /// 横向 PDF 的文字层会按视觉行读取，长课程名的末尾可能被另外两列的
    /// 详情隔开。短尾巴向前寻找最近的纯课程名行，再恢复完整名称。
    private static func courseNamePrefix(
        before index: Int,
        in lines: [String]
    ) -> String? {
        guard index > 0 else { return nil }
        for offset in 1...min(index, 8) {
            let candidate = lines[index - offset]
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(
                    of: #"^\d{1,2}\s*"#,
                    with: "",
                    options: .regularExpression
                )
            if candidate.isEmpty
                || candidate.contains("▲")
                || candidate.contains("/")
                || candidate.contains("周")
                || candidate.contains("节")
                || candidate.contains("教师")
                || candidate.contains("场地")
                || candidate.contains("学分") {
                continue
            }
            let chineseCount = candidate.unicodeScalars.filter {
                (0x4E00...0x9FFF).contains(Int($0.value))
            }.count
            if chineseCount >= 3 { return candidate }
        }
        return nil
    }
}
