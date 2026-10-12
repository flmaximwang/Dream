# Dream v3 · 范围与验收口径（冻结）

**冻结时间**：2026-10-11 · **来源**：用户（Stronghold3369）当日指示 · **计划**：`plans/dream-v3/plan.json`

## 1 · 这一版要做什么

把 dream 从「一轮 4–5 小时、常常失败」改造成**一轮 ≤20 分钟**可无人值守跑完的批次：
枚举 → 判落点 → 交叉审计 → 写入包 → 独立验证 → 整包重装 → 退役，**收编之后默认再跑一遍 SkillWeave**
（调用它的 CLI，不自己实现那套动作；**不强制**，见 §2 口径①）。

## 2 · 四条硬口径

| # | 口径 | 含义与边界 |
|---|---|---|
| ① | **不含「优化/整理 skill pack」这一步 — 已交接（2026-10-11）；但一轮**默认调用**它（2026-10-13 改）** | dream 侧已把 S9（全库去重 + 无类目归位）与 S10（合并 / umbrella）**连脚本一起删除**：① 移交的动作清单 = 去重 / 无类目归位 / 合并 umbrella / 正文下沉 / `description` 改写；② 接手方 = **SkillWeave**（`https://github.com/flmaximwang/SkillWeave`，契约文件 `docs/contract.md`，命令：SkillWeave 的 `weave.py layers|checks <pack>`；本技能被删的 S9/S10 两节即对应它的 `propose` 里的「并 / 分」两类动作）；③ 反向：**profile 级读数与闸门（`MAX_AGENT_SKILLS = 300`）不进 SkillWeave**，那是 dream 的活。**2026-10-13 补充**：一轮在 S5 写入之后**默认跑一遍 SkillWeave**（新 S9，调它的 CLI）——「不实现」不等于「不调用」；**不强制**（包太小 / 预算不够 / 本轮没有包被写入可跳过，但要写明跳过哪些与原因）。**跑 SkillWeave 的问题提 SkillWeave 的 issue**，dream 的问题提 Dream 的 issue。 |
| ② | **一轮 = 一个 profile** | 运行单位是单个 profile（验收用 `default`），跑扫描当刻的**全量** local 候选，**不抽样**。 |
| ③ | **判官 = 可插拔后端，配置驱动** | 后端与分块参数在 `~/.hermes/dream-judge.json` 里配置，按数组顺序由上往下尝试。当前支持 `jev`（TypeSafe System One）和 `openai`（任何 `/chat/completions` 端点）两类。JEV 曾稳定 451（区域限制），Clash 规则修复后可访问，现作为可选后端放在配置列表首位。 |
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

## 6 · 16 态分类与逐态去向（用户 2026-10-11 拍板；2026-10-12 补第 16 格）

四根轴各 2 值；Hermes 侧 **16 态**、general agent 侧 **7 态**（无 A 轴，且同一处坍缩）。

**为什么是 16 不是 15**：原表的前提是「第三方来源必有本地可比者」。Skills-Manager 接管后这个前提不成立 ——
`hermes skills install` 时代的 `hub` 副本（名字在 `.hub/lock.json` 里）**权威副本只在远端**，profile 里没有
任何同名的 built-in / official / bundled / external / manager 那一份 ⇒ 判不出 same/differs ⇒ 第 4 轴坍缩。
2026-10-12 在 dev-general profile 实测撞上（9 条 hub 技能），当时 `STATE_ACTIONS` 没有这一格，
`scan` 直接 `KeyError: ('third', 'no', 'no', 'fold')` 崩掉。

- **A 轴 · Hermes 来源**：`local`，或**第三方维护**的四种子类，按此优先级取第一个命中：
  - `built-in` = 发行版 `~/.hermes/hermes-agent/skills/`
  - `official` = 发行版 `~/.hermes/hermes-agent/optional-skills/`（`hermes skills repair-official --restore` 的来源）
  - `bundled` = `~/.hermes/skills/.bundled_manifest` 的 `名字:md5` 账本
  - `hub` = `~/.hermes/skills/.hub/lock.json`
