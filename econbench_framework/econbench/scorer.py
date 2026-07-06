"""
econbench.scorer

Generic scoring engine for empirical-analysis agent benchmarks.

The scorer is intentionally benchmark-agnostic. It reads all benchmark-specific
requirements from a YAML file: required files, expected panel/time values,
variable mappings, required columns, scoring weights, and tolerance bands.

Typical usage:

    python -m econbench.scorer \
        --benchmark benchmarks/wallace/benchmark.yaml \
        --submission examples/submissions/agent_run_001 \
        --output reports/score_report.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import re
import sys

import pandas as pd
import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# M6: cap submission CSV size to avoid reading arbitrarily large files.
MAX_CSV_BYTES = 100 * 1024 * 1024  # 100MB


def safe_read_csv(path: Path) -> tuple[pd.DataFrame | None, str | None]:
    """Read a CSV, returning (df, error). error is None on success."""
    if not path.exists():
        return None, "FileNotFoundError: file does not exist"
    try:
        size = path.stat().st_size
        if size > MAX_CSV_BYTES:
            return None, f"FileTooLargeError: {size} bytes exceeds limit of {MAX_CSV_BYTES}"
        return pd.read_csv(path), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def normalize_name(x: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(x).strip().lower()).strip("_")


def normalize_value(x: Any) -> str:
    """Normalize a cell value for comparison.

    M1: strips trailing '.0' from float-like representations so integer
    expected values (e.g. 2020) match float columns (e.g. 2020.0).
    """
    s = str(x).strip()
    if re.fullmatch(r"-?\d+\.\d*0*", s) and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def flatten_acceptable_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        out = []
        for v in value.values():
            if isinstance(v, list):
                out.extend(v)
            else:
                out.append(v)
        return out
    if isinstance(value, list):
        return value
    return [value]


def score_required_files(submission_dir: Path, required_files: list[str]) -> dict[str, Any]:
    found = []
    missing = []

    for file in required_files:
        path = submission_dir / file
        # Entries must be actual non-empty files (not directories).
        if path.is_file() and path.stat().st_size > 0:
            found.append(file)
        else:
            missing.append(file)

    return {
        "score": len(found) / len(required_files) if required_files else 1.0,
        "found": found,
        "missing": missing,
    }


def score_required_columns(df: pd.DataFrame | None, required_columns: list[str]) -> dict[str, Any]:
    if df is None:
        return {"score": 0.0, "matched_columns": [], "missing_columns": required_columns}

    observed = {normalize_name(c) for c in df.columns}
    # Low: normalize_name can collapse distinct columns (e.g. "Std Error" and
    # "std_error") to the same normalized form. Surface collisions for
    # debugging; scoring behavior is unchanged.
    if len(observed) < len(df.columns):
        print(
            "WARNING: normalize_name collision in observed columns: "
            f"{len(df.columns)} columns normalized to {len(observed)} unique names "
            f"(columns: {list(df.columns)})",
            file=sys.stderr,
        )
    required = {normalize_name(c) for c in required_columns}

    matched = sorted(required & observed)
    missing = sorted(required - observed)

    return {
        "score": len(matched) / len(required) if required else 1.0,
        "matched_columns": matched,
        "missing_columns": missing,
    }


def score_expected_values(
    df: pd.DataFrame | None,
    column: str,
    expected_values: list[Any],
) -> dict[str, Any]:
    if df is None:
        return {"score": 0.0, "observed": [], "missing": expected_values, "extra": []}

    normalized_lookup = {normalize_name(c): c for c in df.columns}
    column = normalized_lookup.get(normalize_name(column), column)

    if column not in df.columns:
        return {
            "score": 0.0,
            "observed": [],
            "missing": expected_values,
            "extra": [],
            "error": f"Column '{column}' not found",
        }

    observed_raw = df[column].dropna().unique().tolist()
    observed = sorted({normalize_value(x) for x in observed_raw})
    expected = sorted({normalize_value(x) for x in expected_values})

    matched = sorted(set(observed) & set(expected))
    missing = sorted(set(expected) - set(observed))
    # NOTE: `extra` is intentionally diagnostic-only and does not affect the
    # score; it helps maintainers spot unexpected values in submissions.
    extra = sorted(set(observed) - set(expected))

    return {
        "score": len(matched) / len(expected) if expected else 1.0,
        "observed": observed,
        "missing": missing,
        "extra": extra,
    }


def tier_score(
    tolerance_tiers: dict[str, Any],
    rows: pd.DataFrame,
    quality_col: str,
) -> tuple[float | None, list[str]]:
    """Score a variable's rows against graded tolerance tiers.

    Returns (score, unrecognized_tiers). score is None when tier-based
    scoring cannot be applied (no tiers configured, column missing, or no
    recognized tier values) — callers should fall back to binary scoring.

    When multiple rows carry recognized tiers, the minimum tier score
    governs (the worst-quality row determines credit, anti-gaming).
    """
    if not tolerance_tiers or quality_col not in rows.columns:
        return None, []

    tier_lookup = {
        normalize_name(name): float(cfg.get("score", 0.0)) if isinstance(cfg, dict) else float(cfg)
        for name, cfg in tolerance_tiers.items()
    }

    recognized: list[float] = []
    unrecognized: list[str] = []
    for value in rows[quality_col].dropna():
        norm = normalize_name(value)
        if norm in tier_lookup:
            recognized.append(tier_lookup[norm])
        else:
            unrecognized.append(str(value))

    if not recognized:
        return None, unrecognized
    return min(recognized), unrecognized


def score_variable_crosswalk(
    submission_dir: Path,
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    spec = benchmark.get("crosswalk_file", {})
    filename = spec.get("filename", "variable_crosswalk.csv")
    path = submission_dir / filename
    df, read_error = safe_read_csv(path)

    if df is None:
        return {"score": 0.0, "error": f"{filename} missing or unreadable ({read_error})"}

    column_score = score_required_columns(df, spec.get("required_columns", []))

    expected_values_score = {"score": 1.0}
    if "expected_values" in spec:
        expected_values_score = score_expected_values(
            df,
            spec["expected_values"]["column"],
            spec["expected_values"]["values"],
        )

    variable_specs = benchmark.get("variable_mappings", {})
    required_vars = list(variable_specs.keys())

    final_variable_col = spec.get("final_variable_column", "final_variable")
    source_variable_col = spec.get("source_variable_column", "source_variable")
    transformation_col = spec.get("transformation_column", "transformation")

    normalized_cols = {normalize_name(c): c for c in df.columns}
    final_variable_col = normalized_cols.get(normalize_name(final_variable_col), final_variable_col)
    source_variable_col = normalized_cols.get(normalize_name(source_variable_col), source_variable_col)
    transformation_col = normalized_cols.get(normalize_name(transformation_col), transformation_col)

    # Graded tolerance tiers (top-level `tolerances:` in benchmark.yaml).
    # When present and the submission includes quality-tier columns, tier
    # scores replace binary source/transformation matching.
    tolerances = benchmark.get("tolerances", {}) or {}
    source_quality_col = spec.get("source_quality_column", "source_quality")
    transformation_quality_col = spec.get("transformation_quality_column", "transformation_quality")
    source_quality_col = normalized_cols.get(normalize_name(source_quality_col), source_quality_col)
    transformation_quality_col = normalized_cols.get(
        normalize_name(transformation_quality_col), transformation_quality_col
    )

    if final_variable_col not in df.columns:
        variable_coverage_score = 0.0
        variable_details = {"error": f"{final_variable_col} missing"}
        mapping_quality_score = 0.0
    else:
        # H1: exact normalized match only — substring matching was gameable.
        submitted_vars = set(df[final_variable_col].dropna().map(normalize_name))
        required_norm = {normalize_name(v): v for v in required_vars}
        matched_required = {
            original for norm, original in required_norm.items()
            if norm in submitted_vars
        }
        variable_coverage_score = len(matched_required) / len(required_vars) if required_vars else 1.0

        wave_col = spec.get("wave_column", "wave")
        wave_col = normalized_cols.get(normalize_name(wave_col), wave_col)

        variable_details = {}
        for var_name, var_spec in variable_specs.items():
            var_norm = normalize_name(var_name)
            # H1: same exact-match logic as the coverage check above.
            rows = df[df[final_variable_col].map(normalize_name) == var_norm]
            if rows.empty:
                variable_details[var_name] = {
                    "coverage": 0.0,
                    "source_match": None,
                    "transformation_match": None,
                    "score": 0.0,
                }
                continue

            acceptable_sources_spec = var_spec.get("acceptable_sources", [])
            acceptable_transformations = flatten_acceptable_values(
                var_spec.get("acceptable_transformations", var_spec.get("acceptable_construction", []))
            )

            source_match = None
            if acceptable_sources_spec and source_variable_col in df.columns:
                if isinstance(acceptable_sources_spec, dict) and wave_col in df.columns:
                    # H5: wave-keyed acceptable_sources — check each row's
                    # source against only the acceptable sources for its wave.
                    wave_acceptable = {
                        normalize_name(w): {
                            normalize_name(s)
                            for s in (v if isinstance(v, list) else [v])
                        }
                        for w, v in acceptable_sources_spec.items()
                    }
                    per_row_matches = []
                    for _, row in rows.iterrows():
                        src = row.get(source_variable_col)
                        wave = row.get(wave_col)
                        if pd.isna(src) or pd.isna(wave):
                            continue
                        allowed = wave_acceptable.get(normalize_name(wave))
                        if allowed is None:
                            continue
                        per_row_matches.append(normalize_name(src) in allowed)
                    source_match = bool(per_row_matches) and all(per_row_matches)
                else:
                    acceptable_sources = flatten_acceptable_values(acceptable_sources_spec)
                    observed_sources = set(rows[source_variable_col].dropna().map(normalize_name))
                    acceptable_sources_norm = {normalize_name(s) for s in acceptable_sources}
                    source_match = bool(observed_sources & acceptable_sources_norm)

            transformation_match = None
            if acceptable_transformations and transformation_col in df.columns:
                observed_transformations = set(rows[transformation_col].dropna().map(normalize_name))
                acceptable_transformations_norm = {normalize_name(t) for t in acceptable_transformations}
                transformation_match = bool(observed_transformations & acceptable_transformations_norm)

            # H2: missing columns (match is None) score 0.0 — omitting
            # columns must not be rewarded with partial credit.
            source_score = 1.0 if source_match is True else 0.0
            transformation_score = 1.0 if transformation_match is True else 0.0

            # Graded tier scoring overrides binary matching when the
            # benchmark defines `tolerances:` and the submission declares a
            # recognized quality tier for this variable. If the tier column
            # is absent or its values are unrecognized, binary scoring
            # applies (backward compatible).
            source_tier_score, source_unrecognized = tier_score(
                tolerances.get("variable_mapping", {}), rows, source_quality_col
            )
            transformation_tier_score, transformation_unrecognized = tier_score(
                tolerances.get("transformation", {}), rows, transformation_quality_col
            )
            if source_tier_score is not None:
                source_score = source_tier_score
            if transformation_tier_score is not None:
                transformation_score = transformation_tier_score

            variable_details[var_name] = {
                "coverage": 1.0,
                "source_match": source_match,
                "transformation_match": transformation_match,
                "source_tier_score": source_tier_score,
                "transformation_tier_score": transformation_tier_score,
                "unrecognized_tiers": sorted(set(source_unrecognized + transformation_unrecognized)),
                "score": 0.5 + 0.25 * source_score + 0.25 * transformation_score,
            }

        mapping_quality_score = (
            sum(v.get("score", 0.0) for v in variable_details.values()) / len(variable_details)
            if variable_details else 1.0
        )

    weights = spec.get(
        "score_weights",
        {
            "columns": 0.20,
            "expected_values": 0.15,
            "variable_coverage": 0.30,
            "mapping_quality": 0.35,
        },
    )

    weight_total = sum(weights.values())
    total = 0.0 if weight_total == 0 else (
        weights.get("columns", 0) * column_score["score"]
        + weights.get("expected_values", 0) * expected_values_score["score"]
        + weights.get("variable_coverage", 0) * variable_coverage_score
        + weights.get("mapping_quality", 0) * mapping_quality_score
    ) / weight_total

    return {
        "score": float(total),
        "column_score": column_score,
        "expected_values_score": expected_values_score,
        "variable_coverage_score": variable_coverage_score,
        "mapping_quality_score": mapping_quality_score,
        "variable_details": variable_details,
    }


def score_tabular_artifact(
    submission_dir: Path,
    artifact_spec: dict[str, Any],
) -> dict[str, Any]:
    filename = artifact_spec["filename"]
    path = submission_dir / filename
    df, read_error = safe_read_csv(path)

    if df is None:
        return {"score": 0.0, "error": f"{filename} missing or unreadable ({read_error})"}

    column_score = score_required_columns(df, artifact_spec.get("required_columns", []))

    has_expected_values = "expected_values" in artifact_spec
    expected_values_score = {"score": 1.0}
    if has_expected_values:
        expected_values_score = score_expected_values(
            df,
            artifact_spec["expected_values"]["column"],
            artifact_spec["expected_values"]["values"],
        )

    minimum_rows = artifact_spec.get("minimum_rows")
    row_score = min(len(df) / minimum_rows, 1.0) if minimum_rows else 1.0

    weights = dict(artifact_spec.get(
        "score_weights",
        {"columns": 0.45, "expected_values": 0.35, "minimum_rows": 0.20},
    ))

    # M2: if expected_values is not defined, exclude it from the weighting
    # instead of granting a free 1.0 with weight.
    if not has_expected_values:
        weights.pop("expected_values", None)

    weight_total = sum(weights.values())
    total = 0.0 if weight_total == 0 else (
        weights.get("columns", 0) * column_score["score"]
        + weights.get("expected_values", 0) * expected_values_score["score"]
        + weights.get("minimum_rows", 0) * row_score
    ) / weight_total

    return {
        "score": float(total),
        "column_score": column_score,
        "expected_values_score": expected_values_score,
        "row_score": row_score,
    }


def score_text_artifact(
    submission_dir: Path,
    artifact_spec: dict[str, Any],
) -> dict[str, Any]:
    filename = artifact_spec["filename"]
    path = submission_dir / filename

    if not path.exists():
        return {"score": 0.0, "error": f"{filename} missing"}

    raw_text = read_text(path)
    text = raw_text.lower()
    required_terms = artifact_spec.get("required_terms", [])
    required_regex = artifact_spec.get("required_regex", [])

    term_matches = [term for term in required_terms if term.lower() in text]
    regex_matches = [pattern for pattern in required_regex if re.search(pattern, text, flags=re.I | re.M)]

    denom = len(required_terms) + len(required_regex)
    score = (len(term_matches) + len(regex_matches)) / denom if denom else 1.0

    # M3: anti-gaming checks — pure keyword presence is trivially gamed by
    # dumping the term list verbatim, so apply structural penalties.
    penalties: list[str] = []

    words = re.findall(r"\b\w+\b", text)
    word_count = len(words)
    min_words = artifact_spec.get("minimum_words", 100)
    if word_count < min_words:
        penalties.append(f"document too short ({word_count} words < {min_words})")

    term_density = None
    if word_count > 0 and term_matches:
        matched_term_words = sum(len(re.findall(r"\b\w+\b", t.lower())) for t in term_matches)
        term_density = matched_term_words / word_count
        max_density = artifact_spec.get("max_term_density", 0.5)
        if term_density > max_density:
            penalties.append(
                f"suspicious term density ({term_density:.2f} > {max_density}); "
                "matched terms dominate the document"
            )

    # Terms must be spread across distinct lines/sentences, not clustered
    # in a single keyword-dump line.
    if len(term_matches) > 1:
        segments = [
            s.lower()
            for s in re.split(r"[\n\r]+|(?<=[.!?])\s+", raw_text)
            if s.strip()
        ]
        distinct_segments = set()
        for term in term_matches:
            for i, seg in enumerate(segments):
                if term.lower() in seg:
                    distinct_segments.add(i)
                    break
        if len(distinct_segments) <= 1:
            penalties.append(
                "matched terms all clustered in a single sentence/line "
                "(possible keyword dump)"
            )

    if penalties:
        score *= 0.5

    return {
        "score": score,
        "matched_terms": term_matches,
        "missing_terms": sorted(set(required_terms) - set(term_matches)),
        "matched_regex": regex_matches,
        "missing_regex": sorted(set(required_regex) - set(regex_matches)),
        "word_count": word_count,
        "term_density": term_density,
        "penalties": penalties,
    }


def score_coefficients(
    submission_dir: Path,
    ground_truth: dict[str, Any],
) -> dict[str, Any]:
    """Score regression coefficients against judge-only ground truth.

    Ground truth lives in a separate YAML (never shown to agents) with
    structure: coefficients -> model -> variable -> {expected, atol, rtol,
    weight}. A coefficient passes if
    |observed - expected| <= max(atol, rtol * |expected|).
    Score is the weighted fraction of coefficients within tolerance.
    """
    spec = ground_truth.get("coefficients", {}) or {}
    filename = ground_truth.get("filename", "regression_tables.csv")
    path = submission_dir / filename
    df, read_error = safe_read_csv(path)

    if df is None:
        return {"score": 0.0, "error": f"{filename} missing or unreadable ({read_error})"}

    normalized_cols = {normalize_name(c): c for c in df.columns}
    model_col = normalized_cols.get("model")
    variable_col = normalized_cols.get("variable")
    coefficient_col = normalized_cols.get("coefficient")

    missing_cols = [
        name for name, col in
        [("model", model_col), ("variable", variable_col), ("coefficient", coefficient_col)]
        if col is None
    ]
    if missing_cols:
        return {"score": 0.0, "error": f"{filename} missing required columns: {missing_cols}"}

    details: dict[str, dict[str, Any]] = {}
    total_weight = 0.0
    passed_weight = 0.0

    model_norm = df[model_col].map(normalize_name)
    variable_norm = df[variable_col].map(normalize_name)

    for model_name, variables in spec.items():
        for var_name, coef_spec in (variables or {}).items():
            key = f"{model_name}.{var_name}"
            expected = float(coef_spec["expected"])
            atol = float(coef_spec.get("atol", 0.0))
            rtol = float(coef_spec.get("rtol", 0.0))
            weight = float(coef_spec.get("weight", 1.0))
            tolerance = max(atol, rtol * abs(expected))
            total_weight += weight

            rows = df[
                (model_norm == normalize_name(model_name))
                & (variable_norm == normalize_name(var_name))
            ]
            if rows.empty:
                details[key] = {
                    "expected": expected,
                    "observed": None,
                    "tolerance": tolerance,
                    "distance": None,
                    "passed": False,
                    "error": "no matching (model, variable) row",
                }
                continue

            observed_raw = rows.iloc[0][coefficient_col]
            try:
                observed = float(observed_raw)
            except (TypeError, ValueError):
                details[key] = {
                    "expected": expected,
                    "observed": str(observed_raw),
                    "tolerance": tolerance,
                    "distance": None,
                    "passed": False,
                    "error": "coefficient is not numeric",
                }
                continue

            distance = abs(observed - expected)
            passed = distance <= tolerance
            if passed:
                passed_weight += weight

            details[key] = {
                "expected": expected,
                "observed": observed,
                "tolerance": tolerance,
                "distance": distance,
                "passed": passed,
            }
            if len(rows) > 1:
                details[key]["warning"] = (
                    f"{len(rows)} rows matched (model, variable); used the first"
                )

    return {
        "score": passed_weight / total_weight if total_weight else 1.0,
        "n_coefficients": len(details),
        "n_passed": sum(1 for d in details.values() if d["passed"]),
        "coefficient_details": details,
    }


def score_submission(
    submission_dir: str | Path,
    benchmark_path: str | Path,
    ground_truth_path: str | Path | None = None,
) -> dict[str, Any]:
    submission_dir = Path(submission_dir)
    benchmark = load_yaml(benchmark_path)

    component_scores = {}

    component_scores["required_files"] = score_required_files(
        submission_dir,
        benchmark.get("required_outputs", []),
    )

    if "crosswalk_file" in benchmark:
        component_scores["variable_crosswalk"] = score_variable_crosswalk(submission_dir, benchmark)

    for name, artifact_spec in benchmark.get("tabular_artifacts", {}).items():
        component_scores[name] = score_tabular_artifact(submission_dir, artifact_spec)

    for name, artifact_spec in benchmark.get("text_artifacts", {}).items():
        component_scores[name] = score_text_artifact(submission_dir, artifact_spec)

    if ground_truth_path is not None:
        ground_truth = load_yaml(ground_truth_path)
        component_scores["coefficient_accuracy"] = score_coefficients(submission_dir, ground_truth)

    weights = benchmark.get("scoring_weights", {})
    if not weights:
        weights = {k: 1.0 / len(component_scores) for k in component_scores}

    # M4: warn on mismatches between scoring_weights and computed components,
    # so misconfigured keys don't silently drop components from the total.
    missing_in_scores = sorted(set(weights) - set(component_scores))
    missing_in_weights = sorted(set(component_scores) - set(weights))
    if missing_in_scores:
        print(f"WARNING: scoring_weights keys with no matching component: {missing_in_scores}")
    if missing_in_weights:
        print(f"WARNING: components with no scoring_weights entry (weight 0): {missing_in_weights}")

    total_weight = sum(weights.get(k, 0.0) for k in component_scores)
    total = 0.0 if total_weight == 0 else (
        sum(component_scores[k]["score"] * weights.get(k, 0.0) for k in component_scores) / total_weight
    )

    return {
        "benchmark_id": benchmark.get("benchmark_id"),
        "benchmark_name": benchmark.get("benchmark_name"),
        "total_score": float(total),
        "component_scores": component_scores,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score an empirical-analysis benchmark submission.")
    parser.add_argument("--benchmark", required=True, help="Path to benchmark YAML.")
    parser.add_argument("--submission", required=True, help="Path to submission directory.")
    parser.add_argument("--output", required=False, help="Optional JSON output path.")
    parser.add_argument(
        "--ground-truth",
        required=False,
        help="Path to judge-only ground-truth YAML with expected coefficients.",
    )

    args = parser.parse_args()

    result = score_submission(args.submission, args.benchmark, ground_truth_path=args.ground_truth)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
