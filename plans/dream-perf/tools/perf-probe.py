#!/usr/bin/env python3
"""Dream 慢因探针 —— 每拍心跳都用这一条命令取同一组指纹。

读 ~/.hermes/state.db（只读）：cron 52fe6cf3a3dc 那一轮的 messages 与 session_model_usage，
外加它的 10 路子代理会话。把这一拍的指纹追加到 evidence/rounds.jsonl，并只打印与上一拍的差
（循环指令纪律：没变就一句话说完）。

  python3 perf-probe.py            # 自动取最新那轮的 session
  python3 perf-probe.py --session cron_52fe6cf3a3dc_20261011_090717
  python3 perf-probe.py --self-test
"""
import argparse, datetime, json, os, sqlite3, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = os.path.join(HERE, os.pardir, "evidence", "rounds.jsonl")
DEFAULT_HOME = os.path.expanduser("~/.hermes")
JOB_PREFIX = "cron_52fe6cf3a3dc"          # dream: 全 profile 每日收编
BIG = 40 * 1024                            # 单条工具结果超过这个字节数 = 上下文炸弹


def latest_session(db, prefix=JOB_PREFIX):
    row = db.execute(
        "select id from sessions where id like ? order by started_at desc limit 1",
        (prefix + "%",)).fetchone()
    return row[0] if row else None


def turn_stats(rows):
    """(LLM 延时列表, 工具耗时列表)。rows = [(role, ts, bytes, tool_name)]，按 rowid 有序。"""
    llm, tool = [], []
    for a, b in zip(rows, rows[1:]):
        dt = b[1] - a[1]
        if a[0] == "tool" and b[0] == "assistant":
            llm.append(dt)
        elif a[0] == "assistant" and b[0] == "tool":
            tool.append(dt)
    return llm, tool


def probe(home, session):
    db = sqlite3.connect("file:%s/state.db?mode=ro" % home, uri=True)
    if session is None:
        session = latest_session(db)
    if session is None:
        return {"error": "找不到 %s* 的会话" % JOB_PREFIX}

    rows = [(r[0], r[1], r[2] or 0, r[3]) for r in db.execute(
        "select role,timestamp,length(coalesce(content,'')),tool_name from messages "
        "where session_id=? order by rowid", (session,))]
    usage = db.execute("select api_call_count,input_tokens,cache_read_tokens,output_tokens,"
                       "reasoning_tokens from session_model_usage where session_id=?",
                       (session,)).fetchone() or (0,) * 5
    llm, tool = turn_stats(rows)
    ctx = sum(r[2] for r in rows)
    bombs = sorted(((r[2], r[3] or "?", datetime.datetime.fromtimestamp(r[1]).strftime("%H:%M"))
                    for r in rows if r[0] == "tool" and r[2] > BIG), reverse=True)[:3]

    kids = [r[0] for r in db.execute(
        "select id from sessions where source='subagent' and started_at > "
        "(select min(started_at) from sessions where id=?)", (session,))]
    k_calls = sum((db.execute("select api_call_count from session_model_usage where session_id=?",
                              (k,)).fetchone() or (0,))[0] for k in kids)
    k_prompt = sum(sum((db.execute("select input_tokens,cache_read_tokens from session_model_usage "
                                   "where session_id=?", (k,)).fetchone() or (0, 0))) for k in kids)
    k_tools = sum(db.execute("select count(*) from messages where session_id=? and role='tool'",
                             (k,)).fetchone()[0] for k in kids)
    k_span = [t for k in kids for t in
              [db.execute("select min(timestamp),max(timestamp) from messages where session_id=?",
                          (k,)).fetchone()] if t and t[0]]
    k_wall = (max(t[1] for t in k_span) - min(t[0] for t in k_span)) / 60 if k_span else 0

    calls = usage[0] or 0
    prompt_tok = (usage[1] or 0) + (usage[2] or 0)
    first, last = rows[0][1], rows[-1][1]
    now = datetime.datetime.now().timestamp()
    leaks = scan_leaks(home)
    m = {
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session,
        "elapsed_min": round((now - first) / 60, 1),
        "silent_min": round((now - last) / 60, 1),
        "msgs": len(rows),
        "ctx_kb": round(ctx / 1024),
        "max_msg_kb": round(max([r[2] for r in rows] or [0]) / 1024, 1),
        "llm_turns": len(llm), "llm_med_s": round(statistics.median(llm), 1) if llm else 0,
        "llm_max_s": round(max(llm), 0) if llm else 0,
        "llm_min": round(sum(llm) / 60, 1), "tool_min": round(sum(tool) / 60, 1),
        "api_calls": calls, "prompt_per_call_k": round(prompt_tok / calls / 1000, 1) if calls else 0,
        "out_per_call": round((usage[3] or 0) / calls) if calls else 0,
        "bombs": bombs, "kids": len(kids), "kids_calls": k_calls, "kids_tools": k_tools,
        "kids_prompt_per_call_k": round(k_prompt / k_calls / 1000, 1) if k_calls else 0,
        "kids_wall_min": round(k_wall, 1), "leaks": leaks,
    }
    m["verdict"] = verdict(m)
    return m


