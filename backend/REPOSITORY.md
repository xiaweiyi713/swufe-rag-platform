# 仓库约定

- 主仓：`https://github.com/xiaweiyi713/swufe-rag-platform`
- 默认分支：`main`
- 后端位于 monorepo 的 `backend/`；iOS 客户端与爬虫分别位于 `client-ios/`、`swufe-crawler/`。
- `main` 是唯一集成基线，其他改动通过功能分支和 Pull Request 接入。
- 禁止向 `main` 强制推送，禁止重新引入嵌套 Git 仓库。
- 模拟知识块只存在于 `backend/tests/fixtures/`，ID 全部以 `fixture_` 开头。
- 索引、向量、模型缓存、密钥和运行时评估输出不提交 Git。

后端在合并前的独立历史已经通过 Git subtree 保留在当前 `main` 中；旧仓库不再是发布或协作入口。
