# Dream v3 · 完整流程（flow.md）

这份是**一轮 dream 的全貌**：谁跑、跑什么命令、产出什么、判据是什么、哪一步要模型、哪一步会停下。
口径的冻结版在 `docs/scope.md`（§2 四条硬口径、§5 开发测试口径、**§6 15 态分类与逐态去向**）；
逐步的实操细节在 `skills/dream/references/dream-method.md`（s0–s8）。

---

## 0 · 一轮在干什么

把**一个 profile 里没有归属的技能**，判出它们该落进哪个包 → 写进包仓库 → 独立验证 → 装回 → 退役 profile 里的旧副本。
**一轮 = 一个 profile**（验收用 `default`），跑扫描当刻的**全量**候选，不抽样。判据是 **wall clock ≤ 20 min**。
v3 **不含** skill-pack 的结构优化（去重、无类目归位、合并/umbrella）——那部分已整体移交 **SkillWeave**。

---

## 1 · 输入（开跑前必须齐）

| 输入 | 从哪来 | 说明 |
|---|---|---|
| **profile** | `--profile <名>`，默认 `default` | 决定扫哪棵技能树：`$HERMES_HOME/skills` 或 `profiles/<名>/skills` |
| **落点池** | `.env` 的 `DREAM_EXTERNAL_DIRS` → 一份本地 JSON | JSON 里**只放本人点名的目录**（现在 1 条：`~/Documents/AgentSkill`）；每个目录自身或它的直接子目录＝一个包。Skills-Manager 管的技能**不写进 JSON**，用 `external-dirs.py manager-skills` 走它的 CLI 现查 |
| **产出目录** | `--out <dir>`；不给就 `~/.hermes/cache/scratch/sweep-<时间戳>/` | 一轮的所有产物都落这里 |
| **判官凭据** | `~/.hermes/.env` 的 `DEEPSEEK_API_KEY`（**从不打印**）+ `--model`（默认 `deepseek-flash`）/ `--base-url` | 判官直调 API，不经 JEV |
| **兄弟解析器** | `load-external-skill-index/scripts/skill-index.py` | frontmatter 解析**必须**用它（YAML 坑是真的）；回退链：worktree 旁边 → profile（两种布局）→ Skills-Manager 中央库 → external_dirs 包内 |
| **模式与开关** | `--test` / `--formal` / `--plan`；`--skills-manager`（本次运行是否把该 profile 的技能交给 Skills-Manager 管，决定 S7 走第②还是第③条路） | `--test`：写 `AgentSkill-copy`、`--no-push`、跳过 S7/S8；`--formal`：真包＋真推＋装回＋退役；`--plan`：只打印这一轮会跑什么 |

一条命令跑完整轮：`bash skills/dream/scripts/round.sh [--test|--formal] [--profile <名>] [--out <dir>]`。

---

## 2 · 先行判定：每个技能归到 15 态（Hermes）/ 7 态（general agent）

四根轴各 2 值，**没有可比者时第 4 轴坍缩** → Hermes **15 态**、general agent **7 态**：

- **A** Hermes 来源：`local` 或第三方四种子类 `built-in` / `official` / `bundled` / `hub`
- **B** 归 Skills-Manager 管否 · **C** 归 `external_dirs` 的某个包管否 · **D** 与可比者内容同否
- 可比者：external 命中那份 · Skills-Manager 中央库那份 · `.bundled_manifest` 的 md5 · 发行版 `skills/` · 发行版 `optional-skills/`

**只有 5 类动作会动手**（其余一律 `noop`），完整 15 态表见 `scope.md` §6：

| 动作 | 出现在哪几态 | 谁做 |
|---|---|---|
| `judge-destination`（判落点） | `local·no·no` | S2 判官 → S5 写入 |
| `reconcile`（合回/重装） | `local·no·ext·differs`、`local·yes·ext·differs` | 先判方向，再合回包或让 manager 重装 |
| `patch-package`（建 patch 包） | `local·yes·no·differs`、`third·yes·no·differs` | 建我维护的包 → manager 装 |
| `patch-or-restore` | `third·no·no·differs` | 先判方向：本地新→patch；远端新→更新本地 |
| `noop` | 其余 | 不动 |

