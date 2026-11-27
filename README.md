# Reco MVP

Auditable cross-sell recommendation MVP for the four-agent hackathon team.

## Current status

Agent 1 bootstrap and data contract are ready. The repository deliberately uses
synthetic data until an authorized source mapping is available. The stated
business uplift numbers are hypotheses, not measured results; the project must
not claim revenue impact without prices and exposure events.

## Start here (for every future AI)

1. Read `contracts/CONTRACT.md` before changing interfaces.
2. Read `docs/handoffs/01-data.md` and the role prompt assigned to you.
3. Stay inside your owned paths from the prompt. Do not edit another agent's
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

- Agent 1: root files, `contracts/`, `src/reco_mvp/data/`, data tests/docs/CI.
- Agent 2: `src/reco_mvp/recommender/` and recommender tests/docs.
- Agent 3: `src/reco_mvp/evaluation/` and evaluation tests/docs.
- Agent 4: `app/` and integration/demo tests/docs.

Each agent uses a separate clone/checkout. Merge PRs in order 1 → 2 → 3 → 4.
Do not force-push, reset hard, mass-clean, or silently resolve ownership
conflicts. If you need another agent's file, document the exact interface issue
in your handoff instead.

## Contract-aware handoff

Later agents consume only validated `bundle.json`; their modules must not read
raw files. Recommendation explanations must be based on actual model output.
Evaluation must respect temporal boundaries and report null for empty groups.
See the contract and handoff for the complete details.

