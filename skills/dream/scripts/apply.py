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
import os
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

def _hermes_root() -> pathlib.Path:
    """Hermes 的根：`HERMES_HOME` 是**当前活动 profile 的家**（跑 default 时它就是根），据此认根。

    与 `sweep-plan.hermes_root()` 同一口径 —— 两个脚本必须对「profile 目录在哪」给同一个答案，
    否则 scan 读 A 树、install 却去数 B 树（实测：apply 一直硬编码 `~/.hermes`）。
    """
    h = pathlib.Path(os.environ.get("HERMES_HOME") or pathlib.Path.home() / ".hermes")
    if (h / "profiles").is_dir() or (h / "hermes-agent").is_dir():
        return h
    return h.parent.parent if h.parent.name == "profiles" else h


HERMES_HOME = _hermes_root()                     # Hermes 根，不是「当前 profile 的家」
HERMES_REPO = HERMES_HOME / "hermes-agent"


def profile_dir(profile: str) -> pathlib.Path:
    """目标 profile 的根：`default` = Hermes 根，其余 = <根>/profiles/<名>。"""
    return HERMES_HOME if profile in ("", "default") else HERMES_HOME / "profiles" / profile


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


def ensure_pack(pack: pathlib.Path, row: dict) -> tuple:
    """落点包还不存在就建：`<root>/<repo>/{README.md,skills/}` + git init + 初始提交 → (pack, 是否新建)。
    远端（`gh repo create`）不在这步做——本地建好、报出来，等有人在的会话再建远端。"""
    if (pack / ".git").exists():
        return pack, False
    (pack / "skills").mkdir(parents=True, exist_ok=True)
    axis, topic = row.get("axis") or "", row.get("topic") or ""
    (pack / "README.md").write_text(
        f"# {pack.name}\n\nDream v3 建的落点包（轴 {axis} · 主题 {topic}）。技能在 `skills/<name>/SKILL.md`。\n",
        encoding="utf-8")
    sh(["git", "init", "-q", "-b", "main"], cwd=str(pack), check=True)
    sh(["git", "add", "--", "README.md", "skills"], cwd=str(pack), check=True)
    sh(["git", "-c", "user.name=Hermes", "-c", "user.email=hermes@local", "commit", "-q",
        "-m", f"init: {pack.name}（dream v3 建包）"], cwd=str(pack), check=True)
    return pack, True


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
    """2026-10-12 用户定：**不做** frontmatter 归一，原样保留（归一交给 SkillWeave/代理）。
    这个函数留着只为兼容既有调用点：永远原样返回，dropped 恒为空。"""
    return text, []


def skill_desc(skill_dir: pathlib.Path) -> str:
    """取 SKILL.md 的 description（含折行续行），归一成小写去标点的串，用来比对"两条技能的 desc 是不是同一句"。"""
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return ""
    lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
    out, on = [], False
    for line in lines[1:60]:
        if line.startswith("description:"):
            on = True
            out.append(line.split(":", 1)[1].strip())
            continue
        if on:
            if line[:1] in (" ", "\t"):
                out.append(line.strip())
                continue
            break
    text = " ".join(" ".join(out).strip().strip('"').strip("'").split())   # 折行/多余空白先塌平，否则同一句会被判成不同
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()


