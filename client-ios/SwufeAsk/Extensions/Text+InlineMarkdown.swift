import SwiftUI

extension Text {
    /// answer_md 是 Markdown；用系统的 inline 解析保留换行并渲染粗体等
    /// 行内语法，解析失败时退回纯文本。实时聊天与历史回放共用。
    init(inlineMarkdown source: String) {
        if let attributed = try? AttributedString(
            markdown: source,
            options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            self.init(attributed)
        } else {
            self.init(source)
        }
    }
}
