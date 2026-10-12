# 分类闸门（S1.5）：一条技能该不该进包，先单独判一次

本文件是 `SKILL.md` 的下一层：S1.5 的判据、三类去路、命令与产出。

## 为什么要有这一步

「这条技能像哪个包」和「这条技能该不该进包」是**两次独立的判断**，混在一次里判会出事：
profile 里那些**记本机现状**的技能（某个服务在这台机器上不可达、某个本地路径/版本）和**用户口味的总结**，
按内容也「像某个包」——判官会老老实实给它们挑一个落点，然后它们就**永久住进了一个对外可见的仓库**，
而它们换台机器就不成立了。所以先在 S2 之前加一道闸：**只有通用解决方案才进包**。

| 类 | 是什么 | 去路 |
|---|---|---|
| `general` | **对某个具体问题的、可复用的解决方案**（工具用法 / 固定流程 / 学科操作 / 可复用排障） | 进 S2 判落点 → 写进包 → push → 装回 |
| `preference` | **某个用户本人**的口味或约定（语言 / 语气 / 要不要先确认 / 命名与排版习惯） | **不进包**：压缩后写记忆 |
| `env` | **这一台机器 / 这个 harness 的现状**（服务不可达、本地路径 / 端口 / 版本 / 凭据、只在本机存在的布局） | **不进包**：压缩后写记忆 |

判据是**可复用性**：把这条技能搬到另一台机器、另一个会话、另一个人手上，还成立吗？成立 → `general`。

## 怎么跑

```bash
python3 -B skills/dream/scripts/triage.py --dir <scan 的 --out 目录>   # 后端与分块从 ~/.hermes/dream-judge.json 读
python3 -B skills/dream/scripts/triage.py --dir <目录> --dry-run       # 只看分块计划，不发请求
python3 -B skills/dream/scripts/triage.py --dir <目录> --limit 5       # 烟测
python3 -B skills/dream/scripts/triage.py --self-test                  # 离线：只验三份产物的形状与分流口径
```

- **逐块落盘 + 断点续跑**（`triage-response-<n>.json` 的 `prompt_sha` 一致且答满了才复用）；请求/回复永不删。
- 后端与 S2 是**同一份配置、同一套可插拔**（`jev` / `openai`）；按数组顺序尝试，第一个返回分类的胜出。
- JEV 是决策模型：它只出 `choice`（三类之一），**不生成文本** ⇒ 非 general 行的 `memory` 由脚本机械压缩
  （`why` 里写明），写记忆前请主 agent 复核。OpenAI 兼容后端整块 prompt 一次请求。

## 产出（都在 `--dir` 里）

| 文件 | 内容 | 谁消费 |
|---|---|---|
| `triage-<backend>.jsonl` | 逐条：`id` / `kind` / `reusable` / `why` / `evidence` / `confidence` / `memory` | 主 agent 读摘要与单条 |
| `triage-summary.json` | 计数 + `general_ids` + `memory_queue`（**非 general 的行，带压缩后的 `memory` 载荷**） | 主 agent 写记忆照它写 |
| `judge-input-bodies.routed.json` | 只留 `general` 的候选 | **S2 的输入**（`judge.py --input` 指它） |

`reusable` 由脚本算（`kind == "general"`），**不采信模型自报**；空 `evidence` 的行直接丢掉（同 S2 的纪律）。

## 判完之后

1. **`general`**：进 S2 —— `judge.py --dir <目录> --input judge-input-bodies.routed.json`。
2. **`preference` / `env`**：**不进包、不进 S2**。主 agent 照 `triage-summary.json` 的 `memory_queue`
   把每条的 `memory{title,text}` 写进记忆 —— **用你所在 harness 提供的记忆工具**（本技能不绑定任何记忆基座；
   Hermes 侧的具体接线见适配技能 `dream-hermes-routing` §6）。写完按 S8 的口径退役 profile 里的副本
   （先 `tar czf` 备份、再删；回读没通过不许删）。
3. **`S4 merge` 认这份分流**：`judge-input-bodies.routed.json` 在，覆盖率就只算它那几条 ——
   被分流的行**不算「缺判」**（没有这份产物就按全量走，向后兼容没跑闸门的轮次）。

## 复核口径

- **覆盖率 100%**：每条候选都要有一行 `kind`；有未分类的行 → `triage.py` exit 1，先补判再往下走。
- **两类必须都出现**（真批次里）：全判成 `general`（或全判成 `env`）时要当异常看 —— 先抽查几条。
- 低置信不等于判错：照常分流，疑问留在 `why` 里；`general` 的行交给 S3 用 `claims` 去 grep 接收侧。
