# Dream

把一批「没有仓库归属」的本地技能**无人值守**地批量收编进技能包仓库：枚举 → 按内容判落点 → 交叉审计 →
自动决议 → 写入 → 独立验证 → 整包重装 → 退役。

**边界（2026-10-11）**：只做「收编 + 装回」。包内整理——多条合并 / umbrella、与包同名的本地副本清退、
无类目技能归位——归 **SkillWeave** 项目，dream 不含这一步。目标：一轮 ≤20 分钟（见 `plans/dream-v3/`）。

**一个方法本体 + 一个 agent 一个适配技能**：方法本体不把任何 agent 当主语；某个 agent 的容器判据、
安装命令与判官接线由该 agent 的适配技能提供。

## 索引

| 路径 | 是什么 |
|---|---|
| `skills/dream/` | **general 方法主技能**（`SKILL.md` 第一层 + `references/dream-method.md` 细节 + `scripts/sweep-plan.py`） |
| `skills/dream-hermes-routing/` | Hermes 适配技能（profile 容器判据 / `hermes skills` 安装命令 / JEV 判官接线） |
| `plans/dream-v3/` | **当前计划**：Dream v3 —— 一轮 ≤20 分钟、砍掉 skill-pack 优化步骤（口径冻结在 `docs/scope.md`） |
| `plans/dream-perf/` | 执行效率优化计划（2026-10-11 实测基线驱动：上下文瘦身 / 回合数压缩 / 两条硬事实 / 按 profile 拆批） |
| `plans/dream-upgrade/` | 本项目开发的 loomerto 计划（**计划持久化在项目里**，随项目一起提交） |
| `plans/skill-library-stewardship/` | skill 库结构化整理（归类 / 合并 / umbrella-building）——**已移交给 SkillWeave**（2026-10-11），dream 不再做这一步 |
| `docs/scope.md` | v3 的范围与验收口径（四条硬口径 + 基线） |

## 安装

三段式（Hermes）：

```bash
hermes skills install flmaximwang/Dream/skills/dream --category hermes -y
hermes skills install flmaximwang/Dream/skills/dream-hermes-routing --category hermes -y
hermes skills update dream
```

回读：`hermes skills list` 里两条都在，且 `~/.hermes/skills/.hub/lock.json` 的
`installed.dream.identifier` / `installed.dream-hermes-routing.identifier` 都指向
`flmaximwang/Dream/skills/<名>`。

## 当前状态

- 装：`skills/dream`（general 主技能）、`skills/dream-hermes-routing`（Hermes 适配）——两者都从本仓库装。
- 不装：无（`plans/`、`docs/` 是项目文件，不走 `hermes skills`）。

## 形状（硬约束）

- 第一层（`SKILL.md`）只放：边界 · 分工表 · 决议规则 · 每步一行转指。
- 细节下沉 `references/dream-<topic>.md`；每份文档都必须被 `SKILL.md` 点名（没有孤儿文档）。
- 长度上限是**可自检的常量**：`python3 -B skills/dream/scripts/check-disclosure.py skills/dream`
  （SKILL.md 行数/字节数、单文档字节数、第一层 skill 数；超限 exit 1）。
