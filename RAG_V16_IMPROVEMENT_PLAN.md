# 西南财经大学教务 RAG V16 改善计划（审计修订版）

> 状态：待实施  
> 日期：2026-07-17  
> 基线：V15 全量语料、全校结构化课程库与 QueryPlan 运行时  
> 核心目标：让系统从“检索到相关内容”升级为“理解用户目标、执行正确查询、给出完整且可验证的答案”。

## 1. 审计结论

原计划的总体方向正确，可以作为下一阶段改造基础。以下原则应保留：

- LLM 只负责自然语言理解和基于证据的表达；
- 工具选择、时间换算、条件校验、SQL 编译、学分计算和答案验证由程序完成；
- 课程完整列表、课程代码、学分和学期查询优先使用结构化数据库；
- 政策条款、培养目标、备注和制度文字使用 RAG；
- 不允许 LLM 自由生成并执行 SQL；
- 不允许最终回答直接回传 PDF 表格拍平文本或 OCR 拼接文本；
- SQL 无结果时不得自动删除专业、学期、主题或课程性质条件；
- 本轮不需要重新解析全部 PDF、重建全部向量或降低证据门。

原计划需要修正的关键点：

1. **LLM 不应决定最终 operations。** LLM 只输出语义草稿；确定性工具规划器根据校验后的语义生成白名单操作。
2. **不能把 LLM 原始理解、程序归一化结果和执行计划放在同一个 QueryPlan 中。** 三者必须分层，避免字段互相冲突。
3. **“培养方案中安排了什么”不等于“当学期实际开设、可以选到什么”。** 当前数据库没有实时开课目录，不能据培养方案承诺实际可选。
4. **SQL 返回 0 不一定代表答案是 0。** 只有专业方案、学期字段、主题分类等相关覆盖维度均完整时，才能确认严格零结果。
5. **用户声称“某模块已修完”不能等同于官方成绩单核验。** 可作为规划假设，但正式学分审计仍需课程清单或成绩单。
6. **table-RAG 不得承担完整课程列表兜底。** 除非系统能证明表格行完整，否则只能返回部分证据或证据不足。
7. **答案校验不应依赖从 Markdown 中重新猜课程代码和数字。** LLM 应输出受限回答草稿，程序基于记录 ID 和证据 ID 渲染、校验。
8. **允许增量数据修复。** 本轮不全量重做知识库，但必须清理脏课程名、补齐培养要求页码和增加主题分类字段。

## 2. 本轮范围

### 2.1 必须完成

- 自然语言时间、主题、课程性质、模块和用户目标的标准化；
- 复合问题拆解；
- 确定性工具规划与参数化 SQL；
- 课程列表、课程详情、培养要求和学业进度审计；
- 截止学期、避开学期和培养方案层面的可行性判断；
- SQL、RAG 和 SQL+RAG 的明确边界；
- 统一 EvidencePacket；
- 可读的确定性格式器和受约束 LLM 表达；
- 课程集合、数字、主题、时间、引用和数据边界校验；
- 准确的运行链路和最终答案来源展示；
- 旧 100 题与新增复杂场景的答案级评测。

### 2.2 本轮不做

- 重新解析全部原始 PDF；
- 重新生成全部 embedding；
- 单纯调大 Top-K 或降低证据门；
- 更换嵌入模型或回答模型；
- 允许 LLM 直接执行 SQL；
- 用更多零散正则继续堆叠旁路版本；
- 用 CSS 或提示词掩盖脏数据；
- 在没有实时开课数据源时实现真正的“本学期可选课程”查询。

## 3. 目标架构

```text
用户原始输入
    ↓
LLM 语义理解（UnderstandingDraft）
    ↓
Schema 校验
    ↓
程序归一化与冲突检查（NormalizedQuery）
    ↓
确定性白名单工具规划（ExecutionPlan）
    ↓
SQL / RAG / SQL+RAG / 澄清
    ↓
EvidencePacket
    ↓
程序生成事实表格 + LLM 生成解释性文字
    ↓
事实、集合、数值、引用和边界校验
    ↓
LLM 结果或干净的确定性兜底
```

