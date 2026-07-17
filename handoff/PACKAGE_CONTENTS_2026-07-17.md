# 迁移包内容说明

## 已包含

- 当前项目源码、配置、测试、文档和 Git 历史。
- `data/raw/` 下全部原始资料，包括培养方案总册及推免/保研等非培养方案文档。
- `data/` 下处理后的全文块、来源登记、元数据 SQLite、课程目录 JSON、学业 SQLite。
- `artifacts/` 下当前 FAISS、向量矩阵、chunk id 和构建产物。
- 本轮脚注结构化审计报告和修复脚本。
- 新设备静态验收脚本 `scripts/verify_migration_bundle.py`。

## 未包含

- `.venv/`：与 Python 路径和操作系统绑定，新设备按 `requirements-dev.txt` 重建。
- `tmp/`、`.pytest_cache/`、`__pycache__/`：临时或可再生缓存。
- `backups/`：防止迁移包嵌套旧备份导致体积翻倍；交付前备份单独保存在原设备。
- `*.log`：旧服务日志不影响继续开发。
- API Key、浏览器存储、用户凭据。
- Hugging Face/PyTorch 用户级模型缓存：按新设备文档下载一次或单独复制。

## 当前关键规模基线

| 项目 | 基线 |
|---|---:|
| 全文来源 | 57 |
| 全文知识块/向量 | 60,827 |
| 全文物理页 | 6,699 |
| 结构化培养方案 | 468 |
| 结构化课程行 | 35,828 |
| 结构化培养要求 | 5,515 |

压缩包生成后会在同目录附带 SHA-256 文件。新设备可以执行：

```powershell
Get-FileHash .\swufe-rag-handoff-20260717.zip -Algorithm SHA256
```

并与 `.sha256.txt` 中的值比对。
