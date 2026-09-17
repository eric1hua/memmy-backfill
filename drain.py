#!/usr/bin/env python3
"""消费 memmy 作业队列，带闲时检查和优雅退出。"""
import json, subprocess, sys, time, os, argparse

URL = "http://127.0.0.1:18960/api/v1/worker/run"

def post(limit):
    r = subprocess.run(["curl","-s","--noproxy","*","--max-time","180","-X","POST",
                        f"{URL}?limit={limit}"], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return None

def idle_seconds():
    """距上次键鼠输入的秒数。"""
    try:
        out = subprocess.run(["ioreg","-c","IOHIDSystem"], capture_output=True, text=True).stdout
        for line in out.split("\n"):
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1_000_000_000
    except Exception: pass
    return 0.0

def load1():
    try: return os.getloadavg()[0]
    except Exception: return 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=16, help="每轮作业数")
    ap.add_argument("--max-seconds", type=int, default=0, help="最长运行秒数，0=不限")
    ap.add_argument("--require-idle", type=int, default=0, help="要求空闲秒数，0=不检查")
    ap.add_argument("--max-load", type=float, default=0, help="1分钟负载上限，0=不检查")
    a = ap.parse_args()

    t0, ok, fail, rounds = time.time(), 0, 0, 0
    while True:
        if a.max_seconds and time.time()-t0 > a.max_seconds:
            print(json.dumps({"stop":"time_budget"})); break
        if a.require_idle and idle_seconds() < a.require_idle:
            print(json.dumps({"stop":"user_active","idle":round(idle_seconds(),1)})); break
        if a.max_load and load1() > a.max_load:
            print(json.dumps({"stop":"load_high","load1":round(load1(),2)})); break

        d = post(a.limit)
        if d is None:
            print(json.dumps({"stop":"service_error"})); break
        leased = d.get("leased",0)
        ok   += d.get("succeeded",0)
        fail += d.get("failed",0)
        rounds += 1
        for j in d.get("jobs",[]):
            if j.get("status") != "succeeded":
                print(json.dumps({"job_failed":j.get("jobType"),
                                  "err":str(j.get("error",""))[:160]}, ensure_ascii=False))
        if leased == 0:
            print(json.dumps({"stop":"queue_empty"})); break
        if rounds % 10 == 0:
            print(json.dumps({"progress":{"rounds":rounds,"ok":ok,"fail":fail,
                                          "elapsed_s":int(time.time()-t0)}}))
    print(json.dumps({"done":{"ok":ok,"fail":fail,"rounds":rounds,
                              "elapsed_s":int(time.time()-t0)}}))

if __name__ == "__main__": main()
