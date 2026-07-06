#!/usr/bin/env bash
# run_eval.sh — Run an eval task N times in each condition (with/without skill).
#
# Usage:
#   ./evals/run_eval.sh --task <task.yaml> [--runs N] [--condition both|with|without|interleaved] [--keep-workspaces]
#                       [--docker] [--docker-build] [--docker-image NAME]
#
# Sandboxing:
#   --docker        run each trial inside a Docker container (no network, only
#                   the trial workspace mounted rw, ~/.claude mounted read-only)
#   --docker-build  build/rebuild the sandbox image before running (implies --docker)
#
# Each trial runs in an isolated temp directory. Results are collected under
# evals/results/<task_name>/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

N_RUNS=5
CONDITION="both"
TASK_FILE=""
MODEL=""
MAX_BUDGET=""
DEFAULT_BUDGET="20"
KEEP_WORKSPACES=0
USE_DOCKER=0
DOCKER_BUILD=0
DOCKER_IMAGE="claude-eval-harness"
CONDITION_SPEC="claude_md,skill,hooks,agents"
TRIAL_TIMEOUT=3600
PARALLEL=1
PROMPT_VARIANT="prompt"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task) TASK_FILE="$2"; shift 2 ;;
    --runs) N_RUNS="$2"; shift 2 ;;
    --condition) CONDITION="$2"; shift 2 ;;
    --condition-spec) CONDITION_SPEC="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --max-budget) MAX_BUDGET="$2"; shift 2 ;;
    --default-budget) DEFAULT_BUDGET="$2"; shift 2 ;;
    --trial-timeout) TRIAL_TIMEOUT="$2"; shift 2 ;;
    --parallel) PARALLEL="$2"; shift 2 ;;
    --prompt-variant) PROMPT_VARIANT="$2"; shift 2 ;;
    --keep-workspaces) KEEP_WORKSPACES=1; shift ;;
    --docker) USE_DOCKER=1; shift ;;
    --docker-build) USE_DOCKER=1; DOCKER_BUILD=1; shift ;;
    --docker-image) DOCKER_IMAGE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Validate condition-spec components
for comp in ${CONDITION_SPEC//,/ }; do
  case "$comp" in
    claude_md|skill|hooks|agents) ;;
    *) echo "ERROR: invalid --condition-spec component '$comp' (valid: claude_md,skill,hooks,agents)" >&2; exit 1 ;;
  esac
done

# Membership test for the condition spec
spec_has() {
  case ",$CONDITION_SPEC," in
    *,"$1",*) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "$PROMPT_VARIANT" != "prompt" && "$PROMPT_VARIANT" != "naturalistic" ]]; then
  echo "ERROR: --prompt-variant must be 'prompt' or 'naturalistic' (got '$PROMPT_VARIANT')" >&2
  exit 1
fi

