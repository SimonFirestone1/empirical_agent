---
name: empirical-research
description: >-
  Operating standard for AI-assisted empirical and quantitative research:
  planning an analysis, acquiring and validating data, preparing time series,
  estimating statistical or econometric models, validating results, and
  reporting reproducibly. Use this skill WHENEVER the task involves analyzing a
  dataset, estimating a model, running a regression, building a forecast, doing
  causal inference (DiD, IV, RD, event study), profiling or cleaning data,
  feature engineering, or producing charts/tables from data — even if the user
  does not say "research" or "model" explicitly. If the work will read data and
  produce a number, figure, or table, follow this skill.
---

# Empirical Research Operating Standard

This is an agent playbook, not a human checklist. It exists to make analyses
**correct, reproducible, and verifiable by artifacts rather than by your own
assurances.** Optimize for the research objective; when two valid approaches
exist, choose the cheaper or clearer one. The stages below are an ordered
sequence you may revisit, not a one-way pipeline; returning to an earlier stage
when later work reveals a problem is expected.

## Stage applicability

Not every stage applies to every task. Before beginning, read the stages and
identify which ones the task actually requires based on what the work involves:

| Stage | Applies when the task involves |
|-------|-------------------------------|
| 1 — Planning | Always |
| 2 — Computing environment | Always |
| 3 — Data acquisition | Downloading, querying, or loading external data |
| 4 — Validation & transformation | Joins, cleaning, derived variables |
| 5 — Time-series preparation | Panel or time-series data requiring stationarity, alignment, or vintage dating |
| 6 — Computational efficiency | Datasets >1M rows, hyperparameter search, or iterative estimation |
| 7 — Model estimation | Regression, ML, or any fitted model |
| 8 — Execution monitoring | Jobs expected to run >5 minutes |
| 9 — Validation | Any estimated model or quantitative finding |
| 10 — Reporting | Always (when producing deliverables) |
| 11 — Self-review | Always |
| 12 — Reproducibility | Always (when writing to outputs/) |

