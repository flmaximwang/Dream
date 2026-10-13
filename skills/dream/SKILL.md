---
name: dream
description: "Use when 要把一批「没有仓库归属」的技能批量收编进技能包仓库（枚举 → 按内容判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 → 退役）。通用方法本体，不绑定任何 agent；某个 agent 的适配（容器判据 / 安装命令 / 判官接线）见该 agent 的路由技能。触发：「批量收编」「sweep skills into packs」「把本地技能迁到包仓库」"
---

# dream · 批量收编一批本地 skill 进技能包仓库（通用方法）

一条批次：**枚举 → 先分类（通用 / 偏好 / 本机环境）→ 判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 → 退役**。
本文件是第一层披露：边界 + 分工表 + 决议规则 + 每一步一行转指。细节在 `references/`，按文末文档树进入。

## 边界（先读这一节）

- 本 skill 独占的是**批次**：一次枚举、**一次分类**、一次按内容判落点、一次按规则决议、按包并发、一次整包重装。
- **先分类再收编（S1.5）**：候选先过一道分类闸门（`triage.py`）—— 只有 `general`（对某个具体问题的**可复用**
  解决方案）才进 S2 判落点、写进包；`preference`（用户个人偏好）/ `env`（本机 / harness 特定环境）**不进包**：
  压缩成一段 `memory` 载荷写进记忆（用**所处 harness 提供的记忆工具**，本 skill 不绑定记忆基座），
  再按 S8 退役 profile 副本。判据与命令：`references/dream-triage-gate.md`。
- 不属于本 skill：单个 skill 的收编（→ `recruit-learning-in-profile`）、装/更/卸机制（→ `install-` /
  `update-` / `remove-hermes-skills`）、跨 profile 补齐（→ `maintain-hermes-profile-skill-parity`）、
  一轮会话自己的收获（→ `recruit-learning-in-session`）。
- **也不属于本 skill：技能库/pack 的整理与优化（已交接）**——去重 / 无类目归位 / 多条合并（umbrella）/
  正文下沉 / `description` 改写，全部归 **SkillWeave**（`github.com/flmaximwang/SkillWeave`，契约
  `docs/contract.md`，命令：SkillWeave 的 `weave.py`（`layers|checks|propose <pack>`））。dream 只做两件事：
  把「没有仓库归属的本地技能」收编进包仓库并装回（S1–S8），以及**读 profile 的数、守 profile 的闸门**
  （`MAX_AGENT_SKILLS`）。**反向**：profile 级读数与闸门不进 SkillWeave。
- **但一轮默认调用它（2026-10-13 改）**：S5 之后**默认跑一遍 SkillWeave**（S9）整理本轮动过的包——「不实现」不等于
  「不调用」；**不强制**，跳过要写明原因。**跑它的问题提 SkillWeave 的 issue**，dream 的问题提 Dream 的 issue。
- **固定顺序**：`dream 收编（跨包归属）→ SkillWeave 分层（包内结构）→ darwin 磨 description（单技能效果）`。
  **同一个对象上，下游不许越过上游改**：SkillWeave 改过路径后 dream 的落点映射就是 stale（`input_commit` 会拦），
  而 description 的打磨不该发生在路径还在动的时候。
- **本 skill 不把任何 agent 当主语**：某 agent 的容器判据（哪些 skill 算「没有仓库归属」）、安装/更新/卸载命令、
  判官接线、私有路径，一律由**该 agent 的适配技能**提供。**Hermes 的落点见同族 `dream-hermes-routing`**
  （容器 = 「归 Skills-Manager 管 / Hermes 自带种子 / 都不管」；安装 = `skills-manager-cli`（两拍：
  `install --local` → `deploy --agent hermes-<profile>`）；判官 = 分块直调语言模型）。
  本文件正文只写「适配层提供 X」。

## 主 run 的上下文纪律（决定这一轮跑不跑得完）

主 run 只做三件事：**跑一条命令（一轮 = `bash scripts/round.sh`）· 读那段摘要 · 按规则决议**。
不读源码、不读长清单、不灌正文，单次工具结果保持十几行以内 —— 四条禁令与理由见 `references/dream-method.md` s0。

## Agent 分工（本 skill 的主体，先读这张表）

