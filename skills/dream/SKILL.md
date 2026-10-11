---
name: dream
description: "Use when 要把一批「没有仓库归属」的技能批量收编进技能包仓库（枚举 → 按内容判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 → 退役）。通用方法本体，不绑定任何 agent；某个 agent 的适配（容器判据 / 安装命令 / 判官接线）见该 agent 的路由技能。触发：「批量收编」「sweep skills into packs」「把本地技能迁到包仓库」"
---

# dream · 批量收编一批本地 skill 进技能包仓库（通用方法）

一条批次：**枚举 → 判落点 → 交叉审计 → 无人值守决议 → 写入 → 独立验证 → 整包重装 → 退役**。
本文件是第一层披露：边界 + 分工表 + 决议规则 + 每一步一行转指。细节在 `references/`，按文末文档树进入。

## 边界（先读这一节）

- 本 skill 独占的是**批次**：一次枚举、一次判断、一次按规则决议、按包并发、一次整包重装。
- 不属于本 skill：单个 skill 的收编（→ `recruit-learning-in-profile`）、装/更/卸机制（→ `install-` /
  `update-` / `remove-hermes-skills`）、跨 profile 补齐（→ `maintain-hermes-profile-skill-parity`）、
  一轮会话自己的收获（→ `recruit-learning-in-session`）。
- **也不属于本 skill：技能库/pack 的整理与优化**——包内多条合并 / umbrella、与包同名的本地副本清退、
  无类目技能归位。那是 **SkillWeave**（用户在建的项目）的活。dream 只做一件事：把「没有仓库归属的本地技能」
  收编进包仓库并装回（S1–S8），批次收尾就是退役与汇报。
- **本 skill 不把任何 agent 当主语**：某 agent 的容器判据（哪些 skill 算「没有仓库归属」）、安装/更新/卸载命令、
  判官接线、私有路径，一律由**该 agent 的适配技能**提供。**Hermes 的落点见同族 `dream-hermes-routing`**
  （容器 = profile 的 lock/manifest；安装 = `hermes skills install/update/uninstall`；判官 = 分块直调语言模型）。
  本文件正文只写「适配层提供 X」。

## 主 run 的上下文纪律（决定这一轮跑不跑得完）

主 run 只做三件事：**跑一条命令 · 读那段摘要 · 按规则决议**。判断与读写都在脚本和子代理里，不在主 run 的上下文里。

- **不读源码**：`scripts/*.py` 一律不整篇读（要用法/形状读 `references/`，要看实现就 `--help` 或 `--self-test`）。
- **不读长清单**：`plan.md` / `audit.md` / `install.log` 只读脚本打印的**摘要行**；要看某一行用 `grep`/`sed -n` 取那几行。
- **不灌正文**：候选正文只随 `judge.py` 的分块请求走（脚本内联，不进主 run）；主 run 只看 counts 与 verdict 摘要。
- 每次工具调用的结果都应是**十几行以内**的摘要；单个结果 >40KB 就是设计错了，回去改脚本的输出。

## Agent 分工（本 skill 的主体，先读这张表）

| 阶段 | 谁做 | 为什么非它不可 | 明确不许 |
|---|---|---|---|
| S1 枚举 | **主 agent 跑 `sweep-plan.py scan`** | 上百条候选手抄必错；判官输入必须是**脚本产物**（凭记忆转写的题面曾逐条与源文件不同，整轮结论作废） | 不读技能正文、不判断归属 |
| S2 路由 | **主 agent 跑 `judge.py`**（按字节分块，每块一次**非流式**请求、逐块落盘、断点续跑；题面与判据见 `references/dream-judge-routing.md`） | 判断必须独立于枚举：判官只读 `judge-input-bodies.json`，不看 profile 的技能目录 | 不许只凭 name/desc 落点（空 `evidence` 的 verdict 会被丢掉）；不许人工改 `source`/`model` |
| S3 交叉审计 | **主 agent 跑 `audit.py`**（判官的 `claims` × `grep -nF` 接收侧） | 只有 `grep` 的真输出能给出接收侧 `file:line`；模型转述不算证据 | 不改任何文件；只出两张清单 |
| S4 自动决议 | **主 agent 跑 `sweep-plan.py merge` + 规则**（见「无人值守」） | 无人值守下没有闸口：规则把「明确归位」与「没判完」分开 | 不问人、不等人 |
| S5 写入 | **主 agent 跑 `apply.py apply`**（机械行：拷贝 + 归一 frontmatter + 扫描闸 + pathspec 提交推送）；**`merge`/`strengthen` 行仍要按包一个 writer 子代理**改正文（拿 `audit.md` 的 net-new 清单） | 同一 clone 里两个 writer 会互相踩 README、生成段与提交 | 不许 `git add -A`、不许子代理起后台进程、不许碰本包之外的路径、不许替别人提交在途文件 |
| S6 验证 | **主 agent 跑 `apply.py verify`**（逐行核目录 / sha / `diff -rq` 装好的副本） | 「自己写自己验」有乐观偏差 —— v3 改成脚本只读核验，结果按行 PASS/FAIL 落 `apply-report.json` | 只读；FAIL 的行回 S5 修，不静默放过 |
| S7 整包重装 | **主 agent 跑 `apply.py install`**（执行 `merge` 生成的 `install-cmds.sh`） | 命令由 lock 证据生成、条数与代价要在眼前 | 不装本批没改动的包 |
| S8 退役与汇报 | **主 agent 跑 `apply.py retire`** | 删除是最后一步，且必须等回读（脚本先 `tar czf` 再删） | 回读没通过不许删 |

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

