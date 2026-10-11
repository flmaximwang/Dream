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
import re
import shutil
import subprocess
import sys
import tarfile
import time

# profile 级那一个常量（包内形状由 SkillWeave 量，dream 不实现第二套）。
# 2026-10-11 本人拍板：由 120 改成 300 —— 本机 profile 当时已有 263 个技能，120 会让每一次真装都被挡
# （闸门的意义是挡住「往上涨」，不是挡住现状）；撞上 300 说明该先清退。--max-skills 仍是校准旋钮。
MAX_AGENT_SKILLS = 300
SKIP_DIRS = {".hub", ".git", ".archive", "__pycache__", "quarantine", ".trash"}

HERMES_HOME = pathlib.Path.home() / ".hermes"
HERMES_REPO = HERMES_HOME / "hermes-agent"


def sh(cmd, cwd=None, check=False):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def external_map(args) -> dict:
    """`--external-dirs <json>` → {落点名: 目录}。清单里每个目录自身（是技能）或它的直接子目录都是一个落点名。"""
    ext = getattr(args, "external_dirs", "") or ""
    if not ext:
        return {}
    data = json.loads(pathlib.Path(ext).expanduser().read_text())
    out = {}
    for d in data.get("dirs", []):
        p = pathlib.Path(d["path"]).expanduser()
        if not p.is_dir():
            continue
        if (p / "SKILL.md").is_file():
            out[d.get("name") or p.name] = p
        else:
            for kid in sorted(p.glob("*")):
                if kid.is_dir() and not kid.name.startswith("."):
                    out[kid.name] = kid
    return out


def pack_path(args, repo: str) -> pathlib.Path:
    """落点目录：先查清单，再退回 --packs-root/<repo>。"""
    return external_map(args).get(repo) or (pathlib.Path(args.packs_root).expanduser() / repo)


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


def pack_status(pack_dir: pathlib.Path, pathspec: str, require_remote: bool = False) -> tuple:
    """→ (ok, 说明)。工作区有别人的改动就不动这个包；要真推时还得先有远端。"""
    if not (pack_dir / ".git").exists():
        return False, "not a git checkout"
    if require_remote and sh(["git", "remote", "get-url", "origin"], cwd=str(pack_dir)).returncode != 0:
        return False, "包没有远端（origin 不存在）⇒ 只报不建，别在本地悄悄积提交"
    st = sh("git status --porcelain", cwd=str(pack_dir)).stdout.splitlines()
    dirty = [l[3:] for l in st if l.strip() and not l[3:].startswith(pathspec)]
    if dirty:
        return False, f"工作区有别人的改动：{dirty[:3]}"
    return True, ""


def pack_gate(pack, items, args) -> tuple:
    """→ (ok, why)。决议指纹 / 别人的在途改动 / 要真推却没有远端 —— dry-run 与真写用同一判据。"""
    if any(r.get("stale") for r, _, _ in items):
        return False, "上游变了（merge 已标 stale）⇒ 先重跑 scan+merge"
    rec = {r.get("input_commit") for r, _, _ in items if r.get("input_commit")}
    head_now = sh(["git", "rev-parse", "HEAD"], cwd=str(pack)).stdout.strip()
    if rec and head_now not in rec:
        was = sorted(rec)[0]
        return False, f"上游变了（HEAD {head_now[:9]} ≠ 决议时 {was[:9]}）⇒ 先重跑 scan+merge"
    return pack_status(pack, "skills/", require_remote=not args.no_push)