def scan_leaks(home):
    """各 profile 的 skills/ 根目录里最近 3 小时新增的散落文件（子代理越界的迹象）。"""
    now, out = __import__("time").time(), []
    roots = [home] + [os.path.join(home, "profiles", d)
                      for d in (os.listdir(os.path.join(home, "profiles"))
                                if os.path.isdir(os.path.join(home, "profiles")) else [])]
    for r in roots:
        d = os.path.join(r, "skills")
        if not os.path.isdir(d):
            continue
        for e in os.listdir(d):
            if e.startswith("."):      # .usage.json / .curator_ledger.jsonl 是 Hermes 自己的账本，不是散落物
                continue
            p = os.path.join(d, e)
            try:
                if os.path.isfile(p) and now - os.stat(p).st_mtime < 3 * 3600:
                    out.append(p)
            except OSError:
                pass
    return out


def verdict(m):
    v = []
    if m["prompt_per_call_k"] > 25:
        v.append("上下文重放：每次调用 prompt %.1fK tokens" % m["prompt_per_call_k"])
    if m["kids_prompt_per_call_k"] > 25:
        v.append("子代理同类问题：每调用 %.1fK" % m["kids_prompt_per_call_k"])
    if m["llm_min"] and m["llm_min"] > 2 * (m["tool_min"] or 0.1):
        v.append("LLM 占绝对多数：%.1fmin vs 工具 %.1fmin" % (m["llm_min"], m["tool_min"]))
    if m["llm_max_s"] > 300:
        v.append("存在超长回合：最长 %.0fs" % m["llm_max_s"])
    if m["silent_min"] > 3 and m["elapsed_min"]:
        v.append("当前沉默 %.1fmin（长回合 / 等子代理）" % m["silent_min"])
    if m["bombs"]:
        v.append("上下文炸弹 %d 发，最大 %.1fKB" % (len(m["bombs"]), m["bombs"][0][0] / 1024))
    if m["leaks"]:
        v.append("skills 根散落文件 %d 个：%s" % (len(m["leaks"]), m["leaks"][0]))
    return v or ["无异常指纹"]


def show(m, prev):
    if "error" in m:
        print(m["error"]); return 1
    print("[%s] %s" % (m["at"], m["session"]))
    print("  跑 %.1fmin · 静默 %.1fmin · msgs %d · 上下文 %dKB(最大单条 %.1fKB)"
          % (m["elapsed_min"], m["silent_min"], m["msgs"], m["ctx_kb"], m["max_msg_kb"]))
    print("  LLM %d 回合(中位 %.1fs · 最长 %.0fs · 合计 %.1fmin) · 工具 %.1fmin"
          % (m["llm_turns"], m["llm_med_s"], m["llm_max_s"], m["llm_min"], m["tool_min"]))
    print("  调用 %d 次 · 每次 prompt %.1fK / 出 %d tokens"
          % (m["api_calls"], m["prompt_per_call_k"], m["out_per_call"]))
    print("  子代理 %d 个 · %d 次调用 · 每调用 %.1fK · %d 工具往返 · 跨度 %.1fmin"
          % (m["kids"], m["kids_calls"], m["kids_prompt_per_call_k"], m["kids_tools"],
             m["kids_wall_min"]))
    if prev:
        keys = ["elapsed_min", "msgs", "ctx_kb", "api_calls", "prompt_per_call_k",
                "llm_turns", "silent_min", "kids_calls", "leaks"]
        d = ["%s %s→%s" % (k, prev.get(k), m[k]) for k in keys if prev.get(k) != m[k]]
        print("  Δ 上一拍: " + ("; ".join(d) if d else "无变化"))
    for x in m["verdict"]:
        print("  · %s" % x)
    return 0


def append(m):
    with open(ROUNDS, "a") as f:
        f.write(json.dumps(m, ensure_ascii=False) + "\n")


def read_prev():
    if not os.path.exists(ROUNDS):
        return None
    with open(ROUNDS) as f:
        lines = [l for l in f if l.strip()]
    return json.loads(lines[-1]) if lines else None


def self_test():
    day = 86400
    rows = [("tool", 0, 10, None), ("assistant", 30, 0, None), ("tool", 35, 100, "terminal"),
            ("assistant", 95, 0, None), ("tool", 100, 50, "read_file")]
    llm, tool = turn_stats(rows)
    assert llm == [30, 60], llm          # tool→assistant 才是 LLM 延时
    assert tool == [5, 5], tool
    assert verdict({"prompt_per_call_k": 56, "kids_prompt_per_call_k": 0, "llm_min": 40,
                    "tool_min": 3, "llm_max_s": 576, "silent_min": 0, "elapsed_min": 47,
                    "bombs": [(104785, "read_file", "10:12")], "leaks": []})[0].startswith("上下文重放")
    assert verdict({"prompt_per_call_k": 5, "kids_prompt_per_call_k": 0, "llm_min": 1,
                    "tool_min": 1, "llm_max_s": 10, "silent_min": 0, "elapsed_min": 5,
                    "bombs": [], "leaks": []}) == ["无异常指纹"]
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=DEFAULT_HOME)
    ap.add_argument("--session")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-append", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    m = probe(a.home, a.session)
    prev = read_prev()
    rc = show(m, prev)
    if rc == 0 and not a.no_append:
        append(m)
    return rc


if __name__ == "__main__":
    sys.exit(main())
