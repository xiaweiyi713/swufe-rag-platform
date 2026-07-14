# ingest/ —— 模块A(解析与切分)

此目录归**模块A**所有,其他模块 PR 不得改动。

模块A并入后应包含:

- `parse.py`:docx(python-docx,保留 Heading 层级)与 pdf(pdfplumber,含 extract_tables)解析;表格转 Markdown,整表一个知识块(`is_table=true`),块首加一句表格说明
- `chunk.py`:条款感知切分(正则锚点"第X条/章/节"与"一、二、…"),一条一块,超 `chunk_max_len`(500字)按句子边界二次切分并继承元数据;每块 text 头部注入"《文件名》+章节路径"前缀

产出:`data/chunks.jsonl`,字段严格遵守主 README 契约1。