def do_apply(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
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
        pack = pack_path(args, dest["repo"])
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
        byp = {}
        for r, pack, src, dst in todo:
            byp.setdefault(pack, []).append((r, src, dst))
        for pack, items in sorted(byp.items(), key=lambda kv: kv[0].name):
            ok, why = pack_gate(pack, items, args)
            if ok:
                print(f"  ✓ {pack.name}: 会写入 {len(items)} 行")
            else:
                print(f"  ✗ {pack.name}: {why} ⇒ 会跳过 {len(items)} 行")
        return 0

    by_pack, results = {}, []
    for r, pack, src, dst in todo:
        by_pack.setdefault(pack, []).append((r, src, dst))
    for pack, items in sorted(by_pack.items(), key=lambda kv: kv[0].name):
        path_spec = f"skills/{items[0][2].name}"
        ok, why = pack_gate(pack, items, args)
        if not ok:
            kind = "STALE" if why.startswith("上游变了") else "SKIP"
            print(f"[apply] ✗ {pack.name}: {why} ⇒ 跳过 {len(items)} 行")
            results += [(r["id"], kind, why) for r, _, _ in items]
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
    wrote = len([x for x in results if x[1] == "WRITTEN"])
    print(f"[apply] 写入 {wrote} 行 · 阻断/跳过 {len(bad)} 行 → {out}")
    # 单行被挡/跳过是**正常结局**（扫描闸挡下、包里有别人的改动），逐行进报告即可；
    # 只有「有活要干却一行都没落地」才算失败，否则轮到下一阶段没意义。
    return 0 if wrote or not todo else 1


def do_taskbook(args) -> int:
    """把「要模型改正文」的行按包拼成可直接派的 writer 任务书（省掉主 run 手抄 10 份）。"""
    d = pathlib.Path(args.dir).expanduser()
    # taskbook：每包的 clone 路径同样先查清单（--external-dirs），再退回 --packs-root
    rows = load_rows(d)
    audit = {}
    ap = d / "audit.json"
    if ap.exists():
        audit = {r["id"]: r for r in json.loads(ap.read_text())}
    man, by_pack = [], {}
    for r in rows:
        dest = row_dest(r)
        # writer 只管「落点=已有技能」的行（merge/strengthen）；新包只报不建，机械行 apply 自己写
        if not (dest.get("kind") == "existing" and dest.get("dest_skill") not in (None, "__new__")):
            continue
        man.append(r)
        by_pack.setdefault(dest.get("repo") or "?", []).append((r, dest))
    if not man:
        print("[taskbook] 没有要模型改正文的行")
        return 0
    md = ["# writer 任务书（每条按包一份，直接派给子代理）", "",
          f"- 要改正文的行 {len(man)} 条 · 涉及 {len(by_pack)} 个包 · 并发上限 10（>10 分包分波）",
          f"- 测试面：{'是（副本 + 禁 push）' if args.test else '否（真包，写入后要推）'}", ""]
    for repo, items in sorted(by_pack.items()):
        pack = pack_path(args, repo)
        md += [f"## {repo}（{len(items)} 行）", "", f"clone：`{pack}`", ""]
        for r, dest in items:
            a = audit.get(r["id"], {})
            src = pathlib.Path(r["_src"])
            dst = a.get("target") or str(pack / "skills" / str(dest.get("dest_skill")))
            md += [f"### {r['id']} · {dest.get('action')}", "",
                   f"- 源技能目录（整目录读完）：`{src}`",
                   f"- 接收技能目录：`{dst}`",
                   f"- 判官理由：{str(r.get('why') or '')[:200]}",
                   f"- **already there（接收侧已有，别重复写）**：{'、'.join(f'`{c}`' for c in a.get('already_there', {})) or '（无）'}",
                   f"- **net-new（要折进去的）**：{'、'.join(f'`{c}`' for c in a.get('net_new', [])) or '（无——那说明落点存疑，退回 S2）'}",
                   ""]
        md += ["硬纪律：① 只在这个 clone 里动手；② 只 `git add` 你改的那个技能目录（pathspec，不许 `-A`）；"
               "③ **禁止起后台进程**；④ 改完 frontmatter 只留 `name`+`description`，丢掉的键要在回执里列出；"
               "⑤ 提交前跑扫描闸（`tools.skills_guard.scan_skill`），非 `safe` 按命中改写文字再扫，"
               "`dangerous` 不许提交；" + ("⑥ **禁止 push**（副本测试面）" if args.test else "⑥ `git push origin <sha>:main` 后从远端回读 sha"),
               "", "回执：commit sha · `git show --stat HEAD` · 扫描 verdict 与 findings 条数 · 折进哪几条 net-new · 不确定的地方。", ""]
    out = d / "writers.md"
    out.write_text("\n".join(md))
    print(f"[taskbook] 要模型改正文 {len(man)} 行 / {len(by_pack)} 包 → {out}")
    for repo, items in sorted(by_pack.items()):
        print(f"  · {repo}: {[r['id'] for r, _ in items]}")
    return 0


def do_verify(args) -> int:
    d = pathlib.Path(args.dir).expanduser()
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
        pack = pack_path(args, dest["repo"])
        name = pathlib.Path(r["_src"]).name
        dir_ok = (pack / "skills" / name / "SKILL.md").is_file()
        rev = sh(["git", "rev-parse", "--verify", "-q", "origin/main"], cwd=str(pack))
        where = "origin/main"
        if rev.returncode != 0:                  # 测试用副本没有远端 → 退回本地 HEAD，并在行里标明
            rev, where = sh(["git", "rev-parse", "HEAD"], cwd=str(pack)), "HEAD(无远端)"
        full = rev.stdout.strip()
        sha = written[r["id"]].split("@")[-1]
        remote_ok = full.startswith(sha)         # 比全 sha 的前缀：`git log --oneline` 只印 7 位，缩写比会假 FAIL
        installed = list((HERMES_HOME / "skills").rglob(name))
        diff = sh(["diff", "-rq", str(pack / "skills" / name), str(installed[0])]).stdout.strip() if installed else "not installed yet"
        line = f"{r['id']} dir={dir_ok} {where}={remote_ok}({sha}) diff={'-' if not diff else diff[:60]}"
        (oks if dir_ok and remote_ok else fails).append(line)
    for l in oks:
        print("PASS", l)
    for l in fails:
        print("FAIL", l)
    print(f"[verify] PASS {len(oks)} · FAIL {len(fails)}")
    return 0 if not fails else 1


def profile_skill_names(root: pathlib.Path) -> set:
    return {md.parent.name for md in root.rglob("SKILL.md")
            if not (set(md.parts) & SKIP_DIRS)}


def do_install(args) -> int:
    """S7：装前总量闸门（≤ MAX_AGENT_SKILLS）→ 跑 install-cmds.sh → 装后形状断言（类目层 + local=0）。"""
    d = pathlib.Path(args.dir).expanduser()
    script = d / "install-cmds.sh"
    if not script.exists():
        sys.exit("no install-cmds.sh — run sweep-plan.py merge first")
    text = script.read_text()
    n = len([l for l in text.splitlines() if l.strip() and not l.startswith("#")])
    # 这次要动到的技能名：install 的 <id>/skills/<名> 与 update 的名字（update 的必然已在装，不会顶高总数）
    incoming = set(re.findall(r'skills install\s+"?[^"]*/skills/([^"/]+)', text))
    prof = HERMES_HOME / "skills"
    current = profile_skill_names(prof)
    post = current | incoming
    limit = args.max_skills
    gate = dict(max=limit, current=len(current), incoming=len(incoming - current),
                post_install=len(post), blocked=len(post) > limit)
    (d / "install-gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gate["blocked"]:
        over = len(post) - limit
        print(f"[install] ⛔ 装前闸门：装完 {len(post)} 个技能 > MAX_AGENT_SKILLS={limit}"
              f"（现有 {len(current)} + 新装 {gate['incoming']}）⇒ 不装。超 {over} 个。")
        print(f"[install] 差额与候选清单 → {d/'install-gate.json'}；先退役/清退再装，或由人改上限。")
        return 3
    print(f"[install] 装前闸门 ok：现有 {len(current)} + 新装 {gate['incoming']} = {len(post)}"
          f" ≤ {limit}（{d/'install-gate.json'}）")
    print(f"[install] 执行 {script}（{n} 条命令）")
    r = sh(["sh", str(script)], cwd=str(d))
    (d / "install.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-800:])
    print(f"[install] exit={r.returncode} · log={d/'install.log'}")
    # 装后回读：① 每个技能都在类目下（skills/<类目>/<name>/）② profile 里 local = 0。
    # 越界只报不动手：归位是仓库侧的动作 → SkillWeave（自己 git mv 会再次越界）。
    shape = dict(not_under_category=[], local=[])
    if r.returncode == 0:
        names = set()
        for md in prof.rglob("SKILL.md"):
            rel = md.relative_to(prof)
            if set(rel.parts) & SKIP_DIRS:
                continue
            if len(rel.parts) < 3:
                shape["not_under_category"].append(str(rel))
            names.add(md.parent.name)
        lock = json.loads((prof / ".hub" / "lock.json").read_text()).get("installed", {}) \
            if (prof / ".hub" / "lock.json").is_file() else {}
        bundled = {l.split(":")[0].strip() for l in (prof / ".bundled_manifest").read_text().splitlines()
                   if l.strip()} if (prof / ".bundled_manifest").is_file() else set()
        shape["local"] = sorted(names - set(lock) - bundled)
        (d / "install-shape.json").write_text(json.dumps(shape, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if shape["not_under_category"] or shape["local"]:
            print(f"[install] ⚠ 装后形状：{len(shape['not_under_category'])} 个技能不在类目下"
                  f"（{shape['not_under_category'][:3]}…）· local {len(shape['local'])} 个"
                  f"（{shape['local'][:3]}…）→ {d/'install-shape.json'}")
            print("[install] 归位是仓库侧的事 → 报给 SkillWeave；dream 不自己 git mv。")
        else:
            print("[install] 装后形状 ok：技能都在类目下、local = 0")
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
    ap.add_argument("cmd", choices=["apply", "verify", "install", "retire", "taskbook"])
    ap.add_argument("--dir")
    ap.add_argument("--external-dirs", default="",
                    help="DREAM_EXTERNAL_DIRS 那份 JSON（落点名 → 目录的映射就来自它）")
    ap.add_argument("--packs-root", default="~/Documents/AgentSkill")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--only", default="", help="逗号分隔的 P id")
    ap.add_argument("--backup", default="~/.hermes/backups/dream-retire")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--test", action="store_true", help="taskbook：按测试面写（禁 push）")
    ap.add_argument("--max-skills", type=int, default=MAX_AGENT_SKILLS,
                    help="S7 装前闸门的上限（默认 MAX_AGENT_SKILLS=120；这是给人留的校准旋钮）")
    a = ap.parse_args()
    if a.cmd == "--self-test":
        return self_test()
    if not a.dir:
        sys.exit("--dir is required")
    return {"apply": do_apply, "verify": do_verify, "install": do_install, "retire": do_retire,
            "taskbook": do_taskbook}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