### 3.1 LLM 可以做什么

- 识别用户提到的专业、年级、课程、阶段、相对时间和目标；
- 识别用户是在问培养方案、政策、学业进度还是实际开课；
- 把 EvidencePacket 中已经确认的事实整理成自然中文；
- 在不新增事实的前提下解释数据边界和下一步需要补充的信息。

### 3.2 LLM 不可以做什么

- 选择最终数据库操作；
- 生成表名、JOIN、WHERE、URL 或可执行 SQL；
- 自行换算学期或计算学分；
- 猜测未提供的已修课程；
- 修改课程代码、名称、学分、学期、性质或模块；
- 把培养方案安排描述为当学期实际开课；
- 对 SQL 零结果擅自放宽过滤条件。

## 4. 三层查询数据模型

### 4.1 UnderstandingDraft：LLM 原始语义草稿

建议文件：`swufe_rag/query_understanding.py`、`swufe_rag/query_plan_schema.py`。

LLM 只输出语义，不输出 operations：

```python
class UnderstandingDraft(BaseModel):
    domain: Literal["school", "general"]
    primary_intent: Literal[
        "course_query",
        "graduation_requirement",
        "progress_audit",
        "policy",
        "promotion",
        "general_chat",
    ]
    requested_outputs: list[Literal[
        "course_list",
        "course_detail",
        "credit_total",
        "module_breakdown",
        "remaining_courses",
        "remaining_credits",
        "feasibility",
        "policy_explanation",
    ]]

    college_mention: str | None
    major_mention: str | None
    cohort_mention: int | str | None

    current_stage: AcademicStage | None
    explicit_semesters: list[int]
    target_relation: Literal[
        "current",
        "next_semester",
        "previous_semester",
        "before_semester",
        "before_year_4",
        "during_year_4",
    ] | None

    course_names: list[str]
    course_codes: list[str]
    subject_domain_mentions: list[str]
    course_nature_mentions: list[str]
    course_module_mentions: list[str]

    completed_course_mentions: list[str]
    completed_module_claims: list[str]
    goal_mentions: list[str]

    information_scope: Literal[
        "curriculum_plan",
        "actual_offerings",
        "school_policy",
        "unknown",
    ]
    confidence: float
```

LLM 可以建议缺失信息，但最终 `missing_fields` 必须由程序计算。

### 4.2 NormalizedQuery：程序归一化结果

建议文件：`swufe_rag/query_normalizer.py`。

该对象由程序生成，不接受 LLM 直接填充：

- 专业、学院使用数据库中的 canonical ID 和 display name；
- `23级` 归一为 `2023`；
- `大三下` 归一为第 6 学期；
- `当前大三下 + 下学期` 推导为第 7 学期；
- `大四前` 推导为截止第 7 学期之前，即普通学期 1—6；
- `不想大四排课` 推导为 `avoid_semesters=[7,8]`，但不自动认定可行；
- 课程性质、模块和主题映射到枚举；
- 区分已匹配课程、未匹配课程和未经成绩单验证的模块声明；
- 由程序生成 `missing_fields`、`normalization_warnings` 和 `data_requirements`。

冲突字段不应静默互相覆盖。派生字段以原始语义为依据重新计算，并记录修正说明。

### 4.3 ExecutionPlan：确定性执行计划

建议文件：`swufe_rag/tool_planner.py`。

工具规划器根据 NormalizedQuery 生成白名单操作。操作必须是带类型的对象，而不是任意字符串：