Skip inapplicable stages with a one-line note ("Stage 5: N/A, cross-sectional
data"). Do not write a paragraph justifying the skip. The invariants hold
regardless of which stages are active.

## Invariants (hold at all times, in every stage)

1. **No fabricated results.** Never state a number, coefficient, p-value, or
   summary statistic you did not just compute in this session. Every reported
   value in any writeup must trace to a value produced by executed code and saved
   under `outputs/`. If you cannot point to the artifact, do not report the
   number — recompute it or state that it is not available. The same applies to
   figures: a plot must not imply observations that do not exist — through axis
   padding, zero-count bins, or extrapolation drawn as if it were data (see
   Stage 10). It also applies to numbers or signs attributed to an external
   source (a benchmark or a directional prior from a paper): report only what you
   actually retrieved, with a verifiable citation and the table/figure it came
   from — never a statistic or a sign reconstructed from memory. An unsourced
   prior may be used only if labeled as domain knowledge, not as a citation.
2. **Verify by execution; persist only deliverables.** A claim that a step
   happened must be backed by *running code*, not prose. For checks (cardinality,
   leakage, range, alignment), that means the check raises or exits non-zero when
   violated — it leaves no file. Persist a file only when a reader or a later run
   actually needs it: the data-quality gate, final figures and tables, the cost
   ledger, the reproducibility record. Do not write a "proof" artifact for every
   intermediate step; that adds clutter without adding trust. (Anti-fabrication
   is Invariant 1 and does not require a file — only that the number was computed
   in-session.)
3. **Default-and-flag, never silently guess.** When a decision needs the user's
   judgment (see the convention below), choose a defensible, documented default,
   proceed, and record it in `REVIEW_NEEDED.md`. Block to ask the user only in a
   live interactive session, and only when the default could materially change
   conclusions.
4. **Never silently repair to pass a check.** When a validation fails —
   cardinality, range, temporal alignment, leakage — do not delete, deduplicate,
   coerce, or filter data to make it pass. Stop, report the failure with the
   offending counts, and fix the upstream cause or surface it via
   default-and-flag. Forcing a check to pass hides the error it exists to catch.
5. **Secrets stay out of files.** Read credentials (Helius, BigQuery, exchange
   APIs, AWS) from environment variables; never hard-code them into tracked
   files or shell commands. (A hook enforces this; do not bypass it.)
6. **Immutable inputs.** Never modify `data/raw/` or `data/external/`. Derived
   data goes to `data/processed/`; analysis outputs go to `outputs/`.
7. **Stay consistent with established facts.** As the analysis establishes
   load-bearing facts — sample coverage and date ranges, row counts after
   cleaning, join cardinality, adjustment (SA/NSA) choices, treatment dates, unit
   definitions — record each in `outputs/FINDINGS.md` as a short dated line.
   Before reporting a conclusion or building a figure or strategy on such a fact,
   check it against that record. If new work contradicts a recorded fact, it is a
   hard stop (Invariant 4): one of the two is wrong — determine which and fix it,
   do not silently proceed. Check against the recorded ledger, not your memory of
   earlier turns: a fact established many turns ago can be underweighted, or in a
   long session truncated out of context entirely — the ledger cannot.

## The default-and-flag convention

When the standard says "ask the user," do this instead unless a human is clearly
present and responsive:

- Choose the most defensible default (documented below or by domain norm).
- Append an entry to `REVIEW_NEEDED.md` in this format:

  ```
  ## [stage] short decision title
  - Decision taken: <what you did>
  - Default rationale: <why this is the safe default>
  - Alternatives: <what else was plausible>
  - To override: <exact change the user would make>
  ```

- Continue the analysis. List the open flags in the final report.

This keeps headless / `claude -p` runs unblocked while preserving every judgment
call for review. Note: default-and-flag is for legitimate forks where several
choices are each defensible. An error signal (a failed cardinality/leakage/range
check) is **not** a fork — handle it under Invariant 4, by stopping.

---

## Stage 1 — Planning

Before writing code, state explicitly:

- The research objective and the **unit of observation**.
- Whether the task is **prediction, causal inference, description, or
  forecasting** — these imply different validation regimes; do not conflate them.
- Forecast horizon(s), if any.
- Two or three candidate estimators with their key assumptions, failure modes,
  and the cost/interpretability/performance tradeoff among them.

Choose one strategy and write a one-paragraph plan. Done when the approach and
its assumptions are explicit.

## Stage 2 — Computing environment

- Default to `pandas`, `numpy`, `statsmodels`, `scipy`, `scikit-learn`,
  `matplotlib`.
- **Size-conditional defaults:** for tabular data above ~5 GB on disk or wider
  than memory comfortably allows, default to **Polars or DuckDB** (lazy /
  out-of-core) rather than pandas, and state why. Use **PyTorch** when deep
  learning, GPU, or scale justifies it. For any other non-standard package,
  justify it in one line.
- **Declare dependencies in a lockfile** — `uv.lock` or a fully pinned
  `requirements.txt` — and build the environment from it. This is the
  environment record that Stage 12 points back to: an analysis that cannot
  rebuild from the lockfile is not reproducible, so pin versions here, not at
  the end.
- Estimate whether the data will fit in memory before loading it whole.

Done when the environment matches the scale of the problem and dependencies are
pinned.

## Stage 3 — Data acquisition

- Inspect a real sample before any full download or large compute: missing
  values, malformed records, wrong dtypes, duplicates, impossible values,
  stray text. If the sample shows any of these, do not proceed to full
  processing: characterize the problem, then fix the parse/source or define an
  explicit exclusion rule before continuing — never drop the bad records
  silently (Invariant 4).
- For API pulls (BigQuery, Helius, exchange/FRED-style endpoints): validate a
  representative response and its metadata first; confirm units, variable
  definitions, geographic/entity identifiers, and order-of-magnitude
  plausibility before pulling everything. If validation fails — wrong units,
  unexpected schema, implausible magnitudes — stop and do **not** initiate the
  full download; resolve the discrepancy first (a wrong full pull also wastes
  time and may cost money).
- Log **provenance** for every source to `outputs/REPRODUCIBILITY.md` (or a
  `data/PROVENANCE.md`): source URL/endpoint, access timestamp, query/params,
  and row counts retrieved.
- Distinguish public from proprietary sources.
- **Seasonal adjustment:** if both adjusted and unadjusted series exist, default
  to the series matching the analysis (SA for level/trend economic analysis, NSA
  if you will model seasonality explicitly) and flag the choice via
  default-and-flag.

**Cost-aware data access.** When a data operation costs money (BigQuery scans,
paid APIs), first determine whether a free or cheaper path gives the same
information — direct RPC (e.g. Helius for on-chain), a cached or materialized
table, a smaller public extract, or a free-tier source (Dune/Flipside). When a
paid query is unavoidable:

- Route every BigQuery query through `scripts/bq_run.run_query()`. It dry-runs
  to estimate scan cost, caps `maximum_bytes_billed`, and appends each estimate
  to `outputs/COST_LEDGER.jsonl`. Direct `bigquery.Client` / `bq query` calls are
  blocked by a hook because they bypass the meter.
- Reduce scan cost before running: select explicit columns (never `SELECT *` —
  on-demand bills by columns scanned), filter on the partition/cluster key so the
  planner prunes, and reuse cached results (identical queries within ~24h are
  free). Treat the dry-run estimate as an upper bound; it ignores the cache.
- If an estimate exceeds the per-query budget, do not silently run it: log the
  decision via default-and-flag, then override explicitly only if warranted.
- Report the running cost total at the end (see Stage 12).

Done when inputs are understood, major quality issues are catalogued, and any
issue found in the sample has been resolved or excluded by a documented rule.

## Stage 4 — Validation & transformation

- Report counts and percentages to a small `outputs/data_quality.html` or `.md`
  (not only printed inline in chat): missing values, duplicates, observations
  removed, and **merge success / cardinality** for every join.
- **Joins (pandas):** perform every join through
  `scripts/safe_merge.safe_merge()` with an explicit `validate=` contract
  (`1:1`, `1:m`, `m:1`, `m:m`); it raises when the realized cardinality differs
  from the one you declared. A cardinality violation is a **hard stop**
  (Invariant 4): report intended vs. actual cardinality with the offending key
  counts, do not deduplicate/coerce/drop to force the expected shape, and fix
  the upstream key — interactive: ask; headless: halt, record the anomaly in
  `REVIEW_NEEDED.md` and the data-quality report. For Polars/SQL joins, assert
  row counts before and after with `scripts/safe_merge.assert_rowcount()`.
- After each join, confirm it introduced no unexpected nulls in the join keys
  and no unintended fan-out (row identity preserved); name the keys you checked.
- Investigate missingness above ~5% on any analysis variable (a tunable
  threshold) and any economically implausible value. Handle each explicitly —
  document it, exclude it by a stated rule, or impute it with a named method —
  never by a silent drop.
- Examine descriptive statistics, distributions, and pairwise correlations for
  the analysis variables, and compare key series against a reputable external
  source where one exists.

Done when: missing/duplicate/removed counts and per-join cardinality are
reported to `outputs/`, every join passed its `validate=` contract, flagged
missingness and implausible values are resolved, and the data-quality report
exists as a file.

## Stage 5 — Time-series preparation

- When a downstream method requires **stationarity**, run a diagnostic (ADF
  and/or KPSS) and apply the indicated transformation (differencing, logs);
  when several transformations are each defensible, choose one via
  default-and-flag.
- **Fit every transformation, scaler, and imputation on training data only**,
  then apply to validation/test. This is the most common silent leakage.
- Verify temporal alignment explicitly: observation date vs. **publication /
  vintage date** vs. reference period vs. prediction date. For anything used as
  a real-time feature, use the value that was actually available at that time,
  not the latest revision. If you find a feature already built from revised or
  future values, treat it as leakage (Invariant 4): stop, rebuild it from the
  real-time vintage, and do not proceed to estimation until it is fixed.

Done when temporal assumptions are documented, alignment is verified, and no
feature is built from data unavailable at its prediction time.

## Stage 6 — Computational efficiency

- Before any expensive run, estimate runtime, peak memory, and the likely
  bottleneck. If the estimate exceeds ~30 minutes (a tunable budget, not a fixed
  rule), redesign for speed before executing; if no faster approach exists,
  record the expected cost/time via default-and-flag before proceeding.
- Prefer vectorized operations over Python loops unless that hurts correctness or
  readability. Consider better algorithms, chunking, out-of-core (DuckDB/Polars
  lazy), parallelism, and compiled implementations.
- When searching a configuration space (hyperparameters, feature subsets), match
  the strategy to the space rather than defaulting to grid search: grid is
  exponential and wasteful beyond a few low-cardinality axes; prefer random
  search, Bayesian optimization (e.g. Optuna), or successive-halving/Hyperband
  for larger or continuous spaces. State the budget (trials x cost per fit)
  before launching.
- On new code over ~1M rows, run on a **representative subsample first** to
  validate logic and get a timing estimate, then scale to the full dataset.

Done when the runtime/memory estimate is within budget, or a faster approach or
an explicit flag has been recorded.

## Stage 7 — Model estimation

- **Check for information leakage before estimating**, with particular attention
  to features unavailable at prediction time, target leakage, and
  train/test-fitted transforms. State the leakage check you ran and its result.
  If you find leakage, it is a **hard stop** (Invariant 4): do not estimate until
  the offending feature is removed or the train/test split is corrected; record
  what you found and the fix.
- State the estimator's assumptions, the most plausible violations, their
  implications for your conclusions, and the robustness checks you will run.

Done when the leakage check has passed, assumptions and plausible violations are
documented, and the model is estimated.

## Stage 8 — Execution monitoring

- Confirm resources are adequate before long runs. For jobs that may run long,
  background them and poll rather than blocking.
- Watch for deviation beyond ~2x the runtime/memory estimate; if a run becomes
  impractical, stop and propose a workaround rather than wasting compute.

Done when execution completes or the failure is clearly diagnosed.

## Stage 9 — Validation

- Explain every diagnostic you report and what counts as good or poor for it.
- Keep the categories distinct: **prediction error, calibration, discrimination,
  goodness of fit, stability, robustness** — they answer different questions.
- Run the task-appropriate robustness checks: out-of-sample / time-series CV for
  prediction and forecasting; placebo / pre-trends / sensitivity for causal
  designs. If a check would be expensive, list it and flag it via
  default-and-flag rather than skipping it silently.
- If performance is poor or a diagnostic fails, report it as a finding. Do not
  re-specify or tune until results "look good" and then present only the winner
  — undisclosed specification search manufactures significance (Invariant 1). If
  you tried multiple specifications, report that you did.
- After a search run, assess the search itself, not just its winner: did it
  converge or plateau, and does the best configuration sit at a boundary of the
  searched range? A boundary optimum means the true optimum is likely outside the
  range — widen it and rerun; a different strategy or range may be warranted. But
  this diagnoses search adequacy; it is not license to try strategies until the
  score improves. Additional search is model selection: run it inside the
  training/validation folds (nested CV), never against the held-out test, and
  disclose it (Invariant 1).
- **Benchmark against the literature — directionally first.** For each key
  relationship, record in `outputs/BENCHMARKS.md` the expected **sign**, whether
  that prior is well-established or contested, and its basis: a retrieved citation
  with the specific table (public datasets, only if real literature retrieval is
  available) or, failing that, an explicitly-labeled domain prior. Compare the
  sign of your estimate — matched on **conditioning set** (their partial effect
  vs your partial effect, never their partial vs your raw correlation; a sign can
  legitimately flip between univariate and multivariate). Responses differ by
  prior strength: a sign opposite to a **well-established** prior (e.g. higher
  FICO appearing to raise default) is more likely a pipeline bug — reversed factor
  level, mis-signed transform, bad join — than a discovery, so investigate before
  trusting the result; a sign against a **contested** prior is a finding to report
  and foreground, not a discrepancy to reconcile.
- **Magnitude comparison is secondary and gated.** Compare levels/effect sizes to
  a paper only when real retrieval is available and you have matched sample
  window, filters, variable definitions, and vintage — most level gaps are
  definitional, not bugs. Flag a gap as material only when it exceeds the paper's
  reported uncertainty (CI/SE) *and* is not explained by those differences; an
  unexplained material gap points to a probable pipeline error. Cited numbers and
  signs obey Invariant 1. If retrieval is unavailable, do the directional check
  from domain priors and record that magnitude benchmarking was skipped for lack
  of retrieval — do not invent benchmarks.

Done when performance is understood, key relationships have been checked
directionally against priors/literature, and residual risks are written down.

## Stage 10 — Reporting

- Write large tables to `outputs/*.html`; do not print them inline in chat.
- Produce publication-quality figures to `outputs/` (≥150 DPI, readable labels,
  concise titles, axis units) generated entirely by script — no manual edits, so
  they regenerate.
- **Plot only over real support; never imply data that isn't there.** Bound axes
  to the data's actual extent — do not let a default or parameter range (e.g. a
  hardcoded `max_age=72`) stretch an axis past the last real observation. Derive
  limits from the data, not from a constant. Drop bins or points with zero or
  below-threshold counts instead of letting the library draw a line across the
  gap, which renders a trend through nonexistent data. For empirical curves
  (actual vs predicted, calibration, survival/age curves), show or annotate the
  per-bin sample size so thinning support is visible. Showing predicted or
  forecast values is fine, but visually separate them from observed values and
  label which is which — a prediction must never be drawn so it reads as an
  observation. This is Invariant 1 applied to figures.
