#!/bin/bash
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if pkill -f "$PWD/server.py"; then
  echo "面板已停止。"
else
  echo "面板本来就没在运行。"
fi
echo
echo "注意：这只停面板，不影响后台的定时补全任务。"
echo "定时任务的开关在面板里，或用 launchctl unload ~/Library/LaunchAgents/com.memmy.backfill.plist"
