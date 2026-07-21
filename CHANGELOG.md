# CHANGELOG

> 规则:每次合入 `dev` / `main` 的改动都追加一条,**先写记录再提交**,commit 与记录一一对应。
> 集成期间接口相关的任何变动(哪怕一个字段名)必须记录并同步到组内。

## [2026-07-14] dev

- feat: providers 切换层与 FastAPI 后端——app/providers.py 定义 Retriever/Generator 抽象基类(签名严格=契约2/3)+ mock/real 工厂(real 为薄适配层,B/C 未就位时报错并给指引);mock/mock_provider.py 实现契约2过滤逻辑+简易字符相似度检索(开方拉伸对齐 refuse_th 语义)与关键词路由生成;app/server.py 实现 POST /ask(含 refuse_th 前置拒答保险、分段计时)、GET /source/{chunk_id}、GET /meta、静态托管、统一 500 错误处理、请求日志落盘 logs/requests.jsonl
- test: eval/smoke_test.py 37项接口自测全部通过(契约4字段、年级/学院过滤、跨学院零污染、历史版本零泄漏、同题不同院差异化、拒答链路、404/422)
- 影响文件:app/providers.py、app/server.py、mock/mock_provider.py、eval/smoke_test.py、app/__init__.py、mock/__init__.py、eval/__init__.py
- 接口注意:server 调用 generator.answer 时额外传 college/cohort(MockGenerator 使用;RealGenerator 适配层丢弃,待模块C确认 README 待对齐项 D-7)

- feat: mock 桩数据——mock_chunks.jsonl 24条知识块(严格契约1字段,覆盖校级/院级、计算机与金融两学院、2022/2023两年级、现行+历史、3个表格块、超长条款)、mock_answers.json 17组问答+拒答模板(严格契约3格式,覆盖单引用/多引用/跨文件/表格引用/范围提醒/同题不同院);mock/README 声明假数据性质与编造原则
- 影响文件:mock/mock_chunks.jsonl、mock/mock_answers.json、mock/README.md
- 遗留:mock_provider.py 随后端一起提交

## [2026-07-14] main

- chore: 初始化主仓骨架——目录结构(data/ingest/retrieval/generation/app/mock/eval)、.gitignore、config.yaml(契约5+模块D扩展键)、requirements.txt(按模块分节,由D统一维护)
- docs: README 写入冻结的接口契约1~5、模块D对契约的具体化约定(D-1~D-6,待A/B/C确认)、运行方法、评分标准、协作规范与各模块并入指引
- 历史备注:当时的旧上游为空仓库且 D 无 push 权限；当前归属与克隆地址以 `REPOSITORY.md` 为准。