- **Lead with economic/substantive interpretation, then statistical
  significance.** Distinguish established findings from tentative ones. Name the
  major sources of uncertainty. Avoid unwarranted precision (round to the
  precision the data supports; report intervals).
- Restate any open items from `REVIEW_NEEDED.md`.

Done when results are understandable, honest, and decision-ready, and every
figure is bounded to the data that actually exists.

## Stage 11 — Self-review

Before presenting, review code and logic for: leakage, wrong assumptions, merge
errors, off-by-one and date-alignment bugs, inefficient computation, untested
edge cases, and **alternative interpretations of the same result**. Fix what you
can; report the rest plainly.

Then re-read `outputs/FINDINGS.md` and check the final conclusions, figures, and
reported numbers against it. Any result that contradicts a recorded fact — a
curve extending past the known data range, an N inconsistent with the
post-cleaning count, a claim that assumes SA when the series is NSA — is a hard
stop, not a rounding difference: resolve it before presenting (Invariant 7).

## Stage 12 — Reproducibility (gated by hook)

Write `outputs/REPRODUCIBILITY.md` covering:

- Random seed(s) set (and where).
- Package versions: reference the `uv.lock` / pinned `requirements.txt` from
  Stage 2, or capture `pip freeze`.
- Data provenance from Stage 3 (source, access date, query, row counts).
- The exact command(s) to regenerate every artifact in `outputs/`.
- If any paid query ran, a line reporting the running total, e.g.
  `Estimated query cost (BigQuery scan): $X.XX (see outputs/COST_LEDGER.jsonl)`.
  The Stop hook blocks finishing until this line is present whenever a cost
  ledger with entries exists.