## S2 · 路由判断

**第一步是二选一，不是挑名字**：这条技能的**主语**是「某个软件/工具」还是「一类任务/学科」（主题）？
先答这一句，再在对应轴里挑落点。**严禁偷懒判法**：① 必须读 SKILL.md 全文（含正文点名的 `references/`）；
② 每个落点必须回一条正文证据；③ 禁止按类目批量套模板——每条独立判。判官还要为「落点=已有技能」的行
逐字抄 3–8 条 `claims`（S3 拿它 grep）。三段问法、`dest` 形状、`source` 标注：`references/dream-judge-routing.md`。

做法：主 agent 跑 `scripts/judge.py --dir <scan 的 --out 目录>`（默认 `--chunk-bytes 140000`、`--workers 2`）。
实测 99 候选 = 8 块、整批 341–447s。**只读它最后那行 wall/verdicts 摘要**。

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
判据：`merge` 覆盖率必须 100%（有未判定或被拒收的行 exit 4），且没有落点落在 `answer-space.json` 之外。

## S5 · 写入

```bash
python3 -B skills/dream/scripts/apply.py apply --dir <scan 的 --out 目录>   # 机械行；加 --dry-run 先看清单
```

机械行（`action=move` + `dest_skill=__new__`）= 拷贝 + frontmatter 只留 `name`/`description` + 扫描闸
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
python3 -B skills/dream/scripts/apply.py install --dir <scan 的 --out 目录>   # 跑 install-cmds.sh，日志落盘
```

先数条数写进汇报（一包 20 行 = 20 个 prompt 行，是代价）。先 `install` 新成员，再 `update` 改动过的与
**本包既有成员**（整包重装）。回读三件（同 S6 判据）。适配层的安装命令与坑：`dream-hermes-routing`。

## S8 · 退役与汇报

```bash
python3 -B skills/dream/scripts/apply.py retire --dir <scan 的 --out 目录> --backup <备份目录>  # 先 tar 再删
```

退役**只在回读通过之后**（脚本只对 verify 过的行动手）：先 `tar czf` 备份并把路径写进汇报，再删本地副本；
回读有 FAIL 的行先修，修不动的那行跳过（**副本不删**）。最后汇报四段：枚举数 · 判定表 · **每行对应的证据** ·
≤3 条要人拍板的事。**只读产出与真写入分开写**（只读：N 行落点涉及 M 个包名 / 真写入：K 个包仓库 + 各自 sha）。

## 检查点

| 触发 | 动作 |
|---|---|
| 判官分歧 / 置信低 / verdicts 缺失 / 无类目又给不出目标类目 | **没判完**：补一次判官重判；仍拿不准就在汇报里单列，**不进包也不删副本** |
| 判官只凭 name / description 就下了落点（`evidence` 空） | 那条 verdict 不算数：退回判官，要求读全文 + 回一条正文原句 |
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
- **不要让主 run 读脚本源码或长清单**：那正是这一轮跑不完的原因；只跑命令、只读摘要。
- **不要给「只关某个 profile 的记录或流程」建包**：新包只吃通用方法。

## 分层披露与长度上限（形状，硬约束）

第一层（本文件）只放：边界 · 分工表 · 决议规则 · 每步一行转指。细节一律下沉到 `references/dream-<topic>.md`。
上限是**可自检的常量**，超限即拆，不是建议：

| 常量 | 值 | 管什么 |
|---|---|---|
| `MAX_SKILL_LINES` | 240 | SKILL.md 行数 |
| `MAX_SKILL_BYTES` | 16000 | SKILL.md 字节数 |
| `MAX_DOC_BYTES` | 20000 | 单份 `references/*.md` 字节数 |
| `MAX_FIRST_LAYER_SKILLS` | 20 | 单个 skill pack 第一层披露的 skill 数 |
| `MAX_AGENT_SKILLS` | 120 | 单个 agent/profile 的技能总数 |

自检（超限 exit 1）：`python3 -B skills/dream/scripts/check-disclosure.py skills/dream`

## 文档树

`SKILL.md`（第一层：边界/分工/决议/每步一行转指）→ `references/`（细节）→ `scripts/`（命令，见下表）。
没有孤儿文档：每份 `references/*.md` 都被本文件点名一行（`check-disclosure.py` 会替你把这条守住）。

## Support files

| 文件 | 承担什么 |
|---|---|
| `references/dream-method.md` | S1–S8 的完整做法与判据 |
| `references/dream-agent-roster.md` | merge/strengthen 行的 writer 任务书、并发与分波、收据清单 |
| `references/dream-judge-routing.md` | S2 判官题面模板、分块、`dest` 形状、`claims`、`source` 标注、复核口径 |
| `scripts/judge.py` | S2 判官：分块直调语言模型（逐块落盘 + 断点续跑） |
| `scripts/audit.py` | S3 交叉审计：`claims` 逐条 grep，出 already-there / net-new |
| `scripts/apply.py` | S5–S8：`apply` / `verify` / `install` / `retire`（各带 `--dry-run` 或只读语义） |
| `scripts/sweep-plan.py` | `scan` / `merge`（含 `--self-test`） |
| `scripts/check-disclosure.py` | 分层披露长度上限自检 |

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
    └── check-disclosure.py
```

<!-- Generated by Scripts -->
