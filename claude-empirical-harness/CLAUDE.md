# Project conventions

This repo follows the empirical-research operating standard. The full playbook
lives in `.claude/skills/empirical-research/SKILL.md` and loads on demand — when
a task involves analyzing data, estimating a model, forecasting, or producing
figures/tables, follow it.

## Always-true rules

- **No fabricated numbers.** Never report a statistic you did not just compute.
  Every number must trace to a value produced by executed code this session — not
  necessarily a saved file; persist files only for deliverables (see below). The
  same holds for numbers or signs cited from papers: cite only what you actually
  retrieved, with a source — never a benchmark or directional prior recalled from
  memory (an unsourced prior must be labeled domain knowledge, not a citation).
- **Python default.** pandas/numpy/statsmodels/scikit-learn for normal scale;
  default to **Polars or DuckDB** above ~5 GB or out-of-memory; PyTorch when
  scale/GPU justifies it.
- **Secrets via env only.** Read API keys (Helius, BigQuery, exchanges, AWS)
  from environment variables; never hard-code them. A hook enforces this.
- **Cost-aware data.** Prefer free/cheaper sources over paid queries. Route all
  BigQuery through `scripts/bq_run.run_query()` (estimates, caps, and logs cost);
  direct client/`bq query` access is blocked. Report the running total at the end.
- **Never silently repair to pass a check.** Don't dedup/coerce/drop data to make
  a bad join, range, or alignment check pass — stop and surface it. Pandas joins
  go through `scripts/safe_merge.safe_merge()` with an explicit `validate=`
  contract; a cardinality violation is a hard stop.
- **Stay consistent.** Record load-bearing facts (data coverage, dates, row
  counts, cardinality, SA/NSA) in `outputs/FINDINGS.md` as they're established,
  and check new conclusions and figures against that ledger — not against memory
  of earlier turns. A result that contradicts a recorded fact is a hard stop.
- **Decisions: default-and-flag.** When a judgment call needs the user, pick a
  documented default, proceed, and log it in `REVIEW_NEEDED.md`. Only block to
  ask in a live interactive session.

## Directory contract

- `data/raw/`, `data/external/` — immutable inputs (write-protected by a hook).
- `data/processed/` — derived data you keep and reuse.
- `outputs/` — **deliverables only**: final figures, tables, models, reports,
  `REPRODUCIBILITY.md`, `COST_LEDGER.jsonl`. Use deterministic filenames a re-run
  overwrites; never timestamped or `_final` names that pile up.
- `scratch/` — throwaway exploratory/intermediate files. Git-ignored, safe to
  delete; keep out of `outputs/` so the Stop hook ignores it.
- `REVIEW_NEEDED.md` — deferred judgment calls.

Before finishing any analysis that wrote to `outputs/`, write
`outputs/REPRODUCIBILITY.md` (seeds, package versions, data provenance,
regeneration commands). A Stop hook enforces this.

## Context-window continuity

After each milestone, append a short entry to `scratch/session_log_YYYY-MM-DD.md`
(today's date; fresh file per day). A milestone is any of:

- Writing or updating a file in `outputs/`
- Adding a fact to `outputs/FINDINGS.md`
- Completing a user-requested task or phase
- Hitting a dead end or changing approach

Each entry: a `---` separator, a timestamp, then ≤5 bullet points covering
what just happened, any surprises, and what's next. Reference file paths
rather than re-explaining. Skip if the milestone is already fully captured
in `FINDINGS.md` or commit messages.
