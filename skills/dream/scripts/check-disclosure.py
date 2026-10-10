#!/usr/bin/env python3
"""分层披露自检：把一个 skill 目录按「第一层只放薄入口、细节下沉 references」的规则量一遍。

判据（超限即 exit 1，不是建议）：
  MAX_SKILL_LINES       SKILL.md 行数
  MAX_SKILL_BYTES       SKILL.md 字节数
  MAX_DOC_BYTES         单份 references/*.md 字节数
  MAX_FIRST_LAYER       SKILL.md 里点名的、同 pack 第一层披露的 skill 数

另外守「没有孤儿文档」：每份 references/*.md 必须在 SKILL.md 里被点名一次。

用法: python3 -B check-disclosure.py <skill 目录> [--first-layer N]
"""
import os
import sys

MAX_SKILL_LINES = 240
MAX_SKILL_BYTES = 16000
MAX_DOC_BYTES = 20000
MAX_FIRST_LAYER = 20

# 适配技能（单文件的 `<agent>-routing`）允许没有 references/；方法主技能不许：
# 加 --require-references 时，references 为空就是错误。默认只警告。
REQUIRE_REFS_DEFAULT = False


def main(argv):
    if len(argv) < 2:
        print("usage: check-disclosure.py <skill-dir> [--first-layer N]")
        return 2
    root = argv[1]
    first_layer = MAX_FIRST_LAYER
    if "--first-layer" in argv:
        first_layer = int(argv[argv.index("--first-layer") + 1])
    require_refs = REQUIRE_REFS_DEFAULT or ("--require-references" in argv)
    if not os.path.isdir(root):
        print(f"[disclosure] FAIL 目录不存在: {root}")
        return 1

    fails = []
    sk = os.path.join(root, "SKILL.md")
    if not os.path.isfile(sk):
        print(f"[disclosure] FAIL 缺 SKILL.md: {sk}")
        return 1
    body = open(sk, encoding="utf-8").read()
    lines = body.count("\n") + (0 if body.endswith("\n") else 1)
    nbytes = len(body.encode("utf-8"))
    print(f"[disclosure] SKILL.md  lines={lines}/{MAX_SKILL_LINES}  bytes={nbytes}/{MAX_SKILL_BYTES}")
    if lines > MAX_SKILL_LINES:
        fails.append(f"SKILL.md 行数超限: {lines} > {MAX_SKILL_LINES}")
    if nbytes > MAX_SKILL_BYTES:
        fails.append(f"SKILL.md 字节数超限: {nbytes} > {MAX_SKILL_BYTES}")

    refs_dir = os.path.join(root, "references")
    docs = sorted(f for f in os.listdir(refs_dir)) if os.path.isdir(refs_dir) else []
    if not docs:
        if require_refs:
            fails.append("references/ 为空：方法主技能的第一层没有可下沉的细节，分层披露未成立")
        else:
            print("[disclosure]   warn references/ 为空（单文件适配技能允许；主技能请加 --require-references）")
    for d in docs:
        p = os.path.join(refs_dir, d)
        b = os.path.getsize(p)
        named = d in body
        flag = "ok " if (b <= MAX_DOC_BYTES and named) else "BAD"
        print(f"[disclosure]   {flag} references/{d}  bytes={b}/{MAX_DOC_BYTES}  named_in_SKILL.md={named}")
        if b > MAX_DOC_BYTES:
            fails.append(f"单文档超限: references/{d} {b} > {MAX_DOC_BYTES}")
        if not named:
            fails.append(f"孤儿文档: references/{d} 没有被 SKILL.md 点名")

    # 第一层披露的 skill 数：SKILL.md 里出现的 `name:` 形状引用 + 同 pack 兄弟技能目录名
    siblings = [d for d in sorted(os.listdir(os.path.dirname(root) or "."))
                if os.path.isdir(os.path.join(os.path.dirname(root) or ".", d))]
    named_siblings = [s for s in siblings if s in body]
    print(f"[disclosure] 第一层 skill 数 = {len(named_siblings)}/{first_layer} "
          f"(同 pack 被点名: {', '.join(named_siblings) or '无'})")
    if len(named_siblings) > first_layer:
        fails.append(f"第一层 skill 数超限: {len(named_siblings)} > {first_layer}")

    if fails:
        print("\n[disclosure] ❌ 不通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("\n[disclosure] ✅ 通过：第一层薄、细节在 references、无孤儿文档、上限全在范围内")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
