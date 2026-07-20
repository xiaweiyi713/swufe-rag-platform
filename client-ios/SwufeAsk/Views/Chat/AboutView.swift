import SwiftUI

struct AboutView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage(APIClient.baseURLOverrideKey) private var apiBaseURLOverride = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    AboutRow(
                        icon: "building.columns",
                        title: "数据来源",
                        text: "知识库由教务处、研究生院与各学院官网的公开文件构建（培养方案、推免细则、学籍管理规定等），来源 URL 均属于 swufe.edu.cn 官方域名。"
                    )
                    AboutRow(
                        icon: "quote.opening",
                        title: "引用溯源",
                        text: "学校政策回答中的每个论断都带 [n] 角标；文档标题、条款和网址由可信存储按知识块 ID 绑定，点击角标可查看原文条款和官网出处。"
                    )
                    AboutRow(
                        icon: "person.crop.rectangle",
                        title: "范围过滤",
                        text: "选择学院和入学年级后，检索只在“校级或本学院”且“年级不限或本年级”的现行文件里进行，过滤发生在排序之前。"
                    )
                    AboutRow(
                        icon: "hand.raised",
                        title: "拒答机制",
                        text: "知识库没有足够证据时，系统会明确说明查不到，不会改用通用模型猜测学校事实，并给出已登记的官方入口供进一步核实。"
                    )
                }

                Section("回答边界") {
                    Label("校规、培养方案等学校事实只依据知识库作答。", systemImage: "checkmark.seal")
                    Label("普通聊天、通用知识走通用对话分支，不做检索。", systemImage: "bubble.left.and.bubble.right")
                    Label("回答不能替代教务处、学院教务办的正式答复。", systemImage: "exclamationmark.shield")
                }
                .font(.footnote)

                Section {
                    LabeledContent("后端地址") {
                        TextField(APIClient.defaultBaseURL.absoluteString, text: $apiBaseURLOverride)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .multilineTextAlignment(.trailing)
                            .font(.footnote.monospaced())
                    }
                    Label("会话 ID 在本机生成，仅用于连续追问的范围记忆。", systemImage: "lock.shield")
                    Label("语音识别在设备本地完成，录音不会上传。", systemImage: "mic")
                } header: {
                    Text("连接")
                } footer: {
                    Text("留空使用默认 \(APIClient.defaultBaseURL.absoluteString)（模拟器直连本机）。真机演示时填运行后端的电脑局域网地址，如 http://192.168.1.5:8000，修改立即生效。")
                }
                .font(.footnote)

                Section {
                    LabeledContent("当前模型", value: LLMConfigStore.summary)
                } header: {
                    Text("对话模型（BYOK）")
                } footer: {
                    Text("在侧栏「对话模型」里选择提供商并填入你的 API Key 后,回答走完整的“LLM 理解 + 证据 + LLM 表达 + 事实校验”链路;不配置则使用后端确定性降级模式。Key 保存在本机钥匙串,仅随每次提问通过请求头发送。")
                }
                .font(.footnote)
            }
            .navigationTitle("关于与数据说明")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") {
                        dismiss()
                    }
                }
            }
        }
    }
}

private struct AboutRow: View {
    let icon: String
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(Theme.Color.accent)
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}
