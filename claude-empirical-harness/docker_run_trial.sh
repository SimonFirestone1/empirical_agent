#!/usr/bin/env bash
# docker_run_trial.sh — runs inside the sandbox container for one eval trial.
#
# Usage (invoked by evals/run_eval.sh via `docker run`):
#   docker_run_trial.sh --prompt <prompt> [--model <model>] [--max-budget-usd <usd>] \
#                       [--effort <level>] [--output-format <fmt>]
#
# Assumes cwd is /workspace (the mounted trial workspace) and that auth is
# available via a read-only ~/.claude mount and/or ANTHROPIC_API_KEY /
# CLAUDE_API_KEY env vars passed through by the host.
set -euo pipefail

PROMPT=""
EFFORT="high"
OUTPUT_FORMAT="stream-json"
MODEL_ARGS=()
BUDGET_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT="$2"; shift 2 ;;
    --model) MODEL_ARGS=(--model "$2"); shift 2 ;;
    --max-budget-usd) BUDGET_ARGS=(--max-budget-usd "$2"); shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --output-format) OUTPUT_FORMAT="$2"; shift 2 ;;
    *) echo "docker_run_trial.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PROMPT" ]]; then
  echo "docker_run_trial.sh: --prompt is required" >&2
  exit 2
fi

exec claude -p "$PROMPT" \
  --dangerously-skip-permissions \
  --effort "$EFFORT" \
  "${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}" \
  "${BUDGET_ARGS[@]+"${BUDGET_ARGS[@]}"}" \
  --output-format "$OUTPUT_FORMAT" \
  --verbose \
  2>&1
