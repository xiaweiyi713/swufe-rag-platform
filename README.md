# 基于 RAG 的西南财经大学教务智能问答系统

把分散在教务处、研究生院、研招网与各学院官网的培养方案、推免细则、学籍管理规定等公开文件构建成结构化知识库,基于检索增强生成(RAG)实现**可溯源**的教务政策问答:学生选择自己的学院与入学年级后自然语言提问,系统在其适用范围内检索条款、受约束生成回答,每个论断标注来源角标,点击即可查看原文条款与官网出处。

核心设计原则:**可信优先** —— 只依据知识库作答、强制引用溯源、按学院/年级过滤、知识库未覆盖时明确拒答。

## Docker 开发环境

后端与 Redis 已使用 Docker Compose 编排，知识库/索引以只读制品挂载；iOS 客户端继续由 macOS/Xcode 构建，并通过 `http://127.0.0.1:8000` 访问容器服务。

```bash
cp .env.docker.example .env.docker
docker compose -f "back-end engineer/swufe-rag/docker-compose.yml" \
  --env-file .env.docker up --build -d
curl http://127.0.0.1:8000/readyz
```

后端是独立 Git checkout，准备方式和固定版本见 [REPOSITORY.md](REPOSITORY.md)；完整启动、模型缓存、真机联调与运维命令见 [DOCKER.md](DOCKER.md)。

## 仓库结构与模块归属

```
swufe-rag/
├── data/                 # 模块A:原始文件 raw/、sources.csv、chunks.jsonl
├── ingest/               # 模块A:parse.py 解析、chunk.py 条款感知切分
├── retrieval/            # 模块B:embed.py 向量化、index.py FAISS、retriever.py 检索
├── generation/           # 模块C:llm.py 接入、prompts.py 提示词、cite.py 引用校验
├── app/                  # 模块D:server.py 后端、providers.py 桩/真切换层、static/ 前端
├── mock/                 # 模块D:桩数据与桩实现(演示用假数据,见 mock/README.md)
├── eval/                 # 模块D:questions.csv 测试集、run_eval.py 批量评估
├── config.yaml           # 全局配置(契约5),由模块D统一维护
├── requirements.txt      # 全项目依赖,由模块D统一维护
├── CHANGELOG.md          # 变更记录,每次合入 dev/main 必须追加
└── README.md
```

**约定:PR 只改自己模块的目录;接口任何变动(哪怕一个字段名)必须先在群里同步并更新本 README 的契约,再改代码。接口争议以本 README 契约为裁决标准。**

---

## 7.1 接口契约(开工前冻结,与项目计划书一致)

### 契约1:知识块格式 `data/chunks.jsonl`(模块A产出)

```json
{"chunk_id": "it_py2023_017",
 "text": "《计算机科学与技术专业2023级培养方案》毕业要求:学生须修满165学分…",
 "doc_title": "计算机科学与技术专业2023级培养方案",
 "article": "四、毕业要求",
 "level": "院级", "college": "计算机与人工智能学院",
 "cohort": "2023",
 "year": 2023, "status": "现行",
 "page_url": "https://it.swufe.edu.cn/...", "file_url": "https://...pdf",
 "is_table": false}
```

- `cohort`:适用入学年级,非年级类文件为 `"不限"`
- `level`:`校级 | 院级`;`status`:`现行 | 历史`
- `is_table=true` 的块,`text` 为 Markdown 表格,块首带一句表格说明
- `page_url`=通知页,`file_url`=附件直链,两个都要记

### 契约2:检索接口(模块B提供)

```python
retrieve(query: str, top_k: int = 5,
         college: str = None, cohort: str = None) -> list[dict]
# 过滤规则: 保留 (college∈{全校/校级, 用户学院}) 且 (cohort∈{"不限", 用户年级})
#          且 status=="现行" 的块, 再做语义+BM25融合排序
# 注意: 过滤必须在排序前
```

### 契约3:生成接口(模块C提供)

```python
answer(query, chunks) -> {
  "answer_md": "有不及格记录者原则上不具备推免资格[1],但重修…[2]",
  "citations": [{"marker": 1, "chunk_id": "...", "doc_title": "...",
                 "article": "第四条", "quote": "原文片段",
                 "page_url": "...", "file_url": "..."}],
  "refused": false }
```

### 契约4:HTTP 接口(模块D提供)

```
POST /ask {"question":"...", "college":"计算机与人工智能学院", "cohort":"2023"}
  -> {answer_md, citations, retrieved:[chunk摘要+score], latency_ms}
GET /source/{chunk_id} -> 知识块完整原文
```

### 契约5:`config.yaml`

