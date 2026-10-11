# 判官：把候选判出落点（v3 的路子与判据）

v3 只有一条判官路径：**主 agent 跑 `scripts/judge.py`**——它把 `judge-input-bodies.json` 按字节分块，
**每块一次 deepseek-flash 请求**（非流式），落 `verdicts-<model>.jsonl`。
没有 JEV 探针、没有「每 8–12 条一个分类子代理」：v2 那条路整批算下来是**第二次扇出**，wall 由最慢的孩子
决定，是 4–5 小时那两轮的主要成本之一。

> 为什么不用 JEV 了：JEV（TypeSafe System One）在本机稳定返回 **451**
> （`Typesafe is not available in your region`，2026-10-11 实测）。451 是**区域限制**，不是限流——
> 有 key 也没用，重试到超时只是白花时间。所以 v3 直接把判官换成 deepseek-flash。

## 1 · 怎么跑

```bash
# 在 scan 的 --out 目录上跑（重跑会复用已完成的块，不会重复付费）
python3 -B skills/dream/scripts/judge.py --dir <scan --out 目录> [--chunk-bytes 140000] [--workers 2]

# 只看分块计划，不发请求
python3 -B skills/dream/scripts/judge.py --dir <目录> --dry-run
# 烟测：只判前 5 条（1 块 ≈54s）
python3 -B skills/dream/scripts/judge.py --dir <目录> --limit 5 --workers 1
```

实测（2026-10-11，default profile，99 候选 / 正文 1.32MB）：**8 块**（每块 12KB–138KB）、
`--workers 2` 下**整批 166s**（含复用 4 块缓存；新判的 3 块各 65–114s）。S2 的时间就是这个量级。

- `--chunk-bytes` 默认 140000（≈35K tokens/块）。块大没有好处：实测 3 块 ~140KB 并发时被
  `Connection reset by peer`（Errno 54）打掉过；脚本自带 3 次退避重试，仍失败就降 `--workers`。
- **每块落盘 + 断点续跑**：`judge-response-<n>.json` 里已有能覆盖该块全部 id 的回复时直接复用。
- `judge-request-<n>.json` / `judge-response-<n>.json` **永不删除**：那是「模型当时看到了什么」的唯一副本。

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
