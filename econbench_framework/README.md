# EconBench Framework

A generic, YAML-driven framework for scoring empirical-analysis agent submissions.

The framework is benchmark-agnostic. Paper-specific information belongs in:

- `benchmarks/<benchmark_name>/prompt.md`
- `benchmarks/<benchmark_name>/benchmark.yaml`

The Python scoring engine should not contain paper-specific assumptions.

## Included benchmark

This package includes a Wallace AHS real-options housing-investment benchmark:

```text
benchmarks/wallace/
├── prompt.md
└── benchmark.yaml
```

## Expected submission layout

A submitted agent run should be a folder containing files such as:

```text
replication_plan.md
variable_crosswalk.csv
sample_construction_log.csv
analysis_code.py
regression_tables.csv
replication_memo.md
limitations.md
```

The exact required files are defined by the benchmark YAML.

## One-time data setup

Raw data acquisition is a separate step from agent runs and scoring. Run it
once per benchmark; the downloaded and extracted files are cached under the
benchmark's `data/` directory and reused by every subsequent run:

```bash
python -m econbench.data --benchmark benchmarks/wallace/benchmark.yaml
```

Re-running is a no-op for items already staged (a `.econbench_complete`
marker records completion). Use `--force` to re-download from scratch.
Interrupted downloads are safe to retry. Data URLs and layout are declared
in the benchmark YAML under the `data:` key.

Agent prompts should point at the staged `data/` directory; agents must not
download raw data during a run.

## Run scoring

From the repository root:

```bash
python -m econbench.scorer \
  --benchmark benchmarks/wallace/benchmark.yaml \
  --submission examples/submissions/agent_run_001 \
  --output reports/agent_run_001_score.json
```

## Add a new benchmark

Create a new folder:

```text
benchmarks/new_paper/
├── prompt.md
└── benchmark.yaml
```

Then define in YAML:

- required outputs
- expected panel/time values
- variable mappings
- tabular artifacts
- text artifacts
- tolerances
- scoring weights

No Python code changes should be required.