```python
class OperationSpec(BaseModel):
    name: Literal[
        "get_course_detail",
        "list_courses",
        "get_graduation_requirements",
        "get_module_requirements",
        "audit_completed_courses",
        "list_remaining_required_courses",
        "list_remaining_elective_courses",
        "list_courses_before_semester",
        "list_unavoidable_courses_after_semester",
        "check_curriculum_feasibility",
        "retrieve_policy",
    ]
    arguments: dict
```

`arguments` 仍需针对每个 operation 使用独立 Pydantic 模型校验，不能把任意字典直接传给执行器。

## 5. 确定性归一化规则

### 5.1 学期换算

| 表达 | 学期 |
|---|---:|
| 大一上 | 1 |
| 大一下 | 2 |
| 大二上 | 3 |
| 大二下 | 4 |
| 大三上 | 5 |
| 大三下 | 6 |
| 大四上 | 7 |
| 大四下 | 8 |

“下学期”必须以用户明确提供的当前阶段为基础。仅凭入学年级和系统日期推导当前学期时，需要学校校历；没有校历或存在休学等情况时应澄清。

### 5.2 时间边界

- `before_year_4`：普通课程截止第 6 学期完成；
- `avoid_semesters=[7,8]`：表示用户目标，不代表系统承诺可以做到；
- 第 7、8 学期的毕业实习、毕业论文和指定实践环节必须单独查询；
- 培养方案只说明建议或规定学期，不能证明课程允许提前选、当学期一定开设或不存在先修限制。

### 5.3 缺失信息

- “大三下有哪些课”：缺专业、入学年级；
- “2023级公共外语多少学分”：不缺专业；
- “我想大四不排课”：缺专业、年级和已修课程；
- “23级人工智能毕业多少学分、我已经修了哪些”：毕业要求可先回答；已修情况必须要求成绩单或课程清单。

复合问题允许部分回答，不得因为其中一个子问题缺信息而拒绝全部问题。

## 6. 主题分类与数据质量

### 6.1 主题分类

为课程增加版本化的结构化分类：

- `course_subject_domains`
- `classification_method`
- `classification_confidence`
- `classification_version`

短期使用课程名称、模块和开课学院的确定性词典分类；未知课程保持 `unknown`，不能强行归类。

主题零结果只有在该专业该年级相关课程的主题分类覆盖完整时才可确认为 0。否则应返回“分类覆盖不足”，而不是“没有此类课程”。

### 6.2 增量清洗

本轮不全量重建知识库，但需要执行以下增量修复：

- 清理课程中文名中的断词、英文表头尾巴和重复字段；
- 中文名、英文名分字段保存；
- 补齐 `program_requirements.source_page` 和 `evidence_chunk_id`；
- 为课程和培养要求保存稳定 `record_id`；
- 生成按年级、专业、字段维度的数据质量报告。

结构化数据库变更不要求重新生成全部向量。只有政策正文或 chunk 内容发生实质变化时才考虑增量重建相应向量。

## 7. 结构化执行器

正式收敛到 `academic_audit/structured_executor.py`，停止增加 `structured_executor_vN.py`。

必须支持：

- 专业、年级和学期课程列表；
- 课程名和课程代码详情；
- 课程性质、模块、主题和截止学期过滤；
- 毕业最低总学分和模块学分要求；
- 完整课程集合与记录数量；
- 参数化 SQL 和稳定排序；
- 覆盖状态与严格零结果区分。

执行器返回的不是文本，而是结构化记录和覆盖信息。

### 7.1 零结果状态

至少区分：

- `confirmed_empty`：所需覆盖维度完整，查询结果确实为 0；
- `not_covered`：对应专业方案尚未结构化；
- `classification_incomplete`：主题分类不足，不能确认 0；
- `invalid_scope`：专业、年级或学期无效；
- `missing_input`：用户信息不足；
- `execution_error`：数据库执行失败。

不得把这些状态都转换成 table-RAG。

## 8. Progress Audit

建议正式收敛到 `academic_audit/progress_audit.py`，并把现有 AcademicAuditService 接入聊天主链。