```yaml
embed_model: BAAI/bge-large-zh-v1.5   llm: deepseek-chat|ollama:qwen2.5-7b
top_k: 5  chunk_max_len: 500  use_bm25: true  temperature: 0  refuse_th: 0.35
```

---

## 模块D对契约的具体化约定(待 A/B/C 确认,有异议请提出并更新此处)

契约对以下细节未作规定,模块D按最合理方式先行约定,**均已在 `app/providers.py` 适配层隔离,对齐成本为零或极低**:

| # | 约定 | 依据 |
|---|------|------|
| D-1 | `retrieve()` 返回的每个 dict = **契约1全部字段 + `score: float`**(归一化到 0~1) | 契约3 的 citations 需要块的全部溯源字段;契约4 需要 score |
| D-2 | **校级块的 `college` 字段填 `"全校"`**(`level="校级"`) | 契约2 过滤规则写作 `college∈{全校/校级, 用户学院}` |
| D-3 | `/ask` 响应在契约4基础上**透传 `refused: bool`** | 契约3 已有 refused;前端拒答灰卡样式依赖它 |
| D-4 | 契约4 `retrieved` 的"chunk摘要"具体化为 `{chunk_id, doc_title, article, college, cohort, score, snippet}`(snippet=text 前 120 字) | 前端检索详情面板展示需要 |
| D-5 | 新增 `GET /meta -> {colleges:[...], cohorts:[...]}`,前端身份下拉框数据源;mock 期从 mock 数据统计,real 期从 chunks.jsonl 元数据统计 | 模块D内部接口,不涉及 B/C |
| D-6 | 拒答时 `refused=true`、`citations=[]`,`answer_md` 为拒答话术;前端"最相关条款"列表取自 `retrieved` | 计划书:拒答需"列出最相关的条款供参考" |
| D-7 | **提请模块C注意**:计划书的系统提示模板含"当前用户:{college} {cohort}级本科生",但契约3签名 `answer(query, chunks)` 不含用户身份。D 的 server 在调用 answer 时已额外以关键字参数传入 `college/cohort`(适配层默认丢弃)。建议契约3扩展为 `answer(query, chunks, college=None, cohort=None)`;C 若选择从 chunks 元数据推断身份也可,请确认后更新此行 | 计划书 模块C提示词模板 |

---

## 模块D:运行方法

### 环境准备(mock 模式最小集)

```bash
# 方式一:uv(推荐)
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install fastapi uvicorn pyyaml httpx -i https://pypi.tuna.tsinghua.edu.cn/simple

# 方式二:conda(与计划书一致)
conda create -n rag python=3.12 -y && conda activate rag
pip install fastapi uvicorn pyyaml httpx -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 启动

```bash
uvicorn app.server:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

### mock / real 切换

`config.yaml` 中一行切换,**不改任何代码**:

```yaml
provider: mock   # 桩数据模式(默认):读 mock/ 下的假知识块与假问答
provider: real   # 集成模式:调 retrieval.retriever.retrieve() 与 generation 的 answer()
```

real 模式对 B/C 的导入路径约定见 `app/providers.py` 头部注释;模块B/C就位前切 real 会启动报错并提示缺哪个模块。

### 评估

```bash
python eval/run_eval.py                 # 批量调 /ask,生成待人工评分表 eval/results/<时间戳>/
python eval/run_eval.py --summarize eval/results/<时间戳>/graded.csv   # 评分后汇总
```

评分标准(人工三级):

- **正确(2分)**:要点全部命中,数字与原文一致,引用指向正确条款
- **部分(1分)**:要点部分命中或引用不完整,但无错误信息
- **错误(0分)**:关键信息错误、编造条款/数字,或该拒答未拒答
- **幻觉**:回答中出现知识库不存在的条款、数字或文件名,单独计数
- **拒答正确率**:库外陷阱题中正确拒答的比例

---

## 协作规范

- 分支:`main` 只放可运行稳定版;日常开发走 `dev`;大功能开 `feat/xxx`,合入 `dev` 后删除
- Commit:`类型: 描述`,类型限 `feat / fix / docs / refactor / test / chore`
- **先写 CHANGELOG 再提交**,commit 与记录一一对应
- 合并顺序:A(数据先行)→ B → C → D(D 最后合,顺带把 provider 从 mock 切 real 做首次集成提交)

## 各模块并入指引(集成日操作)

1. clone 主仓,开 `feat/module-<x>` 分支
2. 把自己仓库的文件按上表目录放入(保留提交历史可用 `git subtree add`,文件平移+一次提交也接受)
3. 提 PR;PR 里不许改别人目录
4. 依赖报给模块D负责人,统一进根 `requirements.txt`
