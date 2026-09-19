#!/usr/bin/env python3
"""memmy 记忆补全 · 本地控制面板。仅监听 127.0.0.1，零第三方依赖。"""
import json, os, re, sqlite3, subprocess, threading, time, html
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME  = os.path.expanduser("~")
DIR   = os.path.dirname(os.path.abspath(__file__))   # 跟着脚本走，不假设装在哪
DB    = os.path.join(HOME, ".memmy/memory-service/memory.sqlite")
PLIST = os.path.join(HOME, "Library/LaunchAgents/com.memmy.backfill.plist")
LABEL = "com.memmy.backfill"
LOG   = os.path.join(DIR, "backfill.log")
PORT  = 19180

PH = re.compile(r'^(user|assistant|system|tool|developer|摘要排队中|摘要整理中)$', re.I)
_cache = {"t": 0, "v": None}
_lock  = threading.Lock()


def is_placeholder(v):
    if not v: return True
    first = next((l.lstrip('# ').strip() for l in v.split('\n') if l.strip()), None)
    return bool(first and PH.match(first))


def counts():
    """扫描占位符数量。开销约 1 秒，缓存 15 秒。"""
    with _lock:
        if time.time() - _cache["t"] < 15 and _cache["v"]:
            return _cache["v"]
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    rows = c.execute("""SELECT m.info_json FROM memory_vector_entries e
                        JOIN memories m ON m.id = e.memory_id
                        WHERE e.vector_field='vec_summary'""").fetchall()
    pending = sum(1 for (ij,) in rows if is_placeholder((json.loads(ij) if ij else {}).get('summary') or ''))
    vec = {f"{d}": n for d, n in c.execute(
        "SELECT embedding_dim, COUNT(*) FROM memory_vector_entries GROUP BY 1")}
    jobs = {f"{t}:{s}": n for t, s, n in c.execute(
        "SELECT job_type, status, COUNT(*) FROM evolution_jobs WHERE status IN ('queued','leased') GROUP BY 1,2")}
    c.close()
    v = {"total": len(rows), "pending": pending, "done": len(rows) - pending,
         "vectors": vec, "queue": jobs}
    with _lock:
        _cache.update(t=time.time(), v=v)
    return v


def current_dim():
    """当前嵌入模型的维度：取最近写入的那条向量。"""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
        r = c.execute("""SELECT embedding_dim FROM memory_vector_entries
                         ORDER BY updated_at DESC LIMIT 1""").fetchone()
        c.close()
        return r[0] if r else None
    except Exception:
        return None


