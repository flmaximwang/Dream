#!/usr/bin/env python3
"""dream · 可插拔判官后端。

每个后端实现统一接口：
    judge_chunk(items, answer_space, *, key, timeout) -> tuple[list[dict], str]
        items: 候选列表（每条含 id/skill/category/description/body/refs/state）
        answer_space: dict（tool_dirs/topic_dirs + packs 的技能清单）
        -> (verdicts, error)。verdicts 每条含 id/dest/why/evidence/confidence/claims。

后端按 dream-judge.json 里的顺序尝试，第一个成功返回 verdicts 的后端胜出。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERMES_HOME = pathlib.Path.home() / ".hermes"
# Allow overriding the config path (tests / multiple profiles), default ~/.hermes/dream-judge.json
CONFIG_PATH = pathlib.Path(
    os.environ.get("DREAM_JUDGE_CONFIG") or (HERMES_HOME / "dream-judge.json")).expanduser()

# ─── shared helpers ──────────────────────────────────────────────────────────────


def _read_key(key_env: str, key_file: pathlib.Path) -> str:
    """从环境变量或 .env 文件读 key，不打印值。文件不存在时返回空串（由调用方报错）。"""
    if key_env in os.environ:
        return os.environ[key_env]
    try:
        text = key_file.read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith(key_env + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def _http_post(url: str, payload: dict, key: str, timeout: int) -> tuple[dict, str]:
    """POST JSON to url with Bearer key. -> (response_dict, error)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), ""
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300] if hasattr(e, "read") else ""
        return {}, f"HTTP {e.code}: {detail or e.reason}"
    except Exception as e:  # noqa: BLE001
        return {}, f"{type(e).__name__}: {e}"


# ─── OpenAI-compatible backend ──────────────────────────────────────────────────

_JUDGE_RULES = """你是 dream 批次（把「没有仓库归属」的本地技能收编进技能包仓库）的**判官**。逐条判定落点。

硬口径：
0. **这批候选的归类只有一个**：`local·local·mgr=no·ext=no`——所以这一轮要做的**只有挑落点**这件事。
1. **没有「留本地」这个答案**：每条候选都必须有落点。判不出来请给 confidence=0 并写清缺哪条判据。
2. **第一步先答轴**（只出现在 kind=new 里）：这条技能的**主语**是「某个软件/工具」还是「一类任务/学科/项目」。
3. 落点=已有目录时必须**回一条正文证据**（原文短句，30–120 字符）。只凭 name/description 下结论的会被退回。
4. `dest_skill` 填 `__new__`（表示在这条目录下新增一个技能）；`action` 三选一：move / merge / strengthen。
5. 拿不准时：照常给落点，把疑问写进 why，confidence 给低值。
6. **落点=已有技能**的行还要回 `claims`：从候选正文里**逐字抄** 3–8 条短串。

输出格式：**只输出 JSONL**，一条候选一行，不要解释、不要 markdown 代码围栏。每行形状：
{"id":"P12","dest":{"kind":"existing","repo":"AgentSkill-UsingPyMOL","dest_skill":"__new__","action":"move"},"why":"…","evidence":"正文：「…」","confidence":0.87}
{"id":"P23","dest":{"kind":"existing","repo":"AgentSkill-UsingGit","dest_skill":"investigate-git-history","action":"merge"},"claims":["git log -S","git blame -w"],"why":"…","evidence":"…","confidence":0.6}
{"id":"P31","dest":{"kind":"new","axis":"topic","topic":"RunningLocalBatches","personal":true,"boundary":"不吃常驻服务与 cron"},"why":"…","evidence":"…","confidence":0.8}
"""


