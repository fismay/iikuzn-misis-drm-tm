# Reco MVP

Auditable cross-sell recommendation MVP for the four-part hackathon team.

## Current status

Contributor 1 bootstrap and data contract are ready. The repository deliberately uses
synthetic data until an authorized source mapping is available. The stated
business uplift numbers are hypotheses, not measured results; the project must
not claim revenue impact without prices and exposure events.

## Start here (for future contributors)

1. Read `contracts/CONTRACT.md` before changing interfaces.
2. Read `docs/handoffs/01-data.md` and the role prompt assigned to you.
3. Stay inside your owned paths from the prompt. Do not edit another contributor's
   module, root configuration, contracts, or this README.
4. Preserve string IDs, timezone-aware timestamps, deterministic sorting, and
   warnings about unknown data. Never commit real exports, credentials, `.env`,
   client identifiers, or generated `artifacts/`.
5. Run the narrowest relevant test after each change and report exact commands
   and output. A passing local module is not proof that the whole project works.

## Installation and data smoke test

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest tests -q
python -c "from reco_mvp.data import prepare_dataset; prepare_dataset(None, 'artifacts/data', synthetic=True)"
```

The data API is `make_synthetic_bundle`, `load_bundle`, and `prepare_dataset`.
Real mode requires an explicit mapping and never falls back to synthetic data.
The demonstration cutoff is `2026-06-01T00:00:00Z`; observation ends at least
30 days later in the synthetic bundle.

## Ownership and merge order

- Contributor 1: root files, `contracts/`, `src/reco_mvp/data/`, data tests/docs/CI.
- Contributor 2: `src/reco_mvp/recommender/` and recommender tests/docs.
- Contributor 3: `src/reco_mvp/evaluation/` and evaluation tests/docs.
- Contributor 4: `app/` and integration/demo tests/docs.

Each contributor uses a separate clone/checkout. Merge PRs in order 1 → 2 → 3 → 4.
Do not force-push, reset hard, mass-clean, or silently resolve ownership
conflicts. If you need another contributor's file, document the exact interface issue
in your handoff instead.

## Contract-aware handoff

Later contributors consume only validated `bundle.json`; their modules must not read
raw files. Recommendation explanations must be based on actual model output.
Evaluation must respect temporal boundaries and report null for empty groups.
See the contract and handoff for the complete details.

## Architecture decisions

### Chosen architecture

The MVP is a small Python `src/` package with four deliberately separated
layers:

1. **Data layer (`reco_mvp.data`)** normalizes an authorized CSV/XLSX export into
   one JSON-compatible Bundle and writes a quality report.
2. **Recommender (`reco_mvp.recommender`)** will consume only the validated
   Bundle and implement popular, graph-only, and hybrid strategies.
3. **Evaluation (`reco_mvp.evaluation`)** will perform a time-based cross-sell
   backtest and emit auditable JSON/CSV/JSONL/Markdown artifacts.
4. **Demo/integration (`app/`)** will connect those modules without changing
   their contracts.

The Bundle is the seam between contributors. It keeps IDs as strings, timestamps
timezone-aware, warnings visible, and `synthetic` versus `real` explicit. This
allows contributors 2–4 to work against deterministic data without pretending that
unavailable source data exists.

### Why these technologies

- Python 3.11+ keeps the one-day implementation and demo simple.
- JSON/CSV/XLSX are enough for the supplied data shape and are easy to inspect.
- pandas/openpyxl are declared for tabular input; the core contract validator
  uses only the standard library.
- A plain co-purchase graph in memory is sufficient for this scale; it is
  explainable and easy to backtest.
- pytest and GitHub Actions provide a small repeatable safety net before merge.

### Explicitly rejected for this MVP

- **LLM calls/neural networks:** no evidence or time budget justifies them, and
  deterministic explanations are more important for a judging demo.
- **Graph database:** the task needs a small derived graph, not operational
  graph storage; an in-memory representation avoids deployment failure points.
- **Microservices/Kubernetes/separate frontend/backend:** they add interfaces
  and setup time without improving the requested recommendation proof.
- **Automatic synthetic fallback in real mode:** it would hide missing access or
  schema errors and invalidate the demo's claims.
- **Revenue/uplift claims:** prices and exposure events are absent, so the
  stated 15–25% and 10–15% figures remain hypotheses only.
- **Blind column autodetection:** ambiguous mappings can silently corrupt a
  backtest; real inputs require an explicit mapping JSON.

## Data flow and reproducibility

```text
authorized CSV/XLSX + mapping.json
              |
              v
  reco_mvp.data.prepare_dataset
       |                 |
       v                 v
   bundle.json      quality_report.json
       |
       +--> recommender.fit(bundle, as_of)
       +--> evaluation.evaluate(bundle, as_of, horizon_days)
       +--> Streamlit demo / artifacts (ignored by Git)
```

The synthetic cutoff is `2026-06-01T00:00:00Z` with a 30-day horizon and
`observation_end` of `2026-07-01T00:00:00Z`. Any real run must state its own
cutoff, horizon, source coverage, timezone assumption, and catalog-history
limitations. Generated artifacts stay under ignored `artifacts/`.

## Contributor continuation checklist

Before editing:

```bash
git status --short --branch
git log --oneline -3
git remote -v
sed -n '1,240p' contracts/CONTRACT.md
sed -n '1,240p' docs/handoffs/01-data.md
```

After editing:

```bash
git diff --check
python -m pytest tests -q
git diff --cached --name-only
```

Add only owned paths to each commit. The merge order remains contributor 1, then 2,
then 3, then 4. If a later contributor needs a contract change, document the exact
request in its handoff rather than editing `contracts/` opportunistically.
