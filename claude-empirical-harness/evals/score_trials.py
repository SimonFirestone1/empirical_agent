#!/usr/bin/env python3
"""
Score eval trials against a YAML-defined rubric.

Usage:
    python3 evals/score_trials.py --task evals/tasks/scf_debt_age_income.yaml [--auto-judge]
    python3 evals/score_trials.py --results-dir evals/results/scf_debt_age_income [--auto-judge]

The task YAML defines the prompt, scoring criteria (auto or judge), and
discriminator items. See evals/tasks/scf_debt_age_income.yaml for the schema.
"""
import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


# Number of independent judge calls per trial. Pass/fail criteria take the
# majority vote; graded (0/1/2) criteria take the median.
JUDGE_K = 3

# Number of bootstrap resamples for the effect-size CI.
BOOTSTRAP_N = 10_000


# ── Load task config ─────────────────────────────────────────────────────────

def load_task(task_path: Path) -> dict:
    with open(task_path) as f:
        task = yaml.safe_load(f)
    if "rubric" in task and "criteria" not in task:
        rubric_path = task_path.parent / task["rubric"]
        with open(rubric_path) as f:
            rubric = yaml.safe_load(f)
        task["criteria"] = rubric.get("criteria", [])
        task.setdefault("discriminators", rubric.get("discriminators", []))
    validate_task(task)
    return task


def validate_task(task: dict):
    """Fail fast if any auto criterion references an unknown check type or is
    missing required fields."""
    errors = []
    for c in task.get("criteria", []):
        if c.get("mode") != "auto":
            continue
        cid = c.get("id", "<no id>")
        check = c.get("check", "")
        if check not in CHECKS:
            errors.append(
                f"criterion '{cid}': unknown check type '{check}' "
                f"(known: {', '.join(sorted(CHECKS))})"
            )
            continue
        _, required = CHECKS[check]
        missing = [field for field in required if field not in c]
        if missing:
            errors.append(
                f"criterion '{cid}' (check '{check}'): missing required "
                f"field(s): {', '.join(missing)}"
            )
    if errors:
        raise ValueError(
            "Invalid task config:\n  " + "\n  ".join(errors)
        )


# ── Build searchable text from trial artifacts ───────────────────────────────

def _extract_stream_json_parts(raw: str) -> list[str] | None:
    """Parse a stream-json (JSONL) transcript line by line.

    Returns the extracted text/tool parts, with the initial prompt (the first
    user text message) stripped so the prompt's own words can't trigger
    pattern matches. Returns None if no line parses as JSON.
    """
    parts: list[str] = []
    any_json = False
    prompt_stripped = False

    def extract_blocks(content, is_first_user_text: bool) -> None:
        nonlocal prompt_stripped
        if isinstance(content, str):
            if is_first_user_text and not prompt_stripped:
                prompt_stripped = True
                return
            parts.append(content)
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                if is_first_user_text and not prompt_stripped:
                    prompt_stripped = True
                    continue
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(json.dumps(block.get("input", {})))
            elif btype == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, list):
                    for ib in inner:
                        if isinstance(ib, dict):
                            parts.append(ib.get("text", "") or json.dumps(ib))
                        else:
                            parts.append(str(ib))
                else:
                    parts.append(str(inner))
            else:
                parts.append(json.dumps(block))

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        any_json = True
        if not isinstance(obj, dict):
            parts.append(str(obj))
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = obj.get("type") or msg.get("role", "")
        content = msg.get("content", [])
        is_user = role == "user"
        # The first user text message is the task prompt — strip it.
        extract_blocks(content, is_first_user_text=is_user and not prompt_stripped)
        # Top-level result field (stream-json final result message)
        if isinstance(obj.get("result"), str):
            parts.append(obj["result"])

    return parts if any_json else None


