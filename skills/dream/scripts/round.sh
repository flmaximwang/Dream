#!/usr/bin/env bash
# dream v3 · 一轮的编排器：按 S1→S8 顺序跑，逐阶段记时间，产出 stages.tsv（口径④「wall ≤ 20 min」的证据形状）。
#
#   bash skills/dream/scripts/round.sh --test              # 开发/测试轮：写到 AgentSkill-copy、--no-push、不退役
#   bash skills/dream/scripts/round.sh --profile default   # 正式轮：写真包、推送、退役（默认就是正式轮）
#   bash skills/dream/scripts/round.sh --plan              # 只打印这一轮会跑什么（不执行）
#
# 这一轮里**要模型做**的两件事不在本脚本内：S2 的判官（judge.py 直调，脚本负责调它）与 S5 里
# merge/strengthen 行的正文改写（跑完 apply 后打印清单，由主 agent 派 writer）。脚本只编排机械阶段。
set -uo pipefail

PY=$(command -v python3)
[ -x "$HOME/.hermes/hermes-agent/venv/bin/python3" ] && PY="$HOME/.hermes/hermes-agent/venv/bin/python3"
SKILL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# scan 要一个兄弟技能里的解析器；在 worktree 里开发时它不在旁边，就退回 profile 里装好的那份。
PARSER="$SKILL_DIR/../load-external-skill-index/scripts/skill-index.py"
# 两种布局都认：skills/<类目>/<技能>/ 与 skills/<技能>/（profile 被扁平化后会长成后者）
# 两种布局都认，且 profile / Skills-Manager 中央库 / external_dirs 包内都可能是唯一副本
[ -f "$PARSER" ] || PARSER=$(find "$HOME/.hermes/skills" "$HOME/.skills-manager/skills" "$HOME/Documents/AgentSkill" \
                                 -maxdepth 6 -name skill-index.py -path '*load-external-skill-index*' 2>/dev/null | head -1)
if [ ! -f "$PARSER" ]; then echo "[round] 找不到 load-external-skill-index 的 skill-index.py（scan 的兄弟依赖）" >&2; exit 2; fi

profile=default
mode=formal
sm=0
plan_only=0
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test) mode=test ;;
    --formal) mode=formal ;;
    --profile) profile="$2"; shift ;;
    --out) out="$2"; shift ;;
    --skills-manager) sm=1 ;;
    --plan) plan_only=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$out" ] || out="$HOME/.hermes/cache/scratch/sweep-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$out"
stages="$out/stages.tsv"

# 落点池：正式轮来自 .env 的 DREAM_EXTERNAL_DIRS（一份本地 JSON，脚本 external-dirs.py 管它）；
# 测试轮仍走 AgentSkill-copy 那份一次性副本。路径不写死在脚本里。
ext="${DREAM_EXTERNAL_DIRS:-}"
if [ -z "$ext" ]; then
  ext=$(grep -m1 '^DREAM_EXTERNAL_DIRS=' "$HOME/.hermes/.env" 2>/dev/null | cut -d= -f2-)
fi
ext="${ext/#\~/$HOME}"
[ -f "$ext" ] || ext=""

if [ "$mode" = test ]; then
  packs="$HOME/Documents/AgentSkill-copy"
  loc_arg=(--packs-root "$packs")
  apply_extra=(--no-push)
  if [ ! -d "$packs" ] && [ $plan_only -eq 0 ]; then
    echo "[round] 测试轮要先建副本并摘掉远端："
    echo "        cp -Rc ~/Documents/AgentSkill ~/Documents/AgentSkill-copy"
    echo "        for g in ~/Documents/AgentSkill-copy/*/.git; do git -C \$(dirname \$g) remote remove origin; done"
    exit 2
  fi
else
  packs=""
  if [ -z "$ext" ]; then
    echo "[round] 正式轮要落点池：把 DREAM_EXTERNAL_DIRS=<本地 JSON> 写进 ~/.hermes/.env（首次生成：\n" \
         "        $PY -B $SKILL_DIR/scripts/external-dirs.py init）" >&2
    exit 2
  fi
  loc_arg=(--external-dirs "$ext")
  apply_extra=()
fi

