# 西南财大教务 RAG 换机交付入口

本目录是 2026-07-17 当前可开发版本的交付说明。建议按以下顺序阅读：

1. `FINAL_HANDOFF_2026-07-17.md`：本轮完成内容、已知边界和后续优先级。
2. `CURRENT_ARCHITECTURE_V16.md`：当前代码、数据、SQL、RAG、LLM 和校验链路。
3. `FRONTEND_API_V16.md`：替换前端时需要对接的 HTTP 接口和字段。
4. `NEW_DEVICE_SETUP_2026-07-17.md`：新设备安装、验证、启动和开发流程。
5. `PACKAGE_CONTENTS_2026-07-17.md`：压缩包内容、排除项和完整性基线。

解压后先在项目根目录执行：

```powershell
python scripts/verify_migration_bundle.py
```

该检查不调用 LLM，也不重新建库；它核对关键文件、数据库规模和本轮修复的 2024 级专业选修规则。检查通过后再按新设备文档创建虚拟环境。

> 重要：API Key 不在交付包中，也不应写入代码或配置。网页通过 `X-LLM-API-Key` 请求头按次传入。
