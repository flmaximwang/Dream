#!/usr/bin/env python3
"""skill-mirror · 「这个 profile 技能在外部仓库里有对等物吗？」

判据（2026-10-11 起，Skills-Manager 接管 skill 管理后）：
**一个 profile 技能算「有归属」，当且仅当它在一份被维护的外部仓库列表里能找到同名技能，
且两边内容一致。** 不再看 Hermes 自己的 `.hub/lock.json` / `.bundled_manifest`。

用法：

    # ① 找一个技能在这个仓库里的哪（可能多处：根目录 / skills/ / skills/<类目>/）
    skill-mirror.py where <技能名> <仓库路径> [--skill-dir <相对目录>]

    # ② 比一个 profile 技能与仓库里的那个是不是一致（逐文件 sha256）
    skill-mirror.py same <技能名> <仓库路径> <profile 技能目录> [--skill-dir <相对目录>]

    # ③ 判据本体：把整个 profile 的技能对一遍外部仓库列表
    skill-mirror.py check <profile 根> --repo <仓库> [--repo <仓库> ...] [--json]
    skill-mirror.py check <profile 根> --repos-from <每行一个仓库路径的文件>

    skill-mirror.py --self-test

结论三档：`homed`（有对等物且一致）· `drift`（有同名但内容不同）· `local`（仓库列表里根本没有）。
`--skill-dir` 是可选的**搜索范围提示**（有的仓库把技能放根目录、有的放 `skills/`、有的还多一层类目）；
不给就搜整个仓库（跳过 `.git` 等噪声目录，深度上限 6）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

SKIP_DIRS = {".git", ".hub", ".archive", "quarantine", ".trash", "__pycache__", "node_modules",
             ".venv", "venv", "dist", "build"}
SKIP_FILES = {".DS_Store"}
MAX_DEPTH = 6


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def skill_files(d: pathlib.Path) -> dict:
    """技能目录 → {相对路径: sha256}（跳过噪声）。"""
    out = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name in SKIP_FILES or (set(p.parts) & SKIP_DIRS):
            continue
        out[str(p.relative_to(d))] = sha256(p)
    return out


def diff_dirs(a: pathlib.Path, b: pathlib.Path) -> dict:
    """两个技能目录逐文件比。→ {only_in_a, only_in_b, different, identical}"""
    fa, fb = skill_files(a), skill_files(b)
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    differ = sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k])
    return dict(only_in_a=only_a, only_in_b=only_b, different=differ,
                identical=not (only_a or only_b or differ))


def find_skills(repo: pathlib.Path, name: str, skill_dir: str = "") -> list:
    """仓库里所有叫 `name` 的技能目录（含 SKILL.md）。`skill_dir` 只用来缩小搜索范围。"""
    root = repo / skill_dir if skill_dir else repo
    if not root.is_dir():
        return []
    hits = []
    for p in root.rglob(name):
        if not p.is_dir() or (set(p.parts) & SKIP_DIRS):
            continue
        if len(p.relative_to(repo).parts) > MAX_DEPTH:
            continue
        if (p / "SKILL.md").is_file():
            hits.append(p)
    return sorted(hits)


def build_index(repos: list, skill_dirs: list) -> dict:
    """一次走完所有仓库：{技能名: [技能目录, ...]}。check 靠它做 O(1) 名字查找。"""
    index: dict = {}
    for repo in repos:
        repo = pathlib.Path(repo).expanduser()
        if not repo.is_dir():
            print(f"[mirror] 跳过（不是目录）：{repo}", file=sys.stderr)
            continue
        roots = [repo / d for d in skill_dirs] if skill_dirs else [repo]
        for root in roots:
            if not root.is_dir():
                continue
            for md in root.rglob("SKILL.md"):
                d = md.parent
                if set(d.parts) & SKIP_DIRS or len(d.relative_to(repo).parts) > MAX_DEPTH:
                    continue
                index.setdefault(d.name, []).append(d)
    return index


def classify(skill_dir: pathlib.Path, index: dict) -> dict:
    """→ {verdict: homed|drift|local, matches: [...], detail: {...}}"""
    hits = index.get(skill_dir.name, [])
    if not hits:
        return dict(verdict="local", matches=[], detail={})
    res = []
    for h in hits:
        d = diff_dirs(skill_dir, h)
        res.append(dict(repo_path=str(h), identical=d["identical"], diff=d))
    return dict(verdict="homed" if any(r["identical"] for r in res) else "drift",
                matches=[r["repo_path"] for r in res], detail={r["repo_path"]: r["diff"] for r in res})


def profile_skills(profile: pathlib.Path) -> list:
    prof = pathlib.Path(profile).expanduser()
    root = prof / "skills" if (prof / "skills").is_dir() else prof
    out = []
    for md in root.rglob("SKILL.md"):
        if set(md.parts) & SKIP_DIRS:
            continue
        out.append(md.parent)
    return sorted(out)


def cmd_where(args) -> int:
    hits = find_skills(pathlib.Path(args.repo).expanduser(), args.name, args.skill_dir or "")
    for h in hits:
        print(h)
    if not hits:
        print(f"[mirror] {args.name}：{args.repo} 里没有这个技能"
              f"{'（搜索范围 ' + args.skill_dir + '）' if args.skill_dir else ''}")
    return 0 if hits else 1


def cmd_same(args) -> int:
    skill = pathlib.Path(args.skill_dir_path).expanduser()
    hits = find_skills(pathlib.Path(args.repo).expanduser(), args.name, args.skill_dir or "")
    if not hits:
        print(f"[mirror] local —— {args.repo} 里没有 {args.name}")
        return 1
    rc = 1
    for h in hits:
        d = diff_dirs(skill, h)
        tag = "homed（一致）" if d["identical"] else "drift（同名但不同）"
        print(f"[mirror] {tag}：{h}")
        for k in ("only_in_a", "only_in_b", "different"):
            if d[k]:
                print(f"    {k}: {d[k][:6]}{' …' if len(d[k]) > 6 else ''}")
        rc = 0 if d["identical"] else 1
        if d["identical"]:
            break
    return rc


def cmd_check(args) -> int:
    repos = list(args.repo or [])
    if args.repos_from:
        repos += [l.strip() for l in pathlib.Path(args.repos_from).expanduser().read_text().splitlines()
                  if l.strip() and not l.startswith("#")]
    if not repos:
        sys.exit("check 需要 --repo <仓库> 或 --repos-from <文件>")
    skill_dirs = args.skill_dirs or []
    index = build_index(repos, skill_dirs)
    skills = profile_skills(pathlib.Path(args.profile))
    rows = []
    for s in skills:
        c = classify(s, index)
        rows.append(dict(skill=s.name, path=str(s), verdict=c["verdict"], matches=c["matches"]))
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("homed", "drift", "local")}
    if args.json:
        print(json.dumps(dict(repos=repos, counts=counts, rows=rows), ensure_ascii=False, indent=2))
    else:
        print(f"[mirror] profile 技能 {len(rows)} 个 · 仓库 {len(repos)} 个 · 索引 {len(index)} 个名字")
        print(f"[mirror] homed {counts['homed']} · drift {counts['drift']} · local {counts['local']}")
        for v in ("drift", "local"):
            bad = [r for r in rows if r["verdict"] == v]
            if bad:
                print(f"  {v}（{len(bad)}）：{', '.join(r['skill'] for r in bad[:10])}"
                      f"{' …' if len(bad) > 10 else ''}")
    return 0


def cmd_repos_from_manager(args) -> int:
    """Skills-Manager 的「被维护仓库列表」：它的 clone 缓存 + 中央技能库。

    权威来源是 `skills-manager-cli --json skills list`（source_ref 是 URL/registry 条目），
    本地对等物就是它自己的 clone（`<base>/cache/repos/<hash>`，origin == source_ref）与规范副本库
    （`<base>/skills`）。不带 --json 时一行一个路径，直接喂 `check --repos-from`。
    """
    base = pathlib.Path(args.manager_base).expanduser()
    central = base / "skills"
    clones = []
    for d in sorted((base / "cache" / "repos").glob("*")):
        if not d.is_dir() or d.name.endswith(".lock") or "-sparse" in d.name:
            continue
        r = subprocess.run(["git", "-C", str(d), "remote", "get-url", "origin"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            clones.append((r.stdout.strip(), str(d)))
    managed = []
    r = subprocess.run([str(base / "bin" / "skills-manager-cli"), "--json", "skills", "list"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        managed = json.loads(r.stdout or "[]")
    paths = [p for _, p in clones] + [str(central)]
    if args.json:
        print(json.dumps(dict(manager_base=str(base), central=str(central),
                              clones=[dict(origin=o, path=p) for o, p in clones],
                              managed_skills=len(managed),
                              sources=sorted({s.get("source_ref", "") for s in managed})),
                         ensure_ascii=False, indent=2))
    else:
        print("\n".join(paths))
    print(f"[mirror] 仓库 {len(paths)} 个（clone {len(clones)} + 中央库 1）· 管理中的技能 {len(managed)} 个",
          file=sys.stderr)
    return 0


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        repo = td / "repo"
        # 三种布局：根目录 / skills/ / skills/<类目>/
        for rel, name in (("alpha", "alpha"), ("skills/beta", "beta"),
                          ("skills/cat/gamma", "gamma")):
            d = repo / rel
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n# {name}\n")
            (d / "refs").mkdir()
            (d / "refs" / "a.md").write_text("x\n")
        for name in ("alpha", "beta", "gamma"):
            hits = find_skills(repo, name)
            assert len(hits) == 1, (name, hits)
        # skill_dir 提示：给 skills 就不该找到根目录的 alpha
        assert find_skills(repo, "alpha", "skills") == [], "skill_dir 没起到范围限制"
        assert len(find_skills(repo, "gamma", "skills")) == 1

        prof = td / "prof"; (prof / "skills").mkdir(parents=True)
        import shutil
        shutil.copytree(repo / "skills" / "beta", prof / "skills" / "beta")
        shutil.copytree(repo / "skills" / "cat" / "gamma", prof / "skills" / "gamma")
        (prof / "skills" / "gamma" / "refs" / "a.md").write_text("CHANGED\n")
        (prof / "skills" / "unhomed").mkdir(); (prof / "skills" / "unhomed" / "SKILL.md").write_text("x\n")

        index = build_index([str(repo)], [])
        got = {s.name: classify(s, index)["verdict"] for s in profile_skills(prof)}
        assert got == {"beta": "homed", "gamma": "drift", "unhomed": "local"}, got

        # 逐文件比：多一个文件就 drift，删掉又回到 homed
        import shutil as _sh
        _sh.copytree(repo / "alpha", td / "alpha-copy")
        (td / "alpha-copy" / "extra.md").write_text("y\n")
        assert not diff_dirs(repo / "alpha", td / "alpha-copy")["identical"], "多一个文件竟算一致"
        (td / "alpha-copy" / "extra.md").unlink()
        assert diff_dirs(repo / "alpha", td / "alpha-copy")["identical"], "删回去竟还算不同"
    print("[mirror] self-test OK（三种布局定位 · skill_dir 范围 · homed/drift/local 三档）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="skill-mirror：profile 技能 ↔ 外部仓库的对等物检查")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("where"); w.add_argument("name"); w.add_argument("repo"); w.add_argument("--skill-dir", default="")
    s = sub.add_parser("same"); s.add_argument("name"); s.add_argument("repo"); s.add_argument("skill_dir_path")
    s.add_argument("--skill-dir", default="")
    c = sub.add_parser("check"); c.add_argument("profile"); c.add_argument("--repo", action="append")
    c.add_argument("--repos-from"); c.add_argument("--skill-dirs", nargs="*"); c.add_argument("--json", action="store_true")
    m = sub.add_parser("repos-from-manager"); m.add_argument("--manager-base", default="~/.skills-manager")
    m.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.cmd == "where":
        return cmd_where(args)
    if args.cmd == "same":
        return cmd_same(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "repos-from-manager":
        return cmd_repos_from_manager(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