| 阶段 | 谁做 | 为什么非它不可 | 明确不许 |
|---|---|---|---|
| S1 枚举 | **主 agent 跑 `sweep-plan.py scan`** | 上百条候选手抄必错；判官输入必须是**脚本产物**（凭记忆转写的题面曾逐条与源文件不同，整轮结论作废） | 不读技能正文、不判断归属 |
| S1.5 分类闸门 | **主 agent 跑 `triage.py`**（后端/分块同 S2，从 `~/.hermes/dream-judge.json` 读） | 「该不该进包」必须**独立于**「进哪个包」判：混在一次里判，本机现状记录与用户偏好会被硬塞进包、永久住在对外可见的仓库里（换台机器就不成立） | 不许把三类并成一类；非 `general` 的行**不许**再送进 S2；空 `evidence` 的行丢掉 |
| S2 路由 | **主 agent 跑 `judge.py`**（按字节分块，每块一次**非流式**请求、逐块落盘、断点续跑；后端从 `~/.hermes/dream-judge.json` 读，当前支持 JEV 与 OpenAI 兼容端点；题面与判据见 `references/dream-judge-routing.md`） | 判断必须独立于枚举：判官只读 `judge-input-bodies.routed.json`，不看 profile 的技能目录 | 不许只凭 name/desc 落点（空 `evidence` 的 verdict 会被丢掉）；不许人工改 `source`/`model` |
| S3 交叉审计 | **主 agent 跑 `audit.py`**（判官的 `claims` × `grep -nF` 接收侧） | 只有 `grep` 的真输出能给出接收侧 `file:line`；模型转述不算证据 | 不改任何文件；只出两张清单 |
| S4 自动决议 | **主 agent 跑 `sweep-plan.py merge` + 规则**（见「无人值守」） | 无人值守下没有闸口：规则把「明确归位」与「没判完」分开 | 不问人、不等人 |
| S5 写入 | **主 agent 跑 `apply.py apply`**（新技能：只拷贝 + 查 name/desc 撞车 + 扫描闸 + pathspec 提交推送）；**`merge`/`strengthen` 行按包派一个 writer 子代理看 diff 合并正文**（不做回归测试） | 同一 clone 里两个 writer 会互相踩 README、生成段与提交 | 不许 `git add -A`、不许子代理起后台进程、不许碰本包之外的路径、不许替别人提交在途文件 |
| S6 验证 | **主 agent 跑 `apply.py verify`**（逐行核目录 / sha / `diff -rq` 装好的副本） | 「自己写自己验」有乐观偏差 —— v3 改成脚本只读核验，结果按行 PASS/FAIL 落 `apply-report.json` | 只读；FAIL 的行回 S5 修，不静默放过 |
| S7 整包重装 | **主 agent 跑 `apply.py install`**（执行 `merge` 生成的 `install-cmds.sh`） | 命令由 lock 证据生成、条数与代价要在眼前 | 不装本批没改动的包 |
| S8 退役与汇报 | **主 agent 跑 `apply.py retire`** | 删除是最后一步，且必须等回读（脚本先 `tar czf` 再删） | 回读没通过不许删 |
| S9 SkillWeave（**默认跑、不强制**） | **主 agent 调 SkillWeave 的 CLI** | 「该拆 / 该并 / 该改写」是判断，机械执行在它的 CLI 里 —— dream 不实现第二套形状常量 | 不替它改脚本（问题提 `flmaximwang/SkillWeave` 的 issue）；跳过了必须写明原因 |

并发上限 10：>10 个包时按包分波（每波 ≤10 个 writer）。所有子代理**前台跑完即结束**——子代理起后台进程会把
完成通知的会话路由 pin 到子代理身上，人面会话被顶掉。子代理不能提问，**主 agent 也不提问**。

子代理任务书逐字模板与收据清单：`references/dream-agent-roster.md`。判官题面：`references/dream-judge-routing.md`。

## 无人值守（本 skill 的唯一模式）

一条批次从枚举跑到整包重装，中间**不问人、不等人、不发要点选的表单**。**没有「留本地」这个答案**：
每条候选都必须有落点，判不出来就是**没判完**，回 S2 重判那一条。