echo -e "stage\tstart\tend\tsec\texit\tnums" > "$stages"
run() {          # run <阶段名> <命令...>
  local name="$1"; shift
  local t0 t1 rc nums
  t0=$(date +%s); local h0; h0=$(date +%H:%M:%S)
  echo "[round] ▶ $name @ $h0 :: $*"
  "$@" > "$out/$(echo "$name" | tr -d ' ').log" 2>&1; rc=$?
  t1=$(date +%s); local h1; h1=$(date +%H:%M:%S)
  nums=$(grep -oE '[0-9]+/[0-9]+ (条判定|verdicts)|verdicts=[0-9]+/[0-9]+|candidates=[0-9]+|覆盖率 100%|写入 [0-9]+ 行|PASS [0-9]+ · FAIL [0-9]+' \
         "$out/$(echo "$name" | tr -d ' ').log" | tail -1)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$h0" "$h1" "$((t1-t0))" "$rc" "${nums:-—}" >> "$stages"
  tail -3 "$out/$(echo "$name" | tr -d ' ').log"
  if [ $rc -ne 0 ] && [ "$name" != "S3 交叉审计" ]; then
    echo "[round] ✗ $name 退出码 $rc —— 停下（别往下跑）"; summary; exit $rc
  fi
}
summary() {
  echo "[round] 逐阶段："
  column -t -s $'\t' "$stages" 2>/dev/null || cat "$stages"
  local total; total=$(awk -F'\t' 'NR>1{s+=$4}END{print s+0}' "$stages")
  local cached; cached=$(grep -c cached "$out"/*.log 2>/dev/null | awk -F: '{s+=$2}END{print s+0}')
  echo "[round] 机械阶段合计 ${total}s（预算 1200s）；含缓存命中 ${cached} 处（>0 时这一轮的时间不作数）"
  echo "[round] 仍要模型做：merge/strengthen 行的正文改写（按包派 writer，并发上限 10）："
  echo "        $PY -B $SKILL_DIR/scripts/apply.py taskbook --dir $out ${loc_arg[*]}${mode:+ }$( [ "$mode" = test ] && echo --test)"
  echo "        → 生成 $out/writers.md（每包一段可直接派的任务书）；回执要求见 references/dream-agent-roster.md"
}

if [ $plan_only -eq 1 ]; then
  cat <<EOF
[round] mode=$mode profile=$profile 落点池=$([ "$mode" = test ] && echo "$packs" || echo "$ext") out=$out
  S1 枚举      sweep-plan.py scan   --profile $profile --out $out ${loc_arg[*]}
  S2 判官      judge.py             --dir $out --chunk-bytes 140000 --workers 2
  S3 交叉审计  audit.py             --dir $out
  S4 决议      sweep-plan.py merge  --dir $out --profile $profile $([ "$sm" = 1 ] && echo --skills-manager)
  S5 写入      apply.py apply       --dir $out ${loc_arg[*]} ${apply_extra[*]:-（真推）}
  S6 验证      apply.py verify      --dir $out ${loc_arg[*]}
  $([ "$mode" = test ] && echo "  S7/S8        测试轮跳过（不 install、不 retire）" || echo "  S7 重装      apply.py install --dir $out
  S8 退役      apply.py retire  --dir $out --backup ~/.hermes/backups/dream-retire")
EOF
  exit 0
fi

run "S1 枚举"      "$PY" -B "$SKILL_DIR/scripts/sweep-plan.py" scan --profile "$profile" --out "$out" "${loc_arg[@]}" --parser-script "$PARSER"
run "S2 判官"      "$PY" -B "$SKILL_DIR/scripts/judge.py" --dir "$out" --chunk-bytes 140000 --workers 2
run "S3 交叉审计"  "$PY" -B "$SKILL_DIR/scripts/audit.py" --dir "$out"
run "S4 决议"      "$PY" -B "$SKILL_DIR/scripts/sweep-plan.py" merge --dir "$out" --profile "$profile" "$([ "$sm" = 1 ] && echo --skills-manager)"
run "S5 写入"      "$PY" -B "$SKILL_DIR/scripts/apply.py" apply --dir "$out" "${loc_arg[@]}" "${apply_extra[@]+"${apply_extra[@]}"}"
run "S6 验证"      "$PY" -B "$SKILL_DIR/scripts/apply.py" verify --dir "$out" "${loc_arg[@]}"
if [ "$mode" = formal ]; then
  run "S7 重装"    "$PY" -B "$SKILL_DIR/scripts/apply.py" install --dir "$out"
  run "S8 退役"    "$PY" -B "$SKILL_DIR/scripts/apply.py" retire --dir "$out" --backup "$HOME/.hermes/backups/dream-retire"
fi
summary