**接线现状**：S1 逐条算出 `state` 与 `action`；**S2 已按它接线**——判官的候选就是 `action=judge-destination`
那一类，题面里每条候选带「归类」行。S1 里另有一套旧口径（`verdict=local`）只用于对账，两边不一致会打印 ⚠。
另外四类动作 —— `reconcile`、`patch-package`、`patch-or-restore` —— **还没有任何阶段承接**，
目前只出现在 `states.json` / `inventory.tsv` 里。也就是说：15 态现在**落地在"S1 算出来 + 报表"**这一层，
**没有落地成 S2–S8 的分支**（见 §7）。

**判方向**（只在 `differs` 时需要）：拿 profile 那份撞所有者那一侧的历史版本 ——
命中某历史版本 ⇒ 本地旧（远端新，动作＝更新本地）；所有者命中 HEAD 而 profile 谁都不命中 ⇒
本地已偏离上游（动作＝patch／收编）；那一侧没有历史可比 ⇒ **方向未知，只报**。

**装回只有一个所有者**：装/更新一律交 **Skills-Manager**；dream 不再用 `hermes skills install`，也不退役它装的东西。

---

## 3 · 阶段（S1–S8）

| 阶段 | 谁跑 | 命令 | 输入 | 产出（都在 `--out` 里） | 判据 / 停下条件 |
|---|---|---|---|---|---|
| **S1 枚举** | 脚本（只读） | `sweep-plan.py scan --profile <名> --external-dirs <json> --out <dir> [--parser-script <p>]` | profile 技能树 + 落点池 + 解析器 | `inventory.tsv`（含 `verdict`/`state`/`action`/`duplicate_of`）· `judge-input.json` · `judge-input-bodies.json` · `packs.json` · `answer-space.json` · `pack-index.txt` · `inputs.json`（每包 HEAD + 文件 sha256 + **profile 技能目录 sha256**）· `prescreen.json` · `states.json` | 候选 = `action=judge-destination` 那一类（**当前实现是旧的 `verdict=local`，今天两者恰好等价，两套判据并存**）；同名多份会被显式报出；解析器找不到 → exit 2 |
| **S2 判官** | 脚本调模型 API | `judge.py --dir <out> [--chunk-bytes 140000 --workers 2]` | `judge-input-bodies.json`（**只装 `action=judge-destination` 的候选**，每条带 `state`/`action`/`comparators`） | `verdicts-<model>.jsonl`（每条：id / dest / why / evidence / claims / confidence） | 覆盖率必须 100%；题面里每条候选带「归类」行、硬口径第 0 条写明「这批只有这一种归类，任务只有挑落点」；非流式（卡流正是 v2 的死因） |
| **S3 交叉审计** | 脚本（0 次 LLM） | `audit.py --dir <out>` | `verdicts*.jsonl` + 接收技能目录 | `audit.md` / `audit.json`（一条 claim 一行；命中给 `file:line`，未命中进 net-new） | 「全员未命中」的行标 ⚠ = 落点可能选错，退回 S2 |
| **S4 自动决议** | 脚本（0 次模型调用，只写 `--out`） | `sweep-plan.py merge --dir <out> --profile <名> [--skills-manager] [--manager-agent <名>]` | S2 的 `verdicts-<model>.jsonl` + S3 的 `audit.*` + S1 的 `inputs.json` | `resolved.json`（逐行决议）· `plan.md`（按落点分组给人看）· `install-cmds.sh`（装回命令清单，**不执行**）· `stale.json` · `profile-stale.json` | 见下方「S4 校验什么」；有未判定或被拒收的行 → exit 4 |
| **S5 写入** | 脚本 + **writer 子代理** | 新技能：`apply.py apply --dir <out> [--external-dirs <json>] [--no-push] [--dry-run] [--changes-out <路径>]`；已有技能被改：`apply.py taskbook` → 按包派 writer（**不做回归测试**） | `resolved.json` + `audit.md` | 各包 git commit（新包先建仓 + README）· `apply-report.json` · `writers.md`；改动清单默认在会话里报，给了 `--changes-out` 就落盘 | 扫描闸非 `safe` → 回滚不提交；name/desc 与包内已有撞车 → 跳过该行先改名/改 desc；包工作区有别人的改动 → 跳过该包；`merge`/`strengthen` 行脚本**不动**，交 writer |
| **S6 独立验证** | 脚本（只读，0 次模型调用） | `apply.py verify --dir <out> [--external-dirs <json>]` | 落点 clone + 决议 + S5 的报告 | 逐行 PASS/FAIL（日志可留下，如 `verify-r1.log`） | 见下方「S6 逐行核什么」；有 FAIL → exit 1，回 S5 修 |
| **S7 装回** | 脚本（按归属分三条路，见下） | `apply.py install --dir <out> [--max-skills <n>]` | `install-cmds.sh` + profile + 启动时的 `--skills-manager` 开关 | `install-gate.json`（装前总量闸）· `install-shape.json`（装后形状断言） | 现∪装 > `MAX_AGENT_SKILLS`（**300**）→ 不装、exit 3；形状越界只报给 SkillWeave、不自己 `git mv` |
| **S8 退役** | 脚本 + 汇报 | `apply.py retire --dir <out> --backup ~/.hermes/backups/dream-retire` | verify 通过的行 | 先 `tar czf` 备份（路径写进汇报）再删 profile 副本 | 只对**回读通过**的行动手；修不动的那行跳过，副本不删 |
| **收尾汇报** | 主 agent | —— | 上面全部产物 | 四段：枚举数 · 判定表 · **每行对应的证据** · ≤3 条要人拍板的事 | 只读产出与真写入**分开写**（只读：N 行涉及 M 个包名／真写入：K 个包 + 各自 sha） |

