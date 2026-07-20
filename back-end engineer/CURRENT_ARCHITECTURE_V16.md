# 当前项目架构 V16

## 1. 模块划分

### A：数据与知识库

- 原始资料：`data/raw/`
- 来源登记：`data/sources.csv`
- 全文知识块：`data/chunks.jsonl`
- 来源/块元数据：`data/metadata.sqlite3`
- 向量产物：`artifacts/index.faiss`、`artifacts/vectors.npy`、`artifacts/chunk_ids.json`
- 培养方案结构化中间件：`data/curriculum_catalog_v2.json`
- 课程与培养要求生产库：`data/academic_v2.sqlite3`
- 解析与建库：`ingest/`、`storage/`、`scripts/`

### B：检索与路由

- 混合检索：`retrieval/`
- LLM 语义理解：`swufe_rag/query_understanding.py`
- 确定性归一化：`swufe_rag/normalization_service.py`
- 执行计划：`swufe_rag/tool_planner.py`
- 主编排：`swufe_rag/query_pipeline.py`
- 类型契约：`contracts.py`、`swufe_rag/query_plan_schema.py`

LLM 只输出受约束语义草稿；程序根据数据库覆盖、缺失字段和意图生成执行计划。

### C：事实执行与回答

- 课程/要求数据库：`academic_audit/database.py`
- 参数化执行器：`academic_audit/structured_executor.py`
- 课程审计服务：`academic_audit/service.py`
- 证据表达：`generation/answer_presenter.py`
- 事实校验：`generation/fact_validator.py`
- 引用绑定：`generation/grounded_answer.py`、`generation/cite.py`

精确课程事实使用参数化 SQL；政策文字使用 RAG；组合问题可使用 SQL+RAG。正式体验中，LLM 负责开头的自然语言回答，课程/模块表格和来源由程序追加；课程名、数字、学期和引用必须通过程序校验。

校内知识库无法提供足够依据时，校内回答保持 `refused=true` 且不产生校内引用；随后自动搜索公开网页，由 LLM 生成明确标注为“参考性推测”的补充说明。网页摘要和链接放在 `web_sources`，不会进入学校可信引用账本。

### D：HTTP 与前端

- 正式应用：`app/server/application.py`
- 启动入口：`python -m app.server`
- 当前演示前端：`app/static/chat.html`
- 学业审计演示页：`app/static/academic_audit.html`
- 运行时构造：`app/production_runtime.py`、`app/runtime_factory.py`

前端是可替换的。后端同时提供 OpenAPI 文档 `/docs` 和 `/openapi.json`。

## 2. 请求生命周期

```text
POST /ask + X-LLM-API-Key
        |
        v
QuestionUnderstandingService -> UnderstandingDraft(JSON)
        |
        v
normalize_query -> NormalizedQuery
        |
        v
build_execution_plan -> ExecutionPlan
        |
        +-- clarify ---------> 澄清必要信息
        +-- sql -------------> course_offerings / program_requirements
        +-- rag -------------> 混合检索 + 证据回答
        +-- sql+rag ---------> 合并结构化事实与条款
        +-- general_llm -----> 非学校事实对话
        |
        v
EvidencePacket -> AnswerPresenter -> FactValidator
        |
        +-- 校内证据不足 -> Web Search -> 非权威 LLM 参考回答
        |
        v
answer_md + citations + web_sources + telemetry
```

## 3. 关键数据库表

`data/academic_v2.sqlite3` 主要包括：

- `document_sources`：权威来源、原文件、页链接、下载链接。
- `course_offerings`：年级、学院、专业、模块、课程代码、课程名、学分、学时、性质、学期、开课学院、原页。
- `program_requirements`：模块最低学分、目录学分、规则文本、脚注证据和约束。
- `policy_chunks`：全文政策与培养方案证据。
- 专业/学院别名及覆盖状态相关表。

SQL 只能由后端预定义查询生成，参数由归一化结果绑定；不接受 LLM 自由 SQL。

## 4. 页码与来源

每个事实应尽量绑定：

- `chunk_id`
- `doc_title`
- `physical_page`
- `page_url`
- `file_url`

拆分 PDF 的局部页码通过元数据映射回权威总册物理页。前端不得自行拼接 URL，应直接使用 API 返回值。

## 5. 运行模式

- 携带 `X-LLM-API-Key`：使用 DeepSeek/OpenAI-compatible 模型进行语义理解和最终表达。
- 不携带 Key：用于本地诊断的确定性理解/表达，不代表正式用户体验。
- RAG 查询仍需本地向量模型完成查询编码；默认离线加载，模型缺失时按新设备文档先下载一次。

## 6. 设计约束

- 不把“已向量化”视为“所有规则可计算”。
- 不把 SQL 零结果视为学校没有规定，除非覆盖状态完整。
- 不让 LLM 修改课程集合、学分、学期或来源。
- 不用 table-RAG 承诺完整课程列表；完整列表须来自结构化记录。
- 用户提供的已修情况可用于估算，正式审计需可核验课程记录。
