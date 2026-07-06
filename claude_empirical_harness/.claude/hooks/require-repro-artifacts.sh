#!/usr/bin/env bash
# Stop hook -- when analysis outputs exist, require a reproducibility record
# (and, if any BigQuery cost was incurred, a reported running total) before the
# agent is allowed to finish.
# Exit 2 => block stop; stderr is fed back so Claude continues and complies.
# Exit 0 => allow stop.
set -euo pipefail

input="$(cat)"

# Avoid infinite loops: if we already blocked once this turn, let it stop.
active="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    print(json.load(sys.stdin).get("stop_hook_active", False))
except Exception:
    print(False)
')"
[ "$active" = "True" ] && exit 0

proj="${CLAUDE_PROJECT_DIR:-$PWD}"
out="$proj/outputs"

# No outputs directory => nothing to reproduce.
[ -d "$out" ] || exit 0

# Count result artifacts.
result_count="$(find "$out" -type f \
  \( -name '*.png' -o -name '*.pdf' -o -name '*.svg' -o -name '*.html' \
     -o -name '*.csv' -o -name '*.parquet' -o -name '*.json' -o -name '*.jsonl' \
     -o -name '*.pkl' -o -name '*.joblib' -o -name '*.pt' \) \
  2>/dev/null | wc -l | tr -d ' ')"

[ "$result_count" = "0" ] && exit 0

repro="$out/REPRODUCIBILITY.md"
ledger="$out/COST_LEDGER.jsonl"
missing=()

# Requirement 1: a substantive reproducibility record.
if [ ! -s "$repro" ] || [ "$(wc -w < "$repro" 2>/dev/null | tr -d ' ')" -lt 30 ]; then
  missing+=("- outputs/REPRODUCIBILITY.md is missing or too sparse: add seed(s), package versions, data provenance, and the commands to regenerate every artifact.")
fi

# Requirement 2: if cost was incurred, the running total must be reported.
if [ -s "$ledger" ]; then
  total="$(LEDGER="$ledger" python3 -c '
import json, os
t = 0.0
for line in open(os.environ["LEDGER"]):
    line = line.strip()
    if not line:
        continue
    try:
        t += float(json.loads(line).get("est_usd", 0.0))
    except Exception:
        pass
print(f"{t:.2f}")
')"
  if ! { [ -f "$repro" ] && grep -q "Estimated query cost" "$repro"; }; then
    missing+=("- outputs/REPRODUCIBILITY.md must report query cost. Add a line: 'Estimated query cost (BigQuery scan): \$$total (see outputs/COST_LEDGER.jsonl)'.")
  fi
fi

if [ "${#missing[@]}" -gt 0 ]; then
  {
    echo "BLOCKED: outputs/ has $result_count result file(s) but reproducibility requirements are unmet:"
    printf '%s\n' "${missing[@]}"
    echo "Also confirm no reported number lacks a backing file in outputs/."
  } >&2
  exit 2
fi

exit 0
