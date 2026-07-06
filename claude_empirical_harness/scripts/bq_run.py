#!/usr/bin/env python3
"""Metered BigQuery query runner.

ALL BigQuery queries in this project must go through run_query(). A PreToolUse
hook blocks direct bigquery.Client / `bq query` access so nothing bypasses the
meter. run_query():

  1. dry-runs the query (free) to estimate bytes scanned and dollar cost;
  2. refuses to run if the estimate exceeds the per-query budget
     (default-and-flag: log it in REVIEW_NEEDED.md, rerun with
     allow_over_budget=True to override);
  3. caps maximum_bytes_billed so BigQuery itself aborts an over-budget query
     BEFORE incurring charges -- a stronger guarantee than any custom check;
  4. appends the estimate to outputs/COST_LEDGER.jsonl for a running total.

Config via environment variables:
  BQ_PRICE_PER_TIB     USD per TiB scanned, on-demand. Default 6.25.
                       *** VERIFY against the current published rate for your
                       region -- this rate changes and is NOT guaranteed current. ***
  BQ_QUERY_BUDGET_USD  Per-query budget. Default 1.00.
  BQ_COST_LEDGER       Ledger path. Default <repo>/outputs/COST_LEDGER.jsonl
                       (resolved relative to this script's directory, so it is
                       stable regardless of the process working directory).
  BQ_SESSION_BUDGET_BYTES  Optional session-level cumulative byte cap. If set,
                       run_query() refuses to run once the ledger's cumulative
                       billed bytes would exceed it.

Scope: on-demand query SCAN cost only. Not slot/capacity pricing, storage, or
streaming inserts. The ledger total is "query scan spend," not total GCP spend.
"""
from __future__ import annotations
import json
import os
import time

TIB = 2 ** 40
PRICE_PER_TIB = float(os.getenv("BQ_PRICE_PER_TIB", "6.25"))
BUDGET_USD = float(os.getenv("BQ_QUERY_BUDGET_USD", "1.00"))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LEDGER = os.path.join(os.path.dirname(_SCRIPT_DIR), "outputs", "COST_LEDGER.jsonl")
LEDGER = os.path.abspath(os.getenv("BQ_COST_LEDGER") or _DEFAULT_LEDGER)

_session_budget = os.getenv("BQ_SESSION_BUDGET_BYTES")
SESSION_BUDGET_BYTES = int(_session_budget) if _session_budget else None


class BudgetExceeded(RuntimeError):
    """Raised when a query's estimated cost exceeds the budget and the caller
    has not explicitly opted in via allow_over_budget=True."""


class _RealBQ:
    """Adapter over google.cloud.bigquery. Imported lazily so this module can be
    imported and unit-tested without the dependency installed."""

    def __init__(self):
        from google.cloud import bigquery  # lazy import
        self._bq = bigquery
        self._client = bigquery.Client()

    def dry_run_bytes(self, sql: str) -> int:
        cfg = self._bq.QueryJobConfig(dry_run=True, use_query_cache=False)
        return self._client.query(sql, job_config=cfg).total_bytes_processed

    def run(self, sql: str, maximum_bytes_billed):
        cfg = self._bq.QueryJobConfig(maximum_bytes_billed=maximum_bytes_billed)
        job = self._client.query(sql, job_config=cfg)
        result = job.result()
        return result, getattr(job, "total_bytes_billed", None)


def estimate_usd(bytes_scanned: int, price_per_tib: float = PRICE_PER_TIB) -> float:
    return (bytes_scanned / TIB) * price_per_tib


def _append_ledger(path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def ledger_total_bytes(path: str = LEDGER) -> int:
    """Cumulative billed bytes across all ledger entries (falls back to the
    estimated bytes for entries that lack an actual billed figure)."""
    if not os.path.exists(path):
        return 0
    total = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            billed = entry.get("billed_bytes")
            total += int(billed if billed is not None else entry.get("bytes", 0) or 0)
    return total


def ledger_total(path: str = LEDGER) -> float:
    """Sum of est_usd across all ledger entries. Used by the Stop hook and for
    end-of-run reporting."""
    if not os.path.exists(path):
        return 0.0
    total = 0.0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                total += float(json.loads(line).get("est_usd", 0.0))
            except Exception:
                continue
    return round(total, 4)


def run_query(sql, allow_over_budget=False, adapter=None,
              price_per_tib=PRICE_PER_TIB, budget_usd=BUDGET_USD,
              ledger=LEDGER, label=None,
              session_budget_bytes=SESSION_BUDGET_BYTES):
    """Estimate, gate, cap, run, and log a BigQuery query.

    adapter: inject a fake in tests; defaults to the real BigQuery client.
    label:   optional human tag stored in the ledger entry.
    session_budget_bytes: cumulative byte cap across the ledger; queries whose
        estimate would push the ledger total past it are refused.
    """
    if adapter is None:
        adapter = _RealBQ()

    bytes_scanned = adapter.dry_run_bytes(sql)
    est = estimate_usd(bytes_scanned, price_per_tib)

    if est > budget_usd and not allow_over_budget:
        raise BudgetExceeded(
            f"Query would scan {bytes_scanned / TIB:.4f} TiB ~= ${est:.2f}, over the "
            f"${budget_usd:.2f} per-query budget. Log the decision in REVIEW_NEEDED.md "
            f"and rerun with allow_over_budget=True to proceed."
        )

    # Session-level cumulative cap (BQ_SESSION_BUDGET_BYTES).
    if session_budget_bytes is not None:
        spent = ledger_total_bytes(ledger)
        if spent + bytes_scanned > session_budget_bytes:
            raise BudgetExceeded(
                f"Session budget exceeded: ledger shows {spent} bytes billed and this "
                f"query would add ~{bytes_scanned}, over the session cap of "
                f"{session_budget_bytes} bytes (BQ_SESSION_BUDGET_BYTES)."
            )

    # Even with allow_over_budget, never run fully uncapped: cap at 2x the
    # dry-run estimate so a runaway query still aborts.
    if allow_over_budget:
        cap = max(int(bytes_scanned * 2), int((budget_usd / price_per_tib) * TIB))
    else:
        cap = int((budget_usd / price_per_tib) * TIB)

    ran = adapter.run(sql, cap)
    if isinstance(ran, tuple):
        result, billed_bytes = ran
    else:  # backward-compatible with adapters that return only the row result
        result, billed_bytes = ran, None

    _append_ledger(ledger, {
        "ts": round(time.time(), 3),
        "label": label,
        "bytes": bytes_scanned,
        "billed_bytes": billed_bytes,
        "est_usd": round(est, 6),
        "actual_usd": round(estimate_usd(billed_bytes, price_per_tib), 6)
                      if billed_bytes is not None else None,
    })
    return result
