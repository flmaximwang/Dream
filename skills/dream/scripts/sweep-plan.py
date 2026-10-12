#!/usr/bin/env python3
"""Enumerate a profile's un-homed skills and every local skill pack, then fold judge verdicts into a plan.

The deterministic half of `dream`. Two subcommands:

    # 1. scan — what is local-only in this profile, what packs exist, frozen judge input
    python3 -B sweep-plan.py scan --profile default --packs-root ~/Documents/AgentSkill \
        --out ~/.hermes/cache/scratch/sweep-2026-10-09 --self-test     # --self-test runs a throwaway tree

    # 2. merge — judge verdicts (JSONL) -> plan.md + install-cmds.sh
    python3 -B sweep-plan.py merge --dir <the same --out> --profile default

Neither subcommand writes to a profile, a pack or git: `scan` is read-only, `merge` writes only inside --out.

Run it with the venv interpreter (it needs pyyaml, for the same reason load-external-skill-index does —
`description: >-` must be resolved as YAML, not regexed):
    ~/.hermes/hermes-agent/venv/bin/python3

Classification — the **A axis** (whose upstream this copy is), read from the target profile's own ledgers:
    hub-installed   name is a key in <profile>/skills/.hub/lock.json     -> A=hub
    bundled         name is a line in <profile>/skills/.bundled_manifest -> A=bundled
    built-in/official  name is in the release tree (<hermes 根>/hermes-agent/{skills,optional-skills})
    otherwise -> A=local, CANDIDATE
Whether a skill is already homed is decided later, in `prescreen`: a skill an external pack carries, or one
Skills-Manager's own library holds, does not become a candidate — that is what "owned" means now that
Skills-Manager deploys the profile's skills (it registers each profile as its own agent: `hermes-<profile>`).

## v2 · 判官要读全文，答案空间是全部 AgentSkill-* 目录（2026-10-10 用户改口径）

- `judge-input.json` 每条带 `path` / `skill_md` / `refs`，判官**必须读 SKILL.md 全文**（只说 name+desc
  就是偷懒判法，用户已驳回）；`judge-input-bodies.json` 把正文内联，给只会看文本的判官（分块直调的判官）用。
- 答案空间分两轴先判：`{kind:"new", axis:"tool"|"topic", topic, personal, boundary}` 会落到
  `AgentSkill-Using<Topic>` / `AgentSkill-<Topic>`，归一化后撞上已有目录名就**复用那个仓库**；
  多条同 topic **合并进一个包**。`existing` 的 `repo` 可以是 `~/Documents/AgentSkill` 下**任一**
  `AgentSkill-*` 目录名（`routable` 只决定能不能 install 回来，不限制能否当落点）。
- **没有 `__stay_local__`**：判官输出里出现它一律拒收（exit 4）——每条都必须有落点。

Exit codes: 0 ok | 2 no candidates, no packs, or missing sibling | 3 profile not found | 4 verdict coverage < 100%
            or a rejected verdict (stay-local / unknown dest / non-string dest fields)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import NoReturn

HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME") or pathlib.Path.home() / ".hermes")
SKIP_DIRS = {".hub", ".git", ".archive", "__pycache__", "quarantine", ".trash"}
HERMES_REPO = "hermes-agent"


def hermes_root() -> pathlib.Path:
    """Hermes 的根：底下同时有 `skills/` 与 `hermes-agent/` 的那一层。

    `HERMES_HOME` 指的是**当前活动 profile 的家**：跑 default 时它就是根，跑别的 profile 时
    它是 `<根>/profiles/<名>`。所以 `--profile default` 不能直接拿 `HERMES_HOME` 当根 ——
    在 dev-general 会话里跑 `--profile default` 会扫到 dev-general（实测踩过）。这里先把根认出来，
    `--profile` 再相对根解析，脚本因此与「在哪个 profile 的会话里跑」无关。
    """
    if (HERMES_HOME / "profiles").is_dir() or (HERMES_HOME / HERMES_REPO).is_dir():
        return HERMES_HOME
    return HERMES_HOME.parent.parent if HERMES_HOME.parent.name == "profiles" else HERMES_HOME
# The sibling that already owns frontmatter parsing (YAML, not regex) and the pack index format.
PARSER_SKILL = "load-external-skill-index"
STAY_LOCAL, UNJUDGED = "__stay_local__", "__unjudged__"
ACTIONS = ("move", "merge", "strengthen")
TOOL_PREFIX, TOPIC_PREFIX = "AgentSkill-Using", "AgentSkill-"


def norm(s: str) -> str:
    """Uniquification key: lowercase, hyphens/underscores/spaces dropped (用户 2026-10-10 定的口径)."""
    return "".join(ch for ch in s.lower() if ch not in "-_ ")


def pack_name_for(axis: str, topic: str) -> str:
    """The script only uniquifies — naming policy is exactly these two formulas, nothing cleverer.

    The topic is slugged into something a git remote can carry (no spaces/punctuation); two candidates naming
    the same topic in different styles then land on the same repo name (and on the same normalized key).
    """
    slug = "".join(ch for ch in topic.strip() if ch.isalnum() or ch in "-_") or "Unnamed"
    # 判官有时把前缀写进 topic（topic=UsingDrawio）——先剥掉，别拼成 AgentSkill-UsingUsingDrawio
    for pre in ("AgentSkill",):                      # 罕见但同样会双拼
        if slug.lower().startswith(pre.lower()):
            slug = slug[len(pre):]
    if slug.lower().startswith("using"):   # 注意两边都小写：slug.lower() 比 "Using" 永不成立（踩过一次）             # tool 轴的前缀；topic 轴上也一样剥（UsingX 当主题名没意义）
        slug = slug[5:] or slug
    slug = slug.lstrip("-_") or slug        # 剥完前缀可能留个连字符（AgentSkill-Foo → -Foo）
    return (TOOL_PREFIX if axis == "tool" else TOPIC_PREFIX) + slug



def die(code: int, msg: str) -> NoReturn:
    print(f"[sweep-plan] {msg}", file=sys.stderr)
    raise SystemExit(code)


def profile_root(profile: str) -> pathlib.Path:
    base = hermes_root()
    root = base if profile in ("", "default") else base / "profiles" / profile
    if not (root / "skills").is_dir():
        die(3, f"no skills tree under {root} — profile {profile!r} not found")
    return root


def load_parser(override: str = "") -> tuple:
    """Import the sibling skill-index.py. Never copy its frontmatter parser: the YAML trap is real."""
    here = pathlib.Path(__file__).resolve()
    cands = [pathlib.Path(override).expanduser()] if override else [
        here.parents[2] / PARSER_SKILL / "scripts" / "skill-index.py",          # repo / installed layout
        here.parents[2].parent / PARSER_SKILL / "scripts" / "skill-index.py",   # one level up, defensive
    ]
    if not override and not any(p.is_file() for p in cands):
        # 在 worktree 里开发时兄弟技能不在旁边（它只在 profile 里装好）——退回装好的那份，别让一轮卡在 S1
        for root in (HERMES_HOME, pathlib.Path.home() / ".hermes"):
            # 两种布局都要认：skills/<类目>/<技能>/ 与 skills/<技能>/（profile 被扁平化后就长这样）
            cands += sorted(root.glob(f"skills/*/{PARSER_SKILL}/scripts/skill-index.py"))
            cands += sorted(root.glob(f"skills/{PARSER_SKILL}/scripts/skill-index.py"))
        # 技能交给 Skills-Manager 管之后，profile 里那份可能被挪走：中央库、以及 external_dirs 里的包
        # 都可能是唯一还在的副本（实测 2026-10-12 profile 与中央库都没有了，只剩包内那份）
        cands += sorted((pathlib.Path.home() / ".skills-manager" / "skills").glob(
            f"{PARSER_SKILL}/scripts/skill-index.py"))
        try:
            ed = sibling_module("external-dirs.py")
            for d in json.loads(ed.resolve_file().read_text()).get("dirs", []):
                base = pathlib.Path(d["path"]).expanduser()
                for pack in ([base] if (base / "SKILL.md").is_file()
                             else [c for c in sorted(base.glob("*")) if c.is_dir()]):
                    cands += sorted(pack.rglob(f"{PARSER_SKILL}/scripts/skill-index.py"))[:1]
        except (OSError, ValueError, KeyError, SystemExit):
            pass
    for p in cands:
        if p.is_file():
            spec = importlib.util.spec_from_file_location("_sweep_sibling_index", p)
            if spec is None or spec.loader is None:
                die(2, f"cannot load parser module from {p}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, p
    die(2, "sibling parser not found; looked at: " + ", ".join(str(c) for c in cands)
           + f" — install {PARSER_SKILL} beside this skill, or pass --parser-script")


def parse_fm(mod: object, path: pathlib.Path) -> dict:
    try:
        fm = mod.frontmatter(path)          # type: ignore[attr-defined]
    except ImportError:
        die(2, "pyyaml missing in this interpreter — run with ~/.hermes/hermes-agent/venv/bin/python3")
    return fm if isinstance(fm, dict) else {}


def git(repo: pathlib.Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<git failed: {exc}>"
    return out.stdout.strip() if out.returncode == 0 else f"<git error: {out.stderr.strip()[:80]}>"


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def file_digests(out: pathlib.Path, names) -> dict:
    """只给真实存在的文件算指纹（缺的不写空值，免得把「没有」当成「变了」）。"""
    return {n: sha256_file(out / n) for n in names if (out / n).is_file()}


def identifier(remote: str) -> str:
    """git@github.com:owner/repo.git | https://github.com/owner/repo -> owner/repo ('' when unknown)."""
    r = remote.strip()
    if not r or r.startswith("<"):
        return ""
    r = r.removesuffix(".git").rstrip("/")
    r = r.split(":", 1)[1] if r.startswith("git@") else r
    parts = [p for p in r.split("/") if p and "github.com" not in p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def walk_skill_dirs(skills_root: pathlib.Path) -> list:
    """<cat>/<name>/SKILL.md, plus a plugin's <cat>/<sub>/<name>/SKILL.md; hidden/archive dirs skipped.

    **必须跟着符号链接走**：Skills-Manager 部署用的是符号链接（`skills/<名>` ->
    `~/.skills-manager/skills/<名>`），而 pathlib 的 rglob 不进符号链接目录 —— 用它会把
    所有由 Skills-Manager 装的技能看成"不存在"（实测 default profile 漏掉 30 个）。
    """
    found, seen = [], set()
    for dirpath, dirnames, filenames in os.walk(skills_root, followlinks=True):
        d = pathlib.Path(dirpath)
        rel = d.relative_to(skills_root).parts
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
        if len(rel) > 4:                                        # 太深，不可能是技能目录
            dirnames[:] = []
            continue
        key = os.path.realpath(dirpath)
        if key in seen:                                         # 符号链接成环时别转圈
            dirnames[:] = []
            continue
        seen.add(key)
        if "SKILL.md" in filenames:
            md = d / "SKILL.md"
            if not any(x in SKIP_DIRS or x.startswith(".") for x in rel):
                found.append(md)
            dirnames[:] = []                                    # 技能目录里的 references/ 不算独立技能
    return sorted(found)


def read_lock(root: pathlib.Path) -> dict:
    p = root / "skills" / ".hub" / "lock.json"
    if not p.is_file():
        return {}
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("installed", {}) or {}
    except (json.JSONDecodeError, OSError):
        die(2, f"unreadable lock file: {p}")


def read_bundled(root: pathlib.Path) -> set:
    p = root / "skills" / ".bundled_manifest"
    if not p.is_file():
        return set()
    return {ln.split(":", 1)[0].strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
            if ":" in ln}


def pack_candidates(args) -> list:
    """候选落点目录：优先 `--external-dirs` 的那份 JSON（每个目录自身或它的直接子目录），
    否则退回老的 `--packs-root` 下 glob `AgentSkill-*`。"""
    ext = getattr(args, "external_dirs", "") or ""
    if ext:
        data = json.loads(pathlib.Path(ext).expanduser().read_text())
        out = []
        for d in data.get("dirs", []):
            p = pathlib.Path(d["path"]).expanduser()
            if not p.is_dir():
                continue
            kids = [c for c in sorted(p.glob("*")) if c.is_dir() and not c.name.startswith(".")]
            out += [p] if (p / "SKILL.md").is_file() else kids     # 目录自己就是技能 → 收它；否则收它的一层子目录
        return out
    return sorted(pathlib.Path(args.packs_root).expanduser().glob("AgentSkill-*"))


def sibling_module(filename: str):
    """同目录的兄弟脚本当模块用（复用它的逻辑，不 copy）。"""
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("_dream_sib_" + filename.replace("-", "_").replace(".", "_"),
                                                  here / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dir_fingerprint(mirror, d: pathlib.Path) -> str:
    """技能目录的指纹：身份=相对路径，内容=每个文件的 sha256（复用 skill-mirror 的文件清单）。"""
    h = hashlib.sha256()
    for rel, sha in sorted(mirror.skill_files(d).items()):
        h.update(rel.encode("utf-8")); h.update(sha.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------- 16 态分类（2026-10-11 用户拍板；2026-10-12 补第 16 格）
# A = hermes 侧来源：local 或「第三方维护」四种子类 built-in / official / bundled / hub
# B = 归 Skills-Manager 管否 · C = 归 external_dirs 管否 · D = 与可比者内容相同否（无所有者时坍缩）
# 第三方的权威副本：built-in/optional 在发行版树里、bundled 在 .bundled_manifest、hub 在 .hub/lock.json
STATE_ACTIONS = {
    # local 组（7 态）
    ("local", "no", "no", "fold"): "judge-destination",       # 真候选：判落点 → 写我的包 → push → 交 manager 装
    ("local", "no", "yes", "same"): "noop",                   # 我的包＝profile
    ("local", "no", "yes", "differs"): "reconcile",           # 比方向：profile 新→合回包；包新→manager 重装
    ("local", "yes", "no", "same"): "noop",
    ("local", "yes", "no", "differs"): "patch-package",       # 第三方技能上的本地改动 → 建 patch 包 → manager 装
    ("local", "yes", "yes", "same"): "noop",
    ("local", "yes", "yes", "differs"): "reconcile",          # 以我的包为源合回 → manager 重装
    # 第三方组（9 态，来源无关的处置相同）
    ("third", "no", "no", "same"): "noop",                    # 与官方副本一致
    ("third", "no", "no", "differs"): "patch-or-restore",     # 先判方向：本地新→patch 包；远端新→更新本地
    # 第 16 格（2026-10-12 实测出现，原表按「第三方必有本地可比者」写的，前提已经不成立）：
    # 第三方、B/C 都不管、**本地没有任何可比者**。典型是 `hermes skills install` 留下的 hub 副本
    # （A=hub 由 `.hub/lock.json` 认出，但它的权威副本在远端，profile 里没有同名的
    # built-in/official/bundled/external/manager 那一份）。判不出 same/differs ⇒ 只报不动。
    ("third", "no", "no", "fold"): "noop",
    ("third", "no", "yes", "same"): "noop",
    ("third", "no", "yes", "differs"): "noop",                # 官方是源，不碰（确是我加的改动 → patch 包）
    ("third", "yes", "no", "same"): "noop",
    ("third", "yes", "no", "differs"): "patch-package",
    ("third", "yes", "yes", "same"): "noop",
    ("third", "yes", "yes", "differs"): "reconcile",
}


def third_party_dirs() -> dict:
    """第三方来源的权威副本（名字 → 目录）：发行版 skills/ 与 optional-skills/。

    发行版仓库在 `<hermes 根>/hermes-agent`。旧写法拿 `HERMES_HOME.parent` 去拼，default 时那是家目录、
    别的 profile 时那是 `profiles/` —— 永远指不到，实测 `tp` 一直是空字典（A 轴因此从没认出过
    built-in/official）。
    """
    src = hermes_root() / HERMES_REPO
    out = {}
    for tag, sub in (("built-in", "skills"), ("official", "optional-skills")):
        d = src / sub
        if not d.is_dir():
            continue
        for md in d.rglob("SKILL.md"):
            out.setdefault(md.parent.name, (tag, md.parent))
    return out


def hermes_source(n: str, tp: dict, manifest: dict, lock: set) -> str:
    """A 轴的取值：local 或第三方四种子类（built-in / official / bundled / hub，按这个优先级）。"""
    if tp.get(n, ("", None))[0] == "built-in":
        return "built-in"
    if tp.get(n, ("", None))[0] == "official":
        return "official"
    if n in manifest:
        return "bundled"
    if n in lock:
        return "hub"
    return "local"


def dream_state(r: dict, mirror, index: dict, manager_dirs: dict, tp: dict, manifest: dict,
                exclude: tuple = ()) -> dict:
    """一条 profile 技能的 16 态归属：source/A/B/C/D + action。
    exclude 里是技能名前缀：命中的本轮一律不动（`--exclude loomerto`＝这人正在手改，别碰）。"""
    n = r["name"]; d = r["_md"].parent
    source = hermes_source(n, tp, manifest, r.get("lock_names", set()))
    mgr = "yes" if n in manager_dirs else "no"
    hit = mirror.classify(d, index)
    ext = "yes" if hit["verdict"] in ("homed", "drift") else "no"
    owners, same = [], False
    if ext == "yes":
        owners.append("external"); same = same or hit["verdict"] == "homed"
    if mgr == "yes":
        owners.append("manager"); same = same or mirror.diff_dirs(d, manager_dirs[n])["identical"]
    if n in manifest:
        owners.append("manifest")
        try:
            sys.path.insert(0, str(hermes_root() / HERMES_REPO))
            from tools.skills_sync import _matches_origin_hash
            same = same or _matches_origin_hash(d, manifest[n])
        except Exception:                                     # 拿不到账本就不下这个结论（保守：不算 same）
            pass
    if n in tp:
        owners.append(tp[n][0]); same = same or mirror.diff_dirs(d, tp[n][1])["identical"]
    D = "fold" if not owners else ("same" if same else "differs")
    A = "local" if source == "local" else "third"
    action = STATE_ACTIONS[(A, mgr, ext, D)]
    if action != "noop" and exclude and n.lower().startswith(exclude):
        action = "noop"          # 被 --exclude 点名：连动作都不给，免得下游接手
    return dict(state_source=source, A=A, mgr=mgr, ext=ext, D=D,
                state=f"{A}·{source}·mgr={mgr}·ext={ext}·{D}",
                action=action, comparators=owners)


def find_dups(rows: list) -> dict:
    """同名多份：身份按相对路径区分，名字只用来找可比者。同名两份会互相顶替（实测 docx/xlsx 各两份），
    必须单独报出来而不是当成两个独立技能。"""
    by = {}
    for r in rows:
        by.setdefault(r["name"], []).append(r)
    dups = {n: [x["dir"] for x in rs] for n, rs in by.items() if len(rs) > 1}
    for n, rs in by.items():
        for r in rs:
            r["duplicate_of"] = ", ".join(x["dir"] for x in rs if x is not r) if len(rs) > 1 else ""
    return dups


def prescreen(args, packs, rows, local, mirror) -> None:
    """S1 预筛（口径 2026-10-11，用户拍板）：profile 技能只要**在外部仓库里找得到**（AgentSkills 那些包），
    或**Skills-Manager 管着**，就算已装 —— 不进候选、不判落点。两边都没有的才是候选。
    三档写进每条：homed（一模一样）/ drift（外部有但内容不同）/ managed / local（候选）。"""
    index = mirror.build_index([p["path"] for p in packs], [])
    try:
        mgr_rows = sibling_module("external-dirs.py").manager_skills(pathlib.Path.home() / ".skills-manager")
        managed = {s.get("name", "") for s in mgr_rows if s.get("name")}   # 别叫 rows：会盖住传入的 profile 行
    except SystemExit:                      # CLI 不在/报错都不该让一轮挂掉
        managed = set()
    root = profile_root(args.profile)          # A 轴的两份账本按**目标 profile** 读，不按 HERMES_HOME
    tp = third_party_dirs()
    lock_names = set(read_lock(root))
    manifest = {}
    _bm = root / "skills" / ".bundled_manifest"
    if _bm.is_file():
        for line in _bm.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() and ":" in line:
                manifest[line.split(":", 1)[0]] = line.strip().split(":", 1)[1]
    manager_dirs = {}
    for x in sibling_module("external-dirs.py").manager_skills(pathlib.Path.home() / ".skills-manager"):
        n = x.get("name")
        if n and pathlib.Path(x.get("path", "")).is_dir():
            manager_dirs[n] = pathlib.Path(x["path"])
    for r in rows:
        if "_md" not in r:                                    # 自检里的合成行没有真实目录，跳过
            continue
        r["lock_names"] = lock_names
        ex = tuple(x.strip().lower() for x in (getattr(args, "exclude", None) or []) if x.strip())
        r["excluded"] = any(r["name"].lower().startswith(pfx) for pfx in ex)
        r.update(dream_state(r, mirror, index, manager_dirs, tp, manifest, exclude=ex))
    off = [r["name"] for r in rows if r.get("excluded")]
    if off:
        print(f"[sweep-plan] --exclude 命中 {len(off)} 条，本轮一律不动：{', '.join(sorted(off))}")
    for r in local:
        if r["name"] in managed:
            r["verdict"], r["matched"] = "managed", "skills-manager"
            continue
        c = mirror.classify(r["_md"].parent, index)
        r["verdict"] = c["verdict"]
        r["matched"] = c["matches"][0] if c["matches"] else ""
    return None


def cmd_scan(args) -> int:
    root = profile_root(args.profile)
    mod, parser_path = load_parser(args.parser_script)
    lock, bundled = read_lock(root), read_bundled(root)
    skills_root = root / "skills"
    rows = []
    for md in walk_skill_dirs(skills_root):
        rel = md.relative_to(skills_root)
        fm = parse_fm(mod, md)
        name = str(fm.get("name") or rel.parts[-2]).strip()
        kind = "hub" if name in lock else ("bundled" if name in bundled else "local")
        # 形状缺口：<名字>/SKILL.md 直接躺在 skills/ 下 = 没有类目层（用户要求不存在这种 local skill）
        no_cat = len(rel.parts) == 2
        rows.append(dict(kind=kind, category=("(无类目)" if no_cat else rel.parts[0]), name=name,
                         no_category=no_cat, dir=str(rel.parent), _md=md,
                         desc=" ".join(str(fm.get("description") or "").split()), bytes=md.stat().st_size))
    rows.sort(key=lambda r: (r["kind"] != "local", r["category"], r["name"]))
    dups = find_dups(rows)
    local = [r for r in rows if r["kind"] == "local"]
    if not local:
        die(2, f"no profile-local skills in {root} — nothing to recruit")

    packs = []
    for pack in pack_candidates(args):
        if not pack.is_dir():
            continue
        # A linked worktree of some other clone has `.git` as a FILE, not a directory: its name looks exactly
        # like a pack but pushing there is not what a destination means (measured 2026-10-11: this session's own
        # `AgentSkill-UsingHermes.dream-nostay` worktree showed up as a routable destination).
        if (pack / ".git").is_file():
            continue
        pk_skills_dir = pack / "skills"
        remote = git(pack, "remote", "get-url", "origin")
        installed = {n: str(v.get("install_path", "")) for n, v in lock.items()
                     if pack.name in str(v.get("identifier", "")) or pack.name in str(v.get("metadata", {}).get("repo_url", ""))}
        # Every SKILL.md under skills/, at whatever depth the pack lays its categories out.
        skills = sorted({md.parent.name for md in walk_skill_dirs(pk_skills_dir)}) if pk_skills_dir.is_dir() else []
        packs.append(dict(repo=pack.name, path=str(pack), identifier=identifier(remote), remote=remote,
                          # T-004#B-001：评估时的 HEAD —— 下游拿它当「结论是在哪个上游状态上得出的」
                          head=git(pack, "rev-parse", "HEAD").strip(),
                          has_git=(pack / ".git").exists(),
                          branch=git(pack, "branch", "--show-current"), skills=skills,
                          # An `AgentSkill-*` directory with no SKILL.md anywhere is a placeholder: it is a
                          # legal DESTINATION (the writer creates the repo) but can never be installed back.
                          empty=not skills and not (pack / "SKILL.md").is_file(),
                          installed=installed,
                          categories=sorted({p.split("/")[0] for p in installed.values() if p})))
    if not packs:
        die(2, "没有候选落点目录：" + (f"--external-dirs {getattr(args, 'external_dirs', '')}"
               if getattr(args, "external_dirs", "") else f"no AgentSkill-* checkout under {args.packs_root}"))
    mirror = sibling_module("skill-mirror.py")
    prescreen(args, packs, rows, local, mirror)
    # 候选 = 16 态里 action 为 judge-destination 的那一类（= 三方都没主）。旧的 verdict==local 口径保留
    # 只作对账：两边不一致就报出来，别让两套判据各自漂。
    cands = [r for r in local if r.get("action") == "judge-destination"]
    legacy = {r["dir"] for r in local if r["verdict"] == "local"}
    by_action = {r["dir"] for r in cands}
    if legacy != by_action:
        only_legacy = sorted(legacy - by_action)[:5]
        only_action = sorted(by_action - legacy)[:5]
        print(f"[sweep-plan] ⚠ 候选口径不一致：只按 verdict=local 有 {len(legacy - by_action)} 条"
              f"（{only_legacy}）；只按 action=judge-destination 有 {len(by_action - legacy)} 条"
              f"（{only_action}）—— 以 action 为准，另一套要跟着改")
    routable = [p for p in packs if not p["empty"]]
    if not routable:
        die(2, f"every AgentSkill-* under {args.packs_root} is a placeholder (no SKILL.md) — "
               "there is no pack index to route against")

    out = pathlib.Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "inventory.tsv").write_text(
        "kind\tcategory\tname\tno_category\tverdict\tmatched\tdir\tduplicate_of\tstate\taction\tdesc\n" + "".join(
            f"{r['kind']}\t{r['category']}\t{r['name']}\t{str(r['no_category']).lower()}\t"
            f"{r.get('verdict', '-')}\t{r.get('matched', '')}\t{r['dir']}\t{r.get('duplicate_of', '')}\t"
            f"{r.get('state', '')}\t{r.get('action', '')}\t{r['desc'][:200]}\n"
            for r in rows), encoding="utf-8")
    # Frozen judge input: one P<k> per candidate, script-generated so that no hand transcription can drift
    # from what the judges were actually asked (the pack's own judge lesson). v2 carries the absolute path so
    # the judge CANNOT judge from the name alone — it must read the full SKILL.md; `bodies` inlines the text
    # for judges that only ever see a string (chunked API judge), so both routes see the same full document.
    bodies = {}
    judge_input = []
    for i, r in enumerate(cands, 1):
        md = r["_md"]
        refs = sorted(str(p.relative_to(md.parent)) for p in md.parent.rglob("*")
                      if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts)
        text = md.read_text(encoding="utf-8", errors="replace")
        bodies[f"P{i}"] = text
        judge_input.append(dict(id=f"P{i}", skill=r["name"], category=r["category"], dir=r["dir"],
                                state=r.get("state", ""), action=r.get("action", ""),
                                comparators=r.get("comparators", []),
                                no_category=r["no_category"], description=r["desc"],
                                path=str(md.parent), skill_md=str(md), refs=refs[:30],
                                body_bytes=len(text.encode("utf-8")),
                                body_lines=len(text.splitlines())))
    (out / "judge-input.json").write_text(json.dumps(judge_input, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    (out / "judge-input-bodies.json").write_text(json.dumps(
        [dict(r, body=bodies[r["id"]]) for r in judge_input], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    index_blocks = []
    for pk in routable:
        idx = out / f"index-{pk['repo']}.txt"
        subprocess.run([sys.executable, "-B", str(parser_path), pk["path"], "--out", str(idx),
                        "--quiet", "--max-desc", "110"], capture_output=True, text=True)
        body = idx.read_text(encoding="utf-8").strip() if idx.is_file() else ""
        pk["index_rows"] = len(body.splitlines()) if body else 0
        if body:
            index_blocks.append(f"## {pk['repo']}  ({pk['identifier'] or 'no-remote'})\n{body}")
    (out / "pack-index.txt").write_text("\n\n".join(index_blocks) + "\n", encoding="utf-8")
    (out / "packs.json").write_text(json.dumps(packs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # T-004#B-001 输入指纹：评估时的每个 pack HEAD + 本次产出里会被下游消费的文件的 sha256。
    # 下游（判官 / merge / apply）先比这里，再决定要不要在旧结论上动手。
    (out / "inputs.json").write_text(json.dumps(dict(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        packs={p["repo"]: p["head"] for p in packs},
        # profile 侧也要有"评估时"的快照：技能树被别的会话重排过一次（实测 00:17 整棵扁平化），
        # 没有这个指纹，S1 的结论会在 S4 之前悄悄失效。
        profile_root=str(skills_root),
        profile={r["dir"]: dir_fingerprint(mirror, r["_md"].parent) for r in rows},
        files=file_digests(out, ("judge-input.json", "packs.json", "judge-input-bodies.json",
                                 "inventory.tsv"))), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The judge's answer space, v2: EVERY AgentSkill-* directory name, split by axis, plus the two naming
    # formulas for a brand-new pack. `routable` (remote + SKILL.md) only says whether a pack can be installed
    # back afterwards — it never restricts which name may be a destination.
    (out / "answer-space.json").write_text(json.dumps(dict(
        dirs=[p["repo"] for p in packs],
        tool_dirs=[p["repo"] for p in packs if p["repo"].startswith(TOOL_PREFIX)],
        topic_dirs=[p["repo"] for p in packs if not p["repo"].startswith(TOOL_PREFIX)],
        routable=[p["repo"] for p in routable],
        new=dict(tool=f"{TOOL_PREFIX}<工具名>", topic=f"{TOPIC_PREFIX}<主题名>")),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in ("local", "hub", "bundled")}
    idx_chars = (out / "pack-index.txt").stat().st_size
    print(f"[sweep-plan] profile={root} skills={len(rows)} local={counts['local']} "
          f"hub={counts['hub']} bundled={counts['bundled']}")
    print(f"[sweep-plan] packs={len(packs)} routable={len(routable)} placeholder={len(packs) - len(routable)} "
          f"pack_skills={sum(len(p['skills']) for p in routable)} pack_index_est_tokens~{idx_chars // 2}")
    print(f"[sweep-plan] dest_space={len(packs)} 个目录名（tool {len([p for p in packs if p['repo'].startswith(TOOL_PREFIX)])}"
          f" + topic {len([p for p in packs if not p['repo'].startswith(TOOL_PREFIX)])}）→ answer-space.json")
    n_nocat = sum(1 for r in cands if r["no_category"])
    pre = {v: sum(1 for r in local if r["verdict"] == v) for v in ("homed", "drift", "managed", "local")}
    (out / "prescreen.json").write_text(json.dumps(dict(
        counts=pre,
        rules="外部仓库里找得到（AgentSkills 各包，同名同内容=homed / 内容不同=drift）或 Skills-Manager 管着（managed）"
              " = 已装，不进候选；两边都没有（local）才判落点",
        homed=[r["name"] for r in local if r["verdict"] == "homed"],
        drift=[dict(skill=r["name"], external=r["matched"]) for r in local if r["verdict"] == "drift"],
        managed=[r["name"] for r in local if r["verdict"] == "managed"],
        candidates=[dict(skill=r["name"], dir=r["dir"], category=r["category"],
                         no_category=r["no_category"], duplicate_of=r.get("duplicate_of", "")) for r in cands],
        duplicates=dups,
    ), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    st_counts = collections.Counter(r.get("state", "") for r in rows if r.get("state"))
    act_counts = collections.Counter(r.get("action", "") for r in rows if r.get("action"))
    gen_counts = collections.Counter(
        ("mgr=%s·ext=%s·%s" % (r["mgr"], r["ext"], r["D"])) for r in rows if r.get("mgr"))
    (out / "states.json").write_text(json.dumps(dict(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        rules="A=local|第三方(built-in/official/bundled/hub) · B=skills-manager 管否 · C=external_dirs 管否 · "
              "D=与可比者内容同否（无所有者则坍缩）→ hermes 16 态；general agent 去掉 A 轴 → 7 态",
        counts_hermes=dict(st_counts), actions=dict(act_counts), counts_general=dict(gen_counts),
        rows=[dict(skill=r["name"], dir=r["dir"], state=r.get("state", ""), action=r.get("action", ""),
                   source=r.get("state_source", ""), comparators=r.get("comparators", []))
              for r in rows if r.get("state")]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[sweep-plan] 16 态（hermes）=" + " · ".join(f"{k} {v}" for k, v in st_counts.most_common()))
    print(f"[sweep-plan] 逐态去向=" + " · ".join(f"{k}={v}" for k, v in act_counts.most_common()))
    print(f"[sweep-plan] 预筛（已装的不进候选）：homed={pre['homed']} drift={pre['drift']} "
          f"managed={pre['managed']} → 候选={pre['local']}（{out / 'prescreen.json'}）")
    if dups:
        print(f"[sweep-plan] ⚠ 同名多份 {len(dups)} 组（身份按相对路径区分，名字相同会互相顶替）："
              + " · ".join(f"{n}: {', '.join(ds)}" for n, ds in dups.items()))
    print(f"[sweep-plan] no_category={n_nocat}（候选里直接躺在 skills/ 下、没有类目层的）")
    print(f"[sweep-plan] candidates={len(cands)} judge_input={out / 'judge-input.json'} "
          f"({(out / 'judge-input.json').stat().st_size} B) bodies={(out / 'judge-input-bodies.json').stat().st_size} B "
          f"parser={parser_path}")
    print(f"[sweep-plan] 判官必须读每条 P<k> 的 skill_md 全文（judge-input.json 给了绝对路径；只吃文本的判官用 "
          f"judge-input-bodies.json）")
    print(f"[sweep-plan] next: judge every P<k>（`judge.py --dir <out>`，分块直调） -> {out}/verdicts*.jsonl, then "
          f"`merge --dir {out} --profile {args.profile}`")
    return 0


def _resolve(r: dict, v: dict, packs: dict, by_norm: dict, bad: list):
    """Validate one verdict's `dest` and turn it into a resolution, or record why it was rejected.

    The script never names or classifies anything itself: it only (a) rejects the shapes the user's v2 rules
    forbid, (b) applies the two naming formulas, and (c) lets a new topic colliding with an existing
    directory name reuse that directory instead of creating a twin.
    """
    rid = r["id"]
    dest = v.get("dest")

    def reject(msg: str):
        bad.append(f"{rid}: {msg}")
        return None

    if isinstance(dest, str):                       # a bare string is only ever the v1 / stay-local shape
        if dest.strip() in (STAY_LOCAL, "stay_local", "stay"):
            return reject(f"判官给了 {dest!r} —— 本版已废除 stay local：每条候选都必须落进一个包"
                          "（用户 2026-10-10 改口径）。改判成工具轴或主题轴的落点再交")
        dest = {"kind": "existing", "repo": dest}
    if not isinstance(dest, dict):
        return reject("缺 dest 对象（v2 形状：dest.kind = \"existing\" 或 \"new\"）")

    kind = str(dest.get("kind") or "").strip()
    if kind == "existing":
        repo = str(dest.get("repo") or "").strip()
        if repo in (STAY_LOCAL, "stay_local", "stay"):
            return reject(f"dest.repo={repo!r} 就是 stay local —— 本版已废除（用户 2026-10-10 改口径）")
        if repo not in packs:
            return reject(f"dest.repo {repo!r} 不在 {len(packs)} 个 AgentSkill-* 目录名里"
                          "（落点空间是全部目录名，不是只有能 install 的那些；名字写错就重判）")
        action = str(dest.get("action") or v.get("action") or "move").strip()
        if action not in ACTIONS:
            return reject(f"dest.action {action!r} 不是 {ACTIONS} 之一")
        dskill = str(dest.get("dest_skill") or v.get("dest_skill") or "__new__").strip() or "__new__"
        if not dskill.startswith("__") and dskill not in packs[repo]["skills"]:
            avail = ", ".join(packs[repo]["skills"][:8]) or "（该目录还没有任何技能）"
            return reject(f"dest_skill {dskill!r} 不在 {repo} 的技能清单里 —— 可用：{avail}")
        return dict(repo=repo, kind="existing", dest_skill=dskill, action=action, collided=False,
                    axis="", topic="", personal=False, boundary="")

    if kind == "new":
        axis = str(dest.get("axis") or "").strip()
        if axis not in ("tool", "topic"):
            return reject(f"dest.axis {axis!r} 必须是 \"tool\"（主语是某个软件/工具）或 \"topic\""
                          "（主语是一类任务/学科/项目）—— 二选一先判，别跳过")
        topic = str(dest.get("topic") or "").strip()
        if not topic:
            return reject("dest.topic 为空：新包必须先说出工具名或主题名")
        boundary = str(dest.get("boundary") or "").strip()
        if not boundary:
            return reject("dest.boundary 为空：新包必须写明这个包**不吃**什么（边界是判据的一部分）")
        name = pack_name_for(axis, topic)
        hit = by_norm.get(norm(name))
        if not hit:                                 # 同一 topic 的多条在这一行合成一个包（先到者定名）
            by_norm[norm(name)] = name
        return dict(repo=hit or name, kind="new", dest_skill="__new__", action="move",
                    collided=bool(hit), axis=axis, topic=topic,
                    personal=bool(dest.get("personal")), boundary=boundary)

    return reject(f"dest.kind {kind!r} 不认识（只能是 \"existing\" 或 \"new\"）")


def cmd_merge(args) -> int:
    out = pathlib.Path(args.dir).expanduser()
    all_input = json.loads((out / "judge-input.json").read_text(encoding="utf-8"))
    # S1.5 分类闸门（triage.py）把「用户偏好 / 本机环境」的候选分流去记忆（不进包），它们的 id 不该再算进
    # S2 的覆盖率。产物在 = 本轮跑了闸门；产物不在 = 没跑闸门，按全量走（向后兼容）。
    in_scope = None
    routed = out / "judge-input-bodies.routed.json"
    if routed.is_file():
        in_scope = {r["id"] for r in json.loads(routed.read_text(encoding="utf-8"))}
        dropped = [r["id"] for r in all_input if r["id"] not in in_scope]
        print(f"[sweep-plan] 分类闸门：{len(dropped)} 条判为 preference/env → 不进包（写记忆），"
              f"本轮判落点 {len(in_scope)} 条")
    judge_input = [r for r in all_input if in_scope is None or r["id"] in in_scope]
    packs = {p["repo"]: p for p in json.loads((out / "packs.json").read_text(encoding="utf-8"))}
    routable = {k: v for k, v in packs.items() if not v.get("empty")}
    by_norm: dict = {}
    for name in packs:                              # uniquification: same topic -> the directory already here
        by_norm.setdefault(norm(name), name)

    verdicts, bad = {}, []
    paths = [pathlib.Path(p).expanduser() for p in args.verdicts] if args.verdicts else sorted(out.glob("verdicts*.jsonl"))
    for f in paths:
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                v = json.loads(ln)
            except json.JSONDecodeError:
                bad.append(f"{f.name}: not JSON: {ln[:60]}")
                continue
            vid = str(v.get("id", "")).strip()
            if not vid:
                bad.append(f"{f.name}: verdict without id: {ln[:60]}")
            elif vid in verdicts:
                bad.append(f"{f.name}: duplicate verdict for {vid}")
            else:
                verdicts[vid] = v
    known = {r["id"] for r in all_input}
    bad += [f"{vid}: id is not in judge-input.json (ids are a scan artifact — re-run scan, keep the pair)"
            for vid in verdicts if vid not in known]

    resolved, groups = [], {}
    for r in judge_input:
        v = verdicts.get(r["id"], {})
        if not v:
            groups.setdefault(UNJUDGED, []).append((r, {}, "unjudged", {}))
            continue
        res = _resolve(r, v, packs, by_norm, bad)
        if res is None:                             # rejected: recorded in `bad`, never silently dropped
            continue
        resolved.append(dict(r, **{k: res[k] for k in ("repo", "kind", "dest_skill", "action",
                                                       "collided", "axis", "topic", "personal", "boundary")},
                             why=str(v.get("why") or ""), evidence=str(v.get("evidence") or ""),
                             confidence=v.get("confidence", None), source=str(v.get("source") or "agent")))
        groups.setdefault(res["repo"], []).append((r, v, res["action"], res))
    missing = [r["id"] for r in judge_input if r["id"] not in verdicts]

    # T-004#B-001：拿 scan 的指纹比一遍。上游动过 ⇒ 这一轮结论带 stale，不许直接动手。
    rec = json.loads((out / "inputs.json").read_text()) if (out / "inputs.json").is_file() else {}
    # profile 侧指纹：与 scan 那一刻比，谁变了谁作废（同一个 stale 口径，只是换到技能树这边）
    prof_changed = []
    if rec.get("profile"):
        mirror = sibling_module("skill-mirror.py")
        base = pathlib.Path(rec.get("profile_root") or (pathlib.Path.home() / ".hermes" / "skills"))
        for rel, fp in rec["profile"].items():
            d = base / rel
            now = dir_fingerprint(mirror, d) if d.is_dir() else "（已不在）"
            if now != fp:
                prof_changed.append(rel)
        if prof_changed:
            print(f"[sweep-plan] ⚠ profile 侧变了 {len(prof_changed)} 个技能（别的会话在动这棵树）"
                  f"：{', '.join(prof_changed[:6])}{' …' if len(prof_changed) > 6 else ''} → profile-stale.json")
        (out / "profile-stale.json").write_text(
            json.dumps(dict(changed=prof_changed, profile_root=str(base)), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    rec_packs = rec.get("packs") or {}
    # 「现在」的 HEAD 必须**现场读**（packs.json 是 scan 那一刻的快照，拿它比等于自己跟自己比）
    now_packs = {}
    for name, info in packs.items():
        pth = pathlib.Path((info or {}).get("path") or "")
        now_packs[name] = git(pth, "rev-parse", "HEAD").strip() if (pth / ".git").exists() \
            else (info or {}).get("head")
    changed = sorted(k for k in set(rec_packs) | set(now_packs) if rec_packs.get(k) != now_packs.get(k))
    files_now = file_digests(out, ("judge-input.json", "packs.json", "judge-input-bodies.json"))
    rec_files = rec.get("files") or {}
    # 只比两边都有的文件：merge 不消费的文件（如 inventory.tsv）不该被当成「变了」
    files_changed = sorted(k for k in set(rec_files) & set(files_now) if files_now[k] != rec_files[k])
    for row in resolved:
        repo = row.get("repo") or ""
        # input_commit = **评估时**（scan 那一刻）的 HEAD；上游现在是什么由 stale 表达
        row["input_commit"] = rec_packs.get(repo) or now_packs.get(repo) or None
        row["stale"] = bool(repo and repo in changed)
    (out / "stale.json").write_text(json.dumps(dict(
        recorded_at=rec.get("generated_at"), changed_packs=changed, changed_files=files_changed,
        now={k: now_packs[k] for k in changed if k in now_packs},
        recorded={k: rec_packs[k] for k in changed if k in rec_packs}), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    if changed or files_changed:
        print(f"[sweep-plan] ⚠ stale：pack HEAD 变了 {changed[:5]} · 输入文件变了 {files_changed}"
              f" —— 这些行的结论作废，先重跑 scan+merge（stale.json）")

    info: dict = {}
    for repo, items in groups.items():
        if repo == UNJUDGED:
            continue
        new_items = [t for t in items if t[3].get("kind") == "new"]
        info[repo] = dict(
            on_disk=repo in packs, is_new_pack=repo not in packs, from_new_verdicts=bool(new_items),
            collided=sum(1 for t in items if t[3].get("collided")),
            axis=new_items[0][3]["axis"] if new_items else "",
            topic=new_items[0][3]["topic"] if new_items else "",
            boundary=" / ".join(sorted({t[3]["boundary"] for t in new_items if t[3].get("boundary")})),
            personal=bool(new_items) and len(items) == 1 and all(t[3].get("personal") for t in new_items),
            routable=repo in routable)

    lines = ["# Sweep plan", "",
             f"profile `{args.profile}` · candidates {len(judge_input)} · judged {len(verdicts)}"
             f" · unjudged {len(missing)} · resolved {len(resolved)}", ""]
    new_packs = sorted(r for r, i in info.items() if i["is_new_pack"])
    if new_packs:
        lines += ["## 拟新建的包（`gh repo create <owner>/<name> --private`，只在有人在的会话里做）", ""]
        for repo in new_packs:
            i = info[repo]
            lines.append(f"- `{repo}` · 轴 {i['axis']} · {len(groups[repo])} 条 · boundary：{i['boundary']}"
                         + (" · **私人仓库**（personal 且只 1 条）" if i["personal"] else ""))
        lines.append("")
    reused = sorted(r for r, i in info.items() if i["from_new_verdicts"] and i["on_disk"])
    if reused:
        lines += ["## 判官判了新包、但归一化后撞上已有目录 → 复用该仓库", ""]
        lines += [f"- `{r}`：{info[r]['collided']} 条落在它上面" for r in reused] + [""]

    for dest in sorted(groups, key=lambda d: (d == UNJUDGED, info.get(d, {}).get("is_new_pack", False), d)):
        if dest == UNJUDGED:
            head, meta = "未判定（= 拒收，不生成 plan 交付）", []
        elif info[dest]["is_new_pack"]:
            head, meta = f"新包 {dest}", [f"轴上 {info[dest]['axis']} · topic `{info[dest]['topic']}`"
                                          f" · boundary {info[dest]['boundary']}"]
        else:
            pk = packs[dest]
            head = dest
            meta = [f"clone `{pk['path']}` · id `{pk['identifier'] or '无远端'}` · branch `{pk['branch']}`"
                    f" · 已装类目 `{', '.join(pk['categories']) or '无证据'}`"
                    f"{'' if not pk['empty'] else ' · ⚠️ 空壳（无 SKILL.md）'}"
                    f"{'' if dest in routable else ' · ⚠️ 没有远端，装不回来'}"]
        lines += [f"## {head}", ""] + meta + ["",
                  "| id | skill | 落点技能 | 动作 | 依据 | 判据原句 | 置信 |", "|---|---|---|---|---|---|---|"]
        for r, v, action, res in sorted(groups[dest], key=lambda t: int(t[0]["id"][1:])):
            lines.append(f"| {r['id']} | `{r['skill']}` | {res.get('dest_skill') or '—'} | {action} | "
                         f"{str(v.get('why') or '').replace('|', '/')[:80]} | "
                         f"{str(v.get('evidence') or '').replace('|', '/')[:70]} | {v.get('confidence', '—')} |")
        lines.append("")
    (out / "resolved.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # S7 的装回命令清单（只生成、不执行）。2026-10-12 起不走 agent 自己的安装命令（hermes 那套没有跨 agent 的普适性），
    # 按归属分三条路（docs/flow.md §3「S7 装回的三条路」）：
    #   ① 归 Skills-Manager 管 → 交它装（它按自己的来源取件）
    #   ② 不归它管、但本次运行传了 --skills-manager → 交它从本地那份包装（install --local）
    #   ③ 两者都不是 → 直接复制（与 agent 无关）
    mgr_cli = str(pathlib.Path.home() / ".skills-manager" / "bin" / "skills-manager-cli")   # 绝对路径：非交互 shell 的 PATH 里没有它
    mgr_flag = bool(getattr(args, "skills_manager", False))
    agent = getattr(args, "manager_agent", "") or f"hermes-{args.profile}"
    prof_skills = profile_root(args.profile) / "skills"
    touched, notes, via_mgr, via_copy = [], [], [], []
    # 落点根：packs.json 里任一包路径的父目录（新包还没有路径，只能这么推）
    try:
        _pk = json.loads((out / "packs.json").read_text())
        root = pathlib.Path(_pk[0]["path"]).parent if _pk else pathlib.Path(args.packs_root).expanduser()
    except (OSError, json.JSONDecodeError, KeyError):
        root = pathlib.Path(args.packs_root).expanduser()

    def emit(name: str, local: pathlib.Path, why: str) -> None:
        """一枚技能按三条路择一，写进清单。"""
        entry = prof_skills / name
        if entry.is_symlink():                                  # profile 里是符号链接 = 它部署的
            via_mgr.append(f'"{mgr_cli}" skills update "{name}"        # {why}·profile 里是它部署的符号链接')
        elif mgr_flag:
            via_mgr.append(f'"{mgr_cli}" skills install --local --name "{name}" "{local}"   # {why}·从本地包装')
            via_mgr.append(f'"{mgr_cli}" skills deploy --agent {agent} "{name}"')
        else:
            via_copy.append(f'cp -R "{local}" "{prof_skills}/"        # {why}·直接复制（与 agent 无关）')

    for dest, items in groups.items():
        if dest == UNJUDGED:
            continue
        if info[dest]["is_new_pack"]:
            notes.append(f"# {dest}: 本轮**新建的包**（apply 写入时建本地仓库；远端 `gh repo create` 留给人）")
            for r, v, action, res in sorted(items, key=lambda t_: int(t_[0]["id"][1:])):
                emit(r["skill"], root / dest / "skills" / r["skill"], "新建包")
            continue
        pk = packs[dest]                           # not a new pack => the directory is on disk
        if pk["empty"]:
            notes.append(f"# {dest}: 空壳落点（无 SKILL.md）——写入后按三条路装回（远端标识符：{pk['identifier'] or '无'}）")
            continue
        touched.append(dest)
        for r, v, action, res in sorted(items, key=lambda t: int(t[0]["id"][1:])):
            name = r["skill"]
            local = pathlib.Path(pk["path"]) / "skills" / name
            managed = "mgr=yes" in (r.get("state") or "")
            if managed:
                via_mgr.append(f'"{mgr_cli}" skills update "{name}"        # 第①路：归它管，它按自己的来源取件')
                via_mgr.append(f'"{mgr_cli}" skills deploy --agent {agent} "{name}"')
            elif action == "move":
                emit(name, local, "第②/③路")
            # action=merge：正文并进了接收方技能，不单独装（接收方作为本包既有成员会整包重装）
            if action == "merge" and str(res.get("dest_skill") or "") not in ("", "__new__"):
                notes.append(f"# {r['skill']}: 正文并进 {dest}/{res['dest_skill']}（writer 改正文；装回按上面同一枚技能的清单走）")
        for name in sorted(pk["installed"]):        # 整包重装：本包既有成员
            emit(name, pathlib.Path(pk["path"]) / "skills" / name, "本包既有成员")
    cmds = ["#!/bin/sh",
            f"# 装回 {args.profile!r} —— 三条路：① 交 Skills-Manager ② 它从本地包装 ③ 直接复制（不走 agent 自己的安装命令）",
            f"# 本次 --skills-manager={'yes' if mgr_flag else 'no'} · manager agent={agent} · 类目层由装它的人决定",
            ""] + notes
    if via_mgr:
        cmds += ["", "# ① / ② 交 Skills-Manager："] + sorted(set(via_mgr))
    if via_copy:
        cmds += ["", "# ③ 直接复制到 profile（与 agent 无关）："] + sorted(set(via_copy))
    cmds += [""]
    (out / "install-cmds.sh").write_text("\n".join(cmds) + "\n", encoding="utf-8")

    if bad:                                        # reject BEFORE emitting a plan nobody should consume
        die(4, f"{len(bad)} malformed verdict(s):\n  " + "\n  ".join(bad[:10]))
    if missing:
        die(4, f"coverage {len(verdicts)}/{len(judge_input)} — unjudged: "
               f"{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}")

    # T-004#B-001：产物里明写「这些结论是在哪个上游状态上得出的」。
    lines += ["## 输入指纹（T-004#B-001）", "",
              f"扫描指纹 `{rec.get('generated_at') or '（没有 inputs.json —— 先跑 scan）'}`"
              f" · 上游变动 {len(changed)} 个包 / {len(files_changed)} 个输入文件", "",
              "| 包 | input_commit | stale |", "|---|---|---|"]
    for repo in sorted({(r.get("repo") or "") for r in resolved} - {""}):
        lines.append(f"| `{repo}` | `{(now_packs.get(repo) or rec_packs.get(repo) or '—')[:12]}` | "
                     f"{'⚠ 上游变了' if repo in changed else 'ok'} |")
    lines += [""]
    (out / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[sweep-plan] plan={out / 'plan.md'} install_cmds={out / 'install-cmds.sh'}"
          f" resolved={out / 'resolved.json'} packs_touched={len(touched)} "
          f"({', '.join(sorted(touched)) or 'none'})")
    print(f"[sweep-plan] 新包 {len(new_packs)} 个（复用已有目录 {len(reused)} 个）："
          f"{', '.join(new_packs) or 'none'}")
    for dest in sorted(groups, key=lambda d: -len(groups[d])):
        tag = (" → 新包" if info.get(dest, {}).get("is_new_pack")
               else (" → 复用" if info.get(dest, {}).get("from_new_verdicts") else ""))
        print(f"[sweep-plan]   {dest}: {len(groups[dest])} 条{tag}")
    return 0


def self_test() -> int:
    """Throwaway tree: the three kinds must classify, .archive skipped, merge must refuse a gap."""
    import tempfile
    global HERMES_HOME
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sweep-selftest-"))
    sk = tmp / "skills"
    for cat, name in (("cat", "hubskill"), ("cat", "bundledskill"), ("cat", "localskill"),
                      ("cat/sub", "localskill2"), (".archive", "oldskill"), ("", "rootskill")):
        d = sk / cat / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d-{name}\n---\n# body of {name}\n",
                                    encoding="utf-8")
    (sk / ".hub").mkdir()
    (sk / ".hub" / "lock.json").write_text(json.dumps({"installed": {"hubskill": {
        "identifier": "skills-sh/flmaximwang/AgentSkill-UsingFake/skills/hubskill",
        "install_path": "cat/hubskill"}}}), encoding="utf-8")
    (sk / ".bundled_manifest").write_text("bundledskill:abc\n", encoding="utf-8")
    packs = tmp / "packs"
    (packs / "AgentSkill-UsingFake" / "skills" / "hubskill").mkdir(parents=True)
    (packs / "AgentSkill-UsingFake" / "skills" / "hubskill" / "SKILL.md").write_text(
        "---\nname: hubskill\ndescription: fake\n---\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(packs / "AgentSkill-UsingFake")], check=True)
    subprocess.run(["git", "-C", str(packs / "AgentSkill-UsingFake"), "remote", "add", "origin",
                    "git@github.com:flmaximwang/AgentSkill-UsingFake.git"], check=True)
    (packs / "AgentSkill-Empty").mkdir()          # a placeholder dir IS a legal destination in v2
    subprocess.run(["git", "init", "-q", str(packs / "AgentSkill-Empty")], check=True)
    # a linked worktree of another clone: its name looks like a pack but `.git` is a file, not a directory
    (packs / "AgentSkill-UsingHermes.wt").mkdir()
    (packs / "AgentSkill-UsingHermes.wt" / ".git").write_text("gitdir: /tmp/nope/.git/worktrees/wt\n")
    (packs / "AgentSkill-UsingHermes.wt" / "skills" / "x").mkdir(parents=True)
    (packs / "AgentSkill-UsingHermes.wt" / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: wt\n---\n", encoding="utf-8")

    real = HERMES_HOME
    HERMES_HOME = tmp
    try:
        assert profile_root("default") == tmp
        assert cmd_scan(argparse.Namespace(profile="default", packs_root=str(packs), out=str(tmp / "out"),
                                          parser_script="", repo_stem="")) == 0
        rows = [ln.split("\t") for ln in (tmp / "out" / "inventory.tsv").read_text().splitlines()[1:]]
        kinds = {r[2]: r[0] for r in rows}
        assert kinds == {"hubskill": "hub", "bundledskill": "bundled", "localskill": "local",
                         "localskill2": "local", "rootskill": "local"}, kinds
        # 没有类目层的 local skill 必须被标出来（用户要求这种形状不许存在）
        assert {r[2]: r[3] for r in rows} == {"hubskill": "false", "bundledskill": "false",
                                             "localskill": "false", "localskill2": "false",
                                             "rootskill": "true"}
        ji = json.loads((tmp / "out" / "judge-input.json").read_text())
        assert [r["id"] for r in ji] == ["P1", "P2", "P3"], ji
        assert next(r for r in ji if r["skill"] == "rootskill")["no_category"] is True
        assert {r["skill"] for r in ji} == {"rootskill", "localskill", "localskill2"}
        # v2: 判官拿到的是路径与正文，不是只给 name+desc（否则就是用户驳回的偷懒判法）
        assert all(r["skill_md"].endswith("/SKILL.md") and pathlib.Path(r["skill_md"]).is_file() for r in ji), ji
        assert all(r["body_bytes"] > 0 and r["body_lines"] >= 4 for r in ji), ji
        bodies = json.loads((tmp / "out" / "judge-input-bodies.json").read_text())
        assert [b["id"] for b in bodies] == ["P1", "P2", "P3"]
        assert all(f"body of {b['skill']}" in b["body"] for b in bodies), bodies
        asp = json.loads((tmp / "out" / "answer-space.json").read_text())
        assert sorted(asp["dirs"]) == ["AgentSkill-Empty", "AgentSkill-UsingFake"], asp
        assert asp["tool_dirs"] == ["AgentSkill-UsingFake"] and asp["topic_dirs"] == ["AgentSkill-Empty"], asp
        assert asp["routable"] == ["AgentSkill-UsingFake"], asp
        # 别的 clone 的 worktree（`.git` 是文件）名字再像包也不算落点
        assert "AgentSkill-UsingHermes.wt" not in asp["dirs"], asp["dirs"]
        assert not any("wt" in d for d in asp["dirs"]), asp["dirs"]
        pk = [p for p in json.loads((tmp / "out" / "packs.json").read_text())
              if p["repo"] == "AgentSkill-UsingFake"][0]
        assert pk["installed"] == {"hubskill": "cat/hubskill"} and pk["categories"] == ["cat"], pk
        assert [p["empty"] for p in json.loads((tmp / "out" / "packs.json").read_text())
                if p["repo"] == "AgentSkill-Empty"] == [True]      # a no-SKILL.md repo is not routable
        assert pk["identifier"] == "flmaximwang/AgentSkill-UsingFake", pk["identifier"]

        def merge(*verdicts, code=0):
            """One JSONL per verdict; return (plan, cmds) — both must be absent when the run was rejected."""
            for p in (tmp / "out").glob("verdicts*.jsonl"):
                p.unlink()
            for f in ("plan.md", "install-cmds.sh"):
                (tmp / "out" / f).unlink(missing_ok=True)
            for i, v in enumerate(verdicts):
                (tmp / "out" / f"verdicts-task-{i+1}.jsonl").write_text(json.dumps(v) + "\n", encoding="utf-8")
            try:
                rc = cmd_merge(argparse.Namespace(dir=str(tmp / "out"), profile="default", verdicts=[]))
            except SystemExit as e:
                rc = e.code
            assert rc == code, (rc, code, verdicts)
            if code != 0:               # 拒收时连 plan 都不该落盘（别人会照着 plan 动手）
                assert not (tmp / "out" / "plan.md").exists(), "rejected verdicts must not emit a plan"
            return ((tmp / "out" / "plan.md").read_text() if (tmp / "out" / "plan.md").is_file() else "",
                    (tmp / "out" / "install-cmds.sh").read_text()
                    if (tmp / "out" / "install-cmds.sh").is_file() else "")

        ok1 = {"id": "P1", "dest": {"kind": "existing", "repo": "AgentSkill-UsingFake", "action": "move"},
               "why": "w", "evidence": "e"}
        ok2 = {"id": "P2", "dest": {"kind": "existing", "repo": "AgentSkill-Empty", "action": "move"},
               "why": "w", "evidence": "e"}
        ok3 = {"id": "P3", "dest": {"kind": "existing", "repo": "AgentSkill-UsingFake", "dest_skill": "hubskill",
                                    "action": "merge"}, "why": "w", "evidence": "e"}

        merge(ok1, code=4)                                          # coverage gap must fail, not go quiet
        merge(dict(ok1, id="P9"), code=4)                           # id that is not in the frozen input
        merge(dict(ok1, dest="__stay_local__"), ok2, ok3, code=4)   # v2 废除 stay local（字符串形态）
        merge(dict(ok1, dest={"kind": "existing", "repo": STAY_LOCAL}), ok2, ok3, code=4)
        merge(dict(ok1, dest={"kind": "existing", "repo": "AgentSkill-Nope"}), ok2, ok3, code=4)
        merge(dict(ok1, dest={"kind": "existing", "repo": "AgentSkill-UsingFake",
                              "dest_skill": "ghost-skill", "action": "merge"}), ok2, ok3, code=4)
        merge(dict(ok1, dest={"kind": "new", "axis": "tool", "topic": "Fake"}), ok2, ok3, code=4)  # no boundary
        merge(dict(ok1, dest={"kind": "new", "axis": "subject", "topic": "Fake",
                              "boundary": "b"}), ok2, ok3, code=4)  # axis 只能 tool|topic
        merge(ok1, ok2, ok3, code=0)                                # the happy path must actually produce a plan
        plan, cmds = merge(
            {"id": "P1", "dest": {"kind": "new", "axis": "tool", "topic": "Fake",
                                  "boundary": "不吃 git 历史"}, "why": "w", "evidence": "e"},
            {"id": "P2", "dest": {"kind": "new", "axis": "topic", "topic": "RunningLocalBatches",
                                  "boundary": "不吃长驻服务"}, "why": "w", "evidence": "e"},
            {"id": "P3", "dest": {"kind": "new", "axis": "topic", "topic": "running local batches",
                                  "boundary": "不吃长驻服务"}, "why": "w", "evidence": "e"}, code=0)
        # new.topic 归一化后撞上已有目录名 → 复用那个仓库，不建孪生目录
        assert "复用该仓库" in plan and "`AgentSkill-UsingFake`" in plan, plan
        # 同一 topic 的多条（"RunningLocalBatches" vs "running local batches"）必须合成一个包
        assert plan.count("## 新包 ") == 1, plan
        assert "## 新包 AgentSkill-RunningLocalBatches" in plan, plan
        assert "- `AgentSkill-RunningLocalBatches` · 轴 topic · 2 条" in plan, plan
        assert "skills-manager-cli skills" in cmds or "cp -R" in cmds, cmds     # 装回只走三条路，不再有 hermes 命令
        assert "hermes " not in cmds.replace("hermes-", ""), cmds               # 不留 hermes 自己的安装命令
        assert "本轮**新建的包**" in cmds, cmds                  # 新包也列出来（包由 apply 写入时建）        assert "AgentSkill-RunningLocalBatches/skills/localskill" in cmds, cmds   # 新包的技能也要进装回清单
        assert "AgentSkill-Empty" not in cmds, cmds     # 空壳落点不许生成 install 命令
        # 顺序类不变量：install 段必须整体排在 update 段之前（update 对未装的名字无效）
        assert cmds.index("skills install") < cmds.index("skills update") if "skills update" in cmds else True
        resolved = json.loads((tmp / "out" / "resolved.json").read_text())
        assert [r["repo"] for r in resolved] == ["AgentSkill-UsingFake", "AgentSkill-RunningLocalBatches",
                                                "AgentSkill-RunningLocalBatches"], resolved
        assert [r["collided"] for r in resolved] == [True, False, True], resolved

        # S1.5 分类闸门（triage.py）：routed 文件在 ⇒ 覆盖率只算它那几条（被分流的行不进包、不算缺判）；
        # 产物不在 ⇒ 回到全量口径（向后兼容，没跑闸门的轮次照旧）。
        routed = tmp / "out" / "judge-input-bodies.routed.json"
        routed.write_text(json.dumps([b for b in bodies if b["id"] == "P1"], ensure_ascii=False),
                          encoding="utf-8")
        merge(ok1, code=0)                       # 只有 P1 在范围内 → 覆盖率 100%
        merge(code=4)                            # 范围内一条都没判 → 仍是缺判，不许悄悄放过
        routed.unlink()
        merge(ok1, ok2, ok3, code=0)             # 拿掉闸门产物 → 又按全量（P2/P3 重新算进覆盖率）

    finally:
        HERMES_HOME = real
    assert pack_name_for("tool", "UsingDrawio") == "AgentSkill-UsingDrawio", pack_name_for("tool", "UsingDrawio")
    assert pack_name_for("tool", "Drawio") == "AgentSkill-UsingDrawio"
    assert pack_name_for("topic", "Progress Heartbeat") == "AgentSkill-ProgressHeartbeat"
    assert pack_name_for("topic", "AgentSkill-Foo") == "AgentSkill-Foo"
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _d = pathlib.Path(_td) / "sk"; _d.mkdir()
        (_d / "SKILL.md").write_text("a\n"); (_d / "references").mkdir(); (_d / "references" / "x.md").write_text("y\n")
        _mi = sibling_module("skill-mirror.py")
        _f1 = dir_fingerprint(_mi, _d)
        assert _f1 == dir_fingerprint(_mi, _d), "同内容应同指纹"
        (_d / "references" / "x.md").write_text("z\n")
        assert dir_fingerprint(_mi, _d) != _f1, "内容一变指纹必须变"
        (_d / "references" / "x.md").write_text("y\n"); (_d / "new.md").write_text("n\n")
        assert dir_fingerprint(_mi, _d) != _f1, "多一个文件指纹也要变"
    # 态表自检：A(2)×B(2)×C(2)×D(3)=24 里的合法子集。D=fold 只在**本地没有可比者**时出现：
    #   · local·no·no —— 谁都不管、也没有第三方来源 ⇒ **只能** fold（它就是候选）      1 格
    #   · third·no·no —— 第三方，但可比者可能只在远端（hub 副本）⇒ same/differs/fold 都可能 3 格
    # 其余 6 个 (A,B,C) 组合（B 或 C 至少一个 yes，或第三方有 built-in/official/bundled 那份）必须给出 same/differs。
    _triples = [(a, b, c) for a in ("local", "third") for b in ("no", "yes") for c in ("no", "yes")]
    _valid = set()
    for a, b, c in _triples:
        if (a, b, c) == ("local", "no", "no"):
            _valid.add((a, b, c, "fold"))
            continue
        _valid |= {(a, b, c, "same"), (a, b, c, "differs")}
        if (a, b, c) == ("third", "no", "no"):
            _valid.add((a, b, c, "fold"))
    assert _valid == set(STATE_ACTIONS), sorted(_valid ^ set(STATE_ACTIONS))
    assert len(_valid) == 16, len(_valid)
    # general agent：去掉 A 轴 → B(2)×C(2)×D(3) 减去同一处坍缩 = 7
    _g = {(b, c, d) for b in ("no", "yes") for c in ("no", "yes") for d in ("same", "differs", "fold")}
    _gv = {k for k in _g if (k[2] == "fold") == (k[0] == "no" and k[1] == "no")}
    assert len(_gv) == 7, len(_gv)
    print("[sweep-plan] self-test OK")
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="enumerate candidates + packs, freeze the judge input")
    s.add_argument("--profile", default="default",
                   help="default = Hermes 根（~/.hermes），其余 = <根>/profiles/<名>")
    s.add_argument("--packs-root", default="~/Documents/AgentSkill", help="where the AgentSkill-* checkouts live")
    s.add_argument("--external-dirs", default="",
                   help="DREAM_EXTERNAL_DIRS 那份 JSON：列出的目录（或它们的直接子目录）就是落点候选池")
    s.add_argument("--out", required=True, help="scratch dir: inventory.tsv / judge-input.json / packs.json")
    s.add_argument("--parser-script", default="", help="override the sibling skill-index.py path")
    s.add_argument("--exclude", action="append", default=[],
                   help="技能名前缀，重复可给多个；命中的本轮一律不动（如 --exclude loomerto）")
    s.add_argument("--repo-stem", default="", help=argparse.SUPPRESS)   # accepted, unused: kept for CLI stability
    s.set_defaults(func=cmd_scan)
    m = sub.add_parser("merge", help="judge verdicts (JSONL) -> plan.md + install-cmds.sh")
    m.add_argument("--dir", required=True, help="the --out dir from scan")
    m.add_argument("--skills-manager", action=argparse.BooleanOptionalAction, default=True,
                   help="本次运行是否把该 profile 的技能交给 Skills-Manager 装（默认开；"
                        "`--no-skills-manager`＝走第③路直接复制，给不归它管的 agent 用）")
    m.add_argument("--manager-agent", default="", help="Skills-Manager 侧这个 profile 的 agent 名（默认 hermes-<profile>）")
    m.add_argument("--profile", default="default")
    m.add_argument("--verdicts", action="append", default=[],
                   help="verdict JSONL file (repeatable); default = <dir>/verdicts*.jsonl")
    m.set_defaults(func=cmd_merge)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
