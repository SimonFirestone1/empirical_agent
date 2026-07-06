"""
Wallace AHS real-options replication - analysis code.

Reads the pre-staged AHS national PUF waves from the benchmark's local data
directory (populated once via `python -m econbench.data`). No downloading
happens here.

Outputs (written next to this script):
  - sample_construction_log.csv
  - regression_tables.csv
"""

from __future__ import annotations

from pathlib import Path
import os
import time

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get(
        "ECONBENCH_DATA",
        HERE / ".." / ".." / ".." / "benchmarks" / "wallace" / "data",
    )
).resolve()

WAVES = {
    1985: ("ahs_1985_national/ahs1985n.csv", "NEWADD"),
    1987: ("ahs_1987_national/ahs1987n.csv", "NEWADD"),
    1989: ("ahs_1989_national/ahs1989n.csv", "NEWADD"),
    1991: ("ahs_1991_national/ahs1991n.csv", "NEWADD"),
    1993: ("ahs_1993_national/ahs1993n.csv", "NEWADD"),
    1995: ("ahs_1995_national/ahs1995n.csv", "RAN"),
    1997: ("ahs_1997_national/household.csv", "RAN"),
}

BASE_COLS = ["CONTROL", "ISTATUS", "TENURE", "NUNIT2", "VALUE", "ZINC", "AMTX", "BUILT", "REGION", "SMSA"]

# "No renovation" codes per source variable; blanks/negatives are treated as
# no report. NEWADD=2 is the dominant "no" category in 1985-1993; RAN uses
# 99 (1995) and -6/0 (1997) for not-applicable/none.
NO_RENO = {"NEWADD": {"", "2"}, "RAN": {"", "99", "0", "-6"}}


def load_wave(year: int, log_rows: list[dict]) -> pd.DataFrame:
    filename, reno_col = WAVES[year]
    df = pd.read_csv(DATA_DIR / filename, usecols=BASE_COLS + [reno_col], quotechar="'", dtype=str)
    df = df.apply(lambda s: s.str.strip())

    def log_step(step: str, description: str, before: int, after: int) -> None:
        log_rows.append({
            "step": step, "description": description, "wave": year,
            "rows_before": before, "rows_after": after, "rows_dropped": before - after,
        })

    n0 = len(df)
    df = df[df["ISTATUS"] == "1"]
    log_step("interview_status", "Keep regular completed interviews (ISTATUS=1)", n0, len(df))

    n1 = len(df)
    df = df[df["TENURE"] == "1"]
    log_step("owner_occupied", "Keep owner-occupied units (TENURE=1)", n1, len(df))

    n2 = len(df)
    df = df[df["NUNIT2"] == "1"]
    log_step("single_family_detached", "Keep single-family detached homes (NUNIT2=1)", n2, len(df))

    for col in ["VALUE", "ZINC", "AMTX"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["renovation"] = (~df[reno_col].isin(NO_RENO[reno_col])).astype(int)
    # BUILT is a decade category code in 1985-1995 and a 4-digit year in 1997;
    # harmonize as wave-specific structure-age category fixed effects
    # (categorical_bins transformation).
    df["built_cat"] = f"{year}_" + df["BUILT"]

    n3 = len(df)
    df = df[
        (df["VALUE"] > 0)
        & (df["ZINC"] > 0)
        & (df["AMTX"] >= 0)
        & (df["BUILT"] != "")
        & (df["REGION"] != "")
    ].copy()
    log_step("nonmissing_analysis_vars", "Drop missing/invalid value, income, tax, year built, region", n3, len(df))

    df["wave"] = year
    return df[["CONTROL", "wave", "renovation", "VALUE", "ZINC", "AMTX", "built_cat", "REGION", "SMSA"]]


def main() -> None:
    start = time.time()
    log_rows: list[dict] = []
    waves = []
    for year in WAVES:
        t = time.time()
        waves.append(load_wave(year, log_rows))
        print(f"{year}: {len(waves[-1]):,} obs ({time.time() - t:.1f}s)")

    panel = pd.concat(waves, ignore_index=True)
    pd.DataFrame(log_rows).to_csv(HERE / "sample_construction_log.csv", index=False)

    panel["log_value"] = np.log(panel["VALUE"])
    panel["log_income"] = np.log(panel["ZINC"])
    panel["log_tax"] = np.log1p(panel["AMTX"])

    # Wave effects are absorbed by the wave-specific built_cat dummies, so
    # C(wave) is omitted from the specs to avoid perfect collinearity.
    # Uncertainty proxy: within metro-area-by-wave dispersion (std) of log
    # house value; option-value proxy interacts it with house value.
    grp = panel.groupby(["SMSA", "wave"])["log_value"]
    panel["uncertainty"] = grp.transform("std")
    panel = panel[panel["uncertainty"].notna() & (grp.transform("count") >= 30)].copy()
    panel["option_value_proxy"] = panel["uncertainty"] * panel["log_value"]

    print(panel.groupby("wave")["renovation"].mean().round(3).to_dict())
    print(f"Pooled estimation sample: {len(panel):,} obs, renovation rate {panel['renovation'].mean():.3f}")

    specs = {
        "m1_logit_baseline": ("logit", "renovation ~ log_value + log_income + log_tax + C(built_cat)"),
        "m2_logit_geo_wave": ("logit", "renovation ~ log_value + log_income + log_tax + C(built_cat) + C(REGION)"),
        "m3_logit_uncertainty": ("logit", "renovation ~ log_value + log_income + log_tax + C(built_cat) + C(REGION) + uncertainty"),
        "m4_logit_option_value": ("logit", "renovation ~ log_value + log_income + log_tax + C(built_cat) + C(REGION) + uncertainty + option_value_proxy"),
        "m5_lpm_robustness": ("ols", "renovation ~ log_value + log_income + log_tax + C(built_cat) + C(REGION) + uncertainty + option_value_proxy"),
    }

    rows = []
    for model_name, (kind, formula) in specs.items():
        t = time.time()
        fit_fn = smf.logit if kind == "logit" else smf.ols
        result = fit_fn(formula, data=panel).fit(disp=0) if kind == "logit" else fit_fn(formula, data=panel).fit()
        for variable in result.params.index:
            rows.append({
                "model": model_name,
                "variable": variable,
                "coefficient": result.params[variable],
                "std_error": result.bse[variable],
                "p_value": result.pvalues[variable],
                "n_obs": int(result.nobs),
            })
        print(f"{model_name}: n={int(result.nobs):,} ({time.time() - t:.1f}s)")

    pd.DataFrame(rows).to_csv(HERE / "regression_tables.csv", index=False)
    print(f"Done in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