逐阶段计时落 `stages.tsv`（`round.sh` 自动记），这就是「20 分钟」的证据形状。

---

### S4 校验什么（逐行，机械检查）

S4 把 S2 的落点、S3 的重叠证据、S1 的输入快照合成**一行一条**的决议，同时对每行做这几项检查，不合格就拒收该行：

1. **落点在答案空间内**：判官给的包名必须出现在"所有包的目录名"清单里（名字写错 → 拒收）。
2. **落点＝已有技能时**，`dest_skill` 必须在那个包的技能清单里。
3. **落点＝新建包时**，必须给出主语轴（软件/工具 or 任务/学科）与**边界**（这个包不吃什么）——缺一个就拒收。
4. **不接受"留本地"**：本版没有这个答案，给了就拒收。
5. **覆盖率 100%**：每条候选都要有一行决议；有未判定或被拒收的行 → 整个 S4 以 exit 4 结束，先回去补判。
6. **每行带上"评估时那个包的位置"**（`input_commit`），并当场回读该包现在的状态：变了就标 `stale`（写 `stale.json`），这些行在 S5 会被跳过。
7. **技能树指纹比对**：S1 记下的每个技能目录的哈希当场回读一次，变了就写 `profile-stale.json`。

产出里 `install-cmds.sh` 只是**命令清单**，S4 不执行任何一条；真正装回是 S7。

### S7 装回的三条路（按 S1 算出的归属 + 启动参数）

装回不只有一条路，"谁来装"由 S1 的归属和本次运行的参数决定：

1. **该技能归 Skills-Manager 管**（B 轴 = yes）→ 交 Skills-Manager 装（`skills-manager-cli skills install/update`，它按自己的来源取件）；装完回读它自己的记录（该技能的中央库路径与已部署到哪个 agent）。
2. **不归它管，但本次运行传了 `--skills-manager`**（说明这个 profile 的技能交给 Skills-Manager 管）→ 让 Skills-Manager **从本地那份包**装：把本地包当作来源（本地路径，或就地改指向），再 install/deploy；装完同样回读它的记录。
3. **不归它管、也没传 `--skills-manager`** → 不经过 Skills-Manager，直接把技能目录从本地包复制进 profile 的 `skills/<类目>/<名>/`；装完回读 profile 目录，并与包内那份逐文件比对。