def diagnose():
    """本机记忆库体检。每项返回 level: ok / warn / bad。"""
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    checks = []

    # 1) 占位摘要
    cc = counts()
    pend, tot = cc["pending"], cc["total"]
    checks.append({
        "key": "placeholder",
        "name": "摘要占位符",
        "level": "ok" if pend == 0 else ("warn" if pend < tot * 0.1 else "bad"),
        "value": f"{pend} 条",
        "detail": ("全部记忆都有真实摘要。" if pend == 0 else
                   f"{pend} 条记忆的摘要仍是「摘要排队中」占位符，占 {pend/tot*100:.0f}%。"
                   "它们的向量由占位文本算出，语义检索基本无效。"),
        "fix": None if pend == 0 else "用本面板的「立即补一批」或启用定时任务补齐。",
    })

    # 2) 维度失配：旧向量对当前模型不可见
    #
    # 必须按「谁来修」分类，否则数字会骗人。四种情况的处理方式完全不同：
    #   待重嵌  —— 已激活、摘要是好的、没有在途作业，只有这一类需要人动手
    #   等摘要  —— 摘要还是占位符，补摘要时会顺带用新模型重嵌
    #   在途    —— 已经排着作业，会自己完成
    #   不参与  —— 记忆本身不是 activated（处理中／已归档），根本不进检索
    dim = current_dim()
    rows = dict((d, n) for d, n in c.execute(
        "SELECT embedding_dim, COUNT(*) FROM memory_vector_entries GROUP BY 1"))
    stale = sum(n for d, n in rows.items() if dim and d != dim)
    need_reembed = waiting = inflight = inactive = 0
    if stale:
        for status, ij, busy in c.execute(
                """SELECT m.status, m.info_json,
                          EXISTS(SELECT 1 FROM evolution_jobs j
                                 WHERE j.target_memory_id = m.id
                                   AND j.status IN ('queued','leased'))
                   FROM memory_vector_entries e JOIN memories m ON m.id = e.memory_id
                   WHERE e.embedding_dim != ?""", (dim,)):
            if status != "activated":
                inactive += 1
            elif busy:
                inflight += 1
            elif is_placeholder((json.loads(ij) if ij else {}).get("summary") or ""):
                waiting += 1
            else:
                need_reembed += 1

    parts = []
    if waiting:      parts.append(f"{waiting} 条等补摘要时顺带重嵌")
    if inflight:     parts.append(f"{inflight} 条已排队处理")
    if inactive:     parts.append(f"{inactive} 条属于处理中／已归档的记忆，本来就不进检索")
    if need_reembed: parts.append(f"{need_reembed} 条摘要是好的但补摘要链路会跳过，需要单独重嵌")

    checks.append({
        "key": "dim",
        "name": "向量维度一致性",
        # 只有「需要人动手」的那部分才算问题；后果虽是二元的，但个位数不值得拉红灯
        "level": ("ok" if stale == 0 else
                  ("bad" if need_reembed >= 20 else
                   ("warn" if need_reembed or waiting else "ok"))),
        "value": (f"{dim} 维统一" if stale == 0 else
                  (f"{need_reembed} 条待重嵌" if need_reembed else
                   (f"{waiting} 条随补摘要修复" if waiting else f"{inactive + inflight} 条无需处理"))),
        "detail": (f"全部向量都是 {dim} 维，与当前模型一致。" if stale == 0 else
                   f"当前模型输出 {dim} 维，但有 {stale} 条向量是其他维度"
                   f"（{', '.join(f'{d}维 {n}条' for d, n in sorted(rows.items()) if d != dim)}）。"
                   "检索按维度严格隔离，这些向量对语义召回完全不可见，且不会报错。"
                   + ("其中 " + "、".join(parts) + "。" if parts else "")),
        "fix": (None if not need_reembed else
                f"点「重嵌旧向量」处理这 {need_reembed} 条。"),
        "action": "reembed" if need_reembed else None,
    })

    # 3) 死信作业
    #
    # 光数个数没有意义：如果同类型作业在死信之后又成功过，说明故障已经过去，
    # 那些记录只是历史噪音；只有「最后一次成功早于最后一次死信」才是真的还瘫着。
    dl = dict((tp, n) for tp, n in c.execute(
        "SELECT job_type, COUNT(*) FROM evolution_jobs WHERE status='dead_letter' GROUP BY 1"))
    tot_dl = sum(dl.values())
    last_dl = dict(c.execute("""SELECT job_type, MAX(created_at) FROM evolution_jobs
                                WHERE status='dead_letter' GROUP BY 1"""))
    last_ok = dict(c.execute("""SELECT job_type, MAX(created_at) FROM evolution_jobs
                                WHERE status='succeeded' GROUP BY 1"""))
    stuck = {tp: n for tp, n in dl.items()
             if last_ok.get(tp, "") < last_dl.get(tp, "")}
    checks.append({
        "key": "deadletter",
        "name": "失败作业",
        "level": "ok" if tot_dl == 0 else ("bad" if stuck else "warn"),
        "value": (f"{tot_dl} 个" if not stuck else
                  f"{sum(stuck.values())} 个仍未恢复"),
        "detail": ("没有进入死信的作业。" if tot_dl == 0 else
                   "重试耗尽后被放弃的作业："
                   + "、".join(f"{k} {v} 个" for k, v in sorted(dl.items()))
                   + "。"
                   + ("其中 " + "、".join(f"{k}" for k in sorted(stuck))
                      + " 在最后一次失败之后再没成功过，该功能目前是瘫的。"
                      if stuck else
                      "这些类型在失败之后都又成功过，属于历史噪音，不影响当前检索。")),
        "fix": (None if tot_dl == 0 else
                ("点「重试历史失败」把还能救的重新入队。" if not stuck else
                 "「" + "、".join(sorted(stuck)) + "」需要单独排查，重试大概率还是同样的错。")),
        "action": None,
    })

    # 4) 处理状态异常
    st = dict((s, n) for s, n in c.execute(
        "SELECT state, COUNT(*) FROM memory_processing_state GROUP BY 1"))
    bad = sum(n for s, n in st.items() if s not in ("ready", "ready_text_only"))
    checks.append({
        "key": "processing",
        "name": "处理状态",
        "level": "ok" if bad == 0 else "warn",
        "value": f"{bad} 条未就绪",
        "detail": ("全部记忆处理完毕。" if bad == 0 else
                   "状态分布：" + "、".join(f"{k} {v}" for k, v in sorted(st.items()))
                   + "。非 ready 的记忆尚未完成摘要或向量化。"),
        "fix": (None if bad == 0 else
                (f"其中 {st.get('failed', 0)} 条 failed 可以点「重试历史失败」重新入队；"
                 "其余正在排队的会自动处理。" if st.get("failed") else
                 "正在排队的会自动处理，无需干预。")),
        "action": "retry-failed" if st.get("failed") else None,
    })

    # 5) 完全没有向量的记忆
    nov = c.execute("""SELECT COUNT(*) FROM memories m WHERE m.status='activated'
        AND NOT EXISTS(SELECT 1 FROM memory_vector_entries e WHERE e.memory_id=m.id)""").fetchone()[0]
    checks.append({
        "key": "novector",
        "name": "缺失向量",
        "level": "ok" if nov == 0 else "warn",
        "value": f"{nov} 条",
        "detail": ("每条启用中的记忆都有向量。" if nov == 0 else
                   f"{nov} 条启用中的记忆没有任何向量，只能靠全文检索命中。"),
        "fix": None if nov == 0 else "通常是刚写入尚未处理；若长期不变则是嵌入作业失败。",
    })

    c.close()
    worst = "bad" if any(x["level"] == "bad" for x in checks) else \
            ("warn" if any(x["level"] == "warn" for x in checks) else "ok")
    return {"checks": checks, "overall": worst,
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def idle_seconds():
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=8).stdout
        for line in out.split("\n"):
            if "HIDIdleTime" in line:
                return round(int(line.split("=")[-1].strip()) / 1e9, 1)
    except Exception: pass
    return 0.0