| 情形 | 自动动作 |
|---|---|
| 判官一致、落点是可用包 | 写入该包（进 S5） |
| 落点=包内已有技能、且 `audit.md` 给出 net-new 清单 | 按 net-new 折进那条技能（already there 的部分丢掉） |
| `audit.py` 报「落点不存在」或某行 `strengthen` 却全员未命中 | 那条**退回 S2 重判**；重判仍如此就单列进汇报，**不进包也不删副本** |
| 置信低 / 判官分歧 / verdicts 缺失 / `no_category` 但给不出目标类目 | **没判完** → 补一次判官重判；仍拿不准就在汇报里单列（写明缺哪条判据），**不进包、也不删它的副本** |
| 落点是**新包** | **不建仓库**（对外可见的持久动作）→ 进汇报「拟建」段，等有人在的会话点名再做 |
| 上游自有独立仓库的技能 | 照常给落点、进包，但在汇报里单列「该从上游装回」段（给出标识符） |
| 退役 / 整包重装 | 回读全绿 + `tar czf` 备份落盘之后自动执行 |

汇报即交付（四段：枚举数 · 判定表 · 每行的证据 · ≤3 条要人拍板的事）。要人拍板的事**不阻塞本批次**。
**交付验收**：表里不许出现没有落点的行，也不许出现只凭名称/类目/profile 得出的落点——出现一条就得重判。

## S1 · 枚举

主 agent 跑 `sweep-plan.py scan`（参数与容器判据由适配层给）。产出：`inventory.tsv`、**冻结**的
`judge-input.json`（id / skill / category / `path` / `skill_md` 绝对路径 / `refs` / 正文字节数）、
`judge-input-bodies.json`（内联正文）、`packs.json`、`answer-space.json`、`pack-index.txt`。
脚本自报 counts，先核一遍再往下走。完整做法：`references/dream-method.md#s1`。

S1 还逐条算 **16 态**（`state` / `action`）：态的定义、可比者与逐态去向见 `docs/scope.md` §6；产出 `states.json`
（hermes 16 态 + general agent 7 态）。控制台的 `16 态（hermes）=` 与 `逐态去向=` 两行先看，再决定这一轮做哪些态。

## S1.5 · 分类闸门

**在挑落点之前先问一句：这条技能该不该进包？** 三类，判据是**可复用性**（换台机器 / 换个会话 / 换个人还成立吗）：

- `general` —— 对某个具体问题的**可复用解决方案** → 进 S2 挑落点。
- `preference` —— **某个用户本人**的口味或约定（换了主人就不成立）→ **不进包**。
- `env` —— **这一台机器 / 这个 harness 的现状**（服务不可达、本地路径 / 端口 / 版本 / 凭据；换了机器就不成立）→ **不进包**。

非 general 的两类：脚本给一段压缩好的 `memory{title,text}`，由主 agent **用所处 harness 提供的记忆工具**
写进记忆（不绑定记忆基座），然后按 S8 退役 profile 副本。这一步只有模型能判（读全文、逐条回正文证据），
脚本负责分块、HTTP、形状校验与分流。

```bash
python3 -B skills/dream/scripts/triage.py --dir <scan 的 --out 目录>   # 实测 5 条 2.4s（JEV）/ 5.5s（deepseek）
```

读它最后两行摘要（`classified=N/M · general=… preference=… env=…` 与分流行）；产出三份：
`triage-<backend>.jsonl`（逐条）· `triage-summary.json`（计数 + `memory_queue`）·
`judge-input-bodies.routed.json`（**S2 的输入**）。S4 的覆盖率只算 routed 里那几条，被分流的行不算缺判。
完整判据、JEV 短板与复核口径：`references/dream-triage-gate.md`。

## S2 · 路由判断

**第一步是二选一，不是挑名字**：这条技能的**主语**是「某个软件/工具」还是「一类任务/学科」（主题）？
先答这一句，再在对应轴里挑落点。**严禁偷懒判法**：① 必须读 SKILL.md 全文（含正文点名的 `references/`）；
② 每个落点必须回一条正文证据；③ 禁止按类目批量套模板——每条独立判。判官还要为「落点=已有技能」的行
逐字抄 3–8 条 `claims`（S3 拿它 grep）。三段问法、`dest` 形状、`source` 标注：`references/dream-judge-routing.md`。

