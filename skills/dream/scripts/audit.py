#!/usr/bin/env python3
"""dream v3 · S3 交叉审计：不再「一条重叠行一个只读子代理」，改成对判官给的 `claims` 逐条 grep。

v2 的 S3 每遇到一条「落点=已有技能」的行派一个子代理去读两边目录再逐条 grep —— 又是一次扇出，
wall 由最慢的孩子决定。v3 把这步拆成：**判官只负责抄串**（claims，逐字抄自候选正文），
**脚本负责证明**（grep 出接收侧 file:line）——证据是 grep 的真输出，不是模型的转述，LLM 调用数为 0。

    python3 -B audit.py --dir <scan --out 目录> [--limit N] [--json]

产出：`<dir>/audit.md`（一行一条 claim：命中给 `file:line`，没命中进 net-new）与 `audit.json`。
「全员未命中」的行会标 ⚠ —— 那通常说明落点选错了（接收方覆盖不了这条候选），不是净新增。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HITS_PER_CLAIM = 3


def grep(claim: str, target: pathlib.Path) -> list:
    """接收侧找这条 claim。→ ['<相对路径>:<行号>: <正文>', ...]"""
    if not target.exists():
        return []
    r = subprocess.run(["grep", "-rnF", "--", claim, str(target)], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        p, _, rest = line.partition(":")
        no, _, text = rest.partition(":")
        try:
            rel = str(pathlib.Path(p).relative_to(target.parent if target.is_file() else target))
        except ValueError:
            rel = p
        out.append(f"{rel}:{no}: {text.strip()[:90]}")
        if len(out) >= HITS_PER_CLAIM:
            break
    return out


def dest_dir(row: dict, packs: dict):
    """接收技能的目录 → (Path|None, 来源)。包 clone 里是 `skills/<类目>/<名字>/` 的分层布局，
    所以要递归找同名技能目录；找不到就退回已装副本，再找不到＝落点不存在（merge 必被拒）。"""
    pack = packs.get(row.get("repo")) or {}
    name = row.get("dest_skill")
    if pack.get("path"):
        root = pathlib.Path(pack["path"])
        hits = [p.parent for p in root.glob(f"skills/**/{name}/SKILL.md")]
        if hits:
            return hits[0], ("pack" if len(hits) == 1 else f"pack/{len(hits)} 处重名")
    inst = (pack.get("installed") or {}).get(name)
    if inst:
        p = pathlib.Path.home() / ".hermes/skills" / inst
        if (p / "SKILL.md").exists():
            return p, "installed"
    return None, "missing"


def load(d: pathlib.Path):
    verdicts = []
    for f in sorted(d.glob("verdicts-*.jsonl")):
        verdicts += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    resolved = {r["id"]: r for r in json.loads((d / "resolved.json").read_text())}
    packs = {p["repo"]: p for p in json.loads((d / "packs.json").read_text())}
    return verdicts, resolved, packs


def run(d: pathlib.Path, limit: int | None) -> int:
    t0 = time.time()
    verdicts, resolved, packs = load(d)
    rows = [v for v in verdicts
            if (v.get("dest") or {}).get("kind") == "existing"
            and (v.get("dest") or {}).get("dest_skill") not in (None, "__new__")]
    if limit:
        rows = rows[:limit]
    md = ["# S3 交叉审计（判官 claims × grep 接收侧）", "",
          f"重叠行 {len(rows)} 条 · 判官调用 0 次（claims 随 S2 的同一批返回）", ""]
    js, warn, missing = [], [], []
    for v in rows:
        dest = v["dest"]
        target, where = dest_dir(dest, packs)
        row = {"id": v["id"], "repo": dest["repo"], "dest_skill": dest["dest_skill"],
               "action": dest.get("action"), "confidence": v.get("confidence"),
               "target": str(target) if target else None, "where": where,
               "already_there": {}, "net_new": v.get("claims") or []}
        md.append(f"## {v['id']} → {dest['repo']}/…/{dest['dest_skill']}（{dest.get('action')} · conf {v.get('confidence')} · 接收侧 {where}）")
        md.append(f"证据：{v.get('evidence','')}")
        md.append("")
        if target is None:
            missing.append(v["id"])
            md.append(f"- ⚠ **落点不存在**：`{dest['dest_skill']}` 在 {dest['repo']} 里找不到技能目录 ⇒ `merge` 会被拒收\n")
            js.append(row)
            continue
        if not row["net_new"]:
            warn.append(f"{v['id']}: 判官没给 claims")
            md.append("- ⚠ 判官没给 claims，无法证明\n")
            js.append(row)
            continue
        for claim in row["net_new"]:
            hits = grep(claim, target)
            if hits:
                row["already_there"][claim] = hits
                row["net_new"] = [c for c in row["net_new"] if c != claim]
                md.append(f"- ✅ already there `{claim}` ⇒ {hits[0]}")
            else:
                md.append(f"- ➕ net-new `{claim}`（接收侧 0 命中）")
        if not row["already_there"]:
            if row["action"] == "merge":
                md.append("- 💬 全净新增（merge＝整段折进接收技能，这是正常形状）\n")
            else:
                warn.append(f"{v['id']}: {row['action']} 却一条都没命中 → 重叠说法没证据（接收方覆盖不了这条）")
                md.append(f"- ⚠ **{row['action']} 却全员未命中**：重叠说法没有证据，别照 merge 处理\n")
        else:
            md.append("")
        js.append(row)
    (d / "audit.md").write_text("\n".join(md))
    (d / "audit.json").write_text(json.dumps(js, ensure_ascii=False, indent=1))
    for m in missing:
        print(f"[audit] ✗ {m}: 落点技能在包内不存在 ⇒ merge/strengthen 会被拒")
    for w in warn:
        print("[audit] ⚠", w)
    wall = time.time() - t0
    claims_total = sum(len(r["already_there"]) + len(r["net_new"]) for r in js)
    print(f"[audit] 重叠行 {len(rows)} · claims {claims_total} · already-there {sum(len(r['already_there']) for r in js)} · "
          f"落点不存在 {len(missing)} · 零命中行 {len([r for r in js if r['target'] and not r['already_there']])} · "
          f"LLM 调用 0 · wall {wall:.1f}s → {d/'audit.md'}")
    return 0 if not missing else 1


def self_test() -> int:
    """最小自检：命中/未命中/markdown 转义（不碰真仓库）。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        sk = tmp / "skills" / "dest"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("# 说明\n\ngit log -S 查引入\n")
        assert grep("git log -S", sk), "应命中"
        assert not grep("git blame -w", sk), "不该命中"
        assert grep("git log -S", sk)[0].startswith("SKILL.md:"), grep("git log -S", sk)
        assert grep("x", tmp / "nope") == []
    finally:
        shutil.rmtree(tmp)
    print("[audit] self-test OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    return run(pathlib.Path(a.dir).expanduser(), a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
