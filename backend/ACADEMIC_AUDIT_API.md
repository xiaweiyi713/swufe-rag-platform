# 培养方案学业审计扩展接口

更新日期：2026-07-15  
接口性质：对 `API_REFERENCE(1).md` 冻结 B/C/正式 HTTP 的附加接口；不修改 `/ask`、`/options`、`/source/{chunk_id}` 的请求或响应。

## 数据边界

- 目录由 `data/raw` 中培养方案 PDF 的正文和表格构建，不按文件名推断课程。
- 当前覆盖 2017–2024 级、29 个专业/方向方案、2245 条“课程×专业×年级×模块”记录。
- 课程学分、性质和建议学期来自培养方案。请求中的自报学分不作为计算依据。
- 未匹配课程不计入学分并返回警告；专项选课约束与总学分缺口分别核验。
- 结构化产物为 `data/curriculum_catalog.json`，可用 `python -m academic_audit` 重建。

## `GET /academic-audit/options`

返回可用年级、各年级专业、各方案模块、目录版本和记录数量。前端应按以下键读取模块：

```text
modules_by_plan["{cohort}::{major}"]
```

## `POST /academic-audit`

请求禁止额外字段。可以提交结构化参数，也可以在 `question` 中使用自然语言；结构化字段优先。

```json
{
  "question": "专业选修还差多少学分，接下来修什么，哪个学期修？",
  "cohort": "2024",
  "major": "计算机科学与技术专业",
  "target_module": "专业选修",
  "current_semester": 5,
  "completed_courses": [
    "CST132",
    {"name": "算法交易", "credits": 99}
  ]
}
```

`completed_courses` 每项可以是课程代码、课程名，或包含 `code`/`name` 的对象。对象中的 `credits` 仅为兼容成绩单适配器保留；核算时使用目录中的官方学分。

成功响应的核心字段：

| 字段 | 含义 |
|---|---|
| `status` | `ok`、`partial` 或 `needs_clarification` |
| `answer_md` | 可直接显示的核算说明 |
| `plan` | 实际匹配的学院、年级、专业和来源标题 |
| `completed_matches` | 已修输入在该方案中匹配到的课程 |
| `unmatched_completed_courses` | 未计入的输入课程 |
| `modules` | 每个模块的要求、已修、缺口、约束和建议课程 |
| `evidence` | 可通过冻结 `/source/{chunk_id}` 回查的原文证据 |
| `warnings` | 不完整或无法确定的计算边界 |

## 测试 Web

正式服务启动后访问 `/academic-audit-ui`。该页面直接调用上述两个接口，并用 `/source/{chunk_id}` 展示完整原文。

本地无云端 LLM 联调：

```powershell
$env:SWUFE_RAG_MODE="local"
python -m app.server
```

`local` 使用正式 BGE/FAISS、范围过滤、重排、门禁、引用绑定和结构化学业审计；路由与回答文本使用确定性本地实现。未设置 `SWUFE_RAG_MODE` 时仍使用 `API_REFERENCE(1).md` 规定的生产 LLM 运行时。
