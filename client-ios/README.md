# 西财教务问答 iOS 客户端（SwufeAsk）

原生 SwiftUI 客户端，对接 swufe-rag 正式 HTTP 接口。当前接口以
[`../backend/API_REFERENCE.md`](../backend/API_REFERENCE.md) 为准；V16 交接记录见
[`../backend/handoff/FRONTEND_API_V16.md`](../backend/handoff/FRONTEND_API_V16.md)。

V16 对接要点：`/options` 的 `majors_by_cohort` 与
`major_colleges_by_cohort` 驱动「提问范围」的学院、年级、专业双向联动；
`/ask` 请求带 `major`，钥匙串配置过 LLM Key 时自动加
`X-LLM-API-Key` 头（BYOK，「关于 › LLM Key」处配置）；响应解析
`execution_path`（课程库/文档检索标签）与 `validation.passed`；回答用
AnswerMarkdownView 渲染 V16 的 Markdown 标题/表格/链接。
前端结构复用自“字节AI全栈挑战赛”电商导购客户端：保留 Liquid Glass 设计系统、
侧边栏抽屉 + SwiftData 历史会话、本地语音输入与朗读，把领域层替换为教务问答。

## 生成与运行

```bash
cd client-ios
xcodegen generate
open SwufeAsk.xcodeproj
```

默认后端 `http://127.0.0.1:8000`（模拟器可直接访问本机服务）。要指向其他后端：
在 App 内「关于与数据说明 › 连接 › 后端地址」直接填写（写入 `UserDefaults` 的
`swufeask.apiBaseURL`，立即生效），或改 `project.yml` 里的 `SWUFE_ASK_API_BASE_URL`
后重新 `xcodegen generate`。真机演示填运行后端的电脑局域网地址（如
`http://192.168.1.5:8000`）；Info.plist 已开 `NSAllowsLocalNetworking` 并带本地网络
权限说明，首次请求时同意「本地网络」授权即可。

后端可在 monorepo 的 `backend/` 目录通过 Docker Compose 启动：

```bash
cd ../backend
cp deploy/.env.example .env
docker compose --profile production up --build -d
```

模拟器访问容器时仍使用 `http://127.0.0.1:8000`；生产运行前必须恢复与代码版本匹配的运行数据包，详见 [`../backend/deploy/README.md`](../backend/deploy/README.md)。

后端侧启动正式服务：`python -m app.server`（在 `backend/` 中执行）。

## 与后端接口的对应

| 后端接口 | 客户端代码 | 界面 |
|---|---|---|
| `POST /ask` | `Services/AskAPIService.ask()` → `ViewModels/ChatViewModel.send()` | 聊天主界面；回答下方挂引用/检索/官方入口 |
| `GET /options` | `AskAPIService.options()` → `ChatViewModel.reloadOptions()` | 「提问范围」学院/年级选择（顶栏胶囊、侧栏入口） |
| `GET /source/{chunk_id}` | `AskAPIService.source()` | 引用角标与检索详情点开的「条款原文」页 |

请求体只发契约允许的 `question / college / cohort / major / session_id` 五个键；
`session_id` 在本机生成（`ios-xxxxxxxx`），「新对话」会换新 ID 以重置后端的范围记忆。

## 行为说明

- **诚实流式**：通用对话的供应商 token 会立即进入独立显示缓冲区；学校事实只展示等待态，完成证据与引用校验后从 `final` 一次落全文。
  界面以约 70～120 字/秒渐进揭示，标点处轻微停顿；缓存答案积压较多时会适度追赶，
  播放结束后才显示引用、检索详情和官方入口等附件。
- **引用溯源**：`citations` 逐条列在回答下方，点击调 `/source/{chunk_id}`
  展示条款全文、回答引用的原句高亮、官网通知页/原始文件链接。
- **检索详情**：`retrieved` 以“检索到 N 条相关条款”入口收起，含融合排序分数与摘要；
  摘要字段兼容 `summary` 与旧版 `snippet` 两种命名。
- **拒答**：`refused=true` 时回答区显示“证据不足”标签，`official_links` 单独成卡。
  `OfficialLink` 字段后端尚未冻结，客户端做全可选宽松解码（title/label/doc_title +
  url/page_url/file_url 按优先级取值），后端定稿后可在 `Models/AskModels.swift` 收紧。
- **模式标签**：`mode=school_rag` 显示“校规检索”，`general_chat` 显示“通用对话”，
  并附 `latency_ms`。
