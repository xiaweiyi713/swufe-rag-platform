#!/bin/bash
# 安装每日 07:30 的定时任务(macOS launchd 用户级)
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
CRAWLER_DIR="$PWD"
PLIST="$HOME/Library/LaunchAgents/com.swufe.crawler.plist"
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__CRAWLER_DIR__|$CRAWLER_DIR|g" com.swufe.crawler.plist > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已安装:每天 07:30 自动爬取并合并进 RAG(含后端重启)。"
echo "查看状态: launchctl list | grep com.swufe.crawler"
echo "立即试跑: launchctl start com.swufe.crawler && tail -f logs/launchd.log"
echo "卸载:     launchctl unload ~/Library/LaunchAgents/com.swufe.crawler.plist"