MINIMAX_QUOTA_URL = "https://www.minimaxi.com/v1/token_plan/remains"
_qcache = {"t": 0, "v": None}


def minimax_key():
    """从 config.yaml 读摘要模型的 key，不落盘、不外传。"""
    try:
        with open(os.path.join(HOME, ".memmy/config.yaml"), encoding="utf-8") as f:
            m = re.search(r'^  summary:.*?apiKey:\s*(\S+)', f.read(), re.S | re.M)
            return m.group(1) if m else None
    except Exception:
        return None


def _minimax_fetch(key):
    """查 MiniMax 余量。先直连，失败再走系统代理。

    MiniMax 是国内服务，直连才是常态；但面板如果从带 HTTP_PROXY 的
    终端启动，curl 会默认走那个代理，而翻墙代理通常到不了国内站点，
    结果就是额度栏莫名其妙显示「查询失败」。从 Finder 双击启动时没有
    代理变量，碰不到这个问题，所以这个坑很难被发现。
    """
    base = ["curl", "-s", "--max-time", "25", "-L", MINIMAX_QUOTA_URL,
            "-H", f"Authorization: Bearer {key}",
            "-H", "Content-Type: application/json"]
    last = None
    for args in ([*base, "--noproxy", "*"], base):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            return json.loads(r.stdout)
        except Exception as e:
            last = e
    raise last


