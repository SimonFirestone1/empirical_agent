#!/usr/bin/env python3
"""Checked join helpers.

A join whose realized cardinality differs from the cardinality you intended is
an ERROR, not something to silently repair by deduplicating or dropping rows.
safe_merge() forces you to declare the intended cardinality and raises the
moment the keys violate it, so the failure surfaces with a stack trace instead
of corrupting N downstream.

Use safe_merge for pandas. For Polars or SQL joins, assert row counts with
assert_rowcount() around the join instead.
"""
from __future__ import annotations

import pandas as pd

_VALID = {"1:1", "1:m", "m:1", "m:m"}


def safe_merge(left, right, *, validate=None, on=None, how="inner",
               left_on=None, right_on=None, report=True, **kwargs):
    """pd.merge with a MANDATORY cardinality contract.

    validate: one of '1:1', '1:m', 'm:1', 'm:m' -- required. pandas raises
        MergeError when the keys do not satisfy it. ('m:m' still forces you to
        declare that you expect a many-to-many fan-out, rather than getting one
        by accident.)
    report: print a one-line merge-success breakdown (matched / left_only /
        right_only) and row counts -- the artifact Stage 4 asks for.

    Raises ValueError if validate is missing/invalid; pandas.errors.MergeError
    if the realized cardinality differs from the declared one.
    """
    if validate not in _VALID:
        raise ValueError(
            "safe_merge requires an explicit validate= cardinality, one of "
            f"{sorted(_VALID)}. Declare the join you intend; do not omit it."
        )

    n_left, n_right = len(left), len(right)

    # Pick an indicator column name that cannot collide with existing columns.
    existing = set(map(str, left.columns)) | set(map(str, right.columns))
    indicator_col = "_merge_src"
    while indicator_col in existing:
        indicator_col = "_" + indicator_col

    out = pd.merge(
        left, right, on=on, how=how, left_on=left_on, right_on=right_on,
        validate=validate, indicator=indicator_col, **kwargs,
    )
    breakdown = out[indicator_col].value_counts().to_dict()
    out = out.drop(columns=indicator_col)

    if report:
        print(
            f"[safe_merge] how={how} validate={validate} "
            f"left={n_left} right={n_right} out={len(out)} "
            f"match={ {str(k): int(v) for k, v in breakdown.items()} }"
        )
    return out


def assert_rowcount(df, expected, *, name="result", tol=0):
    """Assert a join/op produced the expected row count (Polars/SQL equivalent
    of safe_merge's contract). Raises AssertionError on violation.

    expected: intended row count. tol: allowed absolute deviation.
    """
    try:
        n = df.height  # Polars
    except AttributeError:
        n = len(df)    # pandas / list / etc.
    if abs(n - expected) > tol:
        raise AssertionError(
            f"{name}: expected {expected} rows (+/- {tol}), got {n}. "
            f"Investigate the join keys; do not coerce to the expected shape."
        )
    return df