def _build_openai_prompt(items: list[dict], answer_space: dict) -> str:
    """Build the full judge prompt for OpenAI-compatible backends."""
    tool_dirs = answer_space.get("tool_dirs") or []
    topic_dirs = answer_space.get("topic_dirs") or []
    packs = answer_space.get("packs") or []

    space_lines = ["落点空间（`repo` 只能从这里挑；挑不到就 kind=new 造新包）：",
                   f"- 工具轴（{len(tool_dirs)} 个）：{', '.join(tool_dirs)}",
                   f"- 主题轴（{len(topic_dirs)} 个）：{', '.join(topic_dirs)}",
                   "",
                   "各目录的现有技能（`action=merge|strengthen` 时 `dest_skill` **必须是该目录下面列出的名字之一**）："]
    for p in packs:
        if p.get("skills"):
            space_lines.append(f"- {p['repo']}: {', '.join(p['skills'])}")
    space = "\n".join(space_lines) + "\n"

    item_blocks = []
    for it in items:
        refs = it.get("refs") or []
        state = it.get("state") or ""
        lines = [f"### {it['id']} · skill={it['skill']} · category={it.get('category') or '(无类目)'}"
                 f" · refs={refs if refs else '无'}"]
        if state:
            lines.append(f"归类：{state}（这一类就是「三方都没主」→ 你的任务只有一件：挑落点）")
        lines += [f"description: {it.get('description') or ''}",
                  "--- SKILL.md 全文 ---",
                  f"{it.get('body') or ''}"]
        item_blocks.append("\n".join(lines))

    return (_JUDGE_RULES + "\n" + space +
            f"\n待判候选（逐条判，输出条数必须等于 {len(items)}）：\n\n" +
            "\n".join(item_blocks))


def _parse_jsonl(text: str) -> list[dict]:
    """Parse JSONL verdicts from model output text."""
    import re
    text = re.sub(r"^\s*```(?:jsonl?)?|```\s*$", "", text.strip(), flags=re.M)
    verdicts = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            v = json.loads(line)
            verdicts.append(v)
        except json.JSONDecodeError:
            continue
    return verdicts


def judge_openai(items: list[dict], answer_space: dict, *, key: str,
                 model: str, base_url: str, timeout: int) -> tuple[list[dict], str]:
    """OpenAI-compatible backend: send prompt to /chat/completions, parse JSONL response."""
    prompt = _build_openai_prompt(items, answer_space)
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "stream": False, "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"

    raw = body["choices"][0]["message"]["content"]
    verdicts = _parse_jsonl(raw)
    if not verdicts:
        return [], "no valid JSONL in response"
    return verdicts, ""


# ─── JEV backend ────────────────────────────────────────────────────────────────

def _build_jev_questions(items: list[dict], answer_space: dict) -> dict:
    """Build JEV choice questions for axis + destination.

    Returns a dict with two rounds:
    - round 1 (axis): one question per item, criteria = {tool, topic}
    - round 2 (destination): one question per item, criteria = answer-space dirs + new

    JEV supports multiple questions per request, so we batch them.
    """
    tool_dirs = answer_space.get("tool_dirs") or []
    topic_dirs = answer_space.get("topic_dirs") or []
    packs = answer_space.get("packs") or []

    # Build per-item state text (description + body, truncated)
    def item_state(it: dict) -> str:
        desc = it.get("description") or ""
        body = it.get("body") or ""
        # JEV state limit: keep it reasonable (first ~4000 chars of body)
        if len(body) > 4000:
            body = body[:4000] + "\n…（正文过长已截断）"
        return f"技能名：{it['skill']}\n描述：{desc}\n--- 正文 ---\n{body}"

    # Round 1: axis questions
    axis_questions = {}
    for it in items:
        axis_questions[it["id"]] = {
            "type": "choice",
            "instructions": "这条技能的**主语**是「某个软件/工具」还是「一类任务/学科/项目」？",
            "criteria": {
                "tool": "主语是某个具体软件/工具（PyMOL、Dolt、git-annex、ComfyUI、Obsidian 等）",
                "topic": "主语是一类任务、学科或项目（做 SAXS 分析、整理笔记库、跑本地批处理等）"
            }
        }

    return axis_questions


def _jev_call_questions(questions: dict, state_text: str, model: str,
                        key: str, base_url: str, timeout: int) -> tuple[dict, str]:
    """Call JEV with a batch of questions. -> (answers_dict, error).

    answers_dict maps question_id -> {choice, confidence, probabilities}.
    """
    payload = {
        "state": state_text,
        "model": model,
        "questions": questions
    }
    resp, err = _http_post(base_url, payload, key, timeout)
    if err:
        return {}, err
    return resp.get("answers") or {}, ""


def _extract_evidence(body: str) -> str:
    """Extract a short evidence sentence from skill body (mechanical, not model-generated)."""
    import re
    # Find first non-heading, non-empty line with >20 chars
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        if len(line) >= 20:
            # Truncate to 120 chars
            return line[:120]
    return ""


