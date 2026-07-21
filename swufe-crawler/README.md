# swufe-crawler:官网信息每日入库

每天定时爬取西南财经大学官网的**通知公告**与**新闻**,切成契约 1 知识块、
用与后端一致的 bge-large-zh-v1.5 向量化,然后**安全合并**进 swufe-rag 的
RAG 知识库,让教务问答能回答"最近有什么通知/新闻"这类时效性问题。

爬虫是 monorepo 的独立模块:代码、状态、增量产物都在本目录;对 `../backend` 只做
"备份 + 追加 + 末尾提交 manifest"的合并,可一键回滚。

## 流水线

```
crawler.py        列表页发现新文章(state.sqlite 去重、1.5s 限速、UA 标明)
   ↓ output/<日期>/articles.jsonl
build_chunks.py   正文按段落切 ~460 字块,契约 1 十二字段(校级/全校/不限)
   ↓ output/<日期>/chunks.jsonl
embed_chunks.py   bge-large-zh-v1.5 向量化 + 行归一化(后端 venv 运行,离线)
   ↓ output/<日期>/vectors.npy
merge_into_rag.py 合并进后端(默认 dry-run;--apply 执行;--rollback 恢复)
   ↓ 重启后端生效
```

合并会同步更新后端的六处数据并保持启动校验一致:
`data/chunks.jsonl`、`artifacts/{vectors.npy, chunks.json, chunk_ids.json,
index.faiss}`、`data/metadata.sqlite3`(sources+chunks,embedding_row 顺延),
最后重写 `manifest.json`(chunk_count/chunks_sha256)作为提交标记。
合并前自动备份到 `backup/<时间戳>/`(约 600MB,验证无误后可删旧备份)。

## 手动运行

```bash
cd swufe-rag-platform/swufe-crawler
.venv/bin/python crawler.py                 # 抓新文章
.venv/bin/python build_chunks.py            # 切块
BACKEND="../backend"                        # 见 config.yaml
HF_HUB_OFFLINE=1 "$BACKEND/.venv/bin/python" embed_chunks.py
"$BACKEND/.venv/bin/python" merge_into_rag.py           # dry-run 预览
"$BACKEND/.venv/bin/python" merge_into_rag.py --apply   # 执行合并
# 重启后端后,新内容即可被 /ask 检索并带引用
```

一条命令跑完:`./run_daily.sh --restart-backend`

## 每天定时

```bash
./install_schedule.sh     # 按当前绝对路径生成并安装 launchd 任务
```

日志在 `logs/launchd.log`。卸载:
`launchctl unload ~/Library/LaunchAgents/com.swufe.crawler.plist`。
Mac 需在 07:30 处于开机状态(合盖睡眠时 launchd 会顺延到唤醒后补跑)。

## 出错恢复

合并中任何一步失败都会提示回滚命令:

```bash
"$BACKEND/.venv/bin/python" merge_into_rag.py --rollback backup/<时间戳>
```

## 配置(config.yaml)

- `sites`:抓取的列表页,可自行添加;`topic` 写入 metadata.sources.topic
  (course_selection / notice / campus_news / …)。已接入并实测:
  - **教务处** `jwc.swufe.edu.cn`(通知公告 + 首页动态)—— 选课、转专业、
    停开课程、微专业等,与教务问答最对口;
  - 主站通知公告、新闻网要闻。
  三个站点都是博达 CMS(`info/<栏目>/<文章>.htm` + `v_news_content` 正文容器),
  同一套解析逻辑通用;新增其它学院站点(如 `sc.swufe.edu.cn`)大概率直接可用,
  加一行配置试跑即可。
- 发布日期优先取**列表页条目**里的 `[YYYY年MM月DD日]`;列表页不带日期时
  (如教务处首页轮播的常驻服务指南)才退回详情页启发式,后者可能被正文里的
  年份干扰,属已知降级路径。
- `crawl.max_new_per_site`:每站每天新文章上限(默认 15)。
- `crawl.delay_seconds`:请求间隔(默认 1.5s,请保持礼貌抓取)。

## 后端契约里的两个隐藏关卡(已在 merge 脚本处理)

- 后端启动会按 `data/sources.csv` + chunks **重建** metadata.sqlite3,并要求
  每个块的七元组 `(doc_title, level, college, cohort, year, status, file_url)`
  精确命中一行登记,否则整库拒绝启动 —— 所以合并必须登记 sources.csv,
  直接写 metadata.sqlite3 没有用。
- sources.csv 的 `file` 列(source_key)有后缀白名单 `.pdf/.docx/.txt/.md`,
  网页内容统一登记为 `web/<hash>.md`。

## 设计边界

- 只抓学校官网的公开页面,限速、带明确 UA,不抓需登录内容。
- 网页块的元数据固定为 校级/全校/年级不限/现行,不参与专业级 SQL 审计,
  只进全文/向量检索;引用角标与 /source 原文回查照常工作。
- 附件(pdf/doc)只记录链接进 file_url,不下载解析正文(后续可扩展)。