def desc_dups(pack: pathlib.Path, incoming: list) -> dict:
    """新增技能的 name/desc 与包内**已有**技能是否撞：name 撞 → 记；desc 撞 → 记。
    （用户 2026-10-12：新技能只做 copy，然后让模型看一眼 name/desc 有没有重复并改。）"""
    have = {}
    for md in sorted(pack.rglob("SKILL.md")):      # 排序：撞车时要稳定报出**同一个**对手
        if "__pycache__" in md.parts:
            continue
        have.setdefault(skill_desc(md.parent), md.parent.name)
    skip_names = {d.name for _, _, d, _ in incoming}
    out = {}
    for _, _, dst, _ in incoming:
        d = skill_desc(dst)
        if d and d in have and have[d] not in skip_names:
            out[dst.name] = have[d]
    return out


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
    todo, manual, skipped, newpack = [], [], [], {}
    for r in rows:
        if only and r["id"] not in only:
            continue
        dest = row_dest(r)
        new_pack_row = (dest.get("kind") == "new" and dest.get("action") == "move"
                        and dest.get("dest_skill") == "__new__")
        if dest.get("kind") != "existing" and not new_pack_row:
            manual.append(r)
            continue
        if dest.get("action") != "move" or dest.get("dest_skill") != "__new__":
            manual.append(r)
            continue
        src = pathlib.Path(r["_src"])
        name = src.name
        pack = pack_path(args, dest["repo"])
        if new_pack_row:
            newpack.setdefault(dest["repo"], r)      # 落点包还不存在：写入前按需建
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

    by_pack, results, made = {}, [], []
    for r, pack, src, dst in todo:
        by_pack.setdefault(pack, []).append((r, src, dst))
    for pack, items in sorted(by_pack.items(), key=lambda kv: kv[0].name):
        if pack.name in newpack:                     # 落点包还不存在 → 先建（建完照常写入并报出来）
            pack, created = ensure_pack(pack, newpack[pack.name])
            if created:
                made.append(pack.name)
                print(f"[apply] + 新建包 {pack.name}（{len(items)} 条）")
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
            print(f"[apply] ✓ {r['id']} {dst.name}: 拷贝（frontmatter 原样保留）· scan=safe")
        dups = desc_dups(pack, written)
        if dups:
            for name, other in dups.items():
                results.append((next(r["id"] for r, _, d, _ in written if d.name == name), "BLOCKED",
                                f"desc 与包内 {other} 重复 ⇒ 先改 name/desc 再重跑（判断归模型，脚本只报）"))
                print(f"[apply] ⚠ {name}: description 与包内 {other} 撞了 ⇒ 跳过，交代理改写后重跑")
            written = [w for w in written if w[2].name not in dups]
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
    out.write_text(json.dumps({"results": results, "manual": [r["id"] for r in manual],
                               "new_packs": sorted(made),
                               "no_push": bool(getattr(args, "no_push", False))}, ensure_ascii=False, indent=1))
    # 改动清单：默认只在会话里报；给了 --changes-out 就落一份文件（用户 2026-10-12）
    changes = [dict(id=cid, status=st, detail=str(det)) for cid, st, det in results]
    if getattr(args, "changes_out", ""):
        cp = pathlib.Path(args.changes_out).expanduser()
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(dict(profile=args.profile, changes=changes), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(f"[apply] 改动清单 → {cp}（{len(changes)} 条）")
    else:
        print(f"[apply] 改动清单（{len(changes)} 条；要落盘加 --changes-out <路径>）："
              + " · ".join(f"{c['id']}:{c['status']}" for c in changes[:8])
              + (" …" if len(changes) > 8 else ""))
    bad = [x for x in results if x[1] in ("BLOCKED", "SKIP")]
    wrote = len([x for x in results if x[1] == "WRITTEN"])
    print(f"[apply] 写入 {wrote} 行 · 阻断/跳过 {len(bad)} 行"
          + (f" · 新建包 {len(made)} 个：{', '.join(sorted(made))}" if made else "") + f" → {out}")
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
          f"- 测试面：{'是（副本）' if args.test else '否（真包）'} · "
          f"{'本轮 **不推送**（--no-push）：提交只留本地，谁都不许 push' if args.no_push else '本轮写入后要推（push 后从远端回读 sha）'}", ""]
    for repo, items in sorted(by_pack.items()):
        pack = pack_path(args, repo)
        md += [f"## {repo}（{len(items)} 行）", "", f"clone：`{pack}`", ""]
        if sh(["git", "remote", "get-url", "origin"], cwd=str(pack)).returncode != 0:
            md += ["⚠ **本包没有远端**：只在回执里报「要折什么」，**别提交**"
                   "（本地积提交没人收，等它有了 origin 再走一次）", ""]
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
               "③ **禁止起后台进程**；④ 不做 frontmatter 归一（原样保留，别删 version/author/metadata 这些键）；"
               "⑤ 提交前跑扫描闸（`tools.skills_guard.scan_skill`），非 `safe` 按命中改写文字再扫，"
               "`dangerous` 不许提交；"
               + ("⑥ **禁止 push**（本轮 --no-push：提交只留本地）" if (args.test or args.no_push)
                  else "⑥ `git push origin <sha>:main` 后从远端回读 sha"),
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
    _rep = json.loads((d / "apply-report.json").read_text()) if (d / "apply-report.json").exists() else {}
    checks = _rep.get("results", [])
    no_push = bool(_rep.get("no_push"))      # 本轮没推的话，提交只在本地 HEAD，比 origin/main 全是假 FAIL
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
        # 要核实的是"S5 报的那条提交真在这个包里"。别拿它 == HEAD 去比：writer 这类后续提交会把
        # HEAD 往前推，那样上一阶段真写进去的行会被误判 FAIL（实测 40 行假 FAIL）。
        # 判据 = 这条提交在这个包的历史里；命中 origin/main 的历史另算"已发布"。
        sha = written[r["id"]].split("@")[-1]     # 缩写；`git rev-parse` 能解
        full = sh(["git", "rev-parse", "--verify", "-q", f"{sha}^{{commit}}"], cwd=str(pack)).stdout.strip()
        head_now = sh(["git", "rev-parse", "HEAD"], cwd=str(pack)).stdout.strip()
        remote = sh(["git", "rev-parse", "--verify", "-q", "origin/main"], cwd=str(pack)).stdout.strip()
        anc = lambda a, b: bool(a) and bool(b) and \
            sh(["git", "merge-base", "--is-ancestor", a, b], cwd=str(pack)).returncode == 0
        if not full:
            where, remote_ok = "不在这个包里", False
        elif anc(full, remote):
            where, remote_ok = "origin/main 历史里（已发布）", True
        elif anc(full, head_now):
            where, remote_ok = "HEAD 历史里（未推）", True
        else:
            where, remote_ok = "不在这个包的历史里", False
        installed = list((profile_dir(getattr(args, "profile", "default")) / "skills").rglob(name))
        diff = sh(["diff", "-rq", str(pack / "skills" / name), str(installed[0])]).stdout.strip() if installed else "not installed yet"
        line = f"{r['id']} dir={dir_ok} 提交在{where}({sha}) diff={'-' if not diff else diff[:60]}"
        (oks if dir_ok and remote_ok else fails).append(line)
    for l in oks:
        print("PASS", l)
    for l in fails:
        print("FAIL", l)
    print(f"[verify] PASS {len(oks)} · FAIL {len(fails)}")
    return 0 if not fails else 1


