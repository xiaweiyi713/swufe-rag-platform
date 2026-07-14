# CHANGELOG

> 规则:每次合入 `dev` / `main` 的改动都追加一条,**先写记录再提交**,commit 与记录一一对应。
> 集成期间接口相关的任何变动(哪怕一个字段名)必须记录并同步到组内。

## [2026-07-14] main

- chore: 初始化主仓骨架——目录结构(data/ingest/retrieval/generation/app/mock/eval)、.gitignore、config.yaml(契约5+模块D扩展键)、requirements.txt(按模块分节,由D统一维护)
- docs: README 写入冻结的接口契约1~5、模块D对契约的具体化约定(D-1~D-6,待A/B/C确认)、运行方法、评分标准、协作规范与各模块并入指引
- 遗留:主仓 ZorIgn/swufe-rag 为空仓库且 D 无 push 权限,骨架推送与保护分支设置待组长处理(见 README 并入指引)
