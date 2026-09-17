#!/bin/bash
# 双击即可拉起面板并打开浏览器。Finder 里双击 .command 会开一个终端窗口执行本文件。
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
DIR="$PWD"
PORT=19180
URL="http://127.0.0.1:$PORT"

# 已经在跑就不重复拉起，直接开浏览器
if pgrep -f "$DIR/server.py" > /dev/null; then
  echo "面板已在运行 → $URL"
else
  # 用绝对路径启动，否则 pkill/pgrep 按路径匹配会找不到它
  nohup /usr/bin/python3 "$DIR/server.py" > "$DIR/panel.log" 2>&1 &
  for i in $(seq 1 20); do
    sleep 0.5
    if curl -s --noproxy '*' -o /dev/null -m 2 "$URL/api/status"; then break; fi
  done
  if pgrep -f "$DIR/server.py" > /dev/null; then
    echo "面板已启动 → $URL"
  else
    echo "启动失败，日志："; tail -20 "$DIR/panel.log"; exit 1
  fi
fi

open "$URL"
echo
echo "关闭这个终端窗口不会停掉面板。"
echo "要停止：运行同目录的「停止工作台.command」"