**只判 `action=judge-destination` 那一类**（S1 已逐条算出归类，`local·local·mgr=no·ext=no`）：题面里每条候选带一行
「归类」，硬口径第 0 条写明「这批只有这一种归类，任务只有挑落点」。归类不是这一类的行，判官要指出并给 `confidence=0`。

做法：主 agent 跑 `scripts/judge.py --dir <scan 的 --out 目录> --input judge-input-bodies.routed.json`
（后端与分块参数从 `~/.hermes/dream-judge.json` 读；那份 routed 文件就是 S1.5 判出来的 general 那一类）。
**只读它最后那行 wall/verdicts 摘要**。

## S3 · 交叉审计

「迁移」不是移动文件。落点是某包里的**已有技能**时，先证明重叠在哪。v3 不派子代理：判官的 `claims`
逐条 `grep -nF` 接收技能目录 → 命中的给**接收侧 `file:line`**（already there），未命中的进 net-new。

```bash
python3 -B skills/dream/scripts/audit.py --dir <scan 的 --out 目录>   # 实测 25 行重叠 / 160 claims / 1.5s / 0 次 LLM
```

读它的摘要行（重叠行数 · already-there · 落点不存在 · 零命中行）；**别整篇读 `audit.md`**。
`strengthen` 行若一条都没命中＝重叠说法没证据，退回 S2；`merge` 行全净新增是正常形状。

## S4 · 自动决议

`sweep-plan.py merge` → `plan.md`（按落点分组）+ `resolved.json`（逐行机器可读决议）+ `install-cmds.sh`
（S7 的命令清单，脚本生成、**不执行**）。主 agent 按「无人值守」逐行决议、不等人。
每条决议带 `input_commit`（评估时该包的 HEAD）：S5 写入前拿它比一次，上游变了就**跳过整包**并让你重跑
`scan`+`merge`——不许在旧结论上动手（`stale.json` / `plan.md` 的「输入指纹」段）。
判据：`merge` 覆盖率必须 100%（有未判定或被拒收的行 exit 4），且没有落点落在 `answer-space.json` 之外。

## S5 · 写入

```bash
python3 -B skills/dream/scripts/apply.py apply --dir <scan 的 --out 目录>   # 机械行；加 --dry-run 先看清单
```

新技能（`action=move` + `dest_skill=__new__`）= **只拷贝**（frontmatter 原样留着，不做归一）+ 查 name/desc 与包内已有是否撞车 + 扫描闸
（非 `safe` 就回滚不提交）+ 只 `git add` 该技能目录（pathspec）→ commit → `git push origin <sha>:main`；
包工作区有别人的改动就跳过那个包。**`merge`/`strengthen` 行脚本不动**（要改正文）：按包派 writer 子代理，
拿 `audit.md` 的 net-new 清单折进接收技能。新包先建仓库（幂等）→ 换 SSH remote → README 是交付物的一部分
→ 首推前必须扫出 `safe`。writer 任务书与五步形状：`references/dream-agent-roster.md`。

## S6 · 独立验证

```bash
python3 -B skills/dream/scripts/apply.py verify --dir <scan 的 --out 目录>  # 逐行 PASS/FAIL；exit 1 有 FAIL 行
```

逐行核：交付目录在 clone 里存在 · `origin/main` 的 sha 就是 S5 报的那条 · `diff -rq <clone> <安装副本>` ·
安装来源标识符指向该 repo · 从**装好的那份**跑一次脚本（后两件由 S7 后复核）。FAIL 的行回 S5 修。

## S7 · 整包重装

```bash
python3 -B skills/dream/scripts/apply.py install --dir <scan 的 --out 目录> --profile <名>   # 跑 install-cmds.sh，日志落盘
```

`install-cmds.sh` 由 `merge` 按**归属**生成，只有三条路（不再出现 agent 自己的安装命令）：① 归 Skills-Manager 管的
→ 交它 `update`+`deploy`；② 不归它管、但**本次运行把技能交给它管**（`--skills-manager`，**默认开**，
`--no-skills-manager` 才关）→ 交它 `install --local --name`（从本地那份包）+ `deploy --agent hermes-<profile>`；
③ 都不是 → 直接复制进 profile。**Hermes 侧默认走 ②**（Skills-Manager 是唯一所有者）。
口径与代价：`docs/flow.md` §3「S7 装回的三条路」；命令与坑：`dream-hermes-routing` §3。

