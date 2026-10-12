# Dream

把一批「没有仓库归属」的本地技能**无人值守**地批量收编进技能包仓库：枚举 → 按内容判落点 → 交叉审计 →
自动决议 → 写入 → 独立验证 → 整包重装 → 退役。

**边界（2026-10-11，已交接）**：dream 只做**profile 侧**的两件事——把「没有仓库归属」的本地技能**收编进包**、
装回后读 profile 的数并守闸门。**包内结构整理全部归 SkillWeave**（`github.com/flmaximwang/SkillWeave`）：
去重 / 无类目归位 / 多条合并（umbrella）/ 正文下沉 / `description` 改写，dream 不再含这些步骤、也不再实现第二套形状常量
（契约：SkillWeave 的 `docs/contract.md`）。反向亦然：**profile 级读数与闸门（`MAX_AGENT_SKILLS`）不进 SkillWeave**。
目标：一轮 ≤20 分钟（见 `plans/dream-v3/`）。

**一轮的默认形状（2026-10-13 改）**：收编之后**默认跑一遍 SkillWeave**——调它的 `weave.py`，不自己实现那套动作——
把本轮动过的包的分层披露整理掉。**跑 SkillWeave 不强制**：包太小、预算不够、或本轮没有任何包被写入时可以跳过，
但要在收尾汇报里写明**跳过了哪些包、为什么**。**跑 SkillWeave 时遇到的问题提 SkillWeave 的 issue**
（`flmaximwang/SkillWeave`）；dream 自己的问题提 Dream 的 issue。SkillWeave 的命令顺序
（`prepare → disclosure → dispatch → apply → verify → visualize → deliver`）以它仓库的 `docs/flow.md` 为准
（**唯一流程来源**），本仓库不复制一份。

**一个方法本体 + 一个 agent 一个适配技能**：方法本体不把任何 agent 当主语；某个 agent 的容器判据、
安装命令与判官接线由该 agent 的适配技能提供。

## 索引

| 路径 | 是什么 |
|---|---|
| `skills/dream/` | **general 方法主技能**（`SKILL.md` 第一层 + `references/dream-method.md` 细节 + `scripts/sweep-plan.py`） |
| `skills/dream-hermes-routing/` | Hermes 适配技能（profile 容器判据 / Skills-Manager 安装命令 / 可插拔判官接线） |
| `plans/dream-v3/` | **当前计划**：Dream v3 —— 一轮 ≤20 分钟、砍掉 skill-pack 优化步骤（口径冻结在 `docs/scope.md`） |
| `plans/dream-perf/` | 执行效率优化计划（2026-10-11 实测基线驱动：上下文瘦身 / 回合数压缩 / 两条硬事实 / 按 profile 拆批） |
| `plans/dream-upgrade/` | 本项目开发的 loomerto 计划（**计划持久化在项目里**，随项目一起提交） |
| `plans/skill-library-stewardship/` | skill 库结构化整理（归类 / 合并 / umbrella-building）——**已移交给 SkillWeave**（2026-10-11）；该计划的存量块与本次交接方向相反，口径待 `plans/handover-to-skillweave/` 的 T-005#B-001 拍板 |
| `docs/flow.md` | **一轮 dream 的全貌**（输入 · 16 态 · S1–S8 · 闸门 · 缺口 · 进度锚点） |
| `docs/scope.md` | v3 的范围与验收口径（四条硬口径 + 基线 + §6 16 态分类与去向） |

## 安装

Hermes profile 的 skill 由 **Skills-Manager** 装（用户 2026-10-11 起完全接管；`hermes skills` 那套不再用）：

```bash
CLI="$HOME/.skills-manager/bin/skills-manager-cli"      # 必须绝对路径：非交互 shell 的 PATH 里没有它
"$CLI" skills install --git "https://github.com/flmaximwang/Dream/tree/main/skills/dream"
"$CLI" skills install --git "https://github.com/flmaximwang/Dream/tree/main/skills/dream-hermes-routing"
"$CLI" skills deploy --agent hermes-default dream dream-hermes-routing   # default profile 的 agent key
"$CLI" skills update dream                                              # 已有源 → 取最新
```

回读：`"$CLI" --json skills show dream` 的 `source_ref` 指向 `github.com/flmaximwang/Dream`、
`deployed_to` 含 `hermes-default`。实测（2026-10-12）：`install --git` 只吃 GitHub tree URL
（`.../tree/<分支>/<子目录>`）；写 `owner/repo/skills/<名>` 会被当整仓克隆（404 或装成一条叫 `skills` 的技能）。

## 当前状态

- **装**：`skills/dream` —— 库里一份、default profile 里是符号链接 `~/.hermes/skills/dream` →
  `~/.skills-manager/skills/dream`（`deployed_to` = `hermes-default`、`hermes-asset-quant-invest`）。
- **未装**：`skills/dream-hermes-routing` —— 2026-10-12 实测：Skills-Manager 库里没有它，
  `~/.hermes/skills/` 下也没有它。按上面第 2 条装。
- 不装：`plans/`、`docs/` 是项目文件，不走 Skills-Manager。

## 形状（硬约束）

- 第一层（`SKILL.md`）只放：边界 · 分工表 · 决议规则 · 每步一行转指。
- 细节下沉 `references/dream-<topic>.md`；每份文档都必须被 `SKILL.md` 点名（没有孤儿文档）。
- 包内形状（行数 / 字节 / doc 字节 / 第一层条目）由 **SkillWeave** 量：
  `python3 scripts/weave.py layers <pack 目录>`、`python3 scripts/weave.py checks <pack 目录>`——repo 侧只留这一个实现。
- dream 自己只守 **profile 侧**那一个常量：`MAX_AGENT_SKILLS = 300`（S7 装前闸门）。