def _extract_claims(body: str) -> list[str]:
    """Extract claim-like strings from skill body (commands, flags, paths)."""
    import re
    claims = []
    for line in body.splitlines():
        line = line.strip()
        # Match lines that look like commands/flags/paths
        if re.search(r'(--[\w-]+|```|\.py|\.sh|/Users/|~/|\.md\b)', line):
            # Clean up and truncate
            c = line.strip('`').strip()
            if 2 <= len(c) <= 120:
                claims.append(c)
        if len(claims) >= 8:
            break
    return claims


def _dir_criteria(dirs: list, packs: list) -> tuple[dict, dict]:
    """Build JEV choice criteria for a list of destination directories.

    Criteria text carries the pack's existing skill names — JEV only sees text, and a bare
    directory name gives it almost no signal (measured: with bare names it answered `new` even
    when `AgentSkill-UsingPyMOL` was on the list for a PyMOL skill).
    """
    pack_skills = {p["repo"]: (p.get("skills") or []) for p in packs}
    criteria, cmap = {}, {}
    for d in dirs[:250]:  # JEV single-question option limit ~255
        sk = pack_skills.get(d) or []
        criteria[d] = f"{d}（已有技能：{', '.join(sk[:6])}）" if sk else f"{d}（目录存在但还没有技能）"
        cmap[d] = ("existing", d)
    criteria["new"] = "以上目录都不合适，需要新建一个包"
    cmap["new"] = ("new", None)
    return criteria, cmap


