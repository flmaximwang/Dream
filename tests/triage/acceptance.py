#!/usr/bin/env python3
"""S1.5 分类闸门的验收：一批 fixture 技能 → 跑 triage.py → 核它确实把两类分开。

    python3 -B tests/triage/acceptance.py [--keep] [--limit N]

fixture 里就是用户点名的两类，加一条偏好：

- **程序文档**（通用解决方案，该进包）：`parse-csv-with-pandas` / `git-bisect-workflow`
- **本机现状记录**（不该进包、该压缩写记忆）：`jev-unreachable-here`（JEV 不可达）/ `local-python-interpreters`
- **用户偏好**（同样不进包）：`console-language-preference`

判据（全绿才 exit 0）：

1. 每条 fixture 判出的 kind = `expected.json` 里的期望；
2. 两类确实被分开：既有一组 general、又有一组非 general（不是全判成同一类）；
3. `judge-input-bodies.routed.json` 里只剩 general 那几条（= S2 的输入）；
4. 非 general 的行都带上非空的 `memory{title,text}` 载荷，general 的行不带。

不带 `--limit` 时真调 `~/.hermes/dream-judge.json` 配的后端——那才是「判官确实能分出两类」的证据。
离线只验管道（不证明判官会分）：`python3 -B skills/dream/scripts/triage.py --self-test`。
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

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent.parent / "skills" / "dream"
FIX = HERE / "fixtures"


def read_fixture(md: pathlib.Path) -> tuple[str, str]:
    """-> (description, 全文)；description 从 frontmatter 里取一行，够 mimick scan 的题面。"""
    text = md.read_text(encoding="utf-8")
    m = re.search(r"^description:\s*(.*)$", text, re.M)
    return (m.group(1).strip().strip('"') if m else ""), text


def build_out(d: pathlib.Path) -> dict:
    """按 scan 的 `judge-input-bodies.json` 形状造一份候选表。-> {id: fixture 名}"""
    items, id2name = [], {}
    for i, p in enumerate(sorted(FIX.iterdir()), 1):
        md = p / "SKILL.md"
        if not md.is_file():
            continue
        desc, text = read_fixture(md)
        pid = f"P{i}"
        id2name[pid] = p.name
        items.append(dict(id=pid, skill=p.name, category="", dir=p.name,
                          state="local·local·mgr=no·ext=no", action="judge-destination",
                          comparators=[], no_category=True, description=desc, path=str(md.parent),
                          skill_md=str(md), refs=[], body_bytes=len(text.encode("utf-8")),
                          body_lines=len(text.splitlines()), body=text))
    (d / "judge-input-bodies.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    return id2name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true", help="留住产出目录（默认测完删掉）")
    ap.add_argument("--limit", type=int, default=0, help="只判前 N 条（烟测；不覆盖全部 fixture 时必挂）")
    args = ap.parse_args()

    expect = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))["expect"]
    out = pathlib.Path(tempfile.mkdtemp(prefix="triage-acceptance-"))
    id2name = build_out(out)
    cmd = [sys.executable, "-B", str(SKILL / "scripts" / "triage.py"), "--dir", str(out)]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    print(f"[accept] out={out}（--keep 可留住）\n[accept] $ {' '.join(cmd)}")
    rc = subprocess.run(cmd, text=True).returncode
    if rc != 0:
        print(f"[accept] ✗ triage.py 退出码 {rc}（有候选没判出）")
        return rc

    rows = {}
    for f in sorted(out.glob("triage-*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                row = json.loads(ln)
                rows[row["id"]] = row

    fails = []
    print(f"\n{'fixture':30} {'判出':11} {'期望':11} {'reusable':9} {'conf':6} {'memory':6} 证据")
    for pid, name in id2name.items():
        row = rows.get(pid) or {}
        got, exp, mem = row.get("kind", "(没判出)"), expect[name], row.get("memory")
        if got != exp:
            fails.append(f"{name}: 判出 {got!r} ≠ 期望 {exp!r}（why={row.get('why', '')[:60]}）")
        if exp != "general" and not mem:
            fails.append(f"{name}: 非 general 却没给 memory 载荷（它的去路是写记忆）")
        if exp == "general" and mem:
            fails.append(f"{name}: general 却带了 memory 载荷")
        print(f"{name:30} {got:11} {exp:11} {str(row.get('reusable')):9} "
              f"{str(row.get('confidence')):6} {'有' if mem else '—':6} {str(row.get('evidence', ''))[:40]}")

    gens = {p for p in id2name if rows.get(p, {}).get("reusable")}
    others = {p for p in id2name if p in rows and not rows[p]["reusable"]}
    if not gens or not others:
        fails.append(f"两类没分开：general={sorted(gens)} 非 general={sorted(others)}")

    routed = json.loads((out / "judge-input-bodies.routed.json").read_text(encoding="utf-8"))
    if {r["id"] for r in routed} != gens:
        fails.append(f"routed 不是 general 那一组：{sorted(r['id'] for r in routed)} vs {sorted(gens)}")
    summ = json.loads((out / "triage-summary.json").read_text(encoding="utf-8"))
    print(f"\n[accept] 分流：general={summ['counts']['general']} preference={summ['counts']['preference']} "
          f"env={summ['counts']['env']} · memory_queue={len(summ['memory_queue'])} "
          f"· routed={len(routed)}（= S2 的输入）")

    if not args.keep:
        shutil.rmtree(out, ignore_errors=True)
    else:
        print(f"[accept] 产出留在 {out}")

    if fails:
        for f in fails:
            print(f"[accept] ✗ {f}")
        print(f"[accept] FAIL（{len(fails)} 条）")
        return 1
    print(f"[accept] PASS — {len(id2name)} 条 fixture 全部判对，两类分开了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
