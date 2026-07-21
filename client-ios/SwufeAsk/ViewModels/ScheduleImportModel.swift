import Foundation
import PhotosUI
import SwiftUI

/// 一次导入解析出的课程草稿,弹确认页用。
struct ImportBatch: Identifiable {
    let id = UUID()
    var courses: [ParsedCourse]
}

/// 课表导入流程状态:接收图片/PDF → OCR → 解析 → 产出待确认草稿。
@MainActor
@Observable
final class ScheduleImportModel {
    var isParsing = false
    var errorMessage: String?
    var needsVisionConfirmation = false
    /// 非 nil 时弹出确认编辑页。
    var batch: ImportBatch?
    private var pendingVisionImage: Data?

    private static let emptyResultMessage = """
    没有识别出课程。图片中的课表文字可能过小或不够清晰，\
    请改用原始 PDF、更高清截图，或者“手动添加”逐门录入。
    """

    func importImage(_ item: PhotosPickerItem) {
        guard !isParsing else { return }
        isParsing = true
        Task {
            defer { isParsing = false }
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else {
                    throw ScheduleTextRecognizer.RecognizerError.unreadableImage
                }
                let blocks = try await ScheduleTextRecognizer.recognize(imageData: data)
                let courses = ScheduleParser.parse(blocks: blocks)
                if courses.isEmpty,
                   LLMConfigStore.current() != nil,
                   let redacted = ScheduleTextRecognizer.redactedImageDataForVision(data) {
                    pendingVisionImage = redacted
                    needsVisionConfirmation = true
                } else {
                    publish(courses)
                }
            } catch {
                errorMessage = "图片识别失败：\(error.localizedDescription)"
            }
        }
    }

    func importPDF(at url: URL) {
        guard !isParsing else { return }
        isParsing = true
        Task {
            defer { isParsing = false }
            do {
                let pages = try await ScheduleTextRecognizer.recognize(pdfURL: url)
                publish(pages.flatMap { page in
                    ScheduleParser.reconcile(
                        ScheduleParser.parse(blocks: page.blocks),
                        with: page.hints
                    )
                })
            } catch {
                errorMessage = "PDF 识别失败：\(error.localizedDescription)"
            }
        }
    }

    func confirmVisionImport() {
        guard !isParsing, let image = pendingVisionImage else { return }
        needsVisionConfirmation = false
        pendingVisionImage = nil
        isParsing = true
        Task {
            defer { isParsing = false }
            do {
                publish(try await AskAPIService().parseScheduleImage(image))
            } catch {
                errorMessage = "模型增强识别失败：\(error.localizedDescription)"
            }
        }
    }

    func cancelVisionImport() {
        needsVisionConfirmation = false
        pendingVisionImage = nil
    }

    private func publish(_ courses: [ParsedCourse]) {
        if courses.isEmpty {
            errorMessage = Self.emptyResultMessage
        } else {
            batch = ImportBatch(courses: courses)
        }
    }
}
