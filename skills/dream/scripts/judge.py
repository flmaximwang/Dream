#!/usr/bin/env python3
"""dream v3 · S2 judge: chunk judge-input-bodies.json -> one deepseek-flash call per chunk -> verdicts.jsonl.

Deterministic plumbing only (chunking / HTTP / JSONL assembly / coverage report); the *judgment* is the
model's. No JEV probe, no judge subagents — v2 ran one classifier child per 8–12 rows, which was a whole
extra fan-out whose wall was set by the slowest child.

    python3 -B judge.py --dir <scan --out dir> [--limit 5] [--dry-run]

Non-streaming on purpose: a stalled stream is exactly the failure mode that killed v2's rounds.
Raw prompt + raw reply of every chunk are kept next to the verdicts (judge-request-<n>.json /
judge-response-<n>.json) — they hold the only copy of what the model was asked, so they are never deleted.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME") or pathlib.Path.home() / ".hermes")
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

RULES = """你是 dream 批次（把「没有仓库归属」的本地技能收编进技能包仓库）的**判官**。逐条判定落点。

硬口径：
1. **没有「留本地」这个答案**：每条候选都必须有落点。判不出来请给 confidence=0 并写清缺哪条判据，不要编。
2. **第一步先答轴**（只出现在 kind=new 里）：这条技能的**主语**是「某个软件/工具」还是「一类任务/学科/项目」。
3. 落点=已有目录时必须**回一条正文证据**（原文短句，30–120 字符）。只凭 name/description 下结论的会被退回。
4. `dest_skill` 填 `__new__`（表示在这条目录下新增一个技能）；`action` 三选一：move（整条迁进包）/
   merge（内容并进 dest_skill 指的已有技能）/ strengthen（两边各加界限声明）。
5. 拿不准（两轴相邻、或与已有技能高度重叠）时：照常给落点，把疑问写进 why，confidence 给低值。
6. **落点=已有技能**（`dest_skill` ≠ `__new__`，即 action=merge/strengthen）的行还要回 `claims`：
   从候选正文里**逐字抄** 3–8 条短串（表头 / 命令旗标 / 源路径 / 实测数字），是「若接收方已覆盖就该在那儿出现」的东西。
   这些串会被脚本拿去做 `grep -n`，命中与否就是 already-there / net-new 的证据 —— 抄错一个字等于判错。

