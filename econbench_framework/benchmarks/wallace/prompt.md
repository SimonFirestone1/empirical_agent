# Wallace Replication Benchmark Prompt

You are a data-analysis agent. Your task is to implement a replication-style empirical analysis based on a methods-only specification. You must not use or search for the paper's empirical results, coefficient estimates, standard errors, p-values, conclusions, or interpretation of findings.

## Research objective

Evaluate whether homeowner renovation/addition behavior can be modeled as an investment-timing decision under uncertainty, using a real-options framework and American Housing Survey microdata.

## Conceptual framework

Treat a major home renovation, addition, or replacement as a partially irreversible investment. The homeowner has the option to invest now or wait. Waiting has value when future housing values, household conditions, or local market conditions are uncertain. Therefore, a homeowner may delay renovation even when expected net present value is positive because exercising the renovation option destroys the value of waiting.

A standard net-present-value model predicts investment when expected benefits exceed costs. A real-options model adds an investment threshold: because renovation is costly and at least partly irreversible, the expected payoff must exceed cost by enough to compensate the owner for giving up the option to wait. Higher uncertainty should raise the investment threshold and reduce the probability of immediate renovation, all else equal.

## Data

Use the national American Housing Survey biennial waves: 1985, 1987, 1989, 1991, 1993, 1995, and 1997.

The raw data has already been downloaded and staged locally. Do not download, search for, or re-acquire AHS data. Each wave's national PUF CSV files are extracted under the benchmark's `data/` directory, one subdirectory per wave:

```text
data/ahs_1985_national/
data/ahs_1987_national/
data/ahs_1989_national/
data/ahs_1991_national/
data/ahs_1993_national/
data/ahs_1995_national/
data/ahs_1997_national/
```

Read the data directly from these directories.

## Unit of observation

Housing-unit-by-survey-wave observation, linked longitudinally where possible using the AHS housing-unit identifier.

## Sample

Restrict to owner-occupied units, single-family detached homes, regular completed interviews, and observations with nonmissing variables needed for the analysis.

## Operationalization

Use AHS panel observations to identify whether a homeowner undertook a major addition, replacement, or renovation between survey waves. Model this decision as the observed exercise of a housing-investment option.

Construct or harmonize:

- Renovation/addition indicator
- House value
- Property taxes
- Household income
- Year built / age of structure
- Metropolitan/geographic identifier
- Recent mover indicator
- Listed-for-sale indicator
- Routine maintenance or improvement expenditure variables, if available
- Uncertainty proxy
- Option-value proxy

## Recommended econometric structure

Estimate discrete-choice models where the dependent variable is the renovation/addition indicator. Suitable models include logit, probit, or linear probability models used as robustness checks. Include controls for household resources, property characteristics, geography, time/wave effects, and housing-market conditions where feasible.

Minimum model progression:

1. Baseline model with household and property controls.
2. Add geography and wave controls.
3. Add uncertainty proxy.
4. Add option-value proxy or uncertainty/incentive interactions.
5. Robustness check using an alternative model form or alternative uncertainty proxy.

## Required deliverables

- `replication_plan.md`
- `variable_crosswalk.csv`
- `sample_construction_log.csv`
- `analysis_code.py`
- `regression_tables.csv`
- `replication_memo.md`
- `limitations.md`

## Restrictions

Do not use the original paper's coefficient estimates, standard errors, p-values, tables, or conclusions. Do not search for summaries of the paper's results. Use the methods-only specification to construct and evaluate the empirical analysis independently.