def build_corpus(trial_dir: Path) -> tuple[str, str]:
    """Combine trial artifacts into searchable strings.

    Returns (transcript_corpus, full_corpus). The transcript corpus contains
    only session activity (the transcript itself, minus the initial prompt);
    the full corpus adds code and output files. Checks that should reflect
    what happened during the session (e.g. data_retrieved) must use the
    transcript corpus so they are not confounded by treatment-produced
    output files.
    """
    transcript_parts = []

    transcript = trial_dir / "transcript.txt"
    if transcript.exists():
        raw = transcript.read_text(errors="replace")
        # Try line-by-line stream-json (JSONL) first
        jsonl_parts = _extract_stream_json_parts(raw)
        if jsonl_parts is not None:
            transcript_parts.extend(jsonl_parts)
        else:
            # Handle whole-file JSON transcripts (--output-format json)
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for msg in data:
                        content = msg.get("content", [])
                        if isinstance(content, list):
                            for block in content:
                                transcript_parts.append(
                                    json.dumps(block) if isinstance(block, dict) else str(block)
                                )
                        elif isinstance(content, str):
                            transcript_parts.append(content)
                else:
                    transcript_parts.append(raw)
            except (json.JSONDecodeError, TypeError):
                transcript_parts.append(raw)

    parts = list(transcript_parts)

    # Python files in the trial (rglob also covers the code/ subdirectory
    # where the runner saves workspace .py files)
    for f in trial_dir.rglob("*.py"):
        parts.append(f.read_text(errors="replace"))

    # Output artifacts (recursive)
    outputs = trial_dir / "outputs"
    if outputs.exists():
        for f in outputs.rglob("*"):
            if f.is_file() and f.suffix.lower() in (
                ".html", ".md", ".csv", ".txt", ".json", ".jsonl"
            ):
                parts.append(f.read_text(errors="replace"))

    return "\n".join(transcript_parts), "\n".join(parts)


# ── Auto-scoring checks ─────────────────────────────────────────────────────
#
# Each handler takes (criterion, transcript_corpus, full_corpus, trial_dir)
# and returns 0 or 1. Handlers are registered in the CHECKS dict below along
# with their required criterion fields; task configs are validated against
# the registry at load time.

def _check_data_retrieved(criterion, transcript_corpus, full_corpus, trial_dir):
    patterns = criterion.get("patterns", [])
    n_patterns = sum(
        1 for p in patterns if re.search(p, transcript_corpus, re.IGNORECASE)
    )
    extensions = criterion.get("data_extensions", [".csv", ".dta", ".zip"])
    has_files = any(any(trial_dir.rglob(f"*{ext}")) for ext in extensions)
    # Tightened: a single pattern match alone is too gameable. Require
    # either >=2 independent pattern matches, or a pattern match backed
    # by an actual data file on disk.
    return 1 if (n_patterns >= 2 or (n_patterns >= 1 and has_files)) else 0


def _check_url_documented(criterion, transcript_corpus, full_corpus, trial_dir):
    patterns = criterion.get("patterns", [])
    return 1 if any(re.search(p, transcript_corpus, re.IGNORECASE) for p in patterns) else 0


def _check_row_count_plausible(criterion, transcript_corpus, full_corpus, trial_dir):
    ranges = criterion.get("plausible_ranges", [])
    # Two-pass: find number+unit matches in the transcript, then require a
    # data-related keyword within 200 chars of the match so incidental
    # numbers elsewhere in the corpus don't pass.
    keyword_re = re.compile(
        r"\b(?:loaded|downloaded|read|observations|dataset|sample|data)\b",
        re.IGNORECASE,
    )
    for m in re.finditer(
        r"(\d[\d,]+)\s*(?:rows|observations|households|records|entries|samples)",
        transcript_corpus, re.IGNORECASE,
    ):
        window = transcript_corpus[max(0, m.start() - 200):m.end() + 200]
        if not keyword_re.search(window):
            continue
        n = int(m.group(1).replace(",", ""))
        for lo, hi in ranges:
            if lo <= n <= hi:
                return 1
    return 0


def _check_pattern_count(criterion, transcript_corpus, full_corpus, trial_dir):
    patterns = criterion.get("patterns", [])
    min_matches = criterion.get("min_matches", 2)
    count = sum(1 for p in patterns if re.search(p, full_corpus, re.IGNORECASE))
    return 1 if count >= min_matches else 0


