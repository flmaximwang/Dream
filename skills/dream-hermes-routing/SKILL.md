---
name: dream-hermes-routing
description: "Use when 要给某个 Hermes profile 批量收编没有仓库归属的本地技能（dream 的 Hermes 适配层）：把 general 方法本体接到 profile 的 skill 容器、Skills-Manager 安装命令（skills-manager-cli 两拍：install --local → deploy --agent hermes-<profile>）与可插拔判官（judge.py 分块调模型，后端从 dream-judge.json 读）上。方法本体在同仓库 skills/dream，本技能只做 Hermes 侧的接口与安装。触发：「一键收编」「sweep profile skills into packs」「Hermes 上跑 dream」"
---

# dream-hermes-routing · dream 方法在 Hermes 上的适配层

**方法本体不在这里**：批次算法（枚举 → 分类 → 判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 →
退役）在同仓库的 `skills/dream`。本技能只补 Hermes 的四处接口：**容器判据 / 安装命令 / 判官接线 / 记忆写入**。
先在 `skill_view dream` 读方法本体，再回本文件拿 Hermes 的具体值。

## 1 · 容器判据（Hermes 的 profile；Skills-Manager 接管之后）

**装/更/卸的唯一所有者是 Skills-Manager**（用户 2026-10-11 起完全接管 Hermes 的 skill）。所以判据不再是
「有没有 `.hub/lock.json` 条目」，而是**谁管着它** —— 两边都不管的才是候选：

- **归 Skills-Manager 管**（B 轴 = yes）— **它管着这个 profile 里的这一份**（2026-10-13 改口径，仓库 issue #1/#3）。
  两条证据任一成立：① `skills-manager-cli --json skills list` 里这条的 `deployed_to` **含本 profile 的 agent**
  （`hermes-default` / `hermes-<profile>`）；② profile 里那一项是**指向 `~/.skills-manager/skills/<名>` 的符号链接**
  （部署的物理形状）。
  **不是**「它库里有这个名字」：库里有、没部署、profile 里躺真实副本的形态回落成 `mgr=no`，**去向再由 A/D 轴定**
  （实测 `dev-general/code-wiki`：`deployed_to: []` 却是真实目录，A 轴=official + D 轴=same ⇒ 改口径后 `mgr` 由 yes 改 no、
  action **仍是 `noop`**；只有本地来源且无可比者才落候选）。旧口径按名字扫 `skills list`，
  把它算成 `mgr=yes` → `noop`，同一轮里 S7 又反过来报它形状越界、要报给 SkillWeave —— 同一个对象两个阶段
  给出互斥结论。
  它在某个 profile 里的部署**就是一条符号链接**：`<profile>/skills/<名>` → `~/.skills-manager/skills/<名>`；
  `skills-manager-cli --json skills show <名>` 的 `deployed_to` 列出它装了哪些 agent。
- **Hermes 自带种子**（A 轴 = bundled）— 名字在 `<profile>/skills/.bundled_manifest`（`名字:md5`）⇒ 不属于用户，跳过。
- **`hub` 残留**（A 轴 = hub）— 名字在 `<profile>/skills/.hub/lock.json`。那是 `hermes skills install` 时代
  留下的条目：权威副本在远端，profile 里**没有任何本地可比者** ⇒ 归到 `third·hub·mgr=no·ext=no·fold`，
  动作 `noop`、只报不动。
- **`local`** — 三者都不是 ⇒ 候选（`local·local·mgr=no·ext=no·fold` ⇒ `judge-destination`）。

**Skills-Manager 侧一个 profile 就是一个 agent**（`skills-manager-cli --json agents list`）：

| agent key | skills_dir |
|---|---|
| `hermes-default` | `~/.hermes/skills` |
| `hermes-<profile>` | `~/.hermes/profiles/<profile>/skills` |

装回时的 `--agent` 就用这个 key（`merge` 默认拼 `hermes-<profile>`，`--manager-agent` 可改）。
内置的 `hermes` agent 是另一条（已被停用以免两份），别用它。

profile 根：**default = Hermes 根（`~/.hermes`）**，其余 = `<根>/profiles/<名字>/`。
注意 `HERMES_HOME` 指的是**当前活动 profile 的家**（跑 default 时它就是根）——所以脚本自己认根，
**不直接拿 `HERMES_HOME` 当根**：否则在 dev-general 会话里跑 `--profile default` 会去扫 dev-general（实测踩过）。