Keep processing modular; avoid hidden notebook state (restart-and-run-all must
work). A Stop hook blocks the session from finishing if `outputs/` contains
results but this file is missing or empty — that is by design.

---

## Expected project layout

```
data/raw/         immutable source data            (write-protected by hook)
data/external/    immutable reference data         (write-protected by hook)
data/processed/   derived data you keep and reuse  (you write here)
outputs/          DELIVERABLES ONLY: final figures, tables, models, reports,
                  REPRODUCIBILITY.md, COST_LEDGER.jsonl
scratch/          throwaway: exploratory plots, intermediate dumps. Git-ignored,
                  safe to delete; keep it OUT of outputs/ so the Stop hook ignores it.
REVIEW_NEEDED.md  deferred judgment calls (default-and-flag log)
src/ | notebooks/ code
```

**Artifact hygiene.** Write to deterministic filenames that a re-run overwrites
(`outputs/data_quality.html`, `outputs/fig_prepayment_curve.png`) — never
timestamped or `_v2` / `_final` names that accumulate. Because the analysis
regenerates every `outputs/` file from seed + lockfile (Stage 12), version
history belongs in git, not in copies on disk. Anything exploratory or
intermediate goes in `scratch/`, so `outputs/` stays small and every file in it
is something you would actually hand to a reader.

