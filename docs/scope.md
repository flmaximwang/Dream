# Dream v3 · 范围与验收口径（冻结）

**冻结时间**：2026-10-11 · **来源**：用户（Stronghold3369）当日指示 · **计划**：`plans/dream-v3/plan.json`

## 1 · 这一版要做什么

把 dream 从「一轮 4–5 小时、常常失败」改造成**一轮 ≤20 分钟**可无人值守跑完的批次：
枚举 → 判落点 → 交叉审计 → 写入包 → 独立验证 → 整包重装 → 退役。

## 2 · 四条硬口径

| # | 口径 | 含义与边界 |
|---|---|---|
| ① | **不含「优化/整理 skill pack」这一步** | 删除 S9（全库去重 + 无类目归位）与 S10（合并 / umbrella）。**SkillWeave** 项目接手这件事，dream 不再碰包内结构的整理。dream 只负责「把没有仓库归属的本地技能收编进包仓库并装回」。 |
| ② | **一轮 = 一个 profile** | 运行单位是单个 profile（验收用 `default`），跑扫描当刻的**全量** local 候选，**不抽样**。 |
| ③ | **判官 = deepseek-flash** | JEV 稳定 451（地域限制），不再作为默认判官，也不做重试降级链；判官直接调 deepseek-flash。 |
| ④ | **判据 = wall clock ≤ 20 min** | 从起跑到「退役完成」，含装回与退役，贴实测时间戳与逐阶段耗时表。 |

## 3 · 不属于本版（明确排除）

- 包内技能的结构整理：合并 / umbrella / 归类 / 无类目归位（S9、S10）→ **SkillWeave**。
- 单个技能的收编 → `recruit-learning-in-profile`；装/更/卸机制 → `install-` / `update-` / `remove-hermes-skills`；
  profile 间补齐 → `maintain-hermes-profile-skill-parity`。
- 全 profile 一波（v2 的「为所有 profile 跑一遍」）→ 已从本轮单位中拿掉；多 profile 由「跑 N 轮」表达。

## 4 · 基线（为什么要重做）

| 轮次 | 起点 | 结果 |
|---|---|---|
| 04:00 定时 | 2026-10-11 04:00 | 08:50 失败，**4h50m**；父块收到的 `delegate_task` 结果是 `status=interrupted`、`waiting for model response (15910.8s)` |
| 09:07 手动重跑 | 2026-10-11 09:07 | 13:44 仍未完成（**≥4h37m**），10 个子代理 3 个未交卷 |

主因不是 dream 的逻辑，而是：①判官与子代理架构带来大量 LLM 回合；②provider（monk）流反复 stale/drop，把子代理的等待拖过中断上限。
逐阶段归因见 `docs/baseline-v2.md`。

## 5 · 开发与测试口径（用户 2026-10-11 指示）

**开发 dream 时绝不拿真 `~/Documents/AgentSkill` 当测试面**：先整份 `cp -Rc ~/Documents/AgentSkill ~/Documents/AgentSkill-copy`
（APFS clonefile，秒级、几乎不占额外磁盘），把每个仓的 `origin` 摘掉（`git remote remove origin`，这样任何误推都会当场失败），
再让 apply/verify 跑 `--packs-root ~/Documents/AgentSkill-copy`；**测完 `rm -rf` 删掉副本**。
真包在工作区的改动数就是判据：跑完测试 `git -C ~/Documents/AgentSkill/<包> status --porcelain` 必须是 0 项（他人的在途改动除外）。
