# Agent 1 handoff

## Delivered

- Shared Bundle v1 in `contracts/CONTRACT.md` and a standard-library validator.
- `reco_mvp.data.make_synthetic_bundle`, `load_bundle`, and `prepare_dataset`.
- Deterministic synthetic data: 61 customers, 15 products, B2B/B2C/unknown,
  repeated timed purchases, segment restriction, inactive product, future-only
  catalog product, and a full 30-day cutoff window ending 2026-07-01.
- Root packaging, dependencies, tests, and CI-safe project layout.
- Synthetic warm/cold-start targets with a separate future-only product, plus
  real-loader quality counts for filtered returns/cancellations.
- Detailed README for future agents and this handoff.

## Known data limitation

The Drive source could not be opened in the available environment and the GitHub
repository was empty at bootstrap time. No client rows were copied. Real mode
requires an explicit mapping, preserves mapped catalog fields, and records
filtered unsuccessful rows in `quality_report.json`; it never silently falls
back to synthetic data.

## For agents 2–4

Start from `origin/main` after the bootstrap, then create the role branch from
that SHA. Use `reco_mvp.data.load_bundle` and the Bundle contract; do not read
source files inside the recommender/evaluator. Synthetic demo cutoff:
`2026-06-01T00:00:00Z`, horizon 30 days. Treat warnings and incomplete catalog
history as visible limitations.

## Checks

`python -m pytest tests -q` and `python -m pip install -e .` are the expected
checks for this branch. The full pipeline and UI belong to agents 2–4.
