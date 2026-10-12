#!/usr/bin/env python3
"""dream · S1.5 分类闸门：先判一条技能是「通用解决方案」还是「用户偏好 / 本机环境」。

插在 S1（枚举）与 S2（判落点）之间，是**分流的第一道闸**：

    S1 scan ──► S1.5 triage ──┬── general    ──► S2 judge（挑落点 → 进包）
                              ├── preference ──► 压缩写记忆（不进包）
                              └── env        ──► 压缩写记忆（不进包）

非 general 的两类**不进包**：脚本只给分类与压缩后的 `memory` 载荷，真正的记忆写入由主 agent 用
**它所在 harness 提供的记忆工具**执行（不绑定某个记忆基座）；之后按 S8 的口径退役 profile 副本。

    python3 -B triage.py --dir <scan --out 目录> [--limit N] [--dry-run] [--input <文件>]

产出（都在 `--dir` 里）：

| 文件 | 内容 |
|---|---|
| `triage-<backend>.jsonl` | 逐条：id / kind / reusable / why / evidence / confidence / memory |
| `triage-summary.json` | 计数 + 分流：general 的 id 列表、非 general 的行（带 memory 载荷，写记忆就照它写） |
| `judge-input-bodies.routed.json` | 只留 general 的候选 = **S2 的输入**（`judge.py --input` 指它） |
| `triage-request-<n>.json` / `triage-response-<n>.json` / `triage-chunk-<n>.jsonl` | 「模型当时看到了什么」的唯一副本（与 judge 同一纪律，永不删） |

后端与分块参数从 `~/.hermes/dream-judge.json` 读（与 judge.py 同一份配置，按数组顺序尝试）。
退出码：0 = 每条候选都判出了 kind；1 = 有候选没判出（重跑同一条命令，已判完的块会复用）。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from judge import chunk                                                # noqa: E402
from judge_backends import TRIAGE_KINDS, load_config, triage_chunk_with_fallback  # noqa: E402


def write_outputs(d: pathlib.Path, items: list, rows: list, backend: str) -> dict:
    """落三份产物：逐条结果 / 分流摘要 / 只留 general 的 S2 输入。-> 逐类计数。"""
    by_id = {it["id"]: it for it in items}
    general = [r["id"] for r in rows if r["reusable"]]
    counts = {k: 0 for k in TRIAGE_KINDS}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1

    (d / f"triage-{backend}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    gset = set(general)
    routed = [it for it in items if it["id"] in gset]
    (d / "judge-input-bodies.routed.json").write_text(
        json.dumps(routed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    queue = [dict(id=r["id"], skill=by_id[r["id"]]["skill"], kind=r["kind"], memory=r["memory"],
                  path=str(by_id[r["id"]].get("skill_md") or by_id[r["id"]].get("path") or ""))
             for r in rows if not r["reusable"]]
    (d / "triage-summary.json").write_text(json.dumps(dict(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"), backend=backend, candidates=len(items),
        classified=len(rows), counts=counts, general_ids=general, memory_queue=queue),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="the --out dir from `scan`")
    ap.add_argument("--input", default="judge-input-bodies.json",
                    help="candidate file（默认 scan 冻结那份；重跑闸门时可指别的）")
    ap.add_argument("--limit", type=int, default=0, help="classify only the first N candidates (smoke test)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true", help="print the chunk plan, call nothing")
    args = ap.parse_args()

    d = pathlib.Path(args.dir).expanduser()
    items = json.loads((d / args.input).read_text(encoding="utf-8"))
    backends, ccfg = load_config()
    chunk_bytes, workers = ccfg["chunk_bytes"], ccfg["workers"]
    key_file = pathlib.Path(ccfg["key_file"]).expanduser()

    if args.limit:
        items = items[:args.limit]
    chunks = chunk(items, chunk_bytes)
    first_name = backends[0]["name"] if backends else "unknown"
    out_path = d / f"triage-{first_name}.jsonl"
    print(f"[triage] backends={[b['name'] for b in backends]} candidates={len(items)} "
          f"chunks={len(chunks)} chunk_bytes={chunk_bytes} workers={workers} out={out_path}")
    for i, c in enumerate(chunks, 1):
        print(f"  chunk {i}: {len(c)} 条 / {len(json.dumps(c, ensure_ascii=False))} B")
    if args.dry_run:
        return 0

    t0 = time.time()

    def work(pair):
        i, c = pair
        ids = [it["id"] for it in c]
        # Resume only on a byte-identical prompt that answered every id (a re-worded prompt must re-judge).
        cached = None
        rp = d / f"triage-response-{i}.json"
        if rp.exists():
            try:
                doc = json.loads(rp.read_text())
                if doc.get("prompt_sha") == hashlib.sha256(
                        json.dumps(c, ensure_ascii=False).encode()).hexdigest() \
                        and not doc.get("error") and doc.get("rows"):
                    got = [r for r in doc["rows"] if r.get("id") in ids]
                    if len(got) >= len(ids):
                        cached = got
            except (json.JSONDecodeError, OSError):
                cached = None
        if cached is not None:
            (d / f"triage-chunk-{i}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in cached))
            return i, ids, cached, [], 0.0, None, first_name
        tt = time.time()
        rows, source, err = triage_chunk_with_fallback(
            c, backends, key_file=key_file, timeout=args.timeout)
        (d / f"triage-request-{i}.json").write_text(json.dumps(
            {"backends": [b["name"] for b in backends], "used": source, "chunk_ids": ids},
            ensure_ascii=False))
        (d / f"triage-response-{i}.json").write_text(json.dumps(
            {"backends": [b["name"] for b in backends], "used": source,
             "prompt_sha": hashlib.sha256(json.dumps(c, ensure_ascii=False).encode()).hexdigest(),
             "error": err, "rows": rows}, ensure_ascii=False))
        # Stamp the actual backend on every row (source/model are never hand-edited).
        for r in rows:
            r["source"] = source or "unknown"
        bad = [] if rows else [err or "empty reply"]
        (d / f"triage-chunk-{i}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return i, ids, rows, bad, time.time() - tt, err, source or "?"

    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for r in ex.map(work, list(enumerate(chunks, 1))):
            results.append(r)
            i, ids, got, bad, dt, err, src = r
            kinds = {k: sum(1 for x in got if x["kind"] == k) for k in TRIAGE_KINDS}
            print(f"[triage] chunk {i}: {len(got)}/{len(ids)} 条分类 · {dt:.1f}s · backend={src}"
                  f" · general={kinds['general']} preference={kinds['preference']} env={kinds['env']}"
                  + (" · cached" if dt == 0.0 else "")
                  + (f" · error={err}" if err else ""))
            for b in bad[:3]:
                print(f"    ⚠ {b}")

    seen, rows = set(), []
    for i, ids, got, bad, dt, err, src in sorted(results):
        for r in got:
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    counts = write_outputs(d, items, rows, first_name)
    missing = [it["id"] for it in items if it["id"] not in seen]
    print(f"[triage] wall {time.time()-t0:.1f}s · classified={len(rows)}/{len(items)} · "
          f"general={counts['general']} preference={counts['preference']} env={counts['env']} "
          f"→ {out_path}")
    print(f"[triage] 分流：{counts['general']} 条进 S2（judge-input-bodies.routed.json）· "
          f"{counts['preference'] + counts['env']} 条不进包（triage-summary.json 的 memory_queue）")
    if missing:
        print(f"[triage] ⚠ 未分类 {len(missing)} 条：{missing[:12]}{' …' if len(missing) > 12 else ''}")
        print("[triage]   重跑同一条命令会整批重判；只补缺口时把缺口单列成一个小 --limit 批次")
    return 0 if not missing else 1


def _self_test() -> int:
    """离线自检：三份产物的形状与分流口径（不调模型）。"""
    import tempfile
    fails = []
    items = [dict(id="P1", skill="a", skill_md="/x/a/SKILL.md", body="…"),
             dict(id="P2", skill="b", skill_md="/x/b/SKILL.md", body="…"),
             dict(id="P3", skill="c", skill_md="/x/c/SKILL.md", body="…")]
    rows = [dict(id="P1", kind="general", reusable=True, why="", evidence="e", confidence=.9, memory=None),
            dict(id="P2", kind="env", reusable=False, why="", evidence="e", confidence=.8,
                 memory={"title": "t", "text": "x"}),
            dict(id="P3", kind="preference", reusable=False, why="", evidence="e", confidence=.7,
                 memory={"title": "t2", "text": "y"})]
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        counts = write_outputs(d, items, rows, "stub")
        if counts != {"general": 1, "preference": 1, "env": 1}:
            fails.append(f"counts={counts}")
        routed = json.loads((d / "judge-input-bodies.routed.json").read_text())
        if [r["id"] for r in routed] != ["P1"]:
            fails.append(f"routed={[r['id'] for r in routed]}（只该留 general）")
        summ = json.loads((d / "triage-summary.json").read_text())
        if summ["general_ids"] != ["P1"] or [q["id"] for q in summ["memory_queue"]] != ["P2", "P3"]:
            fails.append(f"summary={summ}")
        if any(not q["memory"] for q in summ["memory_queue"]):
            fails.append("memory_queue 里有行没带 memory 载荷")
        if len((d / "triage-stub.jsonl").read_text().strip().splitlines()) != 3:
            fails.append("triage-<backend>.jsonl 行数不对")
    if fails:
        for f in fails:
            print(f"[triage] ✗ {f}")
        return 1
    print("[triage] self-test OK")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    raise SystemExit(main())