## When to delegate to a subagent

Move high-volume, low-signal steps into a subagent that returns only a summary,
to protect the main context window for estimation and interpretation.

### Available subagents

| Agent | When to use | Stage |
|-------|-------------|-------|
| `data-profiler` | Schema inspection, missing-value counts, distribution summaries, join-key cardinality, plausibility checks — any time you need to understand a dataset before writing transformation or estimation code. | 3–4 |
| `validation-runner` | Cross-validation folds, out-of-sample evaluation, placebo tests, pre-trend checks, residual diagnostics, calibration — any robustness check that is self-contained and compute-heavy. | 9 |

### Delegation rules

1. **Profile before you transform.** Before writing any Stage 4 cleaning or
   joining code, launch `data-profiler` on every input dataset. Act on its
   summary — do not re-inspect the same data manually in the main session.
2. **Parallelize independent checks.** When Stage 9 calls for multiple
   robustness checks (e.g., CV + placebo + residual diagnostics), launch
   separate `validation-runner` agents in parallel rather than running them
   sequentially in the main session.
3. **Keep estimation in the main session.** Model fitting, interpretation, and
   any decision that requires reasoning across stages must stay in the main
   context — never delegate judgment, only computation.
4. **Trust but verify.** Subagent results obey Invariant 1 (no fabricated
   numbers) via their own system prompts. If a returned metric looks
   implausible, re-run the specific check in the main session rather than
   discarding the whole report.
