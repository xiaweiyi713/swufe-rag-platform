# Tier 1：计智学院 2023 级真实切片

这个切片包含计算机与人工智能学院 2023 级五个本科培养方案的 482 个真实知识块。所有来源链接均指向西南财经大学官方域名，数据不是 fixture，也不包含账号、成绩或其他个人信息。

从仓库的 `backend/` 目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-web.txt

# 首次下载约 1.3GB 的公开 BGE 模型，并从 0 重建真实 FAISS 索引
python -m scripts.reproduce_tier1 build
python -m scripts.reproduce_tier1 verify

# 不需要任何 LLM Key：真实检索 + 引用绑定 + 确定性防幻觉回答
python -m scripts.reproduce_tier1 query "计算机科学与技术专业2023级毕业需要多少学分？"

# 可选：启动 Web 调试界面
python -m scripts.reproduce_tier1 serve
```

浏览器打开 <http://127.0.0.1:8000>。生成的索引和 SQLite 位于 `runtime/`，已被 Git 忽略；删除该目录即可证明下一次仍能从提交的 `chunks.jsonl` 重建。

模型严格钉在：

- ID：`BAAI/bge-large-zh-v1.5`
- revision：`79e7739b6ab944e86d6171e44d24c997fc1e0116`
- license：MIT（模型自身许可）

国内网络可在构建命令前设置 `HF_ENDPOINT=https://hf-mirror.com`。也可以从 ModelScope 下载到本地目录后显式传入，但需要严格复现上述 revision 时优先使用 HF 或 HF Mirror：

```bash
pip install modelscope
modelscope download --model AI-ModelScope/bge-large-zh-v1.5 \
  --local_dir "$HOME/.cache/modelscope/bge-large-zh-v1.5"
python -m scripts.reproduce_tier1 build --clean \
  --model-path "$HOME/.cache/modelscope/bge-large-zh-v1.5"
python -m scripts.reproduce_tier1 verify \
  --model-path "$HOME/.cache/modelscope/bge-large-zh-v1.5"
```

`manifest.json` 固定了输入文件、上游全量语料和模型 revision 的 SHA-256。维护者可用 `python -m scripts.build_tier1_dataset` 从经过审阅的全量语料重新生成本目录的数据文件。