先数条数写进汇报（一包 20 行 = 20 个 prompt 行，是代价）。先 `install` 新成员，再 `update` 改动过的与
**本包既有成员**（整包重装）。回读三件（同 S6 判据）。适配层的安装命令与坑：`dream-hermes-routing`。

`install` 里带两条**只有 dream 能做**的闸（它才看得到 profile）：**装前总量闸门**（`≤ MAX_AGENT_SKILLS`，
超了就不装、exit 3，落 `install-gate.json`）与**装后形状断言**（技能都在 `skills/<类目>/<name>/` · 外部仓库列表里找不到对等物的算 `local`（`skill-mirror.py check` 判，不看 `.hub/lock.json`）· **`skills/` 根下没有 SKILL.md 的孤儿目录与散落文件**（`orphans` / `stray_files`）· 越界只报给 SkillWeave、不自己 `git mv`，落 `install-shape.json`）。判据与命令：`references/dream-method.md` s7；
上限可用 `--max-skills <n>` 改（给人留的校准旋钮）。

## S8 · 退役与汇报

```bash
python3 -B skills/dream/scripts/apply.py retire --dir <scan 的 --out 目录> --backup <备份目录>  # 先 tar 再删
```

退役**只在回读通过之后**（脚本只对 verify 过的行动手）：先 `tar czf` 备份并把路径写进汇报，再删本地副本；
回读有 FAIL 的行先修，修不动的那行跳过（**副本不删**）。最后汇报四段：枚举数 · 判定表 · **每行对应的证据** ·
≤3 条要人拍板的事。**只读产出与真写入分开写**（只读：N 行落点涉及 M 个包名 / 真写入：K 个包仓库 + 各自 sha）。

## S9 · SkillWeave（**默认跑，不强制**）

S5 写入之后，对本轮**动过的包**（`apply-report.json` 里 `WRITTEN` 行的包名）跑一遍 SkillWeave，把包内的分层披露
整理掉。**dream 不实现这套动作**——调 SkillWeave 的 CLI，命令顺序以它仓库的 `docs/flow.md`（**唯一流程来源**）为准：

```
prepare → disclosure → dispatch → apply → verify → （动了 description/入口才有）judge+score → visualize → deliver
```

`SW=<SkillWeave 克隆>/scripts/weave.py`，`PACK=<包目录>`；`deliver` 是**默认动作**：合回真包 main 并 push 各 remote、
回读、重跑 visualize、清 worktree/分支。用法与坑见 SkillWeave 的 `skills/skillweave/SKILL.md`。

- **不强制**：包太小 / S1–S8 已经吃满预算 / 本轮没有任何包被写入 → 跳过，并在收尾汇报里写明**跳过了哪些包、为什么**。
- **不吃 20 分钟预算**：`≤20 min` 只判 S1–S8 的机械阶段；S9 单独计时写进汇报，超了就先跳过。
- **只报不改**：SkillWeave 自己的毛病提 `flmaximwang/SkillWeave` 的 issue，dream 不替它改脚本。

## 检查点

| 触发 | 动作 |
|---|---|
| 落点是**新包** | 只报不建：包名与 boundary 进汇报「拟建」段 |
| 候选 > 30 条 | 分块跑判官 / 分波派子代理，别一次灌进一个上下文 |
| 扫描 verdict 非 `safe` | `apply.py` 自动回滚不提交；按扫描闸手册改写形态再扫，仍 `dangerous` 就不推并写进汇报 |

## 反例（不要做的事）

- **不要问人、不要等人**：判不完就在汇报里单列（写明缺哪条判据），别用 `clarify`、别写「等你批准」、别发表单。
- **不要在 SKILL.md 正文里写出那对 Generated-by-Scripts 注释的原文**：生成段脚本按文本找分隔符，正文里出现
  一次，它就把从那里到文件末尾整段当成自己的区段**覆盖掉**。要提它时写「两行 Generated by Scripts 注释标记」。
- **不要把 N 个候选一次塞进一个子代理**：上下文爆掉、判断质量崩，还违背「一个包一个 writer」的边界。
- **不要同一个包派两个 writer**：README、生成段、提交会互相踩。
- **不要用 `mv` 当迁移**：包是 source of truth，副本只在回读之后退役。
- **不要把「本机现状 / 用户偏好」当通用方案收进包**：先过 S1.5 分类闸门（`triage.py`）——
  非 `general` 的走记忆（压缩后写进记忆工具）并退役副本，**不进 S2、不进包**。