def quota():
    """MiniMax token plan 余量。缓存 120 秒，失败不影响面板其余部分。"""
    with _lock:
        if time.time() - _qcache["t"] < 120 and _qcache["v"] is not None:
            return _qcache["v"]
    out = {"ok": False, "error": "未查询"}
    key = minimax_key()
    if not key:
        out = {"ok": False, "error": "config.yaml 中未找到摘要模型 key"}
    else:
        try:
            d = _minimax_fetch(key)
            if d.get("base_resp", {}).get("status_code") != 0:
                out = {"ok": False, "error": d.get("base_resp", {}).get("status_msg", "接口返回异常")}
            else:
                g = next((m for m in d.get("model_remains", []) if m.get("model_name") == "general"), None)
                if not g:
                    out = {"ok": False, "error": "响应中没有 general 模型"}
                else:
                    out = {"ok": True,
                           "interval_pct": g.get("current_interval_remaining_percent"),
                           "weekly_pct":   g.get("current_weekly_remaining_percent"),
                           "interval_reset_s": int(g.get("remains_time", 0) / 1000),
                           "weekly_reset_s":   int(g.get("weekly_remains_time", 0) / 1000),
                           "interval_end": g.get("end_time"),
                           "weekly_end":   g.get("weekly_end_time")}
        except Exception as e:
            out = {"ok": False, "error": f"查询失败：{e}"}
    with _lock:
        _qcache.update(t=time.time(), v=out)
    return out


