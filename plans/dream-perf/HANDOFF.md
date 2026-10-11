# 交接：Dream 执行效率优化（dream-perf）—— 给 default（或任何 profile）的接手说明

这份文件是**自足**的：读完它 + 仓库里的 `plans/dream-perf/`，不需要聊天记录就能接着干。
原监视者：会话 `20261011_090021_91c8fd`（asset-quant-invest profile，桌面端）。2026-10-11 交接。

## 1 · 这个活在干什么

`dream`（把各 profile 里没有仓库归属的本地技能批量收编进技能包仓库）单轮跑得极慢：
**47 分钟只走到 S2**，上一轮（04:00 定时）跑 4h50m 后失败。本计划 = 把慢因逐条查清并修掉。

计划本体（唯一真相）：`plans/dream-perf/plan.json`，看板 `plans/dream-perf/plan.html`。

```bash
LT="loomerto --plans-root /Users/maxim/Repositories/Repo/Dream/plans"
$LT list                       # 进度
$LT current dream-perf         # 现在该动哪块
$LT check dream-perf           # 图质量（有错误就别往下走）
```
**只经 CLI 改 plan，不许手改 `plan.json`**（三视图会停在旧内容）。

## 2 · 每拍要做的固定动作（原来的 10 分钟心跳）

```bash
python3 -B /Users/maxim/Repositories/Repo/Dream/plans/dream-perf/tools/perf-probe.py
```

一条命令给出这一拍的指纹：主 run 的 wall / 静默 / 上下文峰值、LLM 回合中位与最长、工具占比、
每次调用的 prompt 与 output、上下文炸弹（单条工具结果 > 40KB）、子代理的调用数与工具往返、
**last-15min 的 provider/环境错误计数**、各 profile `skills/` 根 3 小时内的散落文件；
按固定规则算出慢因，并**把这一拍追加进 `evidence/rounds.jsonl`**，同时打印与上一拍的差。

固定动作：**跑探针 → 只报与上一拍相比变了的部分 → 探针报出新慢因就加块/改块，没有就 `$LT note dream-perf "…" --kind decision` 记一行。**
（探针自带 `--self-test`；改了探针先跑它。）

## 3 · 当前状态（2026-10-11 11:1x）

- `dream-perf` 进度 **1/11**：`T-007#B-001`（探针）已完成；其余 10 块待开工。
- 被监视的那一轮：`cron 52fe6cf3a3dc` 手动触发版，execution `57b244d85cd749d4893f9d6ee1ff539c`，
  session `cron_52fe6cf3a3dc_20261011_090717`（default 库），model `monk`。
- 已确认的慢因（都已在 plan 里成块）：
  | 块 | 慢因 | 证据位置 |
  |---|---|---|
  | T-001 | 主 run 把 `sweep-plan.py` 全文读进上下文（56.4KB / 单条最大），此后每回合重放 ⇒ 每次调用 prompt ≈56.5K | `evidence/perf-baseline-2026-10-11.md` |
  | T-002 | 27 个回合里大半是「写小脚本→跑它」，每回合重付一次大 prompt | 同上 |
  | T-003 | JEV 探针稳定 451（地域限制）+ cron 无审批人时 `execute_code` 与 `python3 -c` 被 BLOCKED | 同上 |
  | T-004 | 整轮不可分段，失败即全废 | 同上 |
  | T-006 | 子代理自己把上下文撑到 100–230KB（`skills_list` 一次 63KB），143 次调用只换 399 tokens/次 | 同上的 S2 段 |
  | T-008 | 扇出等待：父块等全部孩子，wall 由最慢者决定（**注意：「端点排队」只是假设，未定案**） | `T-008#B-001` doc |
  | T-009 | **运行期环境目录原地重建**：`installs/*/environments/<hash>/` 被删除 10.5 分钟后重建，期间活进程任何新建 httpx 客户端都 `FileNotFoundError`（truststore）⇒ 子代理集体卡死 | `T-009#B-001` doc |
- 机器背景（同日）：App Store 装 Xcode → load 17.6 / 空闲内存 136MB / mds_stores 119%，
  吞吐从 10:25 起单调衰减；`/usr/bin/python3` 与 `/usr/bin/git` 一度退出码 69（许可未同意，用户已修）。

## 4 · 坑（接手前先看这几条）

1. **别被「慢」骗**：只看延时会完全错过「地基塌陷」（环境被换掉、provider 全挂）。探针的
   `last-15min 错误计数` 与 `env_touched` 两行就是为这个加的，先看它们。
2. **`/usr/bin/python3` 是 Xcode CLT 的 shim**：许可没同意时直接退 69。稳妥解释器 =
   `/Users/maxim/.hermes/tools/python-3.14.7+202****0901-darwin-arm64/bin/python3`（Hermes 自带）。
3. **别用裸 `HERMES_HOME` 猜 profile**：`hermes cron/skills` 认当前 profile。要动 default 的
   dream job 必须 `HERMES_HOME=/Users/maxim/.hermes hermes cron …`。
4. **不要把子代理派成后台进程**：子代理起的后台进程退出时会把会话路由 pin 到子代理，顶掉人面会话。
5. **两边别同时跑监视**：会各自汇报同一件事。监视点只留一处。

## 5 · 待办（`$LT current dream-perf` 的实时答案优先于这里）

`T-001#B-001`（上下文瘦身）· `T-002#B-001`（回合数压缩）· `T-003#B-001`（451 永久降级）·
`T-003#B-002`（审批闸写进路由技能，用户点名要的）· `T-004#B-001`（按 profile 拆批）·
`T-006#B-001`（子代理纪律）· `T-008#B-001/B-002`（并发标定 / 超时看门狗）·
`T-009#B-001`（环境重建定案）· `T-005#B-001`（推送·重装·回读，等前面全部）

## 6 · 顺带挂着的事

- 本轮 dream 的交付目标 `discord:1558166085080055878` 返回 **403 Missing Access**，报告发不出去 ⇒
  要么修投递目标，要么直接读 `~/.hermes/cron/output/52fe6cf3a3dc/`。
- 越界残留：`~/.hermes/profiles/dev-general/skills/_dream_enum.py`（某子代理的临时脚本落在 skills 根），
  等那轮跑完再清（`T-006#B-001` 的判据里已含这条）。