- **不要让主 run 读脚本源码或长清单**：那正是这一轮跑不完的原因；只跑命令、只读摘要。
- **不要给「只关某个 profile 的记录或流程」建包**：新包只吃通用方法。

## 分层披露与长度上限（形状，硬约束）

第一层（本文件）只放：边界 · 分工表 · 决议规则 · 每步一行转指。细节一律下沉到 `references/dream-<topic>.md`。
上限是**可自检的常量**，超限即拆，不是建议：

| 常量 | 值 | 管什么 |
|---|---|---|
| `MAX_AGENT_SKILLS` | 300 | **profile** 技能总数 → S7 装前闸门（`apply.py install`；撞上说明该先清退，`--max-skills` 可改） |

**包内形状（行数 / 字节 / doc 字节 / 第一层条目）一律由 SkillWeave 量**——repo 侧只留 one 实现，dream 不重复实现第二套：
命令：SkillWeave 的 `weave.py layers|checks <pack 目录>`（仓库见下面「边界」节）。

## 文档树

`SKILL.md`（第一层：边界/分工/决议/每步一行转指）→ `references/`（细节）→ `scripts/`（命令，见下表）。
没有孤儿文档：每份 `references/*.md` 都被本文件点名一行（由 SkillWeave 的 `layers` / `audit` 量，dream 不自己实现）。

## Support files

| 文件 | 承担什么 |
|---|---|
| `references/dream-method.md` | S1–S8 的完整做法与判据 |
| `docs/flow.md`（仓库根） | **一轮的全貌**：输入 → 16 态先行判定 → S1–S8 阶段表 → 闸门 → 开发口径 → 缺口 → 进度锚点 |
| `references/dream-agent-roster.md` | merge/strengthen 行的 writer 任务书、并发与分波、收据清单 |
| `references/dream-judge-routing.md` | S2 判官题面模板、分块、`dest` 形状、`claims`、`source` 标注、复核口径 |
| `references/dream-triage-gate.md` | S1.5 分类闸门：三类判据、`memory` 载荷、命令与产出、复核口径 |
| `scripts/triage.py` | S1.5 分类闸门：分块调后端（与 judge 同一份配置），落 triage 三份产物（`--self-test` 离线自检） |
| `scripts/judge.py` | S2 判官：分块调模型（逐块落盘 + 断点续跑；后端从 `~/.hermes/dream-judge.json` 读） |
| `scripts/judge_backends.py` | 可插拔后端：JEV + OpenAI 兼容，按配置顺序尝试（`--self-test` 离线自检） |
| `dream-judge.example.json` | 判官配置示例：复制到 `~/.hermes/dream-judge.json` 并按需改后端列表 |
| `scripts/audit.py` | S3 交叉审计：`claims` 逐条 grep，出 already-there / net-new |
| `scripts/apply.py` | S5–S8：`apply` / `verify` / `install` / `retire` + `taskbook`（按包拼 writer 任务书） |
| `scripts/skill-mirror.py` | profile 技能 ↔ 外部仓库的对等物：`where` / `same` / `check`（homed / drift / local 三档） |
| `scripts/round.sh` | S1 → S1.5 → S2–S8 一条命令编排 + 逐阶段计时落 `stages.tsv`（`--test` 走副本 / `--plan` 只看清单） |
| `scripts/sweep-plan.py` | `scan` / `merge`（含 `--self-test`） |
| `scripts/external-dirs.py` | 落点池清单（`.env` 的 `DREAM_EXTERNAL_DIRS` 指向的本地 JSON）：`init` / `show` / `check` / `refresh` / `manager-skills`（含 `--self-test`） |

## Skill Structure

<!-- Generated by Scripts -->

```
dream/
├── SKILL.md
├── test-prompts.json
├── test-results.md
├── references/
│   ├── dream-method.md
│   ├── dream-agent-roster.md
│   └── dream-judge-routing.md
└── scripts/
    ├── sweep-plan.py
    ├── judge.py
    ├── audit.py
    ├── apply.py
```

<!-- Generated by Scripts -->
