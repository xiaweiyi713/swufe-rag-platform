# 前端对接 API V16

默认地址：`http://127.0.0.1:8000`  
交互文档：`GET /docs`  
OpenAPI：`GET /openapi.json`

## 1. `GET /options`

返回可选学院、年级、专业、运行模式、知识库规模和 LLM 能力。前端应以该接口动态生成筛选项，不要把专业列表写死。

可能状态：

- `200`：可用。
- `503`：数据库或索引未就绪，响应体为 `{"detail":"..."}`。

## 2. `POST /ask`

用于自然语言问答，是新前端的主接口。

请求头：

```http
Content-Type: application/json
X-LLM-API-Key: <用户当前输入的 Key>
```

Key 可省略以运行本地确定性诊断，但正式体验默认应提供。前端不得保存、回显或记录 Key。

请求体：

```json
{
  "question": "2024级网络空间安全专业如果大四不想上课，需要在大四前修读什么选修课？",
  "college": null,
  "cohort": "2024",
  "major": "网络空间安全专业",
  "session_id": "browser-generated-session-id"
}
```

字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `question` | 是 | 1—2000 字符。 |
| `college` | 否 | 可选界面上下文。 |
| `cohort` | 否 | 四位入学年级字符串。 |
| `major` | 否 | 专业上下文；后端仍会做标准化。 |
| `session_id` | 否 | 1—128 字符，用于多轮继承专业/年级。 |

核心响应示例（调试字段会更多）：

```json
{
  "mode": "school_rag",
  "answer_md": "……[1]",
  "citations": [
    {
      "marker": 1,
      "chunk_id": "swufe_...",
      "doc_title": "西南财经大学2024级本科人才培养方案（完整总册）",
      "article": "……原文件第387页",
      "quote": "……选修不低于8学分……",
      "physical_page": 387,
      "page_url": "https://...#page=387",
      "file_url": "https://...pdf"
    }
  ],
  "refused": false,
  "latency_ms": 12000,
  "execution_path": "sql",
  "llm_called": true,
  "final_output_source": "llm",
  "validation": {"passed": true},
  "planner_llm": {"called": true, "accepted": true},
  "presenter_llm": {"called": true, "accepted": true}
}
```

校内证据不足时，后端会自动尝试联网搜索。此时仍返回 `refused=true`、
`validation.passed=false` 和空的 `citations`，并通过 `web_sources` 返回公开网页结果；
`answer_md` 会先说明校内知识库没有确切依据，再给出明确标注的联网参考性回答。
网页来源不得显示为学校官方引用。

前端展示规则：

- `answer_md` 按安全 Markdown 渲染，禁止直接执行 HTML。
- 正文中的 `[n]` 对应 `citations[].marker`。
- 来源卡片显示 `doc_title` 和 `physical_page`。
- “打开对应页”直接使用 `page_url`；“下载原文件”直接使用 `file_url`。
- `refused=true` 或 `validation.passed=false` 时显示证据不足状态，不应把调试字段当成答案。
- `web_sources` 非空时单独显示“联网来源”；它与 `citations` 的校内可信来源不是同一层级。
- 用 `planner_llm`、`presenter_llm`、`execution_path` 展示执行状态，不要仅依据响应速度判断 LLM 是否参与。

错误状态：

- `400`：请求字段或业务输入无效。
- `422`：Pydantic 请求结构校验失败，例如未知字段、空问题。
- `503`：模型、索引、数据库或生成服务不可用。
- `500`：未捕获后端异常；前端应显示服务器错误详情摘要，同时保留服务端日志用于定位。

## 3. `GET /source/{chunk_id}`

返回引用块全文与原文件链接，用于来源详情抽屉。`chunk_id` 必须来自 `/ask` 的引用，不能由前端猜测。

- `200`：返回 `text`、`article`、`doc_title`、`page_url`、`file_url` 等。
- `404`：知识块不存在。

## 4. `GET /academic-audit/options`

返回学业审计可选年级、专业和模块。

## 5. `POST /academic-audit`

用于明确的课程完成度/模块学分审计。它是结构化接口，不替代 `/ask` 的自然语言规划。

自然语言方式：

```json
{
  "question": "我是2024级网安学生，此前专业选修均已修完，还差多少专业选修学分？",
  "cohort": "2024",
  "major": "网络空间安全专业",
  "target_module": "专业选修课模块",
  "current_semester": 6,
  "completed_courses": []
}
```

明确课程方式：

```json
{
  "cohort": "2024",
  "major": "网络空间安全专业",
  "target_module": "专业选修课模块",
  "current_semester": 6,
  "completed_courses": [
    {"code": "CST132", "name": "JavaEE开发实践", "credits": 2},
    "CST403"
  ]
}
```

注意：仅有“以前都修完了”的声明时，系统可以按声明给规划建议或估算；不得将其标记为已由教务成绩核验。需要精确计算时传课程代码/名称/学分，后续也可接入正式成绩单数据源。

## 6. 前端最小实现

新前端只需要：

1. 首次加载调用 `/options`。
2. 在内存中读取 Key；发送 `/ask` 时放入请求头，发送后不持久化。
3. 为会话生成稳定 `session_id`。
4. 渲染 `answer_md`、引用页和下载链接。
5. 对 400/422/503/500 分别提示；不要把错误响应当聊天文本。
6. 需要成绩审计时调用 `/academic-audit`，普通问题统一调用 `/ask`。