- **B 轴**：归 Skills-Manager 管否（它的 CLI `skills list` 里有这个技能名）
- **C 轴**：归 `external_dirs`（`.env` 的 `DREAM_EXTERNAL_DIRS` 那份 JSON）里的某个包管否（同名且能定位到那份目录）
- **D 轴**：与**可比者**内容相同/不同。可比者 = external 命中那份 · Skills-Manager 中央库那份 ·
  `.bundled_manifest` 的 md5 · 发行版 `skills/` 那份 · 发行版 `optional-skills/` 那份。
  **没有可比者时 D 坍缩** —— 出现在两格：`A=local·B=no·C=no`（就是候选）与 `A=third·B=no·C=no`（如 hub 副本）。

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
| 第三方 · 任一种 · mgr=no · ext=no · **fold** | 不动（本地无任何可比者、权威副本在远端，如 `hub` 副本；只报） |
| 第三方 · mgr=no · ext=yes · same / differs | 不碰（官方是源；确是我加的改动才走 patch） |
| 第三方 · mgr=yes · ext=no · same | 不动 |
| 第三方 · mgr=yes · ext=no · differs | **建 patch 包** → manager 装 patch |
| 第三方 · mgr=yes · ext=yes · same | 不动 |
| 第三方 · mgr=yes · ext=yes · differs | 以我的包为源合回 → manager 重装 |

**装回按归属分三条路**（细节见 `docs/flow.md` §3「S7 装回的三条路」）：
① 归 Skills-Manager 管的 → 交它装（`skills-manager-cli skills update` + `deploy`）；
② 不归它管、但**本次运行把技能交给它管**（`merge` 的 `--skills-manager`，**2026-10-12 起默认开**，
   要关用 `--no-skills-manager`）→ 交它**从本地那份包**装（`skills install --local --name` + `deploy`）；
③ 两者都不是 → **直接复制**技能目录进 profile（与 agent 无关）。
**Hermes 侧默认走 ②**：装/更/卸的唯一所有者是 Skills-Manager；`--agent` 用它在 Skills-Manager 侧的那个
profile key（`hermes-default` / `hermes-<profile>`，见 `dream-hermes-routing` §1）。
dream 不走 agent 自己的安装命令（`hermes … skills install`）——那套没有跨 agent 的普适性；
代价是自己要负责"装到哪、类目怎么放"，好处是换 agent 不用改流程。
装/更新的**所有者只有一个**：同一枚技能要么由 Skills-Manager 管，要么由直接复制管，别同时动。

**判方向（只在 differs 时需要）**：拿 profile 那份去撞**所有者那一侧的历史版本**——
① 命中某历史版本 ⇒ **本地旧**（远端新，动作＝更新本地）；
② 所有者命中 HEAD、profile 谁都不命中 ⇒ **本地已偏离上游**（动作＝patch 包／收编）；
③ 那一侧没有历史可比（无 clone、无 md5 账本）⇒ **方向未知，只报**，不动手。

**落地物**：`sweep-plan.py scan` 逐条算 `state`（`A·source·mgr·ext·D`）与 `action`，写进 `inventory.tsv`
两列（`state` / `action`）与 `states.json`（含 general agent 的 7 态计数）；控制台打印态分布与动作分布。
`--hermes <profile>` 给 Hermes 侧 16 态；不给 A 轴时（general agent）就是同一份数据的 7 态视图。

**profile 解析（2026-10-12 修）**：`--profile default` = **Hermes 根**（`~/.hermes`），其余 = `<根>/profiles/<名>`。
脚本自己认根 —— `HERMES_HOME` 是**当前活动 profile 的家**（跑 default 时它才是根），不能直接当根用；
否则在 dev-general 会话里跑 `--profile default` 会去扫 dev-general。A 轴的两份账本（`.hub/lock.json` /
`.bundled_manifest`）也按**目标 profile** 读，不按 `HERMES_HOME`。
