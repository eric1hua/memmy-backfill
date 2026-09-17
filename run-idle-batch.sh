#!/bin/bash
# 闲时批处理：launchd 每 10 分钟唤醒，自检通过才干活。
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/backfill.log"

IDLE_MIN=300      # 键鼠空闲需 >= 5 分钟
CPU_IDLE_MIN=50   # CPU 空闲率下限（%）
                  #
                  # 这里不用 load average：macOS 把大量等待态线程也计入，
                  # 本机 727 个进程 + Time Machine + 音频驱动，基线常驻 4.0 左右，
                  # 而同时 CPU 实际空闲 80%+。拿 load 当闸门会永远开不了。
BATCH=120         # 每次补多少条
BUDGET=1500       # 单次最长 25 分钟

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# 防重入：直接看有没有同名进程在跑（macOS 无 flock）
if pgrep -f "memmy-backfill/drain.py" > /dev/null; then
  log "跳过：上一批仍在运行"; exit 0
fi

IDLE=$(/usr/bin/python3 -c "
import subprocess
o=subprocess.run(['ioreg','-c','IOHIDSystem'],capture_output=True,text=True).stdout
for l in o.split(chr(10)):
    if 'HIDIdleTime' in l: print(int(int(l.split('=')[-1].strip())/1e9)); break
else: print(0)")
# iostat 一次给出 us/sy/id 和三个 load，约 1 秒；比 top -l 2 快 5 倍
CPU=$(iostat -c 2 | tail -1 | awk '{printf "%d %s", $(NF-3), $(NF-2)}')
CPU_IDLE=${CPU%% *}
LOAD=${CPU##* }

if [ "${IDLE:-0}" -lt "$IDLE_MIN" ]; then log "跳过：用户活跃（空闲 ${IDLE}s）"; exit 0; fi
if [ "${CPU_IDLE:-0}" -lt "$CPU_IDLE_MIN" ]; then
  log "跳过：CPU 繁忙（空闲 ${CPU_IDLE}%，负载 $LOAD）"; exit 0
fi

HEALTH=$(curl -s --noproxy '*' --max-time 8 -o /dev/null -w "%{http_code}" http://127.0.0.1:18960/health)
if [ "$HEALTH" != "200" ]; then log "跳过：memory-service 不健康 (HTTP $HEALTH)"; exit 0; fi

SEED=$(/usr/bin/python3 "$DIR/seed.py" "$BATCH" 2>&1)
log "入队: $SEED (键鼠空闲 ${IDLE}s, CPU 空闲 ${CPU_IDLE}%, 负载 $LOAD)"

# 无论是否还有新摘要要补，都消费队列（embedding 作业也要清）
RESULT=$(/usr/bin/python3 "$DIR/drain.py" --limit 16 --max-seconds "$BUDGET" \
         --require-idle 60 --max-load 8.0 2>&1 | tail -2 | tr '\n' ' ')
log "消费: $RESULT"
