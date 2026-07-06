# Empirical Research Skill — Eval Harness

Measures whether the empirical-research skill improves Claude's handling of
real data analysis tasks.

## Quick start

```bash
# Run 1 trial in each condition for the SCF task
bash evals/run_eval.sh --task evals/tasks/scf_debt_age_income.yaml --runs 1 --condition both

# Score with LLM-as-judge
python3 evals/score_trials.py --task evals/tasks/scf_debt_age_income.yaml --auto-judge

# List available tasks
bash evals/run_eval.sh
```

## Architecture

```
evals/
├── run_eval.sh              # Generic runner — reads any task YAML
├── score_trials.py          # Generic scorer — reads criteria from YAML
├── tasks/                   # Task definitions (add new ones here)
│   ├── scf_debt_age_income.yaml
│   └── cps_wage_gap.yaml
├── results/                 # Trial outputs (per-task subdirectories)
│   └── <task_name>/
│       ├── _task.yaml       # Copy of task config used
│       ├── with_run1/
│       │   ├── transcript.txt
│       │   ├── outputs/
│       │   ├── file_manifest.txt
│       │   └── meta.json
│       └── without_run1/
│           └── ...
├── scoring_rubric.md        # Human-readable rubric (SCF reference)
└── README.md
```

## Defining a new task

Create a YAML file in `evals/tasks/` with this schema:

```yaml
name: my_task_name              # Used for results directory naming
description: "..."              # What the task tests

prompt: |                       # The prompt sent to Claude
  Download X data and analyze Y...

criteria:                       # Scoring rubric — list of criteria
  - id: A1                      # Short ID for display
    category: Data Acquisition  # Grouping label
    description: "..."          # One-line description
    mode: auto                  # "auto" or "judge"
    check: data_retrieved       # Auto-check type (see below)
    patterns: ["regex1", ...]   # Check-specific parameters

  - id: B1
    category: Methodology
    description: "..."
    mode: judge                 # Uses Claude-as-judge
    judge_prompt: >             # Rubric for the LLM judge
      Did the analysis do X? Failure to do Y = FAIL.

discriminators: [B1, E2]        # Criteria to highlight in summary
```

### Auto-check types

| Check | Parameters | What it does |
|-------|-----------|--------------|
| `data_retrieved` | `patterns`, `data_extensions` | Regex match OR data files exist |
| `url_documented` | `patterns` | Any URL pattern found in corpus |
| `row_count_plausible` | `plausible_ranges` | Number + "rows/obs/..." in plausible range |
| `pattern_count` | `patterns`, `min_matches` | ≥N distinct patterns match |
| `any_pattern` | `patterns` | Any one pattern matches |
| `min_figures` | `min_figures` | ≥N image files in outputs/ |
| `file_exists` | `path`, `min_words` | File exists in outputs/ with ≥N words |

### Judge criteria

Judge criteria are scored by a separate Claude call that reads the trial's
transcript + output artifacts. Write the `judge_prompt` as a clear rubric
with explicit PASS/FAIL conditions.

## Isolation strategy

Each trial runs in a fresh temp directory with its own git repo. The
with/without difference is whether `.claude/` (hooks, skills) and `CLAUDE.md`
exist in the workspace.

**Caveats:**
- User-level `~/.claude/` (auth, model preference, effort level) applies
  equally to both conditions — not a confound, but `--effort high` is forced
  to override user defaults.
- No project-scoped memory exists for the temp dir path, so user memory
  doesn't leak.
- Rate limits can truncate long sessions. The with-skill condition does more
  work (validation, documentation) and is more likely to hit limits. Consider
  running trials sequentially or using an API key with higher limits.

## Requirements

- `claude` CLI on PATH
- `python3` with `pyyaml` installed
- `--dangerously-skip-permissions` support (Claude Code)
