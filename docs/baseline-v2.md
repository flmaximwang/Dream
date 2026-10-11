# 基线：v2 一轮 dream 的时间花在哪（2026-10-11）

来源与证据：`plans/dream-perf/evidence/perf-baseline-2026-10-11.md`（47 分钟窗口的逐回合账）、
`plans/dream-perf/evidence/rounds.jsonl`（每 10 分钟一拍的探针指纹）、
`plans/dream-perf/evidence/env-rebuild-forensics-2026-10-11.md`（环境目录被回收的取证）、
`~/.hermes/state.db`（sessions / messages / session_model_usage，只读）、`~/.hermes/cron/executions.db`。

## 0 · 两轮整轮的数字

| 轮次 | 起点 | 结束 | 结果 | 证据 |
|---|---|---|---|---|
| 04:00 定时 | 04:00:00 | 08:50:26 | **4h50m 后失败** | `executions` 里 `31341549b8d2…` status=failed，error=`RuntimeError: agent reported failure`；父块收到的 `delegate_task` 结果是 `status=interrupted`、`waiting for model response (15910.8s)` |
| 09:07 手动重跑 | 09:07:17 | 仍在跑（14:04 复核，≥4h57m） | 10 个子代理 7 个已交卷，3 个仍未返回 | `executions` 里 `57b244d85cd7…` status=running；`state.db` 父块仍挂 `delegate_task` |

对比参照：`perf-baseline` 里**47 分钟**那段窗口 = LLM 43.5min (93%) + 工具 3.0min (6%)，27 个回合。
也就是说一轮的账单几乎全是 LLM 回合，不是工具。

## 1 · 逐阶段归因

| 阶段 | 实测 / 证据 | 主因 | v3 处置 |
|---|---|---|---|
| S1 枚举 | `scan` 本身是脚本，很快；但主 run 在 09:19 把 `sweep-plan.py` **全文读进上下文**（单条 57,732B，会话最大），此后每回合重放 | 上下文重放：每次调用 prompt ≈56K tokens，其中它占 36% | 保留 `scan`；**禁读脚本全文**（T-004#B-002） |
| S2 判官 | JEV 稳定 451（`Typesafe is not available in your region`）→ 白付一次探针 + 一个回合；随后降级成「每 8–12 条一个分类子代理」= 10 个子代理 | 判官路径本身慢 + 子代理各自把上下文撑到 100–230KB（`kids_prompt_per_call_k ≈ 75.6`） | **判官直连 deepseek-flash 分块**（T-003#B-001），不再试探 JEV |
| S3 交叉审计 | 「每条落点=已有技能的行一个子代理」 | 行数 × 一个子代理，是第二次扇出 | **待定**，见 T-004#B-003 |
| S4 自动决议 | `merge` 是脚本 | — | 保留 |
| S5 写入 | 每个目标包一个 writer 子代理，包内串行 | 第三次扇出；同一批 10 个子代理里一段时间只有 2–9 个在动 | 写成脚本子命令（T-004#B-001） |
| S6 验证 | 一条与 writer 不同的只读子代理，逐行 PASS/FAIL | 第四次 LLM 往返；判据本身是脚本能核的（`diff -rq` / `git log` / 装回回读） | 脚本化（T-004#B-001） |
| S7 整包重装 | 命令由脚本生成，主 agent 跑 | 便宜 | 保留（脚本化后一条命令） |
| S8 退役与汇报 | 主 agent + 回读 | 便宜 | 保留 |
| **横切 A：provider 流断** | 今天 error log：TLS 握手超时 **104**、`peer closed connection` **66**、ReadTimeout **46**、`Stream stale 180/240/300s` 共 **46**；本机 A/B 实测 monk ttfb **34.8s / 10.2s** vs deepseek **0.79s / 0.88s** | 子代理的等待被拖过中断上限（15910.8s 那次）⇒ 04:00 那轮就是被这个收掉的 | 判官/子代理一律跑 deepseek-flash；dream 不再依赖 monk |
| **横切 B：运行期地基被拆** | `plans/dream-perf` T-009：`installs/*/environments/<hash>/` 被回收 10.5 分钟后重建，期间活进程新建 httpx 客户端 `FileNotFoundError`（今天计数 **209**） | 子代理集体卡死，看起来像「模型慢」 | 与本版无关的独立故障；不要在 20 分钟验收里撞上——跑验收前先确认环境目录没被回收 |
| **横切 C：cron 审批闸** | 09:17:55 带 `-c` 的 `python3` 被 BLOCKED、09:18:14 `execute_code` 被 BLOCKED ⇒ 之后每回合都改成「先写脚本文件再跑」 | 永久/必然，代价记在 LLM 账上（多出 7 次写脚本回合） | 已写进 `dream-hermes-routing`（dream-perf T-003#B-002） |

## 2 · 结论（v3 要动的地方）

1. **砍扇出次数**：v2 一轮最多四次扇出（判官 / 审计 / 写入 / 验证），每次扇出的 wall 由最慢的那个孩子决定。20 分钟目标下，只有「判官判断」必须留给 LLM，其余三次都要脚本化或并进判官那一批。
2. **砍上下文**：主 run 每次调用 56K prompt 里有一大半是重放别人的原文；规则是「一条命令 + 一段摘要」。
3. **换端点**：monk 在大 prompt 上吞吐低 + 流断，20 分钟这个量级不能建立在一个会静默 3–5 分钟的端点上。

## 3 · 怎么复现这份基线

```bash
# 整轮台账
sqlite3 -readonly ~/.hermes/cron/executions.db \
  "select job_id,status,started_at,finished_at,error from executions where job_id='52fe6cf3a3dc' order by rowid desc limit 5;"
# 逐回合延时 + 调用账（会话 id 换成当次的）
sqlite3 ~/.hermes/state.db \
  "select rowid,role,timestamp,length(coalesce(content,'')) from messages where session_id='<session>' order by rowid;"
sqlite3 ~/.hermes/state.db "select * from session_model_usage where session_id='<session>';"
# 每 10 分钟一拍的指纹（含 last-15min provider 错误计数）
python3 -B plans/dream-perf/tools/perf-probe.py
```