def profile_skill_names(root: pathlib.Path, out: pathlib.Path = None) -> set:
    """profile 里现在有哪些技能。**必须跟着符号链接走**：Skills-Manager 部署的就是符号链接，
    `rglob` 看不见它们，那样闸门的分母会凭空少 30（实测 232 被算成 202）。
    顺带把"符号链接"与"本地目录"分开报，装回后要看这两者的比例。"""
    names, linked = set(), set()
    for md in walk_skill_mds(root):
        names.add(md.parent.name)
        if md.parent.is_symlink():
            linked.add(md.parent.name)
    if out is not None:
        (out / "install-current.json").write_text(json.dumps(
            {"total": len(names), "symlinked": sorted(linked), "local_dirs": sorted(names - linked)},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return names


def walk_skill_mds(root: pathlib.Path) -> list:
    """跟着符号链接把 profile 底下的 SKILL.md 找齐（口径与 sweep-plan.walk_skill_dirs 一致）。"""
    found, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        rel = pathlib.Path(dirpath).relative_to(root).parts
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
        if len(rel) > 4:
            dirnames[:] = []
            continue
        key = os.path.realpath(dirpath)
        if key in seen:
            dirnames[:] = []
            continue
        seen.add(key)
        if "SKILL.md" in filenames:
            found.append(pathlib.Path(dirpath) / "SKILL.md")
            dirnames[:] = []
    return found


def do_install(args) -> int:
    """S7：装前总量闸门（≤ MAX_AGENT_SKILLS）→ 跑 install-cmds.sh → 装后形状断言（类目层 + local=0）。"""
    d = pathlib.Path(args.dir).expanduser()
    script = d / "install-cmds.sh"
    if not script.exists():
        sys.exit("no install-cmds.sh — run sweep-plan.py merge first")
    text = script.read_text()
    n = len([l for l in text.splitlines() if l.strip() and not l.startswith("#")])
    # 这次要动到的技能名：install 的 <id>/skills/<名> 与 update 的名字（update 的必然已在装，不会顶高总数）
    # 要从生成的命令里认出新装的技能名：现在两条路都写得出名字 —— `--name "X"`（交 manager 从本地包装）
    # 与 `cp -R ".../skills/<名>"`（直接复制）。只认老格式的话会算出"装回 0"，闸门等于失效（实测）。
    incoming = set(re.findall(r'--name "([^"]+)"', text)) | set(re.findall(r'/skills/([^"/]+)"', text))
    incoming -= set(re.findall(r'^\s*skills-manager-cli\s+skills\s+update "([^"]+)"', text, re.M))
    prof = profile_dir(getattr(args, "profile", "default")) / "skills"
    current = profile_skill_names(prof, d)
    # 分母要扣掉本轮"搬进包、装完就退役"的那些本地副本：它们不是净增（S8 会把本地那份删掉）。
    # 不扣的话，凡是一轮正常搬迁都会被这道闸挡住（实测 232 + 89 > 300）。
    retiring = set()
    _rep = d / "apply-report.json"
    if _rep.exists():
        _rows = {r["id"]: r for r in load_rows(d)}
        for cid, state, _ in json.loads(_rep.read_text()).get("results", []):
            if state == "WRITTEN" and cid in _rows:
                retiring.add(pathlib.Path(_rows[cid]["_src"]).name)
    net_new = incoming - current - retiring          # 真新增：现有没有、也不会被退役的
    post = (current - retiring) | incoming
    limit = args.max_skills
    gate = dict(max=limit, current=len(current), retiring=len(retiring & current),
                incoming=len(incoming), net_new=len(net_new),
                post_install=len(post), blocked=len(post) > limit)
    (d / "install-gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gate["blocked"]:
        over = len(post) - limit
        print(f"[install] ⛔ 装前闸门：装完 {len(post)} 个技能 > MAX_AGENT_SKILLS={limit}"
              f"（现有 {len(current)} + 新装 {gate['incoming']}）⇒ 不装。超 {over} 个。")
        print(f"[install] 差额与候选清单 → {d/'install-gate.json'}；先退役/清退再装，或由人改上限。")
        return 3
    print(f"[install] 装前闸门 ok：现有 {len(current)} − 本轮将退役 {gate['retiring']} + 装回 {gate['incoming']}"
          f"（其中真新增 {gate['net_new']}）= {len(post)} ≤ {limit}（{d/'install-gate.json'}）")
    # merge 是按**计划**生成命令的：被扫描闸挡掉的行、以及 merge 行的源技能，命令里也会出现，
    # 照着跑就是 `skill not found`（实测 34 条）。这里只保留"落点真在磁盘上"的那几行。
    def _names(line: str) -> set:
        """这行命令在装哪几枚技能：install 的 `--name "X"` / 路径尾巴，以及 deploy/update 的 `"X"`。"""
        return (set(re.findall(r'--name "([^"]+)"', line))
                | set(re.findall(r'/skills/([^"/]+)"', line))
                | set(re.findall(r'skills (?:deploy\s+--agent\s+\S+|update)\s+"([^"]+)"', line)))

    kept, dropped, missing_names = [], [], set()
    for line in text.splitlines():                      # 第一遍：落点路径不在磁盘上的 install/cp 行 → 丢，并记下名字
        if not line.strip() or line.lstrip().startswith("#"):
            kept.append(line)
            continue
        paths = [x for x in re.findall(r'"([^"]*)"', line)
                 if "/skills/" in x or x.startswith("/") or x.startswith("~")]
        if paths and any(not pathlib.Path(x).expanduser().exists() for x in paths):
            missing_names |= _names(line)
            dropped.append(line)
            continue
        kept.append(line)
    once = kept                                   # 第二遍：这批名字的 deploy / update 行也一并丢
    kept = []
    for line in once:
        if _names(line) & missing_names:
            dropped.append(line)
            continue
        kept.append(line)
    run_script = d / "install-cmds.filtered.sh"
    run_script.write_text("\n".join(kept) + "\n", encoding="utf-8")
    if dropped:
        print(f"[install] 跳过 {len(dropped)} 条落点不在磁盘上的命令（被闸挡的行 / merge 行的源技能）："
              f"{', '.join(sorted({x.strip()[:60] for x in dropped})[:3])}…")
    print(f"[install] 执行 {run_script}（{len([l for l in kept if l.strip() and not l.startswith('#')])} 条命令）")
    r = sh(["sh", str(run_script)], cwd=str(d))
    (d / "install.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-800:])
    print(f"[install] exit={r.returncode} · log={d/'install.log'}")
    # 装后回读：① 每个技能都在类目下（skills/<类目>/<name>/）② profile 里 local = 0。
    # 越界只报不动手：归位是仓库侧的动作 → SkillWeave（自己 git mv 会再次越界）。
    shape = dict(not_under_category=[], local=[])
    if r.returncode == 0:
        # 「有归属」的判据（Skills-Manager 接管后）：profile 里那一项是**它部署的符号链接**
        # （指向 ~/.skills-manager/skills/<名>），或属于 Hermes 自带的种子（`.bundled_manifest`），
        # 或名字在 `.hub/lock.json` 里（那是 `hermes skills install` 时代留下的、源在远端）。
        # 旧口径拿 `.hub/lock.json` 当**唯一**账本 —— SM 接管后它只剩历史条目，会把已部署的技能全算成 local。
        mds = walk_skill_mds(prof)
        names, deployed = set(), set()
        for md in mds:
            rel = md.relative_to(prof)
            names.add(md.parent.name)
            if md.parent.is_symlink():
                deployed.add(md.parent.name)
                continue     # 它部署的形状就是**扁平** skills/<名>：类目层是 Hermes 老口径，别套在它头上
            if len(rel.parts) < 3:
                shape["not_under_category"].append(str(rel))
        bundled = {l.split(":", 1)[0].strip() for l in (prof / ".bundled_manifest").read_text().splitlines()
                   if ":" in l} if (prof / ".bundled_manifest").is_file() else set()
        lock_names = set()
        _lk = prof / ".hub" / "lock.json"
        if _lk.is_file():
            try:
                lock_names = set(json.loads(_lk.read_text()).get("installed", {}))
            except (OSError, ValueError):
                lock_names = set()
        shape["local"] = sorted(names - deployed - bundled - lock_names)
        (d / "install-shape.json").write_text(json.dumps(shape, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if shape["not_under_category"] or shape["local"]:
            print(f"[install] ⚠ 装后形状：{len(shape['not_under_category'])} 个技能不在类目下"
                  f"（{shape['not_under_category'][:3]}…）· local {len(shape['local'])} 个"
                  f"（{shape['local'][:3]}…）→ {d/'install-shape.json'}")
            print("[install] 归位是仓库侧的事 → 报给 SkillWeave；dream 不自己 git mv。"
                  "（Skills-Manager 部署的扁平符号链接不算越界）")
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
    """最小自检：frontmatter 原样保留 + 形状判定（不碰 git、不碰真仓库）。"""
    text = ("---\nname: x\ndescription: >-\n  两行\n  描述\nversion: 1.0.0\nauthor: me\n"
            "metadata:\n  hermes:\n    tags: [a]\n---\n# body\n")
    new, dropped = normalize_frontmatter(text)
    assert new == text, "不做归一：frontmatter 必须原样保留"
    assert "version" in new and "metadata" in new and dropped == [], \
        "不做归一：version/author/metadata 这类键都要原样留着（交给 SkillWeave/代理处理）"
    assert normalize_frontmatter("# no frontmatter\n") == ("# no frontmatter\n", [])
    # desc 撞车检测：包内已有 A，新来 B 的 description 与 A 同一句 → 必须报出来（判断归模型，脚本只报）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pack = pathlib.Path(td)
        for name, desc in (("a-skill", "Use when X happens. Do Y."), ("b-skill", "use when   x happens, do y"),
                           ("c-skill", "Something else entirely.")):
            (pack / "skills" / name).mkdir(parents=True)
            (pack / "skills" / name / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n# t\n", encoding="utf-8")
        incoming = [(None, None, pack / "skills" / "b-skill", None), (None, None, pack / "skills" / "c-skill", None)]
        dups = desc_dups(pack, incoming)
        assert dups == {"b-skill": "a-skill"}, dups
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
    ap.add_argument("--changes-out", default="", help="把本轮的改动清单落一份到这里（默认只在会话里报）")
    ap.add_argument("--max-skills", type=int, default=MAX_AGENT_SKILLS,
                    help=f"S7 装前闸门的上限（默认 MAX_AGENT_SKILLS={MAX_AGENT_SKILLS}；这是给人留的校准旋钮）")
    a = ap.parse_args()
    if a.cmd == "--self-test":
        return self_test()
    if not a.dir:
        sys.exit("--dir is required")
    return {"apply": do_apply, "verify": do_verify, "install": do_install, "retire": do_retire,
            "taskbook": do_taskbook}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
