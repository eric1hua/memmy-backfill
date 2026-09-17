#!/usr/bin/env python3
"""把「摘要排队中」占位的记忆重新推回摘要队列。只插作业，不改记忆内容。"""
import sqlite3, json, re, secrets, datetime, sys, os

DB  = os.path.expanduser("~/.memmy/memory-service/memory.sqlite")
PH  = re.compile(r'^(user|assistant|system|tool|developer|摘要排队中|摘要整理中)$', re.I)

def is_placeholder(v):
    if not v: return True
    first = next((l.lstrip('# ').strip() for l in v.split('\n') if l.strip()), None)
    return bool(first and PH.match(first))

def main(limit):
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    rows = c.execute("""
        SELECT m.id, m.info_json, m.content_hash, m.user_id, m.session_id,
               m.properties_json, m.tags_json
        FROM memory_vector_entries e JOIN memories m ON m.id = e.memory_id
        LEFT JOIN evolution_jobs j
               ON j.target_memory_id = m.id AND j.status IN ('queued','leased')
        WHERE e.vector_field = 'vec_summary' AND j.id IS NULL
    """).fetchall()

    picked, skipped = [], 0
    for mid, ij, ch, uid, sid, pj, tj in rows:
        if not is_placeholder((json.loads(ij) if ij else {}).get('summary') or ''):
            continue
        p    = json.loads(pj) if pj else {}
        ii   = p.get('internal_info') or {}
        tags = json.loads(tj) if tj else []
        # 关键：import pipeline 的记忆必须用 import_summary，否则作业会被拒
        is_import = str(ii.get('plugin_algorithm','')).startswith('memory.add.import_async.') \
                    or any(t.strip().lower() == 'agent-source' for t in tags)
        if not is_import:
            skipped += 1
            continue
        # 先判满再放，否则 limit=0 会多塞一条
        if len(picked) >= limit: break
        picked.append((mid, ch, uid, sid))

    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
    for mid, ch, uid, sid in picked:
        jid = 'job_' + secrets.token_hex(10)
        c.execute("""INSERT INTO evolution_jobs
            (id,job_type,status,dedupe_key,user_id,session_id,episode_id,target_memory_id,
             scope_key,scope_seq,payload_json,attempts,max_attempts,leased_until,last_error,
             created_at,updated_at)
            VALUES (?,'import_summary','queued',?,?,?,NULL,?,NULL,NULL,?,0,3,NULL,NULL,?,?)""",
            (jid, f"import_summary:{mid}:{ch}", uid, sid, mid,
             json.dumps({"source":"manual.backfill","contentHash":ch}), now, now))
        c.execute("""UPDATE memory_processing_state
            SET state='summary_pending', stage='summary', active_job_id=?, attempt_count=0,
                retry_action='retry', error_code=NULL, error_message=NULL, failed_at=NULL, updated_at=?
            WHERE memory_id=?""", (jid, now, mid))
    c.commit()
    remaining = sum(1 for mid,ij,*_ in rows if is_placeholder((json.loads(ij) if ij else {}).get('summary') or '')) - len(picked)
    print(json.dumps({"seeded": len(picked), "skipped_non_import": skipped,
                      "remaining_after": max(0, remaining)}, ensure_ascii=False))

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