### 8.1 输入层次

- 已修课程代码：可精确匹配；
- 已修课程名称：规范化和别名匹配；
- 成绩单导入：作为正式审计的首选输入；
- “某模块已修完”：作为用户声明，可用于假设性规划，但应标记 `unverified_claim`；
- 用户填写的课程学分不作为官方计算依据。

### 8.2 计算结果

- 毕业最低总学分；
- 各模块要求、已完成和剩余学分；
- 尚未完成的必修课程；
- 选修模块剩余学分；
- 截止学期前培养方案中安排的课程；
- 第 7、8 学期指定的课程、实习、论文和实践环节；
- 未匹配课程和数据质量警告。

### 8.3 可行性必须分层

```json
{
  "curriculum_feasibility": "feasible | infeasible | insufficient_input",
  "operational_feasibility": "unknown | supported",
  "blocking_requirements": [],
  "data_boundaries": []
}
```

- `curriculum_feasibility`：仅依据培养方案课程与学分约束判断；
- `operational_feasibility`：需要实际开课、选课规则、先修要求和容量数据；当前没有这些数据时必须为 `unknown`；
- 即使普通课程可提前完成，也不能据此声称毕业实习、毕业论文可以取消或提前。

## 9. SQL 与 RAG 边界

### 9.1 SQL

用于课程代码、名称、学分、学期、性质、模块、开课学院、完整列表、模块汇总和学业进度计算。

### 9.2 RAG

用于推免、保研、英语免修、缓考、重修、转专业、学籍、培养目标、文字规定、表格备注和脚注。

### 9.3 SQL+RAG

用于同时需要精确课程计算和制度解释的复杂规划问题。

### 9.4 table-RAG 限制

课程完整列表不得仅凭 Top-K 表格 RAG 回答。SQL 未覆盖但正文存在时：

- 单条明确事实可在证据充分时回答；
- 完整列表必须通过表格行完整性证明；
- 无法证明完整时返回部分证据和覆盖不足，不得伪装成完整答案。

## 10. EvidencePacket

建议文件：`swufe_rag/evidence.py`。

```json
{
  "query": {},
  "execution_plan": {},
  "execution_path": "sql+rag",
  "facts": [],
  "courses": [],
  "requirements": [],
  "audit": {},
  "citations": [],
  "coverage": {
    "plan": true,
    "semester": true,
    "subject_classification": true,
    "requirements": true
  },
  "completeness": {
    "expected_records": 3,
    "returned_records": 3,
    "complete": true
  },
  "missing_inputs": [],
  "data_boundaries": []
}
```

每个事实、课程和要求必须携带稳定 `record_id` 与 `evidence_id`。政策 RAG 可以携带清洗后的精确原文 quote；禁止把整段未清洗表格 OCR 作为回答输入。

## 11. 回答生成与验证

建议文件：`generation/answer_presenter.py`、`generation/fact_validator.py`。

### 11.1 渲染策略

- 单值事实：程序直接生成；
- 完整课程列表：程序生成 Markdown 表格，LLM 不重写课程行；
- 政策问题：LLM 根据 RAG 证据组织；
- 复杂规划：程序生成计算结果和课程表，LLM 只生成摘要、解释、风险和澄清问题；
- 引用链接、文件名和页码由程序附加。

### 11.2 LLM 回答草稿

LLM 不直接返回任意 Markdown，而应优先返回结构化草稿：

```json
{
  "summary": "...",
  "explanations": [
    {"text": "...", "evidence_ids": ["E1"]}
  ],
  "warnings": [],
  "clarification_question": null
}
```

后端验证后再渲染为 Markdown。这样无需从自然语言中反向猜测完整课程集合。

### 11.3 必须校验

