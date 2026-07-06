#!/usr/bin/env python3
# PreToolUse hook -- force all BigQuery access through scripts/bq_run.run_query(),
# which estimates cost, caps spend, and logs to the cost ledger.
# Registered for matcher: Write|Edit|NotebookEdit|Bash
# Exit 2 => deny the tool call. Exit 0 => allow.
import sys
import json
import re

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

ti = d.get("tool_input", {}) or {}
payload = "\n".join(
    p for p in [ti.get("content", ""), ti.get("new_string", ""), ti.get("command", "")] if p
)
fp = ti.get("file_path", "") or ti.get("notebook_path", "") or ""

if not payload:
    sys.exit(0)

# The metered wrapper itself is allowed to construct a BigQuery client.
# For commands, only exempt actual invocations of the wrapper script (e.g.
# `python3 scripts/bq_run.py ...`), not any string that merely contains "bq_run".
if fp.endswith("bq_run.py") or re.search(r"python3?\s+\S*bq_run", payload):
    sys.exit(0)

PATTERNS = [
    r"bigquery\.Client\s*\(",
    r"from\s+google\.cloud\s+import\s+bigquery",
    r"\bbq\s+query\b",
]

if any(re.search(p, payload) for p in PATTERNS):
    sys.stderr.write(
        "BLOCKED: route BigQuery through scripts/bq_run.run_query(), which dry-runs to "
        "estimate cost, caps maximum_bytes_billed, and logs to outputs/COST_LEDGER.jsonl. "
        "Direct bigquery.Client / `bq query` calls bypass the cost meter. If a free path "
        "exists (direct RPC, a cached/materialized table, a free-tier source), prefer it.\n"
    )
    sys.exit(2)

sys.exit(0)
