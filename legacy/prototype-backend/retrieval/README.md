# retrieval/ —— 模块B(检索)

此目录归**模块B**所有,其他模块 PR 不得改动。

模块B并入后应包含:

- `embed.py`:bge-large-zh-v1.5 离线编码并归一化;查询侧加官方前缀"为这个句子生成表示以用于检索相关文章:"
- `index.py`:FAISS IndexFlatIP 建索引并持久化(索引文件放 `retrieval/index/`,已 gitignore),随索引保存 chunk_id 映射
- `retriever.py`:对外提供 **契约2** 的 `retrieve()` —— 先按学院/年级/现行做元数据过滤,过滤后向量 top20 与 BM25 top20 做 RRF 融合,返回 top_k

**对模块D的导入约定**:`from retrieval.retriever import retrieve`(函数签名见主 README 契约2;返回字段约定见主 README「D-1」,有异议先同步)。
