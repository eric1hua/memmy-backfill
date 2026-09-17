#!/usr/bin/env python3
"""把旧维度的向量重新嵌入到当前模型的维度。

检索按维度严格隔离：维度不匹配的向量对语义召回完全不可见，且不报错。
换嵌入模型后遗留的旧向量必须重嵌才能重新生效。

只处理已经有真实摘要的记忆。占位符摘要的交给 seed.py + drain.py 那条链路，
因为 embedMemory 遇到占位符会自己转去排 import_summary，在这里排等于空转。
"""
import argparse, datetime, json, os, sqlite3, sys, time, urllib.request, uuid

DB = os.path.expanduser("~/.memmy/memory-service/memory.sqlite")
API = "http://127.0.0.1:18960/api/v1/worker/run"


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import is_placeholder   # noqa: E402  判定必须与面板完全一致


def current_dim(c):
    r = c.execute("""SELECT embedding_dim FROM memory_vector_entries
                     ORDER BY updated_at DESC LIMIT 1""").fetchone()
    return r[0] if r else None


def targets(c, dim):
    """返回 (memory_id, user_id, session_id)，只含真实摘要的旧维度记忆。"""
    rows = c.execute("""SELECT DISTINCT e.memory_id, m.user_id, m.session_id, m.info_json
                        FROM memory_vector_entries e JOIN memories m ON m.id = e.memory_id
                        WHERE e.embedding_dim != ? AND m.status = 'activated'""", (dim,)).fetchall()
    out = []
    for mid, uid, sid, ij in rows:
        summary = (json.loads(ij) if ij else {}).get("summary") or ""
        if is_placeholder(summary):
            continue
        out.append((mid, uid, sid))
    return out


def purge_stale(c):
    """清掉上一轮 reembed 留下的作业记录。

    dedupe_key 会挡住同一条记忆重新入队，所以残骸不清就永远排不进去。
    只删本脚本自己造的（dedupe_key 前缀 reembed:），不碰 memmy 原生作业。
    """
    # 连 succeeded 一起删：dedupe_key 的唯一约束不看状态，
    # 上一轮成功的记录会把同一条记忆永久挡在门外。
    n = c.execute("""DELETE FROM evolution_jobs
                     WHERE job_type='embedding' AND dedupe_key LIKE 'reembed:%'""").rowcount
    c.commit()
    return n


def enqueue(c, items):
    at = now_iso()
    n = 0
    for mid, uid, sid in items:
        jid = "job_" + uuid.uuid4().hex[:20]
        try:
            c.execute("""INSERT INTO evolution_jobs
                (id, job_type, status, dedupe_key, user_id, session_id, target_memory_id,
                 payload_json, attempts, max_attempts, created_at, updated_at)
                VALUES (?, 'embedding', 'queued', ?, ?, ?, ?, ?, 0, 6, ?, ?)""",
                (jid, "reembed:" + mid, uid, sid, mid,
                 json.dumps({"source": "manual.reembed_dim"}), at, at))
            n += 1
        except sqlite3.IntegrityError:
            pass          # dedupe_key 已存在 = 上一轮排过了
    c.commit()
    return n


def run(limit):
    req = urllib.request.Request(f"{API}?limit={limit}", method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    p = argparse.ArgumentParser()
    # DashScope 单次 embeddings 调用最多 20 条，worker 会把一轮 lease 到的
    # 作业合并成一次调用，所以这里超过 20 会整批 400 并直接进 dead-letter。
    p.add_argument("--limit", type=int, default=16, help="每轮处理条数（上限 20）")
    p.add_argument("--max-seconds", type=int, default=900)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.limit > 20:
        print(f"--limit {a.limit} 超过嵌入接口批量上限，收敛到 20")
        a.limit = 20

    c = sqlite3.connect(DB, timeout=30)
    dim = current_dim(c)
    if not dim:
        print("无法确定当前维度，放弃", file=sys.stderr)
        return 1
    items = targets(c, dim)
    print(f"当前维度 {dim}，待重嵌 {len(items)} 条")
    if a.dry_run or not items:
        c.close()
        return 0

    purged = purge_stale(c)
    if purged:
        print(f"清理上一轮作业记录 {purged} 个")
    queued = enqueue(c, items)
    c.close()
    print(f"已入队 {queued} 个 embedding 作业")

    t0, ok, fail = time.time(), 0, 0
    while time.time() - t0 < a.max_seconds:
        r = run(a.limit)
        if r.get("leased", 0) == 0:
            break
        ok += r.get("succeeded", 0)
        fail += r.get("failed", 0)
        print(f"  +{r.get('succeeded',0)} 成功 / {r.get('failed',0)} 失败"
              f"  (累计 {ok}/{fail})  {int(time.time()-t0)}s", flush=True)

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    left = len(targets(c, dim))
    rest = c.execute("SELECT embedding_dim, COUNT(*) FROM memory_vector_entries GROUP BY 1").fetchall()
    c.close()
    print(f"完成：成功 {ok}、失败 {fail}，剩余待重嵌 {left} 条")
    print("维度分布:", dict(rest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