- 数字、学期、学时和课程数量；
- 完整列表的 record_id 集合；
- 专业、年级、主题、课程性质和模块；
- 每个事实的 evidence_id；
- 引用文件和物理页码；
- 培养方案与实际开课的数据边界；
- 禁止 OCR 表头回显和异常长无标点文本。

### 11.4 干净兜底

LLM 超时、失败或校验不通过时，只能使用程序格式器。兜底必须基于同一 EvidencePacket，不得返回原始 chunk、拍平表格或未经验证的模型答案。

## 12. 可观测性与前端

后端至少返回：

```json
{
  "planner_llm": {"called": true, "accepted": true, "latency_ms": 0},
  "normalization": {"passed": true, "warnings": []},
  "execution": {"operations": [], "coverage": {}, "row_count": 0},
  "rag": {"called": false, "retrieved_count": 0},
  "presenter_llm": {"called": true, "accepted": true, "latency_ms": 0},
  "validation": {"passed": true, "checks": []},
  "final_output_source": "llm | deterministic_formatter | clarification | insufficient",
  "fallback_reason": null
}
```

前端显示最终链路，不再用一个“LLM 已参与”概括所有情况。调试数据不得包含 API Key、完整提示词、可执行 SQL 或用户成绩单原文。

## 13. 针对当前失败截图的正确行为

| 问题类型 | 正确处理 |
|---|---|
| “大三下，下学期有什么英语课” | 推导第 7 学期；按主题查询；若主题覆盖完整且为 0，明确回答培养方案未安排，并说明不代表实际不开课 |
| “大四不想上课，怎么安排” | 没有已修课程时先澄清；同时说明毕业实习、论文等大四环节需单独核对 |
| “专业方向课已修完，大三还需什么” | 把模块完成视为未核验声明；查询剩余必修和实践环节，避免重复推荐方向课 |
| “毕业多少学分、已经修了哪些” | 先回答毕业要求；已修情况要求成绩单或课程清单，不得从培养方案猜测 |

## 14. 测试与评测

### 14.1 测试层次

1. UnderstandingDraft Schema 与非法字段拒绝；
2. 时间、别名、主题和复合问题归一化；
3. 确定性 operation 规划；
4. 参数化 SQL、覆盖状态和零结果；
5. progress audit 计算；
6. EvidencePacket 完整性；
7. 回答草稿和确定性渲染；
8. 前端链路标签；
9. HTTP 端到端与真实模型抽测。

### 14.2 必测场景

- 大三下 + 下学期；
- 大四前和最后一年不排课；
- 英语、体育、数学、计算机主题；
- 专业方向课、自由选修课、实践环节；
- 已修课程扣除、未匹配课程和模块声明；
- 毕业总学分与模块拆分；
- 第 7、8 学期不可避免环节；
- SQL 严格零结果；
- 培养方案与实际开课边界；
- LLM 失败、超时、遗漏课程和修改数字；
- 禁止 OCR 原文输出。

### 14.3 指标

指标必须按“结构化覆盖充分”和“覆盖不足”分层统计：

- Query Understanding 字段准确率；
- 时间关系准确率；
- 工具规划准确率；
- 必要澄清准确率；
- SQL 条件完整率；
- 课程列表 record-level recall/precision；
- 专业、学期、主题污染率；
- 数值准确率；
- 引用文件和页码准确率；
- 数据边界正确率；
- LLM 草稿校验通过率；
- 确定性兜底正确率；
- OCR 原文泄漏率。

硬性验收：

- 测试集时间关系准确率 100%；
- 已覆盖结构化列表的专业、学期、主题污染率 0；
- 已覆盖明确数值题准确率 100%；
- 已覆盖引用页码准确率 100%；
- 参数化 SQL 条件自动放宽次数 0；
- OCR/拍平表格直接输出次数 0；
- 结构化完整列表在覆盖充分时 record-level recall 和 precision 均为 100%；
- 覆盖不足时不得伪报严格零结果或完整列表。

原 100 题必须升级为答案级金标准，不能再以“命中文档来源”作为通过条件。

