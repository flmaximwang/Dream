# 实测基线：一轮 dream 的 47 分钟花在哪（2026-10-11）

来源：`cron 52fe6cf3a3dc` 手动触发的那一轮（execution `57b244d85cd749d4893f9d6ee1ff539c`，
session `cron_52fe6cf3a3dc_20261011_090717`，model `monk`，09:07:17 起跑）。
数据出自 `~/.hermes/state.db` 的 `messages` / `session_model_usage` 两张表，只读。

## 总量

```
主 run wall 47.0min = LLM 43.5min (93%) + 工具 3.0min (6%)
LLM: 27 个回合  中位 41.7s  最长 576s
API: 28 次调用 · input 491,277 tok · cache_read 1,090,432 tok · output 28,746 tok
     ⇒ 每次调用 prompt ≈ 56K tokens（含 cache hit）
三条 ~576s 的回合合计 28.8min = 全部时间的 61%
```

## 四个可归因的成因（按贡献排序）

1. **主 run 读了 `sweep-plan.py` 全文**（09:19:39，单条 57,732B，会话最大）。之后每个回合都重放它：
   上下文累计 159KB，其中它占 36%。只为了看用法。
2. **回合数 27、每个回合重付一次大 prompt**。一大半回合是「写一个小脚本 → 跑它」
   （2033 / 257 / 893 / 2206 / 1798 / 613 / 2055 字节各一次），而工具本身中位只跑 0.2s。
   写脚本的动机是绕 cron 审批闸，但代价记在 LLM 账上。
3. **monk 在长 prompt 上吞吐低**：那三条 576s 的回合，产出只有 1.2K–23.6K 字符的 reasoning +
   255B 的 tool call。9.6 分钟 ≈ 56K prompt 的 prefill + 数千 reasoning token。**不是卡死**，
   是便宜模型 + 大上下文的物理结果（选 monk 就是为了便宜，这个账要认）。
   ⚠️ 三条 574.8 / 575.8 / 575.8s 太整齐，像固定阈值而不像自然思考时长 —— 存疑，
   需要开请求耗时日志才能定性；只影响「还能再省多少」。
4. **两条白付的路径**：
   - JEV 探针：`TYPESAFE_API_KEY` 在（`key_present=1`），但稳定 `http_status=451`
     （`Typesafe is not available in your region`）⇒ 每轮白付一次探针 + 一个回合，之后才降级到 10 路子代理。
   - cron 审批闸：09:17:55 带 `-c` 的 python3 被 BLOCKED、09:18:14 `execute_code` 被 BLOCKED，
     之后改写成脚本文件的那些回合。**这两条是永久/必然，不是偶发。**

## 参考：上一轮（04:00 定时那一轮）

04:00:00 准点派发（`on_time`，lateness 0.7s）→ 08:50:26 以 `RuntimeError: agent reported failure`
结束，日志里最后一行是 `[subagent-1] ⚡ Interrupted during API call.`；交付目标
`discord:1558166085080055878` 另返 403 Missing Access。**不是「没跑」，是跑完失败且报告没发出去。**

## 怎么复现这份基线

```bash
# 逐回合延时（tool 结果 → 下一条 assistant = LLM 延时；assistant → tool 结果 = 工具耗时）
sqlite3 -separator '|' ~/.hermes/state.db \
  "select rowid,role,timestamp,length(coalesce(content,'')) from messages where session_id='<session>' order by rowid"
# 调用账
sqlite3 ~/.hermes/state.db "select * from session_model_usage where session_id='<session>'"
```
