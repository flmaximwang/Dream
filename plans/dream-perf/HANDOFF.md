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
  **父块不是「卡死」，是扇出栅栏**（12:00 复核）：父 09:54:33 派完 10 个子代理后再无消息（静默 = 等孩子），
  10 个孩子里 **7 个已经交卷**（最后一条消息是 assistant，11:01–11:22 返回），
  `2529f2` 静默 38min，`33698e` / `d25f75` 12:00 仍在调工具。⇒ 有真实进展，别掐。
  没有 cancel 入口（`hermes cron` 无 cancel 子命令），要中止只能重启 gateway（会连带掐掉监视 job 自己）。
  **止损线（13:05 按实测重标，别用 15min）**：本 run 里孩子静默 38min 后又恢复过两次
  （`33698e` 11:18→11:56、`2529f2` 11:22→12:01），加上 monk 流卡死是 180s 一级、单回合最多重试 3 次
  ⇒ 「孩子静默 15min」在该 provider 下是**假信号**，会误杀。真正的死锁判据：**全部孩子连续 ≥45min
  无任何调用 且 父静默 ≥4h**（或整个 run 越过 4h50m = 04:00 那轮的失败点，仍无孩子交卷）才重启。
  **止损机制（13:2x 读源码确认，别再重复问）**：
  - 没有外科手术式中止：`hermes cron` 无 cancel 子命令；`cron/scheduler.py:1083` 的
    `sweep_stale_inflight()` 对「future 仍 not done」的 claim 明确 `continue`（= 真在跑就不释放），
    所以僵尸不会被自动回收。唯一手段是 `hermes gateway restart`。
  - 重启的代价 = 这一轮被 `recover_interrupted_executions()` 标成 `unknown`（同 09:01 那条
    `43cfe2e6…`：side effects 未定），也就是丢掉这 7 份已交卷孩子的 fan-in；不重启则永久挂着。
  - **硬 deadline：明天 04:00 前必须清掉**。in-flight claim 会挡住同一个 job 的后续 fire
    （`scheduler.py:4170` 注释原文：blocking every future run of that job）⇒ 僵尸不除，每日 dream
    会静默不跑 —— 比重启更糟。04:00 那个 job 是 `0 4 * * *`，14h 之外，所以「现在掐」并不紧急。
  - 所以：**无人值守时不要自己重启**；到了判据就喊人（或让用户一句话授权），最晚今晚 03:00 前处理。
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

- 本轮 dream 的交付目标：`discord:1558166085080055878` 返回 **403 Missing Access**。原因是这个 id
  **根本不在 `~/.hermes/channel_directory.json` 里**（bot 连看都看不见它，不是权限能修的事）。
  2026-10-11 12:0x 已把 `52fe6cf3a3dc` 的 deliver 改成 `origin`（= 建这个 job 的 thread
  `1558075006947426419`，bot 有权限）。想发别处一条命令：
  `HERMES_HOME=/Users/maxim/.hermes hermes cron edit 52fe6cf3a3dc --deliver discord:<thread_id>`。
  历史报告照旧直接读 `~/.hermes/cron/output/52fe6cf3a3dc/`。
- 越界残留：`~/.hermes/profiles/dev-general/skills/_dream_enum.py`（某子代理的 skill 枚举脚本；它用
  `__file__` 定位 skills 根，所以只能待在那个位置）。**没有任何东西会自动清它** —— 那轮跑完得手动 `rm`。
  现在别删：run 还活着（12:00 仍有 2 个子代理在调工具），删了可能踩到正在用它的孩子。
