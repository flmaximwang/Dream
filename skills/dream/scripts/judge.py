#!/usr/bin/env python3
"""dream v3 · S2 judge: chunk judge-input-bodies.json -> one model call per chunk -> verdicts.jsonl.

Deterministic plumbing only (chunking / HTTP / JSONL assembly / coverage report); the *judgment* is the
model's. No judge subagents — v2 ran one classifier child per 8–12 rows, which was a whole
extra fan-out whose wall was set by the slowest child.

    python3 -B judge.py --dir <scan --out dir> [--limit 5] [--dry-run]

Model backends are pluggable via ~/.hermes/dream-judge.json (see judge_backends.py).
Non-streaming on purpose: a stalled stream is exactly the failure mode that killed v2's rounds.
Raw prompt + raw reply of every chunk are kept next to the verdicts (judge-request-<n>.json /
judge-response-<n>.json) — they hold the only copy of what the model was asked, so they are never deleted.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from judge_backends import load_config, judge_chunk_with_fallback  # noqa: E402


def dest_space(d: pathlib.Path) -> dict:
    """Load answer space from scan output files."""
    a = json.loads((d / "answer-space.json").read_text())
    packs = json.loads((d / "packs.json").read_text())
    return {
        "tool_dirs": a.get("tool_dirs") or [],
        "topic_dirs": a.get("topic_dirs") or [],
        "packs": packs,
    }


def chunk(items: list, budget: int) -> list:
    """Greedy byte-budget chunks; a single oversized item still gets its own chunk."""
    out, cur, size = [], [], 0
    for it in items:
        b = len(json.dumps(it, ensure_ascii=False))
        if cur and size + b > budget:
            out.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += b
    if cur:
        out.append(cur)
    return out


def parse_verdicts(verdicts: list, want: list, model: str) -> tuple:
    "-> (valid_verdicts, problems). Strict about shape; merge would reject bad rows anyway."
    got, bad = [], []
    for v in verdicts:
        if v.get("id") not in want:
            bad.append(f"unknown id {v.get('id')!r}")
            continue
        d = v.get("dest") or {}
        if d.get("kind") == "existing":
            if not (d.get("repo") and d.get("dest_skill") and d.get("action") in ("move", "merge", "strengthen")):
                bad.append(f"{v['id']}: bad existing dest {d}")
                continue
        elif d.get("kind") == "new":
            if d.get("axis") not in ("tool", "topic") or not d.get("topic") or not d.get("boundary"):
                bad.append(f"{v['id']}: bad new dest {d}")
                continue
        else:
            bad.append(f"{v['id']}: dest.kind={d.get('kind')!r}")
            continue
        if not str(v.get("evidence") or "").strip():
            bad.append(f"{v['id']}: 空 evidence（只凭 name/desc 的判法不算数）")
            continue
        if d.get("kind") == "existing" and d.get("dest_skill") != "__new__":
            cl = [c.strip() for c in (v.get("claims") or []) if isinstance(c, str) and 2 <= len(c.strip()) <= 120]
            if not cl:
                bad.append(f"{v['id']}: 落点=已有技能但缺 claims（S3 靠它 grep 出 file:line，不再派子代理）")
                continue
            v["claims"] = cl[:8]
        else:
            v.setdefault("claims", [])
        v.setdefault("source", "model")
        v["model"] = model
        v.setdefault("why", "")
        got.append(v)
    return got, bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="the --out dir from `scan`")
    ap.add_argument("--input", default="judge-input-bodies.json",
                    help="候选文件（默认 scan 冻结那份；S1.5 分类闸门跑过后指 judge-input-bodies.routed.json，"
                         "即只判 general 那一类）")
    ap.add_argument("--limit", type=int, default=0, help="judge only the first N candidates (smoke test)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true", help="print the chunk plan, call nothing")
    args = ap.parse_args()

    d = pathlib.Path(args.dir).expanduser()
    items = json.loads((d / args.input).read_text())
    answer_space = dest_space(d)

    # Load pluggable backends and chunk config from ~/.hermes/dream-judge.json
    backends, ccfg = load_config()
    chunk_bytes = ccfg["chunk_bytes"]
    workers = ccfg["workers"]
    key_file = pathlib.Path(ccfg["key_file"]).expanduser()

    if args.limit:
        items = items[:args.limit]
    chunks = chunk(items, chunk_bytes)
    first_name = backends[0]["name"] if backends else "unknown"
    out_path = d / f"verdicts-{first_name}.jsonl"
    print(f"[judge] backends={[b['name'] for b in backends]} candidates={len(items)} "
          f"chunks={len(chunks)} chunk_bytes={chunk_bytes} workers={workers} out={out_path}")
    for i, c in enumerate(chunks, 1):
        print(f"  chunk {i}: {len(c)} 条 / {len(json.dumps(c, ensure_ascii=False))} B")
    if args.dry_run:
        return 0

    t0 = time.time()

    def work(pair):
        i, c = pair
        ids = [it["id"] for it in c]
        # Resume: reuse a previous reply only when the prompt is byte-identical (same sha) AND it answered
        # every id — a re-worded prompt must never silently reuse the old answers.
        cached = None
        rp = d / f"judge-response-{i}.json"
        if rp.exists():
            try:
                doc = json.loads(rp.read_text())
                if doc.get("prompt_sha") == hashlib.sha256(
                        json.dumps(c, ensure_ascii=False).encode()).hexdigest() and not doc.get("error") and doc.get("verdicts"):
                    got, _ = parse_verdicts(doc["verdicts"], ids, first_name)
                    if len(got) >= len(ids):
                        cached = got
            except (json.JSONDecodeError, OSError):
                cached = None
        if cached is not None:
            (d / f"judge-verdicts-chunk-{i}.jsonl").write_text(
                "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in cached))
            return i, ids, cached, [], 0.0, None, first_name
        tt = time.time()
        # Try backends in order; first one that returns verdicts wins
        verdicts, source, err = judge_chunk_with_fallback(
            c, answer_space, backends, key_file=key_file, timeout=args.timeout)
        model_label = source or first_name
        (d / f"judge-request-{i}.json").write_text(json.dumps(
            {"backends": [b["name"] for b in backends], "used": source,
             "chunk_ids": ids}, ensure_ascii=False))
        (d / f"judge-response-{i}.json").write_text(json.dumps(
            {"backends": [b["name"] for b in backends], "used": source,
             "prompt_sha": hashlib.sha256(json.dumps(c, ensure_ascii=False).encode()).hexdigest(),
             "error": err, "verdicts": verdicts}, ensure_ascii=False))
        got, bad = parse_verdicts(verdicts, ids, model_label) if verdicts else ([], [err or "empty reply"])
        # Stamp actual source/model on each verdict
        for v in got:
            v["source"] = source or "unknown"
        # Land this chunk's verdicts immediately: a killed run must not lose the chunks it already paid for.
        (d / f"judge-verdicts-chunk-{i}.jsonl").write_text(
            "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in got))
        return i, ids, got, bad, time.time() - tt, err, source or "?"

    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for r in ex.map(work, list(enumerate(chunks, 1))):
            results.append(r)
            i, ids, got, bad, dt, err, src = r
            print(f"[judge] chunk {i}: {len(got)}/{len(ids)} 条判定 · {dt:.1f}s · backend={src}"
                  + (" · cached" if dt == 0.0 else "")
                  + (f" · error={err}" if err else ""))
            for b in bad[:3]:
                print(f"    ⚠ {b}")

    seen, rows = set(), []
    for i, ids, got, bad, dt, err, src in sorted(results):
        for v in got:
            if v["id"] not in seen:
                seen.add(v["id"])
                rows.append(v)
    out_path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in rows))
    missing = [it["id"] for it in items if it["id"] not in seen]
    print(f"[judge] wall {time.time()-t0:.1f}s · verdicts={len(rows)}/{len(items)} → {out_path}")
    if missing:
        print(f"[judge] ⚠ 未判定 {len(missing)} 条：{missing[:12]}{' …' if len(missing) > 12 else ''}")
        print("[judge]   重跑同一条命令会整批重判；只补缺口时把缺口单列成一个小 --limit 批次")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