def _check_any_pattern(criterion, transcript_corpus, full_corpus, trial_dir):
    patterns = criterion.get("patterns", [])
    return 1 if any(re.search(p, full_corpus, re.IGNORECASE) for p in patterns) else 0


def _check_min_figures(criterion, transcript_corpus, full_corpus, trial_dir):
    min_figs = criterion.get("min_figures", 2)
    outputs = trial_dir / "outputs"
    if outputs.exists():
        figs = [f for f in outputs.rglob("*")
                if f.is_file() and f.suffix.lower() in (".png", ".pdf", ".svg", ".jpg", ".jpeg")]
        return 1 if len(figs) >= min_figs else 0
    return 0


def _check_file_exists(criterion, transcript_corpus, full_corpus, trial_dir):
    path = criterion.get("path", "")
    min_words = criterion.get("min_words", 1)
    outputs = trial_dir / "outputs"
    target = outputs / path if outputs.exists() else None
    if target and target.exists():
        word_count = len(target.read_text(errors="replace").split())
        return 1 if word_count >= min_words else 0
    return 0


def _check_value_in_range(criterion, transcript_corpus, full_corpus, trial_dir):
    """Ground-truth numeric check: extract numbers matched by `pattern` near
    occurrences of `label`, and pass if any falls within `range` [min, max]."""
    label = criterion["label"]
    pattern = criterion["pattern"]
    lo, hi = criterion["range"]
    for m in re.finditer(label, full_corpus, re.IGNORECASE):
        window = full_corpus[max(0, m.start() - 300):m.end() + 300]
        for nm in re.finditer(pattern, window, re.IGNORECASE):
            num_str = nm.group(1) if nm.groups() else nm.group(0)
            try:
                num = float(num_str.replace(",", ""))
            except ValueError:
                continue
            if lo <= num <= hi:
                return 1
    return 0


# Registry: check name -> (handler, required criterion fields)
CHECKS = {
    "data_retrieved": (_check_data_retrieved, ["patterns"]),
    "url_documented": (_check_url_documented, ["patterns"]),
    "row_count_plausible": (_check_row_count_plausible, ["plausible_ranges"]),
    "pattern_count": (_check_pattern_count, ["patterns"]),
    "any_pattern": (_check_any_pattern, ["patterns"]),
    "min_figures": (_check_min_figures, []),
    "file_exists": (_check_file_exists, ["path"]),
    "value_in_range": (_check_value_in_range, ["pattern", "label", "range"]),
}


def auto_score(criterion: dict, transcript_corpus: str, full_corpus: str,
               trial_dir: Path) -> int | None:
    """Score a single auto criterion via the CHECKS registry."""
    check = criterion.get("check", "")
    entry = CHECKS.get(check)
    if entry is None:
        return None
    handler, _ = entry
    return handler(criterion, transcript_corpus, full_corpus, trial_dir)


# ── LLM-as-judge scoring ────────────────────────────────────────────────────

# Filenames whose names reveal the treatment condition. They are presented to
# the judge under neutral names instead of being dropped, so no substantive
# content is lost.
_FILENAME_RENAMES = {
    "FINDINGS.md": "analysis_notes.md",
    "REVIEW_NEEDED.md": "open_questions.md",
    "data_quality.md": "quality_check.md",
}

# Substring replacements applied to content shown to the judge. Only the
# revealing substring is replaced — the surrounding line is kept.
_CONTENT_REPLACEMENTS = [
    (re.compile(re.escape(old), re.IGNORECASE), new)
    for old, new in _FILENAME_RENAMES.items()
] + [
    (re.compile(r"\.claude/", re.IGNORECASE), "config/"),
    (re.compile(r"CLAUDE\.md", re.IGNORECASE), "project_notes.md"),
    (re.compile(r"harness", re.IGNORECASE), "framework"),
    (re.compile(r"skill", re.IGNORECASE), "method"),
]