三条路都要先过**装前总量闸**（现有 ∪ 新装 ≤ 上限），装完都要跑**形状断言**。

### S6 逐行核什么（不采信 S5 的自述，重新去包里读）

对 S5 报过"已写入"的每一行，独立核这四件：

1. **交付目录在包里存在**：那条技能确实在落点包的技能清单里。
2. **提交对得上**：包里 `origin/main` 的 sha 就是 S5 报的那一条（没有远端时退回本地 HEAD）。比较时用完整 sha 前缀，避免缩写造成的假 FAIL。
3. **两边内容一致**：`diff -rq` 比"包里的那份"和"profile 里装的那份"；这一步在装回之前跑，所以通常显示"还没装"。
4. **安装来源标识符指向该仓库**（装回之后才核得到）。

**它不核什么**：正文写得对不对（那是 S3 + writer 的活）；writer 改过正文的那些行目前不在这套检查里（见 §7 缺口）。

**结果**：逐行 PASS/FAIL；出现 FAIL → 这一步以 exit 1 结束，回 S5 修，修不动的行在后面退役时会被跳过、副本不删。

## 4 · 哪几步仍然要模型（其余全是脚本）

1. **S2 判官**：每条候选读全文 → 二选一（工具轴/主题轴）→ 挑落点 → 逐字抄 3–8 条 `claims`。
   脚本只负责分块、HTTP、JSONL 拼装、覆盖率报告。
2. **S5 的 `merge`/`strengthen` 行**：要改接收技能的正文 → 由主 agent 按包派 **writer 子代理**（看 diff 合并，**不回归测试**）
   （`apply.py taskbook` 生成 `writers.md`，一个包一个 writer，任务书与五步形状见 `references/dream-agent-roster.md`）。
3. **S4 的逐行决议**：按「无人值守」判，不等人、不用 `clarify`、不发表单。

主 run 的上下文纪律（决定这一轮跑不跑得完）：**只跑命令、只读摘要**，不读脚本源码、不读长清单——
`references/dream-method.md` s0。

---

## 5 · 闸门与证据（都在脚本里，不靠自觉）

| 闸门 | 位置 | 作用 |
|---|---|---|
| 输入指纹 | `scan` → `inputs.json` | 每包 HEAD + 产出文件 sha256；S5 前比对，上游变了跳过整包 |
| **profile 指纹** | `scan` → `inputs.json`；`merge` 回读 | 每个技能目录的 sha256；树被别的会话改动 → 标 `profile-stale.json`（技能树是会动的，实测 239→237→226） |
| 同名多份 | `scan` → `inventory.tsv.duplicate_of` / `prescreen.json` | 身份＝相对路径，名字只用于找可比者；同名两份会互相顶替，必须显式处理 |
| 扫描闸 | `apply` 每行 | 非 `safe` 就回滚不提交（`dangerous`/`caution` 见扫描闸手册） |
| 装前总量闸 | `install` | 现有 ∪ 新装 ≤ `MAX_AGENT_SKILLS`（300），超了不装 |
| 装后形状断言 | `install` | 技能都在 `skills/<类目>/<名>/`；外部仓库里找不到对等物的算 `local`（`skill-mirror.py check`，**不看 `.hub/lock.json`**） |

---

## 6 · 开发 / 测试口径（用户 2026-10-11 定）

- 开发期一律 `--no-push`（包仓库是测试面）；测试轮跑到 `verify` 就停，**不推、不 retire**。
- 测试面 = `cp -Rc ~/Documents/AgentSkill ~/Documents/AgentSkill-copy` → 每个仓 `git remote remove origin`
  → `apply/verify --packs-root <副本>` → 测完删副本。**判据：真包 `git status --porcelain` 必须为 0。**
