#!/bin/bash
# swufe-crawler 每日流水线:抓取 -> 切块 -> 向量化 -> 合并进 RAG
# 用法: ./run_daily.sh [--restart-backend]
# 日志: logs/daily-<日期>.log(launchd 定时运行时)

set -euo pipefail
cd "$(dirname "$0")"

BACKEND_DIR="$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['backend_dir'])" 2>/dev/null \
  || sed -n 's/^backend_dir: *"\(.*\)"/\1/p' config.yaml)"
CRAWLER_PY=".venv/bin/python"
BACKEND_PY="$BACKEND_DIR/.venv/bin/python"
TODAY="$(date +%F)"

echo "==== swufe-crawler $TODAY $(date +%T) ===="

"$CRAWLER_PY" crawler.py
"$CRAWLER_PY" build_chunks.py --date "$TODAY"

if [ ! -s "output/$TODAY/chunks.jsonl" ]; then
  echo "今天没有新内容,结束。"
  exit 0
fi

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$BACKEND_PY" embed_chunks.py --date "$TODAY"
"$BACKEND_PY" merge_into_rag.py --date "$TODAY" --apply

if [ "${1:-}" = "--restart-backend" ]; then
  echo "重启后端服务…"
  pkill -f "python -m app.server" 2>/dev/null || true
  sleep 2
  (cd "$BACKEND_DIR" && nohup .venv/bin/python -m app.server >> "$OLDPWD/logs/server.log" 2>&1 &)
  echo "后端已在后台重启(日志 logs/server.log)。"
fi

echo "==== 完成 $(date +%T) ===="
