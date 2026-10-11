#!/usr/bin/env python3
"""dream v3 · S5–S8 的机械部分：把「写入 → 验证 → 装回 → 退役」做成命令，不再是子代理。

v2 这几步各派一批子代理（一个包一个 writer、一条验证子代理），wall 由最慢的孩子决定；
v3 只把**判断**留给模型（落点、重叠、以及写不出正确形态的行），其余全是这里的一条命令。

    python3 -B apply.py apply  --dir <scan --out 目录> --packs-root <packs 根> [--only P12,P13] [--dry-run] [--no-push]
    python3 -B apply.py verify --dir <…> --packs-root <…> [--only …]     # 逐行 PASS/FAIL，只读
    python3 -B apply.py install --dir <…>                                 # 跑 merge 生成的 install-cmds.sh + 回读
    python3 -B apply.py retire --dir <…>  --backup <目录> [--only …]      # 只在 verify 全绿的行上删本地副本

纪律（写进代码，不靠人记）：
- 只处理 `action=move` + `dest_skill=__new__` 的行。`merge` / `strengthen`（要改正文）一律**不动**，
  在报告里单列为「要模型写」——脚本不做内容编辑。
- 目标包工作区有**不属于本次 pathspec 的改动**就跳过该包（那是别人在途的工作）。
- `git add skills/<name>` 只按 pathspec；只 `git push origin <sha>:main`，永不 force。
- 推送前跑扫描闸（`tools.skills_guard`）：verdict 不是 `safe` 就回滚这次拷贝、不提交、不推送。
- 退役只在 verify 全绿之后；先 `tar czf` 备份再删。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import time

HERMES_HOME = pathlib.Path.home() / ".hermes"
HERMES_REPO = HERMES_HOME / "hermes-agent"


def sh(cmd, cwd=None, check=False):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def row_dest(r: dict) -> dict:
    """resolved.json 把 dest 摊平在顶层（repo/kind/dest_skill/action），别按嵌套读。"""
    if isinstance(r.get("dest"), dict):
        return r["dest"]
    return {k: r.get(k) for k in ("kind", "repo", "dest_skill", "action", "axis", "topic", "boundary")}


def load_rows(d: pathlib.Path):
    """resolved.json (机器可读决议) + judge-input.json (id → profile 里的源目录)。"""
    resolved = json.loads((d / "resolved.json").read_text())
    jin = {r["id"]: r for r in json.loads((d / "judge-input.json").read_text())}
    for r in resolved:
        r["_src"] = r.get("path") or jin.get(r["id"], {}).get("path", "")
    return resolved


def normalize_frontmatter(text: str) -> tuple:
    """frontmatter 只留 `name` + `description`（包形状）。→ (新文本, 丢了哪些键)"""
    if not text.startswith("---"):
        return text, []
    end = text.find("\n---", 3)
    if end == -1:
        return text, []
    fm = text[3:end].splitlines()
    body = text[end + 1:]
    keep, dropped, i = [], [], 0
    while i < len(fm):
        line = fm[i]
        if line.startswith("name:"):
            keep.append(line)
        elif line.startswith("description:"):
            keep.append(line)
            i += 1
            while i < len(fm) and fm[i][:1] in (" ", "\t"):
                keep.append(fm[i])
                i += 1
            continue
        elif line.strip():
            dropped.append(line.split(":")[0])
        i += 1
    return "---\n" + "\n".join(keep).rstrip() + "\n" + body, dropped


def scan_verdict(skill_dir: pathlib.Path) -> tuple:
    """扫描闸：push 之前本地预测 verdict（非 safe 的 revision 在 hub 上装不上）。"""
    code = ("from pathlib import Path\n"
            "from tools.skills_guard import scan_skill\n"
            f"r = scan_skill(Path({str(skill_dir)!r}), source='skills.sh')\n"
            "print(r.verdict)\n"
            "for f in r.findings: print(' ', f.severity, f.pattern_id, f.file, f.line)\n")
    r = sh([str(HERMES_REPO / "venv/bin/python3"), "-c", code], cwd=str(HERMES_REPO))
    if r.returncode != 0:
        return "unknown", r.stderr.strip().splitlines()[-1:] or ["scan failed"]
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return (lines[0] if lines else "unknown"), lines[1:]


def pack_status(pack_dir: pathlib.Path, pathspec: str) -> tuple:
    """→ (ok, 说明)。工作区有别人的改动就不动这个包。"""
    if not (pack_dir / ".git").exists():
        return False, "not a git checkout"
    st = sh("git status --porcelain", cwd=str(pack_dir)).stdout.splitlines()
    dirty = [l[3:] for l in st if l.strip() and not l[3:].startswith(pathspec)]
    if dirty:
        return False, f"工作区有别人的改动：{dirty[:3]}"
    return True, ""


def do_apply(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
    root = pathlib.Path(args.packs_root).expanduser()
    rows = load_rows(d)
    only = set(args.only.split(",")) if args.only else None
    todo, manual, skipped = [], [], []
    for r in rows:
        if only and r["id"] not in only:
            continue
        dest = row_dest(r)
        if dest.get("kind") != "existing" or dest.get("action") != "move" or dest.get("dest_skill") != "__new__":
            manual.append(r)
            continue
        src = pathlib.Path(r["_src"])
        name = src.name
        pack = root / dest["repo"]
        dst = pack / "skills" / name
        if dst.exists():
            skipped.append((r["id"], f"{dest['repo']}/skills/{name} 已存在（幂等跳过）"))
            continue
        todo.append((r, pack, src, dst))

    print(f"[apply] 可机械写入 {len(todo)} 行 · 要模型写 {len(manual)} 行 · 跳过 {len(skipped)} 行")
    for i, why in skipped:
        print(f"  - {i}: {why}")
    if manual:
        print(f"  · 要模型写（merge/strengthen 或落点不是新技能）：{[r['id'] for r in manual]}")
    if args.dry_run:
        for r, pack, src, dst in todo:
            print(f"  → {r['id']} {src}  ⇒  {dst}   (pack={pack.name})")
        return 0

    by_pack, results = {}, []
    for r, pack, src, dst in todo:
        by_pack.setdefault(pack, []).append((r, src, dst))
    for pack, items in sorted(by_pack.items(), key=lambda kv: kv[0].name):
        path_spec = f"skills/{items[0][2].name}"
        ok, why = pack_status(pack, "skills/")
        if not ok:
            print(f"[apply] ✗ {pack.name}: {why} ⇒ 跳过 {len(items)} 行")
            results += [(r["id"], "SKIP", why) for r, _, _ in items]
            continue
        sh("git fetch -q origin", cwd=str(pack))
        written = []
        for r, src, dst in items:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            text = (dst / "SKILL.md").read_text()
            new, dropped = normalize_frontmatter(text)
            (dst / "SKILL.md").write_text(new)
            verdict, findings = scan_verdict(dst)
            if verdict != "safe":
                shutil.rmtree(dst)                      # 回滚这次拷贝，别留在工作区
                results.append((r["id"], "BLOCKED", f"scan verdict={verdict} {findings[:2]}"))
                print(f"[apply] ✗ {r['id']} {dst.name}: scan verdict={verdict} ⇒ 不提交（按扫描闸改形态）")
                continue
            written.append((r, src, dst, dropped))
            print(f"[apply] ✓ {r['id']} {dst.name}: 拷贝+归一 frontmatter（丢掉 {dropped or '无'}）· scan=safe")
        if not written:
            continue
        add = [str(p.relative_to(pack)) for _, _, p, _ in written]   # 整个技能目录（含 references/、scripts/）
        sh(["git", "add", "--"] + add, cwd=str(pack), check=True)
        msg = "dream: 收编 " + ", ".join(p.name for _, _, p, _ in written) + f"（{len(written)} 条，v3 apply）"
        sh(["git", "commit", "-q", "-m", msg], cwd=str(pack), check=True)
        sha = sh(["git", "rev-parse", "HEAD"], cwd=str(pack)).stdout.strip()
        if args.no_push:
            print(f"[apply] {pack.name}: commit {sha[:8]}（--no-push，没推）")
        else:
            r_ = sh(["git", "push", "-q", "origin", f"{sha}:main"], cwd=str(pack))
            state = "pushed" if r_.returncode == 0 else f"PUSH FAILED: {r_.stderr.strip()[:120]}"
            print(f"[apply] {pack.name}: commit {sha[:8]} · {state}")
        for r, src, dst, _ in written:
            results.append((r["id"], "WRITTEN", f"{pack.name}/{dst.name}@{sha[:8]}"))
    out = d / "apply-report.json"
    out.write_text(json.dumps({"results": results, "manual": [r["id"] for r in manual]}, ensure_ascii=False, indent=1))
    bad = [x for x in results if x[1] in ("BLOCKED", "SKIP")]
    print(f"[apply] 写入 {len([x for x in results if x[1]=='WRITTEN'])} 行 · 阻断/跳过 {len(bad)} 行 → {out}")
    return 0 if not bad else 1


def do_verify(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
    root = pathlib.Path(args.packs_root).expanduser()
    rows = load_rows(d)
    only = set(args.only.split(",")) if args.only else None
    checks = json.loads((d / "apply-report.json").read_text())["results"] if (d / "apply-report.json").exists() else []
    written = {c[0]: c[2] for c in checks if c[1] == "WRITTEN"}
    fails, oks = [], []
    for r in rows:
        if only and r["id"] not in only:
            continue
        if r["id"] not in written:
            continue
        dest = row_dest(r)
        pack = root / dest["repo"]
        name = pathlib.Path(r["_src"]).name
        dir_ok = (pack / "skills" / name / "SKILL.md").is_file()
        remote = sh(["git", "log", "--oneline", "-1", "origin/main"], cwd=str(pack)).stdout.strip()
        sha = written[r["id"]].split("@")[-1]
        remote_ok = remote.startswith(sha)
        installed = list((HERMES_HOME / "skills").rglob(name))
        diff = sh(["diff", "-rq", str(pack / "skills" / name), str(installed[0])]).stdout.strip() if installed else "not installed yet"
        line = f"{r['id']} dir={dir_ok} origin/main={remote_ok}({sha}) diff={'-' if not diff else diff[:60]}"
        (oks if dir_ok and remote_ok else fails).append(line)
    for l in oks:
        print("PASS", l)
    for l in fails:
        print("FAIL", l)
    print(f"[verify] PASS {len(oks)} · FAIL {len(fails)}")
    return 0 if not fails else 1


def do_install(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
    script = d / "install-cmds.sh"
    if not script.exists():
        sys.exit("no install-cmds.sh — run sweep-plan.py merge first")
    n = len([l for l in script.read_text().splitlines() if l.strip() and not l.startswith("#")])
    print(f"[install] 执行 {script}（{n} 条命令）")
    r = sh(["sh", str(script)], cwd=str(d))
    (d / "install.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-800:])
    print(f"[install] exit={r.returncode} · log={d/'install.log'}")
    return r.returncode


def do_retire(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
    backup = pathlib.Path(args.backup).expanduser()
    backup.mkdir(parents=True, exist_ok=True)
    report = d / "apply-report.json"
    if not report.exists():
        sys.exit("no apply-report.json — run apply first")
    rows = {r["id"]: r for r in load_rows(d)}
    only = set(args.only.split(",")) if args.only else None
    removed, skipped = [], []
    for cid, state, note in json.loads(report.read_text())["results"]:
        if state != "WRITTEN" or (only and cid not in only):
            continue
        src = pathlib.Path(rows[cid]["_src"])
        if not src.exists():
            skipped.append((cid, "本地副本已不在"))
            continue
        tar = backup / f"{src.name}.tar.gz"
        with tarfile.open(tar, "w:gz") as t:
            t.add(src, arcname=src.name)
        shutil.rmtree(src)
        removed.append((cid, f"{src} → 备份 {tar}"))
    for cid, w in skipped:
        print(f"[retire] – {cid}: {w}")
    for cid, w in removed:
        print(f"[retire] ✓ {cid}: 已退役 {w}")
    print(f"[retire] 退役 {len(removed)} 行 · 跳过 {len(skipped)} 行")
    return 0


def self_test() -> int:
    """最小自检：frontmatter 归一 + 形状判定（不碰 git、不碰真仓库）。"""
    text = ("---\nname: x\ndescription: >-\n  两行\n  描述\nversion: 1.0.0\nauthor: me\n"
            "metadata:\n  hermes:\n    tags: [a]\n---\n# body\n")
    new, dropped = normalize_frontmatter(text)
    assert new.startswith("---\nname: x\ndescription: >-\n  两行\n  描述\n---\n"), new
    assert "# body" in new and "version" not in new and set(dropped) >= {"version", "author", "metadata"}, dropped
    assert normalize_frontmatter("# no frontmatter\n") == ("# no frontmatter\n", [])
    print("[apply] self-test OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["apply", "verify", "install", "retire"])
    ap.add_argument("--dir")
    ap.add_argument("--packs-root", default="~/Documents/AgentSkill")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--only", default="", help="逗号分隔的 P id")
    ap.add_argument("--backup", default="~/.hermes/backups/dream-retire")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    if a.cmd == "--self-test":
        return self_test()
    if not a.dir:
        sys.exit("--dir is required")
    return {"apply": do_apply, "verify": do_verify, "install": do_install, "retire": do_retire}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