## 15. 实施顺序与阶段门

### Phase 0：冻结失败基线

- 保存当前四类失败问题的原始输入、QueryPlan、执行链路、证据和最终回答；
- 固定旧 100 题与新增复杂场景；
- 不修改现有索引。

**阶段门：** 每个失败案例都能稳定复现并记录最终答案来源。

### Phase 1：新契约和理解层

- 创建 UnderstandingDraft、NormalizedQuery、ExecutionPlan Schema；
- 接入严格结构化输出与非法字段拒绝；
- 移除 LLM 对最终 operations 的决定权。

**阶段门：** 时间、主题、专业、年级、复合输出需求全部能正确表达。

### Phase 2：归一化和工具规划

- 实现时间换算、冲突处理、别名映射和缺失字段判断；
- 实现确定性白名单操作规划；
- 明确培养方案与实际开课边界。

**阶段门：** “大三下 + 下学期”稳定得到第 7 学期；英语条件不丢失。

### Phase 3：结构化数据库与执行器

- 收敛普通课程 SQL；
- 增加主题分类；
- 增量清洗课程名和培养要求证据；
- 实现覆盖维度和零结果状态。

**阶段门：** 完整课程列表只来自结构化记录，SQL 不自动放宽条件。

### Phase 4：Progress Audit

- 接通现有 AcademicAuditService；
- 完成已修匹配、模块差额、剩余课程、截止学期和大四环节检查；
- 输出培养方案可行性和运行可行性边界。

**阶段门：** 信息不足时正确澄清；信息充分时计算可复核。

### Phase 5：EvidencePacket、回答和验证

- 统一事实、课程、要求、审计和引用；
- 程序渲染完整列表；
- LLM 生成结构化摘要；
- 实现事实、集合、主题、时间和引用验证；
- 实现干净兜底。

**阶段门：** LLM 失败时仍可读、完整、无 OCR 原文。

### Phase 6：可观测性与端到端评测

- 更新前端链路标签；
- 跑新增场景、原 100 题、跨学院测试和真实模型抽测；
- 输出答案级指标和失败样本。

**阶段门：** 达到硬性验收指标。

### Phase 7：代码收敛与交付

- 正式代码收敛到无版本后缀模块；
- 调整入口为 `python -m app.server`；
- 在所有导入、回归测试和运行指纹通过后删除旧版本文件；
- 保留 Git 标签或提交历史，不在主代码目录长期归档旧实现；
- 通过分支和 PR 合并，避免在重构过程中直接推送 main。

**阶段门：** CI、启动、端到端和索引指纹检查全部通过。

## 16. 目标代码布局

```text
swufe_rag/
  query_plan_schema.py
  query_understanding.py
  query_normalizer.py
  tool_planner.py
  evidence.py
  orchestration.py

academic_audit/
  structured_executor.py
  progress_audit.py

generation/
  answer_presenter.py
  fact_validator.py

app/
  runtime.py
  server/__init__.py
```

迁移期间可以保留适配层，但不得继续增加 `runtime_v16.py`、`structured_executor_v6.py` 或类似旁路文件。

## 17. 完成定义

本轮只有同时满足以下条件才算完成：

1. 当前截图中的复杂问题不再进入原始 table-RAG 回显；
2. 学期、专业、主题和课程性质条件不会丢失；
3. 完整课程列表来自结构化记录且集合完整；
4. 学业审计能区分已核验课程、用户声明和缺失信息；
5. 能给出培养方案层面的可行性结论，同时明确实际开课边界；
6. 最终答案自然、带标点、带文件与页码，不包含 OCR 表头；
7. 页面准确显示 LLM 是否成功生成最终文字以及是否使用兜底；
8. 新增复杂题与原 100 题均按答案级金标准通过；
9. 正式入口和代码目录完成收敛，测试、CI 和运行指纹全部通过。