def judge_jev(items: list[dict], answer_space: dict, *, key: str,
              model: str, base_url: str, timeout: int) -> tuple[list[dict], str]:
    """JEV backend: use JEV's native choice Q&A to determine axis + destination per item.

    JEV is a decision model (not a generation model): it returns structured choices, not text.
    Two rounds, because one shared `state` cannot hold N different bodies cleanly:
    - Round 1 (axis): one batched request, light state = id/skill/description per item.
    - Round 2 (destination): one request PER ITEM, state = that item's full body — this is what
      makes JEV actually read the content (v2 documented the same two-round shape).

    JEV cannot generate text, so `evidence`/`claims` are extracted mechanically from the body,
    and a `new` destination gets the skill name as topic with a placeholder boundary (flagged in `why`).
    """
    tool_dirs = answer_space.get("tool_dirs") or []
    topic_dirs = answer_space.get("topic_dirs") or []
    packs = answer_space.get("packs") or []

    # ── Round 1: axis (batched, light state) ─────────────────────────────────────
    # A name + description alone flip-flops on the axis question; a short body head stabilises it
    # while staying cheap (measured on 3 real candidates: axis choice went run-to-run unstable
    # without it).
    def _axis_state(it: dict) -> str:
        head = " ".join((it.get("body") or "").split())[:200]
        return f"{it['id']} · {it['skill']} — {it.get('description') or '(无描述)'}｜正文开头：{head}"

    light_state = "\n".join(_axis_state(it) for it in items)
    axis_q = {
        it["id"]: {
            "type": "choice",
            "instructions": "这条技能的**主语**是「某个软件/工具」还是「一类任务/学科/项目」？",
            "criteria": {
                "tool": "主语是某个具体软件/工具（PyMOL / Dolt / Obsidian / ATSAS …）",
                "topic": "主语是一类任务、学科或项目（做 SAXS 分析、整理笔记库、跑批处理 …）",
            },
        }
        for it in items
    }
    axis_answers, err = _jev_call_questions(axis_q, light_state, model, key, base_url, timeout)
    if err:
        return [], f"JEV axis round: {err}"

    # ── Round 2: destination (per item, full body in state) ──────────────────────
    verdicts = []
    for it in items:
        aa = axis_answers.get(it["id"]) or {}
        axis_choice = aa.get("choice") or ""
        # JEV's `confidence` is a *margin* (p_chosen - p_second), not a probability — use the
        # per-option probability for the chosen answer instead (measured: margin 0.14 vs p 0.57).
        axis_conf = float((aa.get("probabilities") or {}).get(axis_choice, aa.get("confidence") or 0))
        body = it.get("body") or ""
        evidence = _extract_evidence(body)
        claims = _extract_claims(body)

        if not axis_choice:
            verdicts.append({
                "id": it["id"],
                "dest": {"kind": "new", "axis": "topic", "topic": it["skill"], "personal": False,
                         "boundary": "（JEV 未判出轴，待 S3/S4 复核）"},
                "why": "JEV 第一轮未给出轴；落点待复核",
                "evidence": evidence, "confidence": 0.0, "claims": claims})
            continue

        dirs = tool_dirs if axis_choice == "tool" else topic_dirs
        criteria, cmap = _dir_criteria(dirs, packs)
        state = (f"技能名：{it['skill']}\n描述：{it.get('description') or ''}\n"
                 f"--- SKILL.md 全文 ---\n{body[:6000]}")
        dest_q = {
            it["id"]: {
                "type": "choice",
                "instructions": "读完整篇正文后：这条技能该归到哪个目录？没有合适的就答 new。",
                "criteria": criteria,
            }
        }
        dest_answers, err = _jev_call_questions(dest_q, state, model, key, base_url, timeout)
        if err:
            verdicts.append({
                "id": it["id"],
                "dest": {"kind": "new", "axis": axis_choice, "topic": it["skill"], "personal": False,
                         "boundary": "（JEV 落点轮失败，待复核）"},
                "why": f"JEV dest round error: {err}",
                "evidence": evidence, "confidence": 0.0, "claims": claims})
            continue

        da = dest_answers.get(it["id"]) or {}
        dest_choice = da.get("choice") or ""
        dest_conf = float((da.get("probabilities") or {}).get(dest_choice, da.get("confidence") or 0))

        if not dest_choice:
            dest = {"kind": "new", "axis": axis_choice, "topic": it["skill"], "personal": False,
                    "boundary": "（JEV 未给出落点，待 S3/S4 复核）"}
            why = f"JEV axis={axis_choice}({axis_conf:.2f}) 但落点轮无 choice"
        elif cmap.get(dest_choice, ("existing", dest_choice))[0] == "new":
            # JEV can't produce free text: use the skill name as the new topic, flag the missing boundary.
            dest = {"kind": "new", "axis": axis_choice, "topic": it["skill"], "personal": False,
                    "boundary": "（JEV 不生成文本；boundary 待 S3/S4 补）"}
            why = (f"JEV: axis={axis_choice}({axis_conf:.2f}) dest=new({dest_conf:.2f})；"
                   f"JEV 不能给新包取名/边界，topic 暂用技能名")
        else:
            repo = cmap[dest_choice][1]
            dest = {"kind": "existing", "repo": repo, "dest_skill": "__new__", "action": "move"}
            why = f"JEV: axis={axis_choice}({axis_conf:.2f}) dest={dest_choice}({dest_conf:.2f})"

        verdicts.append({
            "id": it["id"], "dest": dest, "why": why,
            "evidence": evidence, "confidence": round(axis_conf * dest_conf, 2), "claims": claims})

    return verdicts, ""


# ─── configuration ───────────────────────────────────────────────────────────────


def load_config() -> tuple[list[dict], dict]:
    """Read dream-judge.json -> (backends, chunk_config).

    backends is ordered as in the file. Each has name/type/model/base_url/key_env.
    chunk_config has chunk_bytes/workers.
    """
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"[judge] 找不到判官配置 {CONFIG_PATH}\n"
            f"        复制仓库里的 skills/dream/dream-judge.example.json 到该路径并按需改后端列表。")
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"[judge] 判官配置不是合法 JSON：{CONFIG_PATH}（{e}）")
    backends = config.get("backends", [])
    if not backends:
        raise SystemExit(f"[judge] 判官配置里没有 backends：{CONFIG_PATH}")
    chunk_config = {
        "chunk_bytes": config.get("chunk_bytes", 140000),
        "workers": config.get("workers", 2),
        "key_file": config.get("key_file") or str(pathlib.Path.home() / ".hermes" / ".env"),
    }
    return backends, chunk_config


