#!/usr/bin/env bash
# run_full_eval.sh — Run a complete eval from scratch in a clean Claude session.
#
# Usage:
#   bash evals/run_full_eval.sh --task evals/tasks/scf_debt_age_income.yaml [--runs 1] [--model sonnet]
#
# This script:
#   1. Clears any previous results for the task
#   2. Runs N trials in each condition (with/without skill) sequentially
#   3. Scores all trials (auto + LLM-as-judge)
#   4. Prints the scorecard
#
# Each step uses a fresh `claude -p` call so no context accumulates across trials.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

N_RUNS=1
TASK_FILE=""
MODEL=""
MAX_BUDGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task) TASK_FILE="$2"; shift 2 ;;
    --runs) N_RUNS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --max-budget) MAX_BUDGET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$TASK_FILE" ]]; then
  echo "Usage: $0 --task <task.yaml> [--runs N] [--model MODEL] [--max-budget USD]"
  exit 1
fi

[[ "$TASK_FILE" != /* ]] && TASK_FILE="$(pwd)/$TASK_FILE"

TASK_NAME="$(python3 -c "import yaml, sys; print(yaml.safe_load(open(sys.argv[1]))['name'])" "$TASK_FILE")"
RESULTS_DIR="$SCRIPT_DIR/results/$TASK_NAME"

# ── Step 1: Clear previous results ──────────────────────────────────────────
echo "=== Clearing previous results for $TASK_NAME ==="
rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

# ── Step 2: Run trials ──────────────────────────────────────────────────────
EXTRA_ARGS=()
[[ -n "$MODEL" ]] && EXTRA_ARGS+=(--model "$MODEL")
[[ -n "$MAX_BUDGET" ]] && EXTRA_ARGS+=(--max-budget "$MAX_BUDGET")

echo ""
echo "=== Running $N_RUNS trial(s) per condition (interleaved) ==="
echo "    Task:   $TASK_NAME"
echo "    Model:  ${MODEL:-default}"
echo "    Budget: ${MAX_BUDGET:-unlimited}"
echo ""

# Interleave conditions (without/with alternating per run) to avoid an
# order confound between conditions.
echo "── Running $N_RUNS interleaved trial(s) per condition ────────────────"
bash "$SCRIPT_DIR/run_eval.sh" \
  --task "$TASK_FILE" \
  --runs "$N_RUNS" \
  --condition interleaved \
  "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
echo ""

# ── Step 3: Score ───────────────────────────────────────────────────────────
echo "=== Scoring with LLM-as-judge ==="
python3 "$SCRIPT_DIR/score_trials.py" --task "$TASK_FILE" --auto-judge

echo ""
echo "=== Done. Results in $RESULTS_DIR ==="
