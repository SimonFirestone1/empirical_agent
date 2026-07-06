#!/usr/bin/env bash
# PreToolUse hook — protect immutable data directories from writes/edits.
# Registered for matcher: Write|Edit|NotebookEdit
# Exit 2 => deny the tool call (stderr is shown to Claude). Exit 0 => allow.
set -euo pipefail

# Directories whose contents must never be modified by the agent.
# Raw downloads and external reference data are immutable; derived data
# belongs in data/processed/ and analysis artifacts in outputs/.
PROTECTED_PREFIXES=("data/raw" "data/external")

input="$(cat)"

file_path="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
ti = d.get("tool_input", {}) or {}
print(ti.get("file_path", "") or ti.get("notebook_path", "") or "")
')"

[ -z "$file_path" ] && exit 0

proj="${CLAUDE_PROJECT_DIR:-$PWD}"
rel="${file_path#"$proj"/}"

for p in "${PROTECTED_PREFIXES[@]}"; do
  case "$rel" in
    "$p"/*|"$p")
      echo "BLOCKED: '$rel' is under the immutable directory '$p/'. Raw and external data must not be modified. Write derived data to data/processed/ and analysis artifacts to outputs/ instead." >&2
      exit 2
      ;;
  esac
done

exit 0
