# 判官：把候选判出落点（v3 的路子与判据）

v3 只有一条判官路径：**主 agent 跑 `scripts/judge.py`**——它把 `judge-input-bodies.routed.json`（S1.5 判为
`general` 的那一类）按字节分块，**每块一次模型请求**（非流式），落 `verdicts-<model>.jsonl`。
**分类闸门（S1.5）不是这一步**：它先判「这条技能该不该进包」，有自己的脚本 `triage.py` 与自己的文档
`dream-triage-gate.md`；本文件只讲 S2「挑落点」。
没有「每 8–12 条一个分类子代理」：v2 那条路整批算下来是**第二次扇出**，wall 由最慢的孩子
决定，是 4–5 小时那两轮的主要成本之一。

## 0 · 可插拔后端（模型不写死在 skill 里）

后端与分块参数都在 `~/.hermes/dream-judge.json` 里配置，**按数组顺序由上往下尝试**，
第一个成功返回 verdicts 的后端胜出。目前支持两种后端类型：

- **`jev`** — TypeSafe System One（`api.typesafe.ai/v1/systemone`），用 `TYPESAFE_API_KEY`。
  JEV 是**决策模型**（choice Q&A），不是生成模型。它**两轮**出 verdict：
  ① 轴轮一次批量请求（`state` = 每条的 id/名/描述 + 正文开头 200 字），每条一问「工具 or 主题」；
  ② 落点轮**逐条**一次请求（`state` = 该条正文），criteria = 该轴的目录名 + 各包已有技能名 + `new`。
  置信度取 JEV 返回的 `probabilities[choice]`（**不要用 `confidence`**——它是 margin：
  `p_chosen − p_second`，实测 0.14 vs 概率 0.57）。
  `evidence` / `claims` 由脚本从正文**机械抽取**（JEV 不生成文本）。
  **JEV 的天然短板**：轴判断对 state/criteria 丰富度敏感、且多次重跑可能翻转（属「两轴相邻」歧义，
  靠置信度 + `why` 交 S3/S4 复核）；`new` 落点它取不了名字/边界，topic 暂用技能名、boundary 写占位符。
- **`openai`** — 任何 OpenAI 兼容的 `/chat/completions` 端点（DeepSeek、OpenAI、本地代理等），
  用 `key_env` 指定的环境变量取 key。整块 prompt 一次请求，解析 JSONL 输出。

配置示例（`skills/dream/dream-judge.example.json` 就是这份）：

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

- `backends` 按顺序尝试：JEV 在就先用 JEV，451/超时/没 key 就退回下一个后端。
- `chunk_bytes` / `workers` 也从这里读，不再写死在脚本里。
- `key_file`（可选，默认 `~/.hermes/.env`）：key 从这个文件按 `key_env` 找，**从不打印**。
- 每条 verdict 的 `source` / `model` 字段记下实际用的是哪个后端。
- 后端的实现在 `scripts/judge_backends.py`，统一接口 `judge_chunk(items, answer_space, *, key, model, base_url, timeout)`。
  新增后端 = 实现这个函数 + `judge_chunk` 里加一条分派 + 配置里加一项。
  离线自检：`python3 -B scripts/judge_backends.py --self-test`。

> JEV 历史：2026-10-11 本机对 `api.typesafe.ai` 稳定返回 451（区域限制），当时直接换成了
> deepseek-flash。Clash 规则修复后 JEV 可访问，现在作为可选后端放在配置列表首位。

## 1 · 怎么跑

```bash
# 在 scan 的 --out 目录上跑（重跑会复用已完成的块，不会重复付费）
python3 -B skills/dream/scripts/judge.py --dir <scan --out 目录>

# 只看分块计划，不发请求
python3 -B skills/dream/scripts/judge.py --dir <目录> --dry-run
# 烟测：只判前 5 条
python3 -B skills/dream/scripts/judge.py --dir <目录> --limit 5
```

- **每块落盘 + 断点续跑**：`judge-response-<n>.json` 里已有能覆盖该块全部 id 的回复时直接复用。
- `judge-request-<n>.json` / `judge-response-<n>.json` **永不删除**：那是「模型当时看到了什么」的唯一副本。
- `--workers` 从配置读（默认 2）。实测 3 块 ~140KB 并发时被 `Connection reset by peer`（Errno 54）打掉过；
  脚本自带 3 次退避重试，仍失败就把配置里的 `workers` 降到 1。

## 2 · 判据（已写进脚本题面，改脚本时别丢）

1. **没有「留本地」这个答案**：每条候选都必须有落点；判不出来给 `confidence: 0` 并写清缺哪条判据。
2. **第一步先答轴**（只在 `kind: new` 里出现）：主语是「某个软件/工具」还是「一类任务/学科/项目」。
   落点空间 = `answer-space.json` 的 `tool_dirs` / `topic_dirs`（整批塞进题面，不手抄目录名）。
3. **必须回正文证据**：`evidence` 是正文原句（30–120 字符）。**空 evidence 的 verdict 会被脚本丢掉**——
   只凭 name + description 的判法用户明确驳回过。
4. 形状（脚本逐行校验形状，`merge` 还会再拒一次）：

```json
{"id":"P12","dest":{"kind":"existing","repo":"AgentSkill-UsingPyMOL","dest_skill":"__new__","action":"move"},
 "why":"…","evidence":"正文：「…」","confidence":0.87}
{"id":"P31","dest":{"kind":"new","axis":"topic","topic":"RunningLocalBatches","personal":true,
 "boundary":"不吃常驻服务与 cron"},"why":"…","evidence":"…","confidence":0.8}
```

- `action`：`move`（整条迁进包）/ `merge`（内容并进 `dest_skill`）/ `strengthen`（两边头部各加界限声明）。
- `dest_skill: "__new__"` = 在该目录下**新增**一个技能（不是复用已有技能）。
- `source` / `model` 由脚本写死（`deepseek` / 实际模型名），**不许人工改成别的来源**——两组数字混算就等于说谎。

## 3 · 判完之后

1. `sweep-plan.py merge --dir <目录> --profile <p>`：覆盖率必须 100%（有未判定/被拒收的行 exit 4）。
2. **低置信 ≠ 判错**（同一份材料重跑分数就会变）。要复核的是两类，报告时分清：① 置信低于门限的行；
   ② 两级落点「险胜」的行。低置信的行**照常进包**（没有「留本地」这个答案），疑问留在 `why` 里交 S3 核。
3. 脚本 exit 1 只表示「有候选没判完」，按它打印的缺口补判（重跑复用已完成的块）。
