# generation/ —— 模块C(生成与溯源)

此目录归**模块C**所有,其他模块 PR 不得改动。

模块C并入后应包含:

- `llm.py`:统一 OpenAI 兼容调用,API 与本地 Ollama 仅 base_url 不同,由 `config.yaml` 的 `llm` 键切换;temperature=0
- `prompts.py`:系统提示模板(只依据参考资料、句末角标、拒答话术、数字一致、适用范围提醒)
- `cite.py`:解析 `[n]` 角标映射回知识块生成 citations;校验剔除坏引用;统计引用覆盖率

**对模块D的导入约定**:`from generation.cite import answer`(若入口函数放在其他文件,请先同步模块D并更新此处;函数签名与返回格式见主 README 契约3)。