⚠️ **看着像 `local`、但该从上游装回的一类**：目录里带 `_meta.json` / `skill-card.md`（ClawHub 记号）、
正文头部自报 `GitHub: <url>`、或正文写着 `.claude/skills/<名>/` 这类上游自带路径。它没有归属往往只是被
手工拷进来过。**这类行照样要有落点**（方法本体没有「留本地」），但 `why` 以 `upstream:<owner>/<repo>` 开头，
主 agent 在汇报里单列「该从上游装回（本次先并入 <包>；改走
`"$CLI" skills install <owner>/<repo>[@skill]` 由用户定）」。

## 2 · 落点空间（Hermes 的答案空间）

- **工具轴** → `AgentSkill-Using<软件名>`（PyMOL / ATSAS / BioXTASRAW / Dolt / git-annex / ComfyUI / Zotero /
  Discord / Obsidian / VSCode / OBS / Bilibili …）。
- **主题轴** → 存量主题包名（AgentEvolution / AgentOrchestration / CloudDrive / CodeExplain / DoingSAXS /
  HoldingConversations / JobHunt / LabProject / ObsidianManagement / PlasmidEngineer / ProteinDesign /
  QuantInvestment / SoftwareDev / StructuredResponse / TravelGuide / WatchingVideo / WebInspection）。
- 落点根 = **`DREAM_SKILL_ROOTS`**：Hermes 环境配置里的一个键，值是冒号分隔的绝对路径（当前两个：
  `~/Documents/AgentSkill` 与 `~/Repositories/Repo`）。它只是**路径清单**，
  本技能不读任何密钥、也不读任何配置文件的内容。
- `routable`（有 remote + 本机真有 `SKILL.md`）**只决定能不能 `install` 回来，不限制它能否当落点**。

**包内整理 / 合并（不在本 skill 的范围）**：把包内多条窄技能合成一条 umbrella、清退与包同名的本地副本、
给无类目技能归位，都属于技能库的结构整理，由 **SkillWeave** 项目负责（用户 2026-10-11 定）。
本适配技能只覆盖「收编进包 → 装回 → 回读」这条链（§3 / §5）。

## 3 · 安装命令（`skills-manager-cli`，两拍）

```bash
CLI="$HOME/.skills-manager/bin/skills-manager-cli"      # 必须绝对路径：非交互 shell 的 PATH 里没有它
agent="hermes-<profile>"                                  # 这个 profile 在它那儿的 agent key（default → hermes-default）

# ① 装回一枚技能：先进中央库，再部署给这个 agent
"$CLI" skills install --local --name "<技能名>" "<包>/skills/<技能名>"   # 本地包 → 中央库
"$CLI" skills deploy  --agent "$agent" "<技能名>"                       # 中央库 → profile（挂成符号链接）

# ② 整包重装：库里已有的成员，取件 + 重新部署
"$CLI" skills update "<技能名>"
"$CLI" skills deploy --agent "$agent" "<技能名>"

# ③ 回读 / 只在它自己的库里退掉
"$CLI" --json skills show "<技能名>"        # path · source_ref · deployed_to
"$CLI" --json agents list                   # 这个 profile 的 agent key 与 skills_dir
"$CLI" skills undeploy --agent "$agent" "<技能名>"
```

**装回是两拍，不是一次**（实测的行为，不是可选）：

1. `skills install --local --name <名> <本地包里的技能目录>` → 进中央库（这一步总成功）。
2. `skills deploy --agent hermes-<profile> <名>` → **会被拒**：profile 里那份还是本地目录，它报
   `Refusing to replace …: it is not managed by Skills Manager`。
3. 所以要先 S8 退役（`tar czf` 备份 → 删掉 profile 里的本地副本）。
4. 再跑一次第 2 步（`deploy`）→ 挂上，profile 里变成指向中央库的**符号链接**。

坑（踩过）：

- **命令必须用 CLI 绝对路径**：非交互 shell 的 PATH 里没有 `skills-manager-cli`，写裸名字整段
  `command not found`（exit 127）。
- **`install` 只跑落点真在磁盘上的行**：`merge` 是按计划生成的，被扫描闸挡掉的行、以及 `merge` 行的源技能
  （正文已并进接收方，本就不该单独装）都在命令清单里，照着跑就是 `skill not found`。
  `apply.py install` 会先过滤出 `install-cmds.filtered.sh` 再跑。
- **同一份 merge 生成的命令会随 profile 现状换形**：profile 里已是符号链接的技能生成 `update`，
  还不是的生成 `install --local`。所以**装回之前要重跑一次 `merge`**（本轮实测 86 条从 install 变成 update）。
- **`--json` 才有机器可读回执**；不带它读不到 `deployed_to` / `path`，回读就只能靠 `diff`。
- 装了脚本的副本要用 **`python3 -B`** 跑，否则 `__pycache__` 会让下次取件报「kept your local edits」。