def sanitize_for_judge(text: str) -> str:
    """Replace condition-revealing substrings with neutral ones, keeping the
    surrounding content intact (no lines are dropped)."""
    for pattern, replacement in _CONTENT_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _neutral_name(name: str) -> str:
    return _FILENAME_RENAMES.get(name, name)


def build_judge_context(trial_dir: Path) -> str:
    """Build the blinded judge context: analysis code and output artifacts
    only (no transcript/hook noise), with revealing filenames renamed and
    revealing substrings replaced in content."""
    sections = []

    # Analysis code: code/ dir plus any .py under outputs
    code_files: list[Path] = []
    code_dir = trial_dir / "code"
    if code_dir.exists():
        code_files.extend(sorted(f for f in code_dir.rglob("*.py") if f.is_file()))
    outputs_dir = trial_dir / "outputs"
    if outputs_dir.exists():
        code_files.extend(sorted(f for f in outputs_dir.rglob("*.py") if f.is_file()))
    for f in code_files:
        sections.append(f"=== code: {_neutral_name(f.name)} ===\n"
                        + f.read_text(errors="replace"))

    # Output artifacts
    if outputs_dir.exists():
        for f in sorted(outputs_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".md", ".csv", ".html", ".txt"):
                sections.append(f"=== output: {_neutral_name(f.name)} ===\n"
                                + f.read_text(errors="replace"))

    return sanitize_for_judge("\n\n".join(sections))


def _run_judge_once(prompt: str) -> str:
    """Run one judge subprocess, isolated from user settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env = dict(os.environ)
        # Isolate the judge from ~/.claude settings/memory
        env["HOME"] = tmpdir
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text",
             "--model", "sonnet", "--max-budget-usd", "1"],
            capture_output=True, text=True, timeout=300, cwd=tmpdir, env=env,
        )
    return result.stdout.strip()


def _parse_judge_response(response: str, judge_criteria: list[dict]) -> dict[str, int | None]:
    id_pattern = "|".join(re.escape(c["id"]) for c in judge_criteria)
    graded_ids = {c["id"] for c in judge_criteria if c.get("graded")}
    scores: dict[str, int | None] = {c["id"]: None for c in judge_criteria}
    for line in response.splitlines():
        # Flexible formats: "B1: PASS", "- B1: PASS", "**B1**: PASS", "B1 - PASS"
        match = re.search(
            rf"({id_pattern})\**\s*[:\-–—]\s*\**\s*(PASS|PARTIAL|FAIL)",
            line, re.IGNORECASE,
        )
        if match:
            cid, verdict = match.group(1), match.group(2).upper()
            if cid in graded_ids:
                scores[cid] = {"PASS": 2, "PARTIAL": 1, "FAIL": 0}[verdict]
            else:
                scores[cid] = 1 if verdict == "PASS" else 0
    return scores


def judge_score(judge_criteria: list[dict], trial_dir: Path
                ) -> tuple[dict[str, int | None], dict[str, list[int]]]:
    """Use Claude as a judge for criteria with mode=judge, running JUDGE_K
    independent votes per trial.

    Returns (scores, votes). Pass/fail criteria take the majority vote across
    the k runs; graded (0/1/2) criteria take the median. Criteria the judge
    never scores in any run are returned as None (never dropped), so
    denominators can account for judge failures. `votes` maps criterion id to
    the list of raw votes obtained (for agreement reporting).
    """
    if not judge_criteria:
        return {}, {}

    text = build_judge_context(trial_dir)

    if len(text) > 150_000:
        text = text[:75_000] + "\n\n[... truncated ...]\n\n" + text[-75_000:]

    graded_ids = {c["id"] for c in judge_criteria if c.get("graded")}

    # Build judge prompt from criteria
    criteria_block = "\n".join(
        f"- {c['id']}: {c['description']} — {c.get('judge_prompt', '')}"
        + (" (graded: PASS = fully met, PARTIAL = partially met, FAIL = not met)"
           if c["id"] in graded_ids else "")
        for c in judge_criteria
    )
    format_lines = "\n".join(
        f"{c['id']}: " + ("PASS|PARTIAL|FAIL" if c["id"] in graded_ids else "PASS|FAIL")
        + " — reason"
        for c in judge_criteria
    )

    prompt = f"""You are scoring an AI coding session against a rubric.
