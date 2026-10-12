---
name: git-bisect-workflow
description: Use when 要在 git 历史里找出引入某个回归的提交。触发：「git bisect」「二分找回归」
---

# 用 git bisect 二分找回归提交

## When to Use

有一个「以前好、现在坏」的具体判据（一条命令的退出码、一个测试），想找出是哪个提交引入的。

## 步骤

1. `git bisect start`
2. `git bisect bad`（当前 HEAD 有问题）
3. `git bisect good <已知好的提交>`
4. 每次 checkout 后跑那条判据：好 → `git bisect good`，坏 → `git bisect bad`
5. 找到后用 `git bisect reset` 回到原来的分支
6. 判据是一条命令时可自动化：`git bisect run ./check.sh`

## 坑

- 别在 bisect 中间切分支或手改文件；`git bisect run` 的脚本退出码 `0` = 好、`125` = 跳过这个提交。
- 有 merge 提交时加 `--first-parent`，只在主线里二分。

这是一条通用的排障流程，任何 git 仓库、任何机器上同样适用。
