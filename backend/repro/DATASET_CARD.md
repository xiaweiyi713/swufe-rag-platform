---
pretty_name: SWUFE RAG Reproducibility Data
language:
- zh
license: other
task_categories:
- question-answering
- sentence-similarity
tags:
- rag
- education
- chinese
- faiss
- swufe
---

# SWUFE RAG Reproducibility Data

西南财经大学教务可信 RAG 平台的可复现数据发布仓库，对应代码：
<https://github.com/xiaweiyi713/swufe-rag-platform>。

## 内容

- `tier1-chunks.jsonl`、`tier1-sources.csv`、`tier1-manifest.json`：计智学院 2023 级五个真实培养方案的 482 个知识块与来源登记；体积小，可在 CPU 上从公开 BGE 模型重建索引。
- `swufe-rag-runtime-data-20260721.tar.gz`：69,583 个知识块对应的完整运行数据包，含 SQLite、FAISS、向量及 SHA-256 清单。
- `data-bundle.manifest.json` 与 `.sha256`：完整包的逐文件校验清单和归档摘要。

完整包安装：

```bash
cd swufe-rag-platform/backend
python -m scripts.fetch_runtime_data --source huggingface
```

Tier 1 不需要 LLM Key；混合检索、来源绑定、引用和事实门均可离线验证。LLM 仅用于可选的自然语言润色，正式客户端采用 BYOK。

## 来源与权利

语料只来自 `swufe.edu.cn` 及其子域名上的公开通知、规章与培养方案。来源 URL 和文件 URL 保留在每个知识块及 `sources.csv` 中。本发布用于教学、研究与系统复现，不声称拥有原始学校文件的版权，也不授予超出原始发布者许可的再利用权。使用者应遵守原网站条款并在引用时回链官方来源。

不包含学生成绩、账号、登录后内容、API Key、模型权重或个人隐私数据。
