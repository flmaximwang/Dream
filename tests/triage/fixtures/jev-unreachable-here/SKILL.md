---
name: jev-unreachable-here
description: Use when 本机要给 dream 判官挑后端时的现状记录。触发：「JEV 451」「判官后端不可达」
---

# JEV 在本机不可达（本机现状，不是通用结论）

这台机器上（macOS + 未修好的 Clash 规则）对 `api.typesafe.ai` 稳定返回 **HTTP 451**，
所以 dream 的判官在本机**先走 deepseek-flash**：

- 现象：`https://api.typesafe.ai/v1/systemone` → `451 Unavailable For Legal Reasons`，重试三次都一样
- 可用后端：`https://api.deepseek.com/v1`，模型 `deepseek-flash`
- key 位置：`~/.hermes/.env` 的 `DEEPSEEK_API_KEY`（本机已有）
- 什么时候会变：Clash 规则修好之后 JEV 才可访问；本机当前**没修**

这条结论**只对这台机器成立**——换台机器、换个网络出口要先现场探活，别照抄。
