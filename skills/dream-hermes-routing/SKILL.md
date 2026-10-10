---
name: dream-hermes-routing
description: "Use when 要给某个 Hermes profile 批量收编没有仓库归属的本地技能（dream 的 Hermes 适配层）：把 general 方法本体接到 profile 的 skill 容器、hermes skills 安装命令与判官（JEV/分类子代理）上。方法本体在同仓库 skills/dream，本技能只做 Hermes 侧的接口与安装。触发：「一键收编」「sweep profile skills into packs」「Hermes 上跑 dream」"
---

# dream-hermes-routing · dream 方法在 Hermes 上的适配层

**方法本体不在这里**：批次算法（枚举 → 判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 →
退役）在同仓库的 `skills/dream`。本技能只补 Hermes 的三处接口：**容器判据 / 安装命令 / 判官接线**。
先在 `skill_view dream` 读方法本体，再回本文件拿 Hermes 的具体值。

## 1 · 容器判据（Hermes 的 profile）

Hermes 上一个 profile 的 skill 有三种容器，**只有 `local` 是候选**——判据是 lock 与 manifest，不是目录名：

- `hub` — 名字是 `<profile>/skills/.hub/lock.json` 的 `installed` 的键 ⇒ 已有仓库与更新路径，跳过。
- `bundled` — 名字在 `<profile>/skills/.bundled_manifest` 里 ⇒ Hermes 自带的种子，不属于用户，跳过。
- `local` — 两者都不是 ⇒ 没有 lock 条目、没有 source of truth、没有生命周期 ⇒ 候选。

profile 根：default = `~/.hermes`，其余 = `~/.hermes/profiles/<名字>/`。
`--profile` 是**全局前置 flag**（`hermes --profile X skills install …`），子命令里没有这个参数。

⚠️ **看着像 `local`、但该从上游装回的一类**：目录里带 `_meta.json` / `skill-card.md`（ClawHub 记号）、
正文头部自报 `GitHub: <url>`、或正文写着 `.claude/skills/<名>/` 这类上游自带路径。它没有 lock 往往只是被
手工拷进来过。**这类行照样要有落点**（方法本体没有「留本地」），但 `why` 以 `upstream:<owner>/<repo>` 开头，
主 agent 在汇报里单列「该从上游装回（本次先并入 <包>；改走 `hermes skills install <owner>/<repo>/…` 由用户定）」。

## 2 · 落点空间（Hermes 的答案空间）

- **工具轴** → `AgentSkill-Using<软件名>`（PyMOL / ATSAS / BioXTASRAW / Dolt / git-annex / ComfyUI / Zotero /
  Discord / Obsidian / VSCode / OBS / Bilibili …）。
- **主题轴** → 存量主题包名（AgentEvolution / AgentOrchestration / CloudDrive / CodeExplain / DoingSAXS /
  HoldingConversations / JobHunt / LabProject / ObsidianManagement / PlasmidEngineer / ProteinDesign /
  QuantInvestment / SoftwareDev / StructuredResponse / TravelGuide / WatchingVideo / WebInspection）。
- 落点根 = **`DREAM_SKILL_ROOTS`**：Hermes 环境配置里的一个键，值是冒号分隔的绝对路径（当前两个：
  `/Users/maxim/Documents/AgentSkill` 与 `/Users/maxim/Repositories/Repo`）。它只是**路径清单**，
  本技能不读任何密钥、也不读任何配置文件的内容。
- `routable`（有 remote + 本机真有 `SKILL.md`）**只决定能不能 `install` 回来，不限制它能否当落点**。

## 3 · 安装命令（`hermes skills`）

```bash
hermes skills uninstall <裸名> -y                       # 退役旧来源
hermes skills install <owner>/<repo>/skills/<名> --category <类目> -y
hermes skills update  <裸名>                            # 整包重装用
hermes skills list                                     # 回读名字与类目
```

坑（踩过）：

- **`hermes skills check` 一次只吃一个名字**；不带名字会把 90+ 个技能逐个对远端核，卡死一次交付。
- **`hermes skills list` 的 Name 列会截断** ⇒ 用完整名 grep 它零命中。回读以 `diff -rq` 与 lock 为准。
- **别信收尾那句 `Updated N skill(s).`**（扫描被拒时照打）。
- 装了脚本的副本要用 **`python3 -B`** 跑，否则 `__pycache__` 会让 `update` 报「kept your local edits」。
- **不许用 `head`/`tail` 截 `hermes skills` 的长命令**：SIGPIPE 会让安装半途死掉。重定向到日志再读回。
- 安装到**同一个类目**时会就地替换掉原来的 lockless 副本 —— 那是目标态（一个名字一个目录），不是少了一步。

**扫描闸**：push 之前预测扫描 verdict。非 `safe` 先按扫描闸手册改写形态；`dangerous` 的 revision 在 hub 上
**永远装不上**，`--force` 也覆盖不了。

## 4 · 判官接线（JEV 优先，无 key 走子代理）

1. **探活**：`$HERMES_HOME/.env` 里有没有 `TYPESAFE_API_KEY`（`grep -c`，不回显值），再打一次探针。
   **探针 451**（`Typesafe is not available in your region`）与超时同样算「不可用」，**直接走子代理**，
   别重试到超时。
2. **JEV 在** → 把 `judge-input-bodies.json`（正文内联的那份）当 `state` 分块出题，落 `verdicts-jev.jsonl`，
   **每条记下回复里的 `model` 字段**才算真机答案。题面模板与两层走树：`references/` 见方法本体的
   `dream-judge-routing.md`。
3. **JEV 不在**（无 key / 探针非 200 / 451 / 429 退避两次仍失败）→ 派分类子代理，**每 8–12 条一个**。
4. **标注纪律**：只有带 `model` 字段的算 JEV；子代理/人工填的一律 `source: agent`，两组数字分开写，
   不许混着当「JEV 的效果」。

## 5 · 回读三件（收工判据）

1. `diff -rq <clone>/skills/<名> <profile>/skills/<类目>/<名>` 为空；
2. 逐文件 sha256 全部 MATCH（**内容判据**，不许拿 `source_revision` / commit sha 代替）；
3. `.hub/lock.json` 里 `installed.<裸名>.identifier` 指向该 repo 的 `skills/<名>`（只证明来源）。

## 6 · 扩展点：别的 agent 照本文件形状加

Dream 为一个 agent 提供一个适配技能：**general 方法本体只有一个（`skills/dream`），适配技能一个 agent 一个**。
新 agent 的适配技能按本文件的形状写（容器判据 / 落点空间 / 安装命令 / 判官接线 / 回读三件），
目录名 `<agent>-routing`，正文里写明「方法本体在同仓库 `skills/dream`」。**不要**把 agent 专有机制写回
`skills/dream`。