**扫描闸**：push 之前预测扫描 verdict。非 `safe` 先按扫描闸手册改写形态；`dangerous` 的 revision 装不上，
`--force` 也覆盖不了。

## 4 · 判官接线（可插拔后端，配置驱动）

后端与分块参数在 `~/.hermes/dream-judge.json` 里配置，按数组顺序尝试。
目前支持 `jev`（TypeSafe System One，决策模型）和 `openai`（任何 `/chat/completions` 端点）两种类型。
配置示例：

```json
{
  "backends": [
    {"name": "jev", "type": "jev", "model": "jev-latest",
     "base_url": "https://api.typesafe.ai/v1/systemone", "key_env": "TYPESAFE_API_KEY"},
    {"name": "deepseek", "type": "openai", "model": "deepseek-flash",
     "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"}
  ],
  "chunk_bytes": 140000,
  "workers": 2
}
```

主 agent 直接跑方法本体的 `skills/dream/scripts/judge.py`：

```bash
python3 -B skills/dream/scripts/judge.py --dir <scan --out 目录>
# 只看分块计划：--dry-run ；烟测：--limit 5
```

- 它在 `judge-input-bodies.json`（正文内联那份）上按字节分块，**每块一次非流式请求**，
  落 `verdicts-<backend>.jsonl`；`source` / `model` 由脚本写死（实际后端名 / 实际模型名）。
- **逐块落盘 + 断点续跑**：被打断后重跑同一条命令，已判完的块直接复用。**不要**手写别的判官脚本去绕它。
- 题面、形状、复核口径：方法本体的 `skills/dream/references/dream-judge-routing.md`。
- 后端实现在 `scripts/judge_backends.py`，统一接口 `judge_chunk(items, answer_space, ...)`。
  新增后端 = 实现这个函数 + 配置里加一项。

**JEV 是决策模型**（choice Q&A），不是生成模型：用两轮 choice 题（轴 + 落点）出 verdict，
`evidence` / `claims` 由脚本从正文机械抽取。OpenAI 兼容后端则整块 prompt 一次请求、解析 JSONL 输出。

**并发纪律**：实测 3 块 ~140KB 并发时会被 `Connection reset by peer`（Errno 54）打掉，脚本已带 3 次退避
重试；仍然 reset 就把配置里的 `workers` 降到 1。

## 5 · 回读三件（收工判据）

1. `diff -rq <包>/skills/<名> <profile>/skills/<类目>/<名>` 为空（profile 里那份是符号链接，`diff` 会跟进去）；
2. 逐文件 sha256 全部 MATCH（**内容判据**，不许拿 `source_revision` / commit sha 代替）；
3. `skills-manager-cli --json skills show <名>` 的 `path` 指向中央库那份、`deployed_to` 含这个 profile 的
   agent key（只证明来源与部署，不证明内容一致）。

## 6 · 记忆写入（S1.5 分类闸门分流出来的那两类）

S1.5（`triage.py`）把候选分成三类，只有 `general` 进包；`preference`（用户偏好）/ `env`（本机 / harness
特定环境）**不进包** —— 脚本在 `triage-summary.json` 的 `memory_queue` 里给出每条的压缩载荷
`memory{title,text}`，**写进记忆这件事由主 agent 用 Hermes 自己的记忆工具做**：

- **用什么写**：Hermes 的记忆工具（当前实例是 hindsight；主 agent 直接把 `title` + `text` 写成一条记忆即可——
  内容已经是压缩过的一段，别再自己发挥）。
- **不绑定基座**：换成别的 memory provider / 别的 harness，照各自的记忆工具写同样的 `title` + `text`——
  方法本体只写「适配层提供记忆的写入口」，不写死任何一家。
- **写法**：`title` 当标题、`text` 当正文；`memory` 为空的行**不写**（那是脚本按形状丢掉的坏行，先回 S1.5 重判）。
- **写完**：按 S8 的口径退役 profile 里的副本（先 `tar czf` 备份、再删；回读没通过不许删）。
- **JEV 后端的例外**：JEV 不生成文本，它的 `memory.text` 是脚本机械压的（`why` 里写明）——写之前自己看一遍。

## 7 · 扩展点：别的 agent 照本文件形状加

Dream 为一个 agent 提供一个适配技能：**general 方法本体只有一个（`skills/dream`），适配技能一个 agent 一个**。
新 agent 的适配技能按本文件的形状写（容器判据 / 落点空间 / 安装命令 / 判官接线 / 记忆写入 / 回读三件），
目录名 `<agent>-routing`，正文里写明「方法本体在同仓库 `skills/dream`」。**不要**把 agent 专有机制写回
`skills/dream`。
