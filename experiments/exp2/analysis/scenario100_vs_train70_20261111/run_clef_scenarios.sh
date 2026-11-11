#!/usr/bin/env bash
# Run both scenarios sequentially; derive full/train/test from the same responses.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != "--dry-run" ) ]]; then
  echo "Usage: bash run_clef_scenarios.sh 9b|27b [--dry-run]" >&2
  exit 2
fi
case "$1" in
  9b) EXP2_MODEL="Cloudflare/clef-flash"; EXP2_TAG="clef9_q4" ;;
  27b) EXP2_MODEL="Cloudflare/clef"; EXP2_TAG="clef27_q4" ;;
  *) echo "Expected 9b or 27b" >&2; exit 2 ;;
esac

EXP2_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd -- "$EXP2_REPO_ROOT"
EXP2_PYTHON="$EXP2_REPO_ROOT/.venv-clef/bin/python"
EXP2_BATCH_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EXP2_EXTRA=()
EXP2_DRY_ROOT=""
if [[ "${2:-}" == "--dry-run" ]]; then
  EXP2_EXTRA=(--dry-run --env-file /dev/null)
  EXP2_DRY_ROOT="$(mktemp -d /tmp/exp2-clef-dryrun.XXXXXX)"
fi

for EXP2_VARIANT in 100 70; do
  if [[ "$EXP2_VARIANT" == 100 ]]; then
    EXP2_PROMPT="exp2/prompts/routing_scenarios_ru_exp2.txt"
    EXP2_EVALUATION="retrospective"
  else
    EXP2_PROMPT="exp2/prompts/routing_train70_ru_exp2.txt"
    EXP2_EVALUATION="train70"
  fi
  EXP2_RUNS="experiments/exp2/${EXP2_TAG}_scenarios${EXP2_VARIANT}_${EXP2_BATCH_ID}"
  EXP2_REPORT="experiments/exp2/analysis/${EXP2_TAG}_scenarios${EXP2_VARIANT}_${EXP2_BATCH_ID}"
  if [[ -n "$EXP2_DRY_ROOT" ]]; then
    EXP2_RUNS="$EXP2_DRY_ROOT/scenarios${EXP2_VARIANT}"
  fi
  "$EXP2_PYTHON" exp2/run_exp2.py \
    --backend hf --mode local --adapter clef --model "$EXP2_MODEL" \
    --system-prompt "$EXP2_PROMPT" \
    --bucket data/subagent_routing_exp2/splits/stratified70_seed42/bucket_full.csv \
    --device cuda --dtype bfloat16 --clef-quantization 4bit \
    --shortlist-k 5 --max-input-tokens 32768 --fail-fast \
    --output-dir "$EXP2_RUNS" "${EXP2_EXTRA[@]}"
  if [[ -z "$EXP2_DRY_ROOT" ]]; then
    "$EXP2_PYTHON" exp2/evaluate_holdout_exp2.py \
      --runs-root "$EXP2_RUNS" --models "$EXP2_MODEL" \
      --prompt "$EXP2_PROMPT" --prompt-evaluation "$EXP2_EVALUATION" \
      --output-dir "$EXP2_REPORT"
  fi
done
