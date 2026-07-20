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
    /// 非 nil 时弹出确认编辑页。
    var batch: ImportBatch?

    private static let emptyResultMessage = """
    没有识别出课程。请确认所选课表包含“周一~周日”表头且文字清晰,\
    或者改用“手动添加”逐门录入。
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
                publish(ScheduleParser.parse(blocks: blocks))
            } catch {
                errorMessage = "图片识别失败:\(error.localizedDescription)"
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
                publish(pages.flatMap { ScheduleParser.parse(blocks: $0) })
            } catch {
                errorMessage = "PDF 识别失败:\(error.localizedDescription)"
            }
        }
    }

    private func publish(_ courses: [ParsedCourse]) {
        if courses.isEmpty {
            errorMessage = Self.emptyResultMessage
        } else {
            batch = ImportBatch(courses: courses)
        }
    }
}
