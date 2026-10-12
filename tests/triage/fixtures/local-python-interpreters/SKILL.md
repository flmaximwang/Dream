---
name: local-python-interpreters
description: Use when 本机要跑脚本时该用哪个 python 的现状记录。触发：「python 解释器」「mamba env」
---

# 本机的 python 解释器现状

这台机器上装了三个解释器，各有各的用途：

| 路径 | 是什么 | 有没有 numpy |
|---|---|---|
| `/opt/homebrew/bin/python3.12` | Homebrew 的系统 python | 没有 |
| `/Applications/BioRazer/bin/python3.11` | BioRazer 的 conda env | 有（numpy/scipy） |
| `~/.hermes/hermes-agent/venv/bin/python3` | Hermes 自己的 venv | 有（Hermes 的依赖） |

跑 BioRazer 的脚本用第 2 个；跑 Hermes 相关脚本用第 3 个；**不要**往系统 python 里 `pip install`。

这三个路径是哪台机器装了哪个 env 的现状，换台机器就不成立。
