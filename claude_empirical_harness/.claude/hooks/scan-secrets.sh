#!/usr/bin/env bash
# PreToolUse hook — block writing secrets into files or echoing them in shell.
# Registered for matcher: Write|Edit|NotebookEdit|Bash
# Exit 2 => deny the tool call. Exit 0 => allow.
set -euo pipefail

input="$(cat)"

payload="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
ti = d.get("tool_input", {}) or {}
parts = [ti.get("content", ""), ti.get("new_string", ""), ti.get("command", "")]
print("\n".join(p for p in parts if p))
')"

file_path="$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
ti = d.get("tool_input", {}) or {}
print(ti.get("file_path", "") or ti.get("notebook_path", "") or "")
')"

[ -z "$payload" ] && exit 0

# Exception: CLAUDE.md tells agents to keep secrets in git-ignored .env files,
# so allow writes to .env / .env.* without scanning.
base="$(basename "$file_path")"
case "$base" in
  .env|.env.*) exit 0 ;;
esac

# Conservative patterns for common credential formats. Tune to your sources.
if printf '%s' "$payload" | grep -Eiq \
  -e "AKIA[0-9A-Z]{16}" \
  -e "aws_secret_access_key[\"' ]*[:=]" \
  -e "-----BEGIN [A-Z ]*PRIVATE KEY-----" \
  -e "\"private_key\"[[:space:]]*:" \
  -e "sk-[A-Za-z0-9]{20,}" \
  -e "(api[_-]?key|secret|token|passwd|password)[\"' ]*[:=][\"' ]*[A-Za-z0-9/_+.-]{16,}" \
  -e "(helius|alchemy|infura)[A-Za-z0-9.-]*[?&]api[-_]?key=" ; then
  echo "BLOCKED: this content appears to contain a hard-coded credential. Do not write secrets into tracked files or shell commands. Read them from environment variables (os.environ / os.getenv) and keep them in a git-ignored .env file. If this is a false positive, the user can relax the pattern in .claude/hooks/scan-secrets.sh." >&2
  exit 2
fi

exit 0
