#!/bin/bash
# 在本机安装：生成 launchd 定时任务并加载。可重复执行。
set -eu
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.memmy.backfill.plist"
DB="$HOME/.memmy/memory-service/memory.sqlite"

if [ ! -f "$DB" ]; then
  echo "找不到 memmy 数据库：$DB"
  echo "本工具依赖本地安装的 memmy memory-service。"
  exit 1
fi

if ! curl -s --noproxy '*' --max-time 5 -o /dev/null http://127.0.0.1:18960/health; then
  echo "警告：memory-service 在 18960 没有响应，补全任务会一直跳过。"
fi

mkdir -p "$(dirname "$PLIST")"
sed "s|__DIR__|$DIR|g" "$DIR/com.memmy.backfill.plist.template" > "$PLIST"
echo "已写入 $PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "定时任务已加载（每 10 分钟自检一次）"

chmod +x "$DIR"/*.sh "$DIR"/*.py "$DIR"/*.command 2>/dev/null || true

# 带图标的双击启动器
bash "$DIR/build-app.sh"

echo
echo "安装完成。双击「memmy 工作台.app」打开面板。"
echo "先点一次「开始体检」，看看这台机器的记忆库有没有问题。"