- **语音**：语音输入用设备端 `SFSpeechRecognizer`（不上传录音，无云端转写依赖），
  朗读用 `AVSpeechSynthesizer`；长按顶栏喇叭进语音设置，可开语音连续对话。
- **我的课表**（侧栏入口）：导入教务课表截图/PDF（PDF 渲染成图后统一走 Vision OCR，
  按“周一~周日”表头坐标聚类分列、以“周次”行为锚点切课程块），识别结果进确认页
  逐条可改后入库（SwiftData `CourseEntry`），也支持纯手动添加。开课前 10 分钟本地
  通知提醒（课程名+老师+地点），按未来 7 天滚动排期（iOS 限 64 条 pending 通知，
  打开 App 自动续期），单双周按「课表设置」里的第一周周一计算；节次作息表可逐节调整。
  OCR 与提醒全部在设备本地完成，课表不上传。

## 目录结构

```
client-ios/
├── project.yml                 # XcodeGen 工程定义（显示名、权限、ATS、默认后端地址）
├── design/app_icon_master.png  # 校徽原图（App 图标母版）
└── SwufeAsk/
    ├── SwufeAskApp.swift       # 入口：外观模式 + SwiftData 容器
    ├── Theme.swift             # Liquid Glass 设计系统（复用挑战赛版本）
    ├── Models/
    │   ├── AskModels.swift     # /ask、/options、/source 契约模型 + 本地恢复卡模型
    │   ├── ChatMessage.swift   # 聊天消息（user/assistant/notice）
    │   ├── StoredConversation.swift  # SwiftData 历史会话
    │   ├── CourseEntry.swift   # SwiftData 课表课程
    │   ├── WeeksExpression.swift     # 周次表达式解析/格式化（1-16、2-16双周…）
    │   └── SectionTimetable.swift    # 节次作息表 + 学期周历
    ├── Extensions/
    │   └── Text+InlineMarkdown.swift  # answer_md 行内 Markdown 渲染（实时/历史共用）
    ├── Services/
    │   ├── AskAPIService.swift # APIClient + 三个正式接口
    │   ├── SpeechInputController.swift   # 设备端语音识别（SFSpeechRecognizer）
    │   ├── SpeechOutputController.swift  # 回答朗读（AVSpeechSynthesizer）
    │   ├── ScheduleTextRecognizer.swift  # 课表图片/PDF → Vision OCR 文本块
    │   ├── ScheduleParser.swift          # 文本块 → 课程草稿(列聚类+周次锚点)
    │   └── CourseReminderScheduler.swift # 课前 10 分钟本地通知(7 天滚动)
    ├── ViewModels/
    │   ├── ChatViewModel.swift # @MainActor：发送/打字机/范围持久化/选项加载/错误恢复
    │   └── ScheduleImportModel.swift     # 课表导入流程状态(OCR→解析→草稿)
    ├── Views/Schedule/
    │   ├── ScheduleView.swift            # 课表主页：分天列表、导入菜单、空态
    │   ├── ScheduleImportReviewView.swift # 导入确认页(逐条改/删后入库)
    │   ├── CourseEditorView.swift        # 课程编辑表单(添加/修改共用)
    │   └── ScheduleSettingsView.swift    # 学期第一周、提醒开关、节次作息
    └── Views/Chat/
        ├── ChatView.swift      # 主界面：抽屉容器、消息流、语音交互编排
        ├── ChatTopBar.swift    # 顶部悬浮栏（侧栏/朗读/范围胶囊）
        ├── MessageRow.swift    # 消息行（用户/助手气泡、打字机光标）
        ├── NoticeCard.swift    # 错误恢复引导卡 + 建议按钮
        ├── ListeningBanner.swift     # 语音输入实时转写横幅
        ├── VoiceSettingsView.swift   # 朗读语速/音色/连续对话设置
        ├── ChatChrome.swift    # 欢迎面板、快捷提示条、输入条
        ├── CitationViews.swift # 引用列表、条款原文回查、检索详情、官方入口
        ├── ScopeSettingsView.swift   # 学院/年级选择（数据来自 /options）
        ├── SidebarView.swift   # 侧边栏：范围/关于入口 + 外观 + 历史
        ├── ConversationDetailView.swift  # 历史会话只读回放
        └── AboutView.swift     # 数据来源、拒答机制说明 + 后端地址设置
```
