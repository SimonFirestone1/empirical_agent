#!/usr/bin/env python3
# PreToolUse hook -- warn (non-blocking) when a pandas merge is written without
# an explicit validate= cardinality contract. Registered for Write|Edit|NotebookEdit.
# Always exits 0 so the tool call is never blocked; the warning is printed to
# stderr for visibility only.
# Polars/SQL joins are legitimate and not matched here.
import sys
import json
import re

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

ti = d.get("tool_input", {}) or {}
payload = "\n".join(p for p in [ti.get("content", ""), ti.get("new_string", "")] if p)
fp = ti.get("file_path", "") or ""

if not payload:
    sys.exit(0)

# The wrapper itself, and code actually calling/importing it, are exempt.
# Require a real safe_merge call or import, not just the substring anywhere.
if fp.endswith("safe_merge.py") or re.search(
    r"safe_merge\s*\(|from\s+\S*safe_merge\s+import|import\s+\S*safe_merge",
    payload,
):
    sys.exit(0)

# pandas merges: pd.merge(...) / pandas.merge(...) / df.merge(...).
# Require an identifier before ".merge(" so Polars .merge_sorted() etc. and
# module-level chained calls don't false-positive; pd./pandas. handled explicitly.
has_merge = re.search(
    r"(?:pd|pandas)\.merge\s*\(|\b[A-Za-z_]\w*\s*\.\s*merge\s*\(", payload
)
if has_merge and not re.search(r"validate\s*=", payload):
    sys.stderr.write(
        "WARNING: pandas merge without an explicit validate= contract. Use "
        "scripts.safe_merge.safe_merge(left, right, on=..., how=..., "
        "validate='1:1'|'1:m'|'m:1'|'m:m') so an unexpected cardinality raises "
        "instead of silently fanning out rows or being deduplicated away.\n"
    )
    # Exit 0: warn only, never block the tool call (exit 2 would block it).
    sys.exit(0)

sys.exit(0)