Below are the analysis code and output artifacts from the session. Score ONLY
the criteria listed. For each, respond with the criterion ID and a verdict,
followed by a one-sentence justification.

Criteria to score:
{criteria_block}

Respond in exactly this format (one line per criterion, no extra text):
{format_lines}

CODE AND ARTIFACTS:
{text}
"""

    votes: dict[str, list[int]] = {c["id"]: [] for c in judge_criteria}
    for i in range(JUDGE_K):
        response = ""
        try:
            response = _run_judge_once(prompt)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        # Save each raw judge response for auditing
        try:
            (trial_dir / f"judge_response_k{i}.txt").write_text(response)
        except OSError:
            pass
        run_scores = _parse_judge_response(response, judge_criteria)
        for cid, val in run_scores.items():
            if val is not None:
                votes[cid].append(val)

    scores: dict[str, int | None] = {}
    for c in judge_criteria:
        cid = c["id"]
        vs = votes[cid]
        if not vs:
            scores[cid] = None
        elif cid in graded_ids:
            scores[cid] = int(statistics.median(vs))
        else:
            # Majority vote
            scores[cid] = 1 if sum(vs) * 2 > len(vs) else 0
    return scores, votes


# ── Score a trial ────────────────────────────────────────────────────────────

def score_trial(trial_dir: Path, task: dict, auto_judge: bool = False
                ) -> tuple[dict[str, int | None], dict[str, list[int]]]:
    criteria = task.get("criteria", [])
    transcript_corpus, full_corpus = build_corpus(trial_dir)

    scores: dict[str, int | None] = {}
    votes: dict[str, list[int]] = {}

    # Auto criteria
    for c in criteria:
        if c.get("mode") == "auto":
            scores[c["id"]] = auto_score(c, transcript_corpus, full_corpus, trial_dir)

    # Judge criteria
    if auto_judge:
        judge_criteria = [c for c in criteria if c.get("mode") == "judge"]
        judge_results, votes = judge_score(judge_criteria, trial_dir)
        scores.update(judge_results)
    else:
        for c in criteria:
            if c.get("mode") == "judge" and c["id"] not in scores:
                scores[c["id"]] = None

    return scores, votes


# ── Scorecard display ────────────────────────────────────────────────────────

def print_scorecard(all_scores: dict[str, dict], task: dict, results_dir: Path,
                    all_votes: dict[str, dict[str, list[int]]] | None = None,
                    excluded: dict[str, int] | None = None):
    criteria = task.get("criteria", [])
    discriminators = task.get("discriminators", [])
    task_name = task.get("name", "eval")
    all_votes = all_votes or {}
    excluded = excluded or {}

    with_trials = {k: v for k, v in sorted(all_scores.items()) if k.startswith("with_")}
    without_trials = {k: v for k, v in sorted(all_scores.items()) if k.startswith("without_")}

    def summary_line(scores: dict[str, int | None]) -> str:
        scored = [v for v in scores.values() if v is not None]
        total = sum(scored)
        n = len(scored)
        manual = sum(1 for v in scores.values() if v is None)
        return f"{total}/{n} scored" + (f" ({manual} manual)" if manual else "")

    print("\n" + "=" * 72)
    print(f"EVAL SCORECARD: {task_name}")
    print("=" * 72)

    # Excluded (failed) trials and attrition per condition
    if excluded:
        print("\n── Excluded trials ──")
        attrition = {"with": 0, "without": 0, "other": 0}
        for tid, code in sorted(excluded.items()):
            print(f"  {tid}: EXCLUDED (exit code {code})")
            if tid.startswith("with_"):
                attrition["with"] += 1
            elif tid.startswith("without_"):
                attrition["without"] += 1
            else:
                attrition["other"] += 1
        n_with_total = len(with_trials) + attrition["with"]
        n_without_total = len(without_trials) + attrition["without"]
        print(f"  Attrition: WITH {attrition['with']}/{n_with_total}, "
              f"WITHOUT {attrition['without']}/{n_without_total}")

    all_trial_ids = list(all_scores.keys())
    col_w = 10
    header = f"{'ID':<6} {'Description':<45}"
    for tid in all_trial_ids:
        label = tid.replace("with_", "W").replace("without_", "WO").replace("run", "")
        header += f" {label:>{col_w}}"
    print(header)
    print("-" * len(header))

    for c in criteria:
        cid = c["id"]
        row = f"{cid:<6} {c['description'][:45]:<45}"
        for tid in all_trial_ids:
            val = all_scores[tid].get(cid)
            if val is None:
                cell = "—"
            elif c.get("graded"):
                cell = str(val)
            else:
                cell = "✓" if val == 1 else "✗"
            row += f" {cell:>{col_w}}"
        print(row)

    print("-" * len(header))

    row = f"{'TOTAL':<6} {'':45}"
    for tid in all_trial_ids:
        row += f" {summary_line(all_scores[tid]):>{col_w}}"
    print(row)

    # Criteria mechanically guaranteed by the treatment (e.g. hook-enforced)
    # are excluded from condition averages so they can't inflate the effect.
    mechanical_ids = {c["id"] for c in criteria if c.get("mechanical")}

    # Condition averages
    print("\n── Condition Averages ──")
    if mechanical_ids:
        print(f"  (excluding mechanical criteria: {', '.join(sorted(mechanical_ids))})")
    for label, group in [("WITH skill", with_trials), ("WITHOUT skill", without_trials)]:
        if not group:
            continue
        criterion_sums: dict[str, list[int]] = {}
        unscored = 0
        for scores in group.values():
            for cid, val in scores.items():
                if cid in mechanical_ids:
                    continue
                if val is None:
                    unscored += 1
                else:
                    criterion_sums.setdefault(cid, []).append(val)
        overall = sum(sum(v) for v in criterion_sums.values())
        total_n = sum(len(v) for v in criterion_sums.values())
        mean = overall / total_n if total_n else 0
        line = f"  {label}: {overall}/{total_n} scored = {mean:.1%}"
        if unscored:
            line += f" ({unscored} unscored due to judge failure)"
        print(line)

        for disc in discriminators:
            vals = criterion_sums.get(disc, [])
            if vals:
                rate = sum(vals) / len(vals)
                note = " (mechanical?)" if rate == 1.0 else ""
                print(f"    {disc}: {sum(vals)}/{len(vals)} = {rate:.0%}{note}")

    # ── Judge agreement (k votes per criterion per trial) ──
    if all_votes and any(any(len(vs) > 1 for vs in tv.values()) for tv in all_votes.values()):
        print(f"\n── Judge agreement (k={JUDGE_K} votes per trial) ──")
        judge_ids = [c["id"] for c in criteria if c.get("mode") == "judge"]
        for cid in judge_ids:
            trial_votes = [
                tv[cid] for tv in all_votes.values()
                if cid in tv and len(tv[cid]) > 1
            ]
            if not trial_votes:
                continue
            n_unanimous = sum(1 for vs in trial_votes if len(set(vs)) == 1)
            rate = n_unanimous / len(trial_votes)
            flag = "  ⚠ low judge agreement" if rate < 0.67 else ""
            print(f"  {cid:<6} {n_unanimous}/{len(trial_votes)} trials unanimous "
                  f"= {rate:.0%}{flag}")

    # ── Variance and significance ──
    def stdev(vals: list[int]) -> float:
        if len(vals) < 2:
            return 0.0
        m = sum(vals) / len(vals)
        return (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5

    def by_criterion_values(group: dict[str, dict]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for scores in group.values():
            for cid, val in scores.items():
                if cid in mechanical_ids or val is None:
                    continue
                out.setdefault(cid, []).append(val)
        return out

    def trial_level_scores(group: dict[str, dict]) -> list[float]:
        """Per-trial summary score: mean pass rate across non-mechanical,
        scored criteria."""
        out = []
        for scores in group.values():
            vals = [v for cid, v in scores.items()
                    if cid not in mechanical_ids and v is not None]
            if vals:
                out.append(sum(vals) / len(vals))
        return out

    print("\n── Per-criterion pass rates (mean ± sd across trials) ──")
    for label, group in [("WITH skill", with_trials), ("WITHOUT skill", without_trials)]:
        if not group:
            continue
        by_criterion = by_criterion_values(group)
        print(f"  {label} (n={len(group)} trials):")
        for c in criteria:
            vals = by_criterion.get(c["id"], [])
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            print(f"    {c['id']:<6} {mean:.0%} ± {stdev(vals):.2f} "
                  f"({sum(vals)}/{len(vals)})")

    n_with = len(with_trials)
    n_without = len(without_trials)
    if n_with >= 2 and n_without >= 2:
        try:
            from scipy.stats import fisher_exact, mannwhitneyu

            # Primary test: Mann-Whitney U on trial-level summary scores.
            # The unit of analysis is the trial, not the pooled criterion.
            ts_with = trial_level_scores(with_trials)
            ts_without = trial_level_scores(without_trials)
            print("\n── Condition comparison (trial-level scores) ──")
            print(f"  WITH:    n={len(ts_with)}, mean trial score = "
                  f"{sum(ts_with) / len(ts_with):.1%}")
            print(f"  WITHOUT: n={len(ts_without)}, mean trial score = "
                  f"{sum(ts_without) / len(ts_without):.1%}")
            u_stat, p_value = mannwhitneyu(ts_with, ts_without,
                                           alternative="two-sided")
            print(f"  Mann-Whitney U (trial-level, WITH vs WITHOUT): "
                  f"U = {u_stat:.1f}, p = {p_value:.4f}")
            if p_value >= 0.05:
                print("  Difference is NOT statistically significant at p < 0.05.")

            # Bootstrap 95% CI on the difference in mean trial-level scores
            rng = random.Random(0)
            diff_obs = (sum(ts_with) / len(ts_with)
                        - sum(ts_without) / len(ts_without))
            diffs = []
            for _ in range(BOOTSTRAP_N):
                bw = rng.choices(ts_with, k=len(ts_with))
                bwo = rng.choices(ts_without, k=len(ts_without))
                diffs.append(sum(bw) / len(bw) - sum(bwo) / len(bwo))
            diffs.sort()
            lo = diffs[int(0.025 * BOOTSTRAP_N)]
            hi = diffs[int(0.975 * BOOTSTRAP_N) - 1]
            print(f"  Effect size (WITH − WITHOUT): {diff_obs:+.1%} "
                  f"[95% bootstrap CI: {lo:+.1%}, {hi:+.1%}]")

            # Per-criterion Fisher's exact tests. Note: n here is the number
            # of trials per condition, so power is limited at small n.
            print("\n── Per-criterion Fisher's exact tests "
                  f"(n = trials: with={n_with}, without={n_without}) ──")
            bc_with = by_criterion_values(with_trials)
            bc_without = by_criterion_values(without_trials)
            for c in criteria:
                cid = c["id"]
                if cid in mechanical_ids or c.get("graded"):
                    continue
                vw = bc_with.get(cid, [])
                vwo = bc_without.get(cid, [])
                if not vw or not vwo:
                    continue
                table = [[sum(vw), len(vw) - sum(vw)],
                         [sum(vwo), len(vwo) - sum(vwo)]]
                _, p_c = fisher_exact(table)
                print(f"    {cid:<6} WITH {sum(vw)}/{len(vw)} vs "
                      f"WITHOUT {sum(vwo)}/{len(vwo)}: p = {p_c:.4f}")
        except ImportError:
            print("\n  scipy not available — skipping significance tests "
                  "(pip install scipy to enable).")

    if n_with < 5 or n_without < 5:
        print(f"\n  WARNING: low trial counts (with={n_with}, without={n_without}); "
              f"results have low statistical power. Aim for >=5 trials per condition.")

    # Save JSON
    out_path = results_dir / "scorecard.json"
    with open(out_path, "w") as f:
        json.dump({
            "task": task_name,
            "scores": all_scores,
            "judge_votes": all_votes,
            "excluded": excluded,
        }, f, indent=2)
    print(f"\nFull scores saved to {out_path}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Score eval trials against a YAML rubric")
    parser.add_argument("--task", help="Path to task YAML file")
    parser.add_argument("--results-dir", help="Results directory (default: inferred from task name)")
    parser.add_argument("--auto-judge", action="store_true", help="Use Claude as LLM judge")
    args = parser.parse_args()

    # Find the task config — either from --task or from _task.yaml in results dir
    if args.task:
        task_path = Path(args.task)
    elif args.results_dir:
        task_path = Path(args.results_dir) / "_task.yaml"
    else:
        # Look for results directories with a _task.yaml under this script's
        # own evals/results/ directory (not a cwd-relative path).
        results_root = Path(__file__).resolve().parent / "results"
        candidates = []
        if results_root.exists():
            candidates = sorted(
                d for d in results_root.iterdir()
                if d.is_dir() and (d / "_task.yaml").exists()
            )
        if not candidates:
            print("No task config found. Use --task <path.yaml> or --results-dir <dir>.")
            sys.exit(1)
        if len(candidates) > 1:
            print(f"WARNING: multiple results dirs found under {results_root}: "
                  f"{', '.join(d.name for d in candidates)}. "
                  f"Using {candidates[0].name}; pass --results-dir to choose.")
        task_path = candidates[0] / "_task.yaml"

    try:
        task = load_task(task_path)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    task_name = task["name"]

    # Determine results directory
    if args.results_dir:
        results_dir = Path(args.results_dir)
    else:
        results_dir = Path(__file__).resolve().parent / "results" / task_name

    if not results_dir.exists():
        print(f"No results at {results_dir}. Run evals/run_eval.sh --task {task_path} first.")
        sys.exit(1)

    trial_dirs = sorted([
        d for d in results_dir.iterdir()
        if d.is_dir() and (d / "transcript.txt").exists()
    ])
    if not trial_dirs:
        print(f"No trials found in {results_dir}.")
        sys.exit(1)

    # Exclude trials whose runner exited non-zero (per meta.json)
    excluded: dict[str, int] = {}
    scorable_dirs = []
    for td in trial_dirs:
        meta_path = td / "meta.json"
        if not meta_path.exists():
            print(f"  {td.name}: WARNING — missing meta.json; excluded from scoring")
            excluded[td.name] = -1
            continue
        try:
            meta = json.loads(meta_path.read_text())
            exit_code = int(meta.get("exit_code", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            print(f"  {td.name}: WARNING — corrupt meta.json; excluded from scoring")
            excluded[td.name] = -1
            continue
        if exit_code != 0:
            excluded[td.name] = exit_code
        else:
            scorable_dirs.append(td)

    for tid, code in sorted(excluded.items()):
        print(f"  {tid}: EXCLUDED (exit code {code}) — not scored")

    if not scorable_dirs:
        print("All trials excluded due to non-zero exit codes; nothing to score.")
        sys.exit(1)

    print(f"Scoring {len(scorable_dirs)} trial(s) for task '{task_name}' "
          f"({len(excluded)} excluded) ...")
    all_scores = {}
    all_votes: dict[str, dict[str, list[int]]] = {}
    for td in scorable_dirs:
        trial_id = td.name
        scores, votes = score_trial(td, task, auto_judge=args.auto_judge)
        all_scores[trial_id] = scores
        all_votes[trial_id] = votes
        scored = [v for v in scores.values() if v is not None]
        print(f"  {trial_id}: {sum(scored)}/{len(scored)}")

    print_scorecard(all_scores, task, results_dir, all_votes=all_votes,
                    excluded=excluded)


if __name__ == "__main__":
    main()
