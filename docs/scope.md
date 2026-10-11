# Dream v3 · 范围与验收口径（冻结）

**冻结时间**：2026-10-11 · **来源**：用户（Stronghold3369）当日指示 · **计划**：`plans/dream-v3/plan.json`

## 1 · 这一版要做什么

把 dream 从「一轮 4–5 小时、常常失败」改造成**一轮 ≤20 分钟**可无人值守跑完的批次：
枚举 → 判落点 → 交叉审计 → 写入包 → 独立验证 → 整包重装 → 退役。

## 2 · 四条硬口径

| # | 口径 | 含义与边界 |
|---|---|---|
| ① | **不含「优化/整理 skill pack」这一步 — 已交接（2026-10-11）** | dream 侧已把 S9（全库去重 + 无类目归位）与 S10（合并 / umbrella）**连脚本一起删除**：① 移交的动作清单 = 去重 / 无类目归位 / 合并 umbrella / 正文下沉 / `description` 改写；② 接手方 = **SkillWeave**（`https://github.com/flmaximwang/SkillWeave`，契约文件 `docs/contract.md`，命令：SkillWeave 的 `weave.py layers|checks <pack>`；本技能被删的 S9/S10 两节即对应它的 `propose` 里的「并 / 分」两类动作）；③ 反向：**profile 级读数与闸门（`MAX_AGENT_SKILLS = 300`）不进 SkillWeave**，那是 dream 的活。 |
| ② | **一轮 = 一个 profile** | 运行单位是单个 profile（验收用 `default`），跑扫描当刻的**全量** local 候选，**不抽样**。 |
| ③ | **判官 = deepseek-flash** | JEV 稳定 451（地域限制），不再作为默认判官，也不做重试降级链；判官直接调 deepseek-flash。 |
| ④ | **判据 = wall clock ≤ 20 min** | 从起跑到「退役完成」，含装回与退役，贴实测时间戳与逐阶段耗时表。 |

## 3 · 不属于本版（明确排除）

- 包内技能的结构整理：合并 / umbrella / 归类 / 无类目归位（S9、S10）→ **SkillWeave**（`flmaximwang/SkillWeave`，契约 `docs/contract.md`；dream 侧只剩一行转指）。
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

**开发期一律 `--no-push`**（用户 2026-10-11：包仓库既然是测试用的，肯定是 no-push）——测试轮跑到 `verify` 就停，
不推、也**不 retire**（retire 删的是 profile 里的真副本，不是测试面）。真推与退役留给正式那一轮。
**Dream 仓库收尾**：开发完成后把 `dream-v3` 与主 clone 上别人的提交合并（`git -C Repo/Dream merge`），再按指示推；
**合并之后必须清除工作树**（验收的一部分）：从主 clone 侧 `git worktree remove <worktree>` → `branch -d dream-v3` →
`worktree prune`，并确认主 clone 的 `git status --porcelain` 干净（不许在 worktree 里切 main 来清理）。
条目见 `plans/dream-v3` 的 `T-006#B-002`。

## 6 · 15 态分类与逐态去向（用户 2026-10-11 拍板）

四根轴各 2 值；Hermes 侧 **15 态**、general agent 侧 **7 态**（无 A 轴，且同一处坍缩）。

- **A 轴 · Hermes 来源**：`local`，或**第三方维护**的四种子类，按此优先级取第一个命中：
  - `built-in` = 发行版 `~/.hermes/hermes-agent/skills/`
  - `official` = 发行版 `~/.hermes/hermes-agent/optional-skills/`（`hermes skills repair-official --restore` 的来源）
  - `bundled` = `~/.hermes/skills/.bundled_manifest` 的 `名字:md5` 账本
  - `hub` = `~/.hermes/skills/.hub/lock.json`
- **B 轴**：归 Skills-Manager 管否（它的 CLI `skills list` 里有这个技能名）
- **C 轴**：归 `external_dirs`（`.env` 的 `DREAM_EXTERNAL_DIRS` 那份 JSON）里的某个包管否（同名且能定位到那份目录）
- **D 轴**：与**可比者**内容相同/不同。可比者 = external 命中那份 · Skills-Manager 中央库那份 ·
  `.bundled_manifest` 的 md5 · 发行版 `skills/` 那份 · 发行版 `optional-skills/` 那份。
  **没有可比者时 D 坍缩** —— 只有 `A=local·B=no·C=no` 这一格（所以是 15 不是 16）。

| 态 | 去向 |
|---|---|
| local · mgr=no · ext=no | **判落点** → 写进我的包 → push → 交 manager 装 |
| local · mgr=no · ext=yes · same | 不动（已装） |
| local · mgr=no · ext=yes · differs | **合回/重装**：先判方向（profile 新 → 合回我的包；包新 → manager 重装） |
| local · mgr=yes · ext=no · same | 不动 |
| local · mgr=yes · ext=no · differs | **建 patch 包**（我的改动落在第三方技能上）→ manager 装 patch |
| local · mgr=yes · ext=yes · same | 不动 |
| local · mgr=yes · ext=yes · differs | 以我的包为源合回 → manager 重装 |
| 第三方 · 任一种 · mgr=no · ext=no · same | 不动 |
| 第三方 · 任一种 · mgr=no · ext=no · differs | **先判方向**：本地新 → patch 包；远端新 → 更新本地 |
| 第三方 · mgr=no · ext=yes · same / differs | 不碰（官方是源；确是我加的改动才走 patch） |
| 第三方 · mgr=yes · ext=no · same | 不动 |
| 第三方 · mgr=yes · ext=no · differs | **建 patch 包** → manager 装 patch |
| 第三方 · mgr=yes · ext=yes · same | 不动 |
| 第三方 · mgr=yes · ext=yes · differs | 以我的包为源合回 → manager 重装 |

**装回按归属分三条路**（默认做法，可改；细节见 `docs/flow.md` §3「S7 装回的三条路」）：
① 归 Skills-Manager 管的 → 交它装（`skills-manager-cli skills update` + `deploy`）；
② 不归它管、但本次运行传了 `--skills-manager` → 交它**从本地那份包**装（`skills install --local` + `deploy`）；
③ 两者都不是 → **直接复制**技能目录进 profile（与 agent 无关）。
dream 不走 agent 自己的安装命令（`hermes … skills install`）——那套没有跨 agent 的普适性；
代价是自己要负责"装到哪、类目怎么放"，好处是换 agent 不用改流程。
装/更新的**所有者只有一个**：同一枚技能要么由 Skills-Manager 管，要么由直接复制管，别同时动。

**判方向（只在 differs 时需要）**：拿 profile 那份去撞**所有者那一侧的历史版本**——
① 命中某历史版本 ⇒ **本地旧**（远端新，动作＝更新本地）；
② 所有者命中 HEAD、profile 谁都不命中 ⇒ **本地已偏离上游**（动作＝patch 包／收编）；
③ 那一侧没有历史可比（无 clone、无 md5 账本）⇒ **方向未知，只报**，不动手。

**落地物**：`sweep-plan.py scan` 逐条算 `state`（`A·source·mgr·ext·D`）与 `action`，写进 `inventory.tsv`
两列（`state` / `action`）与 `states.json`（含 general agent 的 7 态计数）；控制台打印态分布与动作分布。
`--hermes <profile>` 给 Hermes 侧 15 态；不给 A 轴时（general agent）就是同一份数据的 7 态视图。