- 拿旧副本跑会大面积误报 stale（`input_commit` 只在同一棵树上可比）——**必须新建副本**。
- 改技能一律在外部包仓库里改 → commit → push → 再由 manager 装回；不在 profile 里直接改。

---

## 7 · 已知缺口（下一步要补）

- **S7 装回只有两条路的实现细节待跑实**：命令清单已按三条路生成（`--skills-manager` 开 → 交 Skills-Manager 从本地包装；关 → 直接复制；profile 里是符号链接的 → 交 Skills-Manager 更新），但**还没真跑**过一遍：
  `skills-manager-cli skills install --local` 这条参数组合只在文档里对过，没有实测回读。
- **S5 的形态（2026-10-12 定）**：不做 frontmatter 归一；**新技能只拷贝**，入口就是它自己的 `SKILL.md`，脚本只机械查 name/desc 与包内已有是否撞车（撞了跳过该行、报出来，改名/改 desc 后重跑）；**已有技能被改就只看 diff 交代理合并，不做回归测试**；不给包额外加 L1 入口——包结构优化整段归 SkillWeave。改动清单默认在会话里报，`--changes-out <路径>` 可落盘。
- **S5 的 writer 产物没有独立验证**（按上面口径**有意为之**）：不看正文质量、不回归测试；S6 只核"装的东西在不在、提交对不对得上"。
- **`reconcile` / `patch-package` / `patch-or-restore` 没有对应阶段**：15 态里这四类动作现在只在
  `states.json` 里列出来，S2–S8 没有任何一步会去执行它们（建 patch 包、判方向、让 manager 重装）。
- **S7 仍发 `hermes … skills install/update`**（见上一条缺口）。
- `drift`/`local` 的形状断言目前**只报不拦**；是否硬失败未定。
- 判方向的②③两档（撞历史版本）还没有落成命令——目前是人工/模型判。

---

## 8 · 一页图

```
输入(profile · 落点池 · out · 判官凭据 · 解析器 · 模式)
   │
S1 scan ── 15 态 + 候选 ── inventory / judge-input / inputs(指纹) / states.json      [脚本·只读]
   │
S2 judge ── 每候选一条决定 ── verdicts-<model>.jsonl                                  [模型·脚本调 API]
   │
S3 audit ── claims 逐条 grep 接收侧 ── audit.md / audit.json                          [脚本·0 LLM]
   │
S4 merge ── 逐行决议 ── resolved.json / plan.md / install-cmds.sh / stale             [脚本·只写 out]
   │
S5 apply ── 新技能拷贝进包 ── commits / apply-report.json / 改动清单                 [脚本]
        └─ merge/strengthen 行 → taskbook → writers.md → 按包派 writer               [模型]
   │
S6 verify ── 逐行 PASS/FAIL                                                          [脚本·只读]
   │
S7 install ── 装前总量闸 + 装回 + 装后形状断言                                        [脚本→(待)Skills-Manager]
   │
S8 retire ── tar 备份 → 删 profile 副本 → 四段汇报                                    [脚本+主 agent]
```

---

## 9 · 进度锚点（每次改这张表就更新）

| 环节 | 状态 |
|---|---|
| 输入口径（`DREAM_EXTERNAL_DIRS` JSON + `external-dirs.py`） | ✅ 落地 |
| 15 态 / 7 态分类与逐态去向 | 🟡 **半落地**：S1 算出来并落报表；**S2 已按 `action` 接线**（候选只取 `judge-destination`，题面带归类）。未落地：`reconcile`/`patch-package`/`patch-or-restore` 仍无承接阶段 |
| S1–S6 | ✅ 跑通过（测试副本：写 74 / 挡 10 / verify PASS 74 · FAIL 0） |
| S7 装回 | 🟡 命令清单已按三条路生成（`--skills-manager` 开关已加；不再出现 hermes 自己的安装命令），**未真跑** |
| S8 退役 | ⏳ 未真跑；口径要按"装回有多个所有者"重写（Skills-Manager 装的那份不该由 dream 删） |
| 正文改写（writer） | ⏳ 任务书已生成过（`writers.md`），真跑待 S5 正式轮 |