if [[ -z "$TASK_FILE" ]]; then
  echo "Usage: $0 --task <task.yaml> [--runs N] [--condition both|with|without|interleaved] [--keep-workspaces]"
  echo ""
  echo "Available tasks:"
  for f in "$SCRIPT_DIR"/tasks/*.yaml; do
    name="$(python3 -c "import yaml, sys; print(yaml.safe_load(open(sys.argv[1]))['name'])" "$f" 2>/dev/null || basename "$f" .yaml)"
    echo "  $f  ($name)"
  done
  exit 1
fi

# Resolve relative paths
[[ "$TASK_FILE" != /* ]] && TASK_FILE="$(pwd)/$TASK_FILE"

# Extract task name and prompt from YAML
TASK_NAME="$(python3 -c "import yaml, sys; print(yaml.safe_load(open(sys.argv[1]))['name'])" "$TASK_FILE")"
if [[ "$PROMPT_VARIANT" == "naturalistic" ]]; then
  PROMPT_FIELD="prompt_naturalistic"
else
  PROMPT_FIELD="prompt"
fi
PROMPT="$(python3 -c "
import yaml, sys
task = yaml.safe_load(open(sys.argv[1]))
field = sys.argv[2]
if field not in task:
    sys.stderr.write(f'ERROR: task YAML has no \'{field}\' field (required by --prompt-variant). Available fields: {sorted(task)}\n')
    sys.exit(1)
print(task[field])
" "$TASK_FILE" "$PROMPT_FIELD")"

RESULTS_DIR="$SCRIPT_DIR/results/$TASK_NAME"
mkdir -p "$RESULTS_DIR"

# Copy the task config into results for the scorer
cp "$TASK_FILE" "$RESULTS_DIR/_task.yaml"

# Apply the default budget when no explicit budget is given (--default-budget
# overrides the default of 20).
if [[ -z "$MAX_BUDGET" ]]; then
  MAX_BUDGET="$DEFAULT_BUDGET"
fi

# Build optional flag arrays (proper quoting; no word-splitting)
MODEL_ARGS=()
BUDGET_ARGS=()
[[ -n "$MODEL" ]] && MODEL_ARGS=(--model "$MODEL")
[[ -n "$MAX_BUDGET" ]] && BUDGET_ARGS=(--max-budget-usd "$MAX_BUDGET")

# ── Trial timeout binary ─────────────────────────────────────────────────────
# GNU coreutils `timeout` (gtimeout via Homebrew on macOS). If neither exists,
# trials run without a timeout and a warning is printed.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
else
  echo "WARNING: no 'timeout'/'gtimeout' on PATH; trials will run without a timeout." >&2
fi

# ── Docker sandbox setup ─────────────────────────────────────────────────────
if [[ "$USE_DOCKER" -eq 1 ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: --docker requested but docker is not installed/on PATH." >&2
    exit 1
  fi
  if [[ "$DOCKER_BUILD" -eq 1 ]]; then
    echo "Building Docker image '$DOCKER_IMAGE' ..."
    docker build -t "$DOCKER_IMAGE" "$REPO_ROOT"
  elif ! docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
    echo "Docker image '$DOCKER_IMAGE' not found; building it (use --docker-build to force rebuilds) ..."
    docker build -t "$DOCKER_IMAGE" "$REPO_ROOT"
  fi
  # Auth: Claude Code may read credentials from ~/.claude (mounted read-only
  # into the container) and/or an API key env var. On macOS, OAuth credentials
  # live in the Keychain, which containers cannot reach — in that case
  # ANTHROPIC_API_KEY (or CLAUDE_API_KEY) must be set in the host environment.
  DOCKER_ENV_ARGS=()
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] && DOCKER_ENV_ARGS+=(-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")
  [[ -n "${CLAUDE_API_KEY:-}"    ]] && DOCKER_ENV_ARGS+=(-e "CLAUDE_API_KEY=$CLAUDE_API_KEY")
  if [[ ! -e "$HOME/.claude/.credentials.json" && ${#DOCKER_ENV_ARGS[@]} -eq 0 ]]; then
    echo "WARNING: no ~/.claude/.credentials.json and no ANTHROPIC_API_KEY/CLAUDE_API_KEY set." >&2
    echo "         Claude inside the container may fail to authenticate (macOS Keychain is unreachable)." >&2
  fi
fi

# ── Workspace cleanup ────────────────────────────────────────────────────────
WORKSPACES=()
cleanup_workspaces() {
  if [[ "$KEEP_WORKSPACES" -eq 1 ]]; then
    return
  fi
  for ws in "${WORKSPACES[@]+"${WORKSPACES[@]}"}"; do
    if [[ -d "$ws" ]]; then
      # Data copies are made read-only; restore write perms so rm succeeds.
      chmod -R u+w "$ws" 2>/dev/null || true
      rm -rf "$ws"
    fi
  done
}
trap cleanup_workspaces EXIT

# ── One-time data provisioning ──────────────────────────────────────────────
# If the task YAML declares a data_provision section (either econbench_benchmark
# or direct items), provision the data once and record the staged path so each
# trial gets a read-only copy.
DATA_PROVISION_DIR=""
DATA_WORKSPACE_LINK=""

read_data_provision() {
  local output
  output="$(python3 "$SCRIPT_DIR/provision_data.py" --task "$TASK_FILE" 2>&1)" || {
    echo "$output" >&2
    exit 1
  }
  echo "$output"

  DATA_PROVISION_DIR="$(echo "$output" | grep '^DATA_DIR=' | cut -d= -f2-)"
  DATA_WORKSPACE_LINK="$(echo "$output" | grep '^WORKSPACE_LINK=' | cut -d= -f2-)"

  if [[ -n "$DATA_PROVISION_DIR" ]]; then
    echo "Data staged at $DATA_PROVISION_DIR (each trial gets a read-only copy as $DATA_WORKSPACE_LINK)"
  fi
}

read_data_provision

# ── helper: run one trial ────────────────────────────────────────────────────
run_trial() {
  local cond="$1"   # "with" or "without"
  local run_id="$2" # integer

  local trial_id="${cond}_run${run_id}"
  local work_dir
  work_dir="$(mktemp -d "/tmp/eval_${TASK_NAME}_${trial_id}_XXXX")"
  WORKSPACES+=("$work_dir")
  local trial_out="$RESULTS_DIR/$trial_id"
  mkdir -p "$trial_out"

  echo "[$trial_id] Setting up workspace at $work_dir ..."

  # Scaffold the workspace
  mkdir -p "$work_dir"/{outputs,scratch,scripts}
  # Only create data/{raw,processed} if we're not copying provisioned data into data/
  if [[ -z "$DATA_PROVISION_DIR" || "$DATA_WORKSPACE_LINK" != "data" ]]; then
    mkdir -p "$work_dir"/{data/raw,data/processed}
  fi
  # Utility scripts (safe_merge.py, bq_run.py) are copied only in the "with"
  # condition: CLAUDE.md and the hooks reference/enforce them, so they are part
  # of the treatment. Giving them to the "without" condition would leak part of
  # the harness into the control and understate the treatment effect.
  if [[ "$cond" == "with" ]]; then
    cp "$REPO_ROOT/scripts/safe_merge.py" "$work_dir/scripts/" 2>/dev/null || true
    cp "$REPO_ROOT/scripts/bq_run.py"     "$work_dir/scripts/" 2>/dev/null || true
  fi

  # Copy pre-provisioned data into the workspace and make it read-only so a
  # trial cannot mutate the shared source data.
  if [[ -n "$DATA_PROVISION_DIR" ]]; then
    cp -r "$DATA_PROVISION_DIR" "$work_dir/$DATA_WORKSPACE_LINK"
    chmod -R a-w "$work_dir/$DATA_WORKSPACE_LINK"
  fi

  if [[ "$cond" == "with" ]]; then
    # Copy the harness components selected by --condition-spec (ablation
    # support). Default spec is all four components — the full harness.
    spec_has "claude_md" && cp "$REPO_ROOT/CLAUDE.md" "$work_dir/"
    mkdir -p "$work_dir/.claude"
    spec_has "skill"  && [[ -d "$REPO_ROOT/.claude/skills" ]] && cp -r "$REPO_ROOT/.claude/skills" "$work_dir/.claude/skills"
    spec_has "hooks"  && [[ -d "$REPO_ROOT/.claude/hooks"  ]] && cp -r "$REPO_ROOT/.claude/hooks"  "$work_dir/.claude/hooks"
    spec_has "agents" && [[ -d "$REPO_ROOT/.claude/agents" ]] && cp -r "$REPO_ROOT/.claude/agents" "$work_dir/.claude/agents"
    # Always include settings.json (minus settings.local.json) when any
    # component is selected — hooks/permissions config lives there.
    if [[ -f "$REPO_ROOT/.claude/settings.json" ]]; then
      cp "$REPO_ROOT/.claude/settings.json" "$work_dir/.claude/settings.json"
    fi
    rm -f "$work_dir/.claude/settings.local.json"
  fi
  # "without" condition: no CLAUDE.md, no .claude/ — bare Claude Code

  # Initialize a git repo so Claude Code can operate
  (cd "$work_dir" && git init -q && git add -A && git commit -q -m "init" --allow-empty)

  echo "[$trial_id] Launching Claude (condition=$cond) ..."
  local start_ts
  start_ts="$(date +%s)"

  # Isolation strategy:
  # - Fresh temp dir as cwd → no project-level ~/.claude/projects/ match
  # - "with" condition: workspace has .claude/ (hooks, skills) + CLAUDE.md
  # - "without" condition: workspace has no .claude/ or CLAUDE.md — vanilla
  # - User-level ~/.claude/ stays intact for auth; its settings (model,
  #   effort, permissions) apply equally to both conditions, so they are
  #   not a confounding factor. No project-scoped memory exists for the
  #   temp dir path.
  local transcript="$trial_out/transcript.txt"
  local exit_code=0
  # Wrap the trial in a timeout (--trial-timeout, default 3600s). GNU timeout
  # exits 124 when the deadline is hit.
  local timeout_cmd=()
  [[ -n "$TIMEOUT_BIN" ]] && timeout_cmd=("$TIMEOUT_BIN" "$TRIAL_TIMEOUT")
  if [[ "$USE_DOCKER" -eq 1 ]]; then
    # Sandboxed trial:
    # - only the trial workspace is mounted (rw) as /workspace
    # - ~/.claude is mounted read-only for auth/config
    # - no network (--network none): all data is pre-staged in the workspace
    # - API key env vars (if set on the host) are passed through
    local helper_args=(--prompt "$PROMPT" --effort high --output-format stream-json)
    [[ -n "$MODEL" ]]      && helper_args+=(--model "$MODEL")
    [[ -n "$MAX_BUDGET" ]] && helper_args+=(--max-budget-usd "$MAX_BUDGET")
    "${timeout_cmd[@]+"${timeout_cmd[@]}"}" docker run --rm \
      --network none \
      -v "$work_dir":/workspace \
      -v "$HOME/.claude":/home/trialuser/.claude:ro \
      "${DOCKER_ENV_ARGS[@]+"${DOCKER_ENV_ARGS[@]}"}" \
      -w /workspace \
      "$DOCKER_IMAGE" \
      docker_run_trial.sh "${helper_args[@]}" \
      > "$transcript" 2>&1 || exit_code=$?
  else
    (
      cd "$work_dir"
      "${timeout_cmd[@]+"${timeout_cmd[@]}"}" claude -p "$PROMPT" \
        --dangerously-skip-permissions \
        --effort high \
        "${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}" \
        "${BUDGET_ARGS[@]+"${BUDGET_ARGS[@]}"}" \
        --output-format stream-json \
        --verbose \
        2>&1
    ) > "$transcript" || exit_code=$?
  fi

  local end_ts
  end_ts="$(date +%s)"
  local elapsed=$(( end_ts - start_ts ))

  local timed_out=false
  if [[ -n "$TIMEOUT_BIN" && "$exit_code" -eq 124 ]]; then
    timed_out=true
    echo "[$trial_id] WARNING: trial timed out after ${TRIAL_TIMEOUT}s."
  fi

  echo "[$trial_id] Completed in ${elapsed}s (exit code: $exit_code)."
  if [[ "$exit_code" -ne 0 ]]; then
    echo "[$trial_id] WARNING: claude exited non-zero ($exit_code)."
  fi

  # Extract the model name from the stream-json transcript (init/system message)
  local trial_model
  trial_model="$(python3 -c "
import json, sys
model = ''
try:
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get('type') in ('system', 'init') and msg.get('model'):
                model = msg['model']
                break
except OSError:
    pass
print(model)
" "$transcript" 2>/dev/null || true)"

  # Collect artifacts — use trailing slash to copy contents, not the dir itself
  mkdir -p "$trial_out/outputs"
  cp -r "$work_dir/outputs/." "$trial_out/outputs/" 2>/dev/null || true

  # Collect analysis code written at the workspace root
  mkdir -p "$trial_out/code"
  find "$work_dir" -maxdepth 1 -name "*.py" -exec cp {} "$trial_out/code/" \;

  # Snapshot what files were created
  find "$work_dir" -type f -not -path '*/.git/*' | sort > "$trial_out/file_manifest.txt"

  # Record metadata (workspace path recorded before any cleanup)
  cat > "$trial_out/meta.json" <<METAEOF
{
  "trial_id": "$trial_id",
  "task": "$TASK_NAME",
  "condition": "$cond",
  "run": $run_id,
  "elapsed_seconds": $elapsed,
  "exit_code": $exit_code,
  "work_dir": "$work_dir",
  "sandboxed": $([[ "$USE_DOCKER" -eq 1 ]] && echo true || echo false),
  "workspace_kept": $([[ "$KEEP_WORKSPACES" -eq 1 ]] && echo true || echo false),
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
METAEOF

  # Generate HTML report
  echo "[$trial_id] Generating report ..."
  python3 "$SCRIPT_DIR/generate_report.py" --trial-dir "$trial_out" || echo "[$trial_id] Report generation failed (non-fatal)"

  echo "[$trial_id] Artifacts saved to $trial_out"
}

# ── helper: remove stale trial dirs for a condition ─────────────────────────
clear_stale_trials() {
  local cond="$1"
  local d
  for d in "$RESULTS_DIR/${cond}_run"*; do
    [[ -d "$d" ]] && rm -rf "$d"
  done
}

# ── main ─────────────────────────────────────────────────────────────────────
echo "=== Eval: $TASK_NAME | $N_RUNS run(s) per condition, condition=$CONDITION ==="
echo "Results → $RESULTS_DIR"
echo ""

if [[ "$CONDITION" == "interleaved" ]]; then
  clear_stale_trials "without"
  clear_stale_trials "with"
  for (( i=1; i<=N_RUNS; i++ )); do
    run_trial "without" "$i"
    echo ""
    run_trial "with" "$i"
    echo ""
  done
else
  conditions=()
  [[ "$CONDITION" == "both" || "$CONDITION" == "with" ]]    && conditions+=("with")
  [[ "$CONDITION" == "both" || "$CONDITION" == "without" ]] && conditions+=("without")

  for cond in "${conditions[@]}"; do
    clear_stale_trials "$cond"
  done

  for cond in "${conditions[@]}"; do
    for (( i=1; i<=N_RUNS; i++ )); do
      run_trial "$cond" "$i"
      echo ""
    done
  done
fi

echo "=== All trials complete. Score with: python3 evals/score_trials.py --task $TASK_FILE ==="