def service_ok():
    try:
        r = subprocess.run(["curl", "-s", "--noproxy", "*", "--max-time", "5", "-o", "/dev/null",
                            "-w", "%{http_code}", "http://127.0.0.1:18960/health"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "200"
    except Exception: return False


def agent_loaded():
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=8)
        return LABEL in r.stdout
    except Exception: return False


def batch_running():
    return subprocess.run(["pgrep", "-f", "memmy-backfill/(drain|reembed)\\.py"],
                          capture_output=True).returncode == 0


def cpu_idle():
    """CPU 空闲百分比。与 run-idle-batch.sh 用同一个数据源。

    不用 load average：macOS 把等待态线程也计入，本机基线常驻 4.0 左右，
    而同时 CPU 实际空闲 80%+，拿它当闸门会永远开不了。
    """
    try:
        out = subprocess.run(["iostat", "-c", "2"], capture_output=True,
                             text=True, timeout=10).stdout.strip().splitlines()
        f = out[-1].split()
        return int(float(f[-4])), round(float(f[-3]), 2)   # (空闲%, load1)
    except Exception:
        return None, round(os.getloadavg()[0], 2)


def status():
    c = counts()
    idle = idle_seconds()
    cidle, load = cpu_idle()
    svc = service_ok()
    q = quota()
    # 额度未知时不拦截：查询失败不应该卡住补全
    q_ok = (not q.get("ok")) or (q.get("interval_pct") or 0) >= 5
    q_val = f"{q['interval_pct']}%" if q.get("ok") else "未知"
    gates = [
        {"name": "键鼠空闲",   "ok": idle >= 300, "value": f"{int(idle)} 秒",
         "want": "需 ≥ 300 秒"},
        {"name": "CPU 空闲",   "ok": (cidle is None or cidle >= 50),
         "value": f"{cidle}%" if cidle is not None else "未知",
         "want": f"需 ≥ 50%（负载 {load}，macOS 下不作判据）"},
        {"name": "记忆服务",   "ok": svc, "value": "正常" if svc else "无响应",
         "want": "需在 18960 响应"},
        {"name": "MiniMax 额度", "ok": q_ok, "value": q_val,
         "want": "本时段需 > 5%" if q.get("ok") else (q.get("error") or "查询失败，不拦截")},
    ]
    return {**c, "idle": idle, "load": load, "cpu_idle": cidle, "quota": q, "gates": gates,
            "allowed": all(g["ok"] for g in gates),
            "enabled": agent_loaded(), "running": batch_running(),
            "log": tail_log(40)}


def tail_log(n):
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except Exception: return []


def append_log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%F %T')}] {msg}\n")


def run_now(batch):
    """忽略闲时闸门，立刻补一批。"""
    def work():
        append_log(f"手动触发：面板请求 {batch} 条")
        s = subprocess.run([sys.executable, f"{DIR}/seed.py", str(batch)],
                           capture_output=True, text=True)
        append_log(f"入队: {s.stdout.strip()}")
        d = subprocess.run([sys.executable, f"{DIR}/drain.py", "--limit", "16",
                            "--max-seconds", "1800"], capture_output=True, text=True)
        append_log(f"消费: {(d.stdout.strip().splitlines() or ['(无输出)'])[-1]}")
    threading.Thread(target=work, daemon=True).start()


def reembed_now():
    """把旧维度的向量重嵌到当前模型的维度。"""
    def work():
        append_log("手动触发：重嵌旧维度向量")
        r = subprocess.run([sys.executable, f"{DIR}/reembed.py",
                            "--limit", "16", "--max-seconds", "1800"],
                           capture_output=True, text=True)
        append_log("重嵌: " + (r.stdout.strip().splitlines() or ["(无输出)"])[-1])
    threading.Thread(target=work, daemon=True).start()


def retry_failed():
    """重试卡在 failed 的记忆处理。

    memmy 会把配置类错误（比如模型名失效）标成 retry_action='none' 永不重试，
    而那个判断只看数据库里存的旧错误字符串，不会重新探测。配置修好之后
    这些记忆就永远卡着，官方 API 也会拒绝。所以先把这个字段放开再走官方通道。
    """
    def work():
        append_log("手动触发：重试历史失败")
        c = sqlite3.connect(DB, timeout=30)
        freed = c.execute("""UPDATE memory_processing_state SET retry_action='retry'
                             WHERE state='failed' AND retry_action='none'""").rowcount
        c.commit()
        ids = [r[0] for r in c.execute(
            "SELECT memory_id FROM memory_processing_state WHERE state='failed'")]
        c.close()
        if freed:
            append_log(f"解除 {freed} 条「不可重试」标记")
        ok = skip = 0
        for mid in ids:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:18960/api/v1/memory/{mid}/processing/retry",
                    data=b"{}", method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    if json.load(r).get("accepted"):
                        ok += 1
                    else:
                        skip += 1
            except Exception:
                skip += 1
        append_log(f"重新入队 {ok} 条（跳过 {skip} 条）")
        if ok:
            d = subprocess.run([sys.executable, f"{DIR}/drain.py", "--limit", "16",
                                "--max-seconds", "900"], capture_output=True, text=True)
            append_log("消费: " + (d.stdout.strip().splitlines() or ["(无输出)"])[-1])
    threading.Thread(target=work, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # 先剥掉 query string，否则 /?foo=1 这类带参数的地址会落到 404
        path = self.path.split("?", 1)[0]
        if path == "/api/diagnose":
            try: self._send(200, json.dumps(diagnose(), ensure_ascii=False))
            except Exception as e: self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
        elif path == "/api/status":
            try: self._send(200, json.dumps(status(), ensure_ascii=False))
            except Exception as e: self._send(500, json.dumps({"error": str(e)}))
        elif path in ("/", "/index.html"):
            try:
                with open(f"{DIR}/panel.html", "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"panel.html not found", "text/plain")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception: payload = {}
        try:
            if self.path == "/api/enable":
                subprocess.run(["launchctl", "load", PLIST], capture_output=True, timeout=15)
                append_log("定时任务已启用")
            elif self.path == "/api/disable":
                subprocess.run(["launchctl", "unload", PLIST], capture_output=True, timeout=15)
                append_log("定时任务已停用")
            elif self.path == "/api/run-now":
                if batch_running():
                    return self._send(409, json.dumps({"error": "已有批次在运行"}, ensure_ascii=False))
                run_now(int(payload.get("batch", 120)))
            elif self.path == "/api/reembed":
                if batch_running():
                    return self._send(409, json.dumps({"error": "已有批次在运行"}, ensure_ascii=False))
                reembed_now()
            elif self.path == "/api/retry-failed":
                if batch_running():
                    return self._send(409, json.dumps({"error": "已有批次在运行"}, ensure_ascii=False))
                retry_failed()
            elif self.path == "/api/stop":
                subprocess.run(["pkill", "-f", "memmy-backfill/drain.py"], capture_output=True)
                append_log("手动停止当前批次")
            else:
                return self._send(404, json.dumps({"error": "not found"}))
            with _lock: _cache["t"] = 0
            self._send(200, json.dumps({"ok": True}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))

    def log_message(self, *a): pass


if __name__ == "__main__":
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"memmy 补全面板 → http://127.0.0.1:{PORT}")
    srv.serve_forever()
