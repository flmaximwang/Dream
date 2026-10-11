#!/usr/bin/env python3
"""external-dirs · dream 的「外部目录清单」：一份本地 JSON，不再把路径写死在脚本里。

口径（本人 2026-10-11 定）：`~/.hermes/.env` 里加一个键

    DREAM_EXTERNAL_DIRS=/Users/maxim/.hermes/dream-external.json

指向一个**本地 JSON**，里面只放**本人点名**的目录（dream 允许当作「外部仓库/落点池」的那些）。
JSON 不自动生成、不手抄自 Skills-Manager —— 想说清「Skills-Manager 管着哪些技能」时，
用 `manager-skills` 走它的 CLI 现查，不落进这个文件。

    external-dirs.py init                 # 写一份骨架（--force 覆盖已存在的）
    external-dirs.py show                 # 看当前清单（--json 出原文）
    external-dirs.py check                # 校验：路径在不在、布局认不认得
    external-dirs.py manager-skills       # 用 skills-manager-cli 列出它管理的技能与来源
    external-dirs.py --self-test

JSON 形状（schema 版本写死在文件里，读的时候校验）：

    {"schema": "dream-external-dirs/1",
     "generated_at": "...", "generated_from": "skills-manager",
     "manager_base": "...",
     "dirs": [{"path": "/abs/dir", "name": "AgentSkill-UsingHermes", "kind": "git-clone",
               "origin": "https://github.com/...", "skills_subdir": "skills",
               "skill_count": 39, "routable": true}]}
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

SCHEMA = "dream-external-dirs/1"
ENV_KEY = "DREAM_EXTERNAL_DIRS"
DEFAULT_JSON = "~/.hermes/dream-external.json"


def env_value(key: str) -> str:
    """先看真环境，再看 ~/.hermes/.env（只读这一个键，别的行不碰、不打印）。"""
    if os.environ.get(key):
        return os.environ[key].strip()
    for env_file in (pathlib.Path.home() / ".hermes" / ".env",
                     pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes")) / ".env"):
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def resolve_file(explicit: str = "") -> pathlib.Path:
    raw = explicit or env_value(ENV_KEY) or DEFAULT_JSON
    return pathlib.Path(os.path.expandvars(raw)).expanduser()


def repo_name_from_origin(origin: str) -> str:
    name = origin.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def skills_subdir(d: pathlib.Path) -> str:
    """这个目录把技能放在哪：`skills` / `Skills` / 根目录（返回 ""）。"""
    for cand in ("skills", "Skills"):
        sub = d / cand
        if sub.is_dir() and any(p.name == "SKILL.md" for p in sub.rglob("SKILL.md")):
            return cand
    return ""


def count_skills(d: pathlib.Path, sub: str) -> int:
    root = d / sub if sub else d
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("SKILL.md") if not (set(p.parts) & {".git", "__pycache__"}))


def entry(path: pathlib.Path, kind: str, name: str = "", origin: str = "") -> dict:
    sub = skills_subdir(path)
    return dict(path=str(path), name=name or repo_name_from_origin(origin) or path.name, kind=kind,
                origin=origin, skills_subdir=sub, skill_count=count_skills(path, sub),
                routable=bool(origin) or kind == "central-store")


def manager_skills(manager_base: pathlib.Path) -> list:
    """Skills-Manager 管着哪些技能：名字 · 来源类型 · 来源 · 中央副本路径（走它的 CLI，不读库）。"""
    cli = pathlib.Path(manager_base).expanduser() / "bin" / "skills-manager-cli"
    if not cli.is_file():
        sys.exit(f"manager-skills：找不到 {cli}")
    r = subprocess.run([str(cli), "--json", "skills", "list"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"manager-skills：{cli} 退出 {r.returncode}\n{r.stderr.strip()}")
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        sys.exit("manager-skills：CLI 的输出不是 JSON")


def load(path: pathlib.Path) -> dict:
    if not path.is_file():
        sys.exit(f"external-dirs：没有清单 {path}（先生成：external-dirs.py from-manager，"
                 f"或把 {ENV_KEY} 写进 ~/.hermes/.env）")
    data = json.loads(path.read_text())
    if data.get("schema") != SCHEMA:
        sys.exit(f"external-dirs：{path} 的 schema 是 {data.get('schema')!r}，本脚本只认 {SCHEMA!r}")
    for d in data.get("dirs", []):
        if not d.get("path"):
            sys.exit(f"external-dirs：{path} 里有一条没有 path 的目录项")
    return data


def template() -> dict:
    return dict(schema=SCHEMA, generated_at=None, generated_from="manual",
                _note="只放本人点名的目录。每个目录可给 name / skills_subdir 提示；"
                      "skills_subdir 为空表示技能直接躺在该目录下（或由脚本自己认）。",
                dirs=[])


def cmd_init(args) -> int:
    out = resolve_file(args.out)
    if out.exists() and not args.force:
        sys.exit(f"init：{out} 已存在（要覆盖加 --force）")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(template(), ensure_ascii=False, indent=2) + "\n")
    print(f"[external-dirs] 骨架已写 {out} —— 把要用的目录填进 dirs[]")
    return 0


def cmd_check(args) -> int:
    p = resolve_file(args.file)
    data = load(p)
    bad = 0
    for d in data["dirs"]:
        path = pathlib.Path(d["path"]).expanduser()
        ok = path.is_dir()
        sub = d.get("skills_subdir", "")
        n = count_skills(path, sub) if ok else 0
        print(f"  {'✓' if ok and n else '✗'} {d.get('name') or path.name:32} {path}"
              f"{'' if ok else ' —— 路径不存在'}{'' if n or not ok else ' —— 没找到 SKILL.md'}")
        bad += 0 if (ok and n) else 1
    print(f"[external-dirs] {len(data['dirs']) - bad}/{len(data['dirs'])} 个目录可用 · {p}")
    return 0 if bad == 0 else 1


def cmd_manager_skills(args) -> int:
    rows = manager_skills(pathlib.Path(args.manager_base))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"[external-dirs] Skills-Manager 管着 {len(rows)} 个技能")
    by = {}
    for r in rows:
        by.setdefault(r.get("source_ref", ""), []).append(r)
    for ref, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(rs):3d}  {r.get('source_type', ''):8} {ref}")
        for r in sorted(rs, key=lambda x: x.get("name", ""))[:4]:
            print(f"        · {r.get('name')}  →  {r.get('path')}")
        if len(rs) > 4:
            print(f"        · … 还有 {len(rs) - 4} 个")
    return 0


def _old_cmd_from_manager(args) -> int:
    base = pathlib.Path(args.manager_base).expanduser()
    only = set() if args.all_clones else managed_sources(base)
    data = from_manager(base, only)
    out = resolve_file(args.out) if args.out else resolve_file()
    if args.dry_run:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"[external-dirs] {len(data['dirs'])} 个目录 → {out}")
    for d in data["dirs"]:
        print(f"  {d['kind']:14} {d['name']:32} 技能 {d['skill_count']:3} · skills_dir={d['skills_subdir'] or '(根)':6} {d['origin']}")
    return 0


def cmd_show(args) -> int:
    p = resolve_file(args.file)
    data = load(p)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"[external-dirs] {p} · schema {data['schema']} · {len(data['dirs'])} 个目录"
          f" · 生成 {data.get('generated_at')}（{data.get('generated_from')}）")
    for d in data["dirs"]:
        path = pathlib.Path(d["path"]).expanduser()          # 手写的清单没有 kind/skill_count，现算
        n = d.get("skill_count")
        if n is None:
            n = count_skills(path, d.get("skills_subdir", "")) if path.is_dir() else 0
        print(f"  {d.get('kind', 'manual'):14} {d.get('name') or path.name:32} 技能 {n:3} · {path}")
    return 0


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        # 一个「根目录放技能」的仓库 + 一个「skills/ 下放技能」的仓库
        (td / "plain" / "alpha").mkdir(parents=True)
        (td / "plain" / "alpha" / "SKILL.md").write_text("x\n")
        (td / "layered" / "skills" / "cat" / "beta").mkdir(parents=True)
        (td / "layered" / "skills" / "cat" / "beta" / "SKILL.md").write_text("x\n")
        assert skills_subdir(td / "plain") == ""
        assert skills_subdir(td / "layered") == "skills"
        assert count_skills(td / "plain", "") == 1
        assert count_skills(td / "layered", "skills") == 1

        data = template()
        data["dirs"] = [dict(path=str(td / "plain"), name="plain", skills_subdir=""),
                        dict(path=str(td / "layered"), name="layered", skills_subdir="skills")]
        f = td / "dirs.json"
        f.write_text(json.dumps(data, ensure_ascii=False))
        assert load(f)["dirs"][1]["name"] == "layered"
        # 坏路径要被 check 抓出来
        data["dirs"].append(dict(path=str(td / "nope"), name="missing", skills_subdir=""))
        f.write_text(json.dumps(data, ensure_ascii=False))
        args = argparse.Namespace(file=str(f))
        assert cmd_check(args) == 1, "不存在的路径没被 check 抓到"
        # schema 不对要报错
        f.write_text(json.dumps({"schema": "nope", "dirs": []}))
        try:
            load(f)
            raise AssertionError("schema 不对竟然没报错")
        except SystemExit:
            pass
    print("[external-dirs] self-test OK（骨架 · 布局识别 · 计数 · check 抓坏路径 · schema 校验）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="dream 的外部目录清单（本地 JSON，由 Skills-Manager 生成）")
    sub = ap.add_subparsers(dest="cmd")
    i = sub.add_parser("init"); i.add_argument("--out", default=""); i.add_argument("--force", action="store_true")
    s = sub.add_parser("show"); s.add_argument("--file", default=""); s.add_argument("--json", action="store_true")
    k = sub.add_parser("check"); k.add_argument("--file", default="")
    m = sub.add_parser("manager-skills"); m.add_argument("--manager-base", default="~/.skills-manager")
    m.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "manager-skills":
        return cmd_manager_skills(args)
    if args.cmd == "show":
        return cmd_show(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
