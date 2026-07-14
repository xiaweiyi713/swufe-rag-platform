# swufe-rag 公共接口契约 v1.0

公共字典使用严格字段集合，运行时校验和类型定义见 `contracts.py`。

## 契约 1：知识块

`data/chunks.jsonl` 每行必须是一个 JSON 对象：

```json
{"chunk_id":"it_py2023_017","text":"知识块文本","doc_title":"文档标题","article":"第四条","level":"院级","college":"计算机与人工智能学院","cohort":"2023","year":2023,"status":"现行","page_url":"https://it.swufe.edu.cn/example","file_url":"https://it.swufe.edu.cn/example.pdf","is_table":false}
```

- 所有字段必填且不接受额外字段，`chunk_id` 全局唯一。
- `level` 只能是 `校级|院级`；校级块必须使用 `college=全校`。
- `cohort` 是四位入学年份或 `不限`；`status` 是 `现行|历史`。
- 页面和附件地址必须是绝对 HTTP(S) URL；网页正文没有附件时二者相同。

## 契约 2：检索

```python
retrieve(query: str, top_k: int = 5,
         college: str | None = None,
         cohort: str | None = None) -> list[dict]
```

每项是完整知识块加唯一扩展字段 `score: float`。列表顺序是 RRF 融合顺序；`score` 固定表示 BGE 余弦相似度，只用于拒答和展示，调用方不得据此重新排序。

过滤规则：始终只取 `status=现行`；指定学院后只保留校级或该学院；指定年级后只保留 `不限` 或该年级。过滤发生在 Top-K 截断前。

## 契约 3：生成与溯源

```python
answer(query: str, chunks: list[dict]) -> dict
```

严格返回：

```json
{
  "answer_md": "回答内容[1]",
  "citations": [{
    "marker": 1,
    "chunk_id": "it_py2023_017",
    "doc_title": "文档标题",
    "article": "第四条",
    "quote": "知识块中的原文片段",
    "page_url": "https://...",
    "file_url": "https://..."
  }],
  "refused": false
}
```

`quote` 必须是知识块原文子串。空结果或最高结果的 `score < refuse_th` 时不调用 LLM。知识不足的固定主句为“现行文件中未找到明确规定，建议咨询教务处或学院教务办。”

## 错误边界

- 数据或接口结构错误：`ContractError`
- 正式知识库或索引未就绪：`KnowledgeBaseNotReadyError`
- LLM 服务不可用：`GenerationUnavailableError`

B、C 只提供 Python 接口。`POST /ask` 和 `GET /source/{chunk_id}` 由后续 D 模块实现。

