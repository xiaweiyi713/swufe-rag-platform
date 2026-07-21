# 仓库拓扑

本目录是客户端、爬虫和本机联调工作区；正式 RAG 后端位于
`back-end engineer/swufe-rag`，它是拥有独立历史与远端的 Git 仓库：

```text
https://github.com/xiaweiyi713/swufe-rag.git
```

当前采用两个仓库分别提交和推送，不把后端目录当普通文件加入外层仓库，也不
在后端提交尚未推送时创建失效的 submodule。`BACKEND_REVISION` 第一行记录协作
分支，第二行记录外层工作区验证过的后端提交。新设备准备命令：

```bash
mkdir -p "back-end engineer"
git clone https://github.com/xiaweiyi713/swufe-rag.git "back-end engineer/swufe-rag"
git -C "back-end engineer/swufe-rag" fetch origin codex/rag-v16-repair
git -C "back-end engineer/swufe-rag" checkout fcbd785
```

后端提交变化后必须同步更新 `BACKEND_REVISION`。外层工作区目前没有配置远端，
因此继续用明确的版本文件钉住后端；后续为外层客户端仓库配置独立远端后，可以
把这套人工钉住升级为 Git submodule。不要把两个独立历史强行推到同一个远端，
也不要让外层 `git add -A` 意外吸收 embedded repository。

Docker 只有一份权威编排文件：
`back-end engineer/swufe-rag/docker-compose.yml`。外层不再维护第二份服务定义。
