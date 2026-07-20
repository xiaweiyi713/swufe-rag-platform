# 新设备继续开发流程

以下流程以 Windows PowerShell、Python 3.12 和 NVIDIA GPU（可选）为例。Git 仓库不包含被忽略的运行数据库和索引，必须同时取得与提交配套的运行数据包。

## 1. 解压与静态验收

克隆代码并将运行数据包解压到仓库根目录，例如：

```text
E:\school\shixun
```

进入目录后先执行：

```powershell
python -m scripts.verify_migration_bundle --checksums-only
git status --short
```

第一个命令应验证 `deploy/data-bundle.manifest.json` 中 10 个运行文件的大小和 SHA-256。依赖安装后再运行不带 `--checksums-only` 的命令，验证数据库、索引和 2024 网安最低 8 学分规则。

## 2. 创建 Python 环境

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

如需 GPU 编码，先按新设备 CUDA 驱动版本从 PyTorch 官方渠道安装对应 CUDA 版 PyTorch，再安装其余依赖。`faiss-cpu` 只影响 FAISS 检索实现；查询向量编码是否使用 GPU 由 PyTorch/transformers 运行时决定。生产索引已经交付，日常问答不需要重新生成 60,827 条向量。

## 3. 首次下载/放置向量模型

配置使用 `BAAI/bge-large-zh-v1.5`。为了避免服务意外联网，正式启动默认设置 transformers 离线模式；新设备若没有本地模型缓存，先临时允许下载一次：

```powershell
$env:SWUFE_RAG_ALLOW_MODEL_DOWNLOAD='1'
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-zh-v1.5')"
Remove-Item Env:SWUFE_RAG_ALLOW_MODEL_DOWNLOAD
```

也可以将原设备 Hugging Face 缓存复制到新设备的同一用户缓存位置。模型缓存和 `.venv` 均为机器相关内容，因此没有放入项目压缩包。

## 4. 运行测试

```powershell
python -m pytest tests/academic_audit/test_requirement_overlay.py tests/academic_audit/test_program_header.py tests/academic_audit/test_service.py tests/v16 -q
```

应运行完整 pytest 门禁。然后执行一个不含真实 Key 的后端数据与索引检查：

```powershell
python -m pytest -q
python -m scripts.verify_migration_bundle
```

## 5. 启动服务

```powershell
python -m app.server
```

打开：

- 聊天页：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`
- OpenAPI：`http://127.0.0.1:8000/openapi.json`

网页 Key 只在当前请求中通过 `X-LLM-API-Key` 发送。不要把 Key 写入 `.env`、源码、截图或 Git。

## 6. 启动后验收问题

建议先测：

```text
2024级网络空间安全专业如果大四不想上课，需要在大四前修读什么选修课？
```

应至少满足：

- 识别 2024 级和网络空间安全专业。
- 说明专业选修模块最低 8 学分，而不是要求把表内 22 学分全部修完。
- 不把网安毕业最低学分写成 155；当前结构化值为 152。
- 引用 2024 完整总册物理第 387 页，并提供打开页/下载链接。
- 携带 Key 时 `planner_llm.called=true`，最终结构化表达通常 `presenter_llm.called=true` 且通过校验。

## 7. 继续开发注意事项

- 正式入口是 `app/server/application.py`；不要再创建 `runtime_v17.py` 一类旁路入口。
- 修改结构化抽取后，先生成审计报告，再重建 `data/academic_v2.sqlite3`，最后跑答案级测试。
- 任何最低学分必须保存原文、`evidence_chunk_id` 和物理页，不能仅保存一个数字。
- SQL 零结果只有在覆盖状态完整时才可解释为没有记录。
- 更换前端只按 `FRONTEND_API_V16.md` 对接，不要复制后端规则到 JavaScript。
- 交付包包含 `.git`，可以继续在当前分支开发；首次连接远程前先执行 `git remote -v` 核对地址。