def judge_chunk(items: list[dict], answer_space: dict, backend: dict, *,
                key_file: pathlib.Path, timeout: int) -> tuple[list[dict], str]:
    """Call a backend's judge_chunk function. -> (verdicts, error)."""
    btype = backend.get("type", "openai")
    model = backend.get("model", "")
    base_url = backend.get("base_url", "")
    key = _read_key(backend.get("key_env", ""), key_file)

    if not key:
        return [], f"no key for {backend.get('name')} ({backend.get('key_env')})"

    if btype == "jev":
        return judge_jev(items, answer_space, key=key, model=model,
                         base_url=base_url, timeout=timeout)
    else:
        return judge_openai(items, answer_space, key=key, model=model,
                            base_url=base_url, timeout=timeout)


def judge_chunk_with_fallback(items: list[dict], answer_space: dict,
                              backends: list[dict], *, key_file: pathlib.Path,
                              timeout: int) -> tuple[list[dict], str, str]:
    """Try backends in order. -> (verdicts, source_name, error).

    First backend that returns a non-empty verdicts list wins.
    """
    last_err = ""
    for b in backends:
        verdicts, err = judge_chunk(items, answer_space, b, key_file=key_file, timeout=timeout)
        if verdicts:
            return verdicts, b.get("name", "?"), ""
        last_err = f"{b.get('name')}: {err}"
    return [], "", last_err or "no backends configured"


# ─── self-test ──────────────────────────────────────────────────────────────────

def _self_test() -> int:
    """Offline checks: config shape, JSONL parsing, backend dispatch, evidence/claims extraction.

    No network calls. Run with `python3 -B judge_backends.py --self-test`.
    """
    import tempfile
    fails = []

    # 1. JSONL parser: valid lines, code fences, bad lines, non-JSON noise
    got = _parse_jsonl('```jsonl\n{"id":"P1","dest":{"kind":"new"}}\nnot json\n{"id":"P2"}\n```')
    if [v["id"] for v in got] != ["P1", "P2"]:
        fails.append(f"parse_jsonl: got {[v.get('id') for v in got]}")

    # 2. evidence extraction picks first substantial body line, skips headings
    ev = _extract_evidence("# Title\n\n---\n\n这是一条足够长的正文证据句子，用来验证抽取逻辑。")
    if "这是一条足够长" not in ev:
        fails.append(f"extract_evidence: got {ev!r}")

    # 3. claims extraction finds flags/paths, caps at 8
    body = "\n".join([f"line {i} uses --flag{i}" for i in range(12)])
    cl = _extract_claims(body)
    if len(cl) != 8 or "--flag0" not in cl[0]:
        fails.append(f"extract_claims: {cl}")

    # 4. dispatch: type=jev routes to judge_jev, type=openai routes to judge_openai (no network)
    seen = {}
    orig_jev, orig_oai = judge_jev, judge_openai
    globals()["judge_jev"] = lambda *a, **k: (seen.setdefault("jev", True) or [], "")
    globals()["judge_openai"] = lambda *a, **k: (seen.setdefault("openai", True) or [], "")
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("K=x\n")
            kf = pathlib.Path(f.name)
        judge_chunk([], {}, {"name": "j", "type": "jev", "key_env": "K"}, key_file=kf, timeout=1)
        judge_chunk([], {}, {"name": "o", "type": "openai", "key_env": "K"}, key_file=kf, timeout=1)
        kf.unlink()
    finally:
        globals()["judge_jev"], globals()["judge_openai"] = orig_jev, orig_oai
    if not (seen.get("jev") and seen.get("openai")):
        fails.append(f"dispatch: {seen}")

    # 5. missing key -> error (not a crash)
    v, err = judge_chunk([], {}, {"name": "x", "type": "openai", "key_env": "NOPE_MISSING"},
                         key_file=pathlib.Path("/nonexistent.env"), timeout=1)
    if v or "no key" not in err:
        fails.append(f"missing key: verdicts={v} err={err!r}")

    # 6. config file present (this machine)
    if CONFIG_PATH.exists():
        try:
            backends, ccfg = load_config()
            if not backends or not all(k in ccfg for k in ("chunk_bytes", "workers", "key_file")):
                fails.append(f"load_config: backends={backends} ccfg={ccfg}")
        except SystemExit as e:
            fails.append(f"load_config raised: {e}")

    if fails:
        for f in fails:
            print(f"[judge_backends] ✗ {f}")
        return 1
    print("[judge_backends] self-test OK")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)