输出格式：**只输出 JSONL**，一条候选一行，不要解释、不要 markdown 代码围栏。每行形状：
{"id":"P12","dest":{"kind":"existing","repo":"AgentSkill-UsingPyMOL","dest_skill":"__new__","action":"move"},"why":"…","evidence":"正文：「…」","confidence":0.87}
{"id":"P23","dest":{"kind":"existing","repo":"AgentSkill-UsingGit","dest_skill":"investigate-git-history","action":"merge"},"claims":["git log -S","git blame -w"],"why":"…","evidence":"…","confidence":0.6}
{"id":"P31","dest":{"kind":"new","axis":"topic","topic":"RunningLocalBatches","personal":true,"boundary":"不吃常驻服务与 cron"},"why":"…","evidence":"…","confidence":0.8}
"""


def read_key(key_file: pathlib.Path) -> str:
    for line in key_file.read_text().splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"\'')
    sys.exit("no DEEPSEEK_API_KEY in " + str(key_file))


def dest_space(d: pathlib.Path) -> str:
    """Answer space = the 290 dir names + each non-empty repo's existing skill list.

    The skill list matters: `merge`/`strengthen` must name a skill that really exists in that repo —
    a plausible-sounding name that isn't there is rejected (exit 4), and that is how the first run lost
    10 rows (judge invented `relayer-clc-classification-paths` etc. inside AgentSkill-ObsidianManagement).
    """
    a = json.loads((d / "answer-space.json").read_text())
    tool = a.get("tool_dirs") or []
    topic = a.get("topic_dirs") or []
    out = ["落点空间（`repo` 只能从这里挑；挑不到就 kind=new 造新包）：",
           f"- 工具轴（{len(tool)} 个）：{', '.join(tool)}",
           f"- 主题轴（{len(topic)} 个）：{', '.join(topic)}",
           "",
           "各目录的现有技能（`action=merge|strengthen` 时 `dest_skill` **必须是该目录下面列出的名字之一**；",
           "目录里没有合适的就退回 `action=move` + `dest_skill=\"__new__\"`。写一个不存在的名字会被 merge 拒收）："]
    packs = json.loads((d / "packs.json").read_text())
    for p in packs:
        if p.get("skills"):
            out.append(f"- {p['repo']}: {', '.join(p['skills'])}")
    return "\n".join(out) + "\n"


def item_block(it: dict) -> str:
    refs = it.get("refs") or []
    head = (f"### {it['id']} · skill={it['skill']} · category={it.get('category') or '(无类目)'}"
            f" · refs={refs if refs else '无'}\n"
            f"description: {it.get('description') or ''}\n"
            f"--- SKILL.md 全文 ---\n{it.get('body') or ''}\n")
    return head


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


def call_api(base_url: str, key: str, model: str, prompt: str, timeout: int) -> str:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "stream": False, "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode())
    return body["choices"][0]["message"]["content"]


def call_with_retry(base_url: str, key: str, model: str, prompt: str, timeout: int,
                    attempts: int = 3) -> tuple:
    """-> (text, error). Big prompts get `Connection reset by peer` from the endpoint now and then
    (实测 3 块 ~140KB 的请求同时被 reset)；重试比让整块判不完便宜得多。"""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return call_api(base_url, key, model, prompt, timeout), None
        except Exception as e:                             # noqa: BLE001
            last = e
            if attempt < attempts:
                time.sleep(5 * attempt)
    return "", f"{type(last).__name__}: {last}"


def parse_verdicts(raw: str, want: list, model: str) -> tuple:
    """-> (verdicts, problems). Strict about shape; merge would reject bad rows anyway."""
    text = re.sub(r"^\s*```(?:jsonl?)?|```\s*$", "", raw.strip(), flags=re.M)
    got, bad = [], []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            v = json.loads(line)
        except json.JSONDecodeError as e:
            bad.append(f"not json: {line[:80]} ({e})")
            continue
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
        v.setdefault("source", "deepseek")
        v["model"] = model
        v.setdefault("why", "")
        got.append(v)
    return got, bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="the --out dir from `scan`")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL)
    ap.add_argument("--key-file", default=str(HERMES_HOME / ".env"))
    ap.add_argument("--chunk-bytes", type=int, default=200_000, help="≈ chars of candidate bodies per call")
    ap.add_argument("--workers", type=int, default=3, help="parallel chunk calls")
    ap.add_argument("--limit", type=int, default=0, help="judge only the first N candidates (smoke test)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true", help="print the chunk plan, call nothing")
    args = ap.parse_args()

    d = pathlib.Path(args.dir).expanduser()
    items = json.loads((d / "judge-input-bodies.json").read_text())
    space = dest_space(d)
    if args.limit:
        items = items[:args.limit]
    chunks = chunk(items, args.chunk_bytes)
    out_path = d / f"verdicts-{args.model}.jsonl"
    print(f"[judge] model={args.model} candidates={len(items)} chunks={len(chunks)} "
          f"chunk_bytes={args.chunk_bytes} workers={args.workers} out={out_path}")
    for i, c in enumerate(chunks, 1):
        print(f"  chunk {i}: {len(c)} 条 / {len(json.dumps(c, ensure_ascii=False))} B")
    if args.dry_run:
        return 0

    key = read_key(pathlib.Path(args.key_file))
    t0 = time.time()

    def work(pair):
        i, c = pair
        ids = [it["id"] for it in c]
        prompt = (RULES + "\n" + space + "\n待判候选（逐条判，输出条数必须等于 " + str(len(c)) + "）：\n\n"
                  + "\n".join(item_block(it) for it in c))
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        # Resume: reuse a previous reply only when the prompt is byte-identical (same sha) AND it answered
        # every id — a re-worded prompt must never silently reuse the old answers.
        cached = None
        rp = d / f"judge-response-{i}.json"
        if rp.exists():
            try:
                doc = json.loads(rp.read_text())
                if doc.get("prompt_sha") == sha and not doc.get("error") and doc.get("raw"):
                    got, _ = parse_verdicts(doc["raw"], ids, args.model)
                    if len(got) >= len(ids):
                        cached = got
            except (json.JSONDecodeError, OSError):
                cached = None
        if cached is not None:
            (d / f"judge-verdicts-chunk-{i}.jsonl").write_text(
                "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in cached))
            return i, ids, cached, [], 0.0, None
        tt = time.time()
        raw, err = call_with_retry(args.base_url, key, args.model, prompt, args.timeout)
        (d / f"judge-request-{i}.json").write_text(json.dumps({"model": args.model, "prompt": prompt},
                                                             ensure_ascii=False))
        (d / f"judge-response-{i}.json").write_text(json.dumps(
            {"model": args.model, "prompt_sha": sha, "error": err, "raw": raw}, ensure_ascii=False))
        got, bad = parse_verdicts(raw, ids, args.model) if raw else ([], [err or "empty reply"])
        # Land this chunk's verdicts immediately: a killed run must not lose the chunks it already paid for.
        (d / f"judge-verdicts-chunk-{i}.jsonl").write_text(
            "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in got))
        return i, ids, got, bad, time.time() - tt, err

    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for r in ex.map(work, list(enumerate(chunks, 1))):
            results.append(r)
            i, ids, got, bad, dt, err = r
            print(f"[judge] chunk {i}: {len(got)}/{len(ids)} 条判定 · {dt:.1f}s"
                  + (" · cached" if dt == 0.0 else "")
                  + (f" · error={err}" if err else ""))
            for b in bad[:3]:
                print(f"    ⚠ {b}")

    seen, rows = set(), []
    for i, ids, got, bad, dt, err in sorted(results):
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
