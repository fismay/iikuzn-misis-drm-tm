# Contributor 3 Handoff — Independent Evaluation & Backtest

## 1. Delivered Scope (Owned Paths Only)

- `src/reco_mvp/evaluation/__init__.py` — public exports (`evaluate`, pure metric & protocol helpers)
- `src/reco_mvp/evaluation/metrics.py` — `compute_slice_metrics`, `customer_precision_at_k`, `compute_metric_delta` (pp vs relative lift, safe `baseline=0`), `bootstrap_precision_diff_ci`
- `src/reco_mvp/evaluation/protocol.py` — timezone-aware UTC parsing, `build_point_in_time_snapshot` (physical leakage barrier, split-order exclusion, conservative point-in-time catalog, cohort assignment, full exclusion accounting), `build_simulated_cold_split` (frozen catalog + original-history cross-sell labels), `validate_recommendation_response`
- `src/reco_mvp/evaluation/report.py` — writers for `metrics.json`, `metrics.csv`, `recommendations.jsonl`, and `evaluation_report.md`
- `src/reco_mvp/evaluation/runner.py` — `evaluate(bundle, *, as_of, horizon_days=30, output_dir, strategies=("popular", "graph_only", "hybrid"))` (no production stub fallback; fails with `ImportError` if `reco_mvp.recommender` is absent)
- `tests/evaluation/test_metrics.py` — hand-calculated 3-customer example, empty cohort (`null`), empty output, zero catalog denominator, zero-baseline lift, deterministic bootstrap CI
- `tests/evaluation/test_protocol_and_leakage.py` — window boundaries (`+03:00` UTC conversion, exact `as_of`, exact `horizon_end`), incomplete `observation_end`, split order across cutoff, late `available_from`, conservative `available_from=None`, unknown segment restrictions, future new customers, missing `order_id` fallback, `simulated_cold` isolation
- `tests/evaluation/test_evaluate_end_to_end.py` — missing recommender `ImportError`, full 4-artifact generation on synthetic bundle (using test-only stub), end-to-end 3-customer hand-calculated check, model error propagation & filter violation detection, structural contract checks, and direct `Recommender.fit` leakage test (auto-enabled once Contributor 2 is merged)
- `docs/evaluation/protocol.md` — full specification of formulas, temporal rules, cold-start protocol, and cutoff policy
- `docs/handoffs/03-evaluation.md` — this handoff

## 2. How to Run

### Unit & Contract Tests (No Contributor 2 Dependency Required)
```bash
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest tests -q
```

### Real Backtest (Requires Contributor 2 `reco_mvp.recommender` Merged)
```python
from reco_mvp.data import make_synthetic_bundle
from reco_mvp.evaluation import evaluate

metrics = evaluate(
    make_synthetic_bundle(),
    as_of="2026-06-01T00:00:00Z",
    horizon_days=30,
    output_dir="artifacts/eval_synthetic",
)
```

For real data (local ignored paths only; never commit real artifacts):
```python
from reco_mvp.data import load_bundle
from reco_mvp.evaluation import evaluate

bundle = load_bundle("artifacts/real/bundle.json")
metrics = evaluate(
    bundle,
    as_of="<PRE_REGISTERED_UTC_ISO_CUTOFF>",
    horizon_days=30,
    output_dir="artifacts/real",
)
```

## 3. Verification Status & Measured Results

### What Was Verified
1. **Local test suite (`python -m pytest tests -v`)**: `17 passed, 1 skipped` (only `test_real_recommender_fit_ignores_future_transactions_when_available` skipped because `reco_mvp.recommender` is not yet merged).
2. **Hand-calculated 3-customer manual verification (`data_mode="real"` fixture + pure metric unit test)**:
   - Pure metric test: `n=3`, `n_with_future_purchase=2`, `Precision@3=0.3333` (`1/3`), `HitRate@3=0.6667` (`2/3`), `FillRate@3=0.8889` (`8/9`), `FullListRate=0.6667` (`2/3`), `CatalogCoverage=1.0000`.
   - End-to-end 3-customer scripted fixture: `Precision@3=0.2222` (`2/9`), `HitRate@3=0.6667` (`2/3`), `FillRate@3=0.6667` (`2/3`), `FullListRate=0.3333` (`1/3`), `CatalogCoverage=1.0000`; `natural_cold` (`n=0`) emits `null` metrics; `simulated_cold` (`n=1`) does not credit repeat purchases as cross-sell hits.
3. **Synthetic bundle dry-run WITH TEST-ONLY STUB (`data_mode="synthetic"`, `as_of="2026-06-01T00:00:00Z"`, `horizon_days=30`)**:
   - Main cohort (`all`): `n_customers=61` (`one_purchase=0`, `warm=60`, `natural_cold=1`, `excluded_future_new_customers=0`).
   - Eligible historical catalog at cutoff: `6` of `15` products (`P01..P05` via pre-cutoff sales + `P14` via `available_from="2026-05-15T00:00:00Z" <= as_of`; `P15` inactive; `8` unobserved products with `available_from=None` excluded conservatively).
   - Future window `[2026-06-01T00:00:00Z, 2026-07-01T00:00:00Z)`: `12` raw future transactions, `0` valid cross-sell targets, `12` excluded as `unobserved_without_available_from` (all 12 future purchases in Contributor 1's synthetic generator are for `P06`, which has `available_from=None` and 0 pre-cutoff sales).
   - Cold-start fill rate with test stub:
     - `natural_cold` (`segment=all`, `n=1`): `graph_only` FillRate@3=`0.0000`, `hybrid`=`1.0000`, `popular`=`1.0000`.
     - `simulated_cold` (`segment=all`, `n=12`, excluded from `all`): `graph_only` FillRate@3=`0.0000`, `hybrid`=`1.0000`, `popular`=`1.0000`.
   - Note: These stub numbers verify pipeline/artifact mechanics ONLY and are NOT a real recommender evaluation.

### What Was NOT Verified (Blockers & Limitations)
- **BLOCKER — Final backtest NOT completed**: `reco_mvp.recommender.Recommender` (Contributor 2, branch `feature/02-recommender`) is not yet published on `origin` or merged into `main`. Final backtest results on synthetic data must be re-run once Contributor 2's branch is available/merged.
- **Real data unavailable**: No authorized real sales/catalog export or `local_mapping.json` is present in this workspace; real-data backtest could not be executed.
- **No business uplift or revenue conclusions**: `data_mode="synthetic"` proves nothing about real customers; prices and personal-account impression logs are absent.

## 4. Requested Minimal Fixes for Other Owners (Not Edited by Contributor 3)

### For Contributor 1 (`.github/workflows/tests.yml` — CI failure)
- **Problem**: GitHub Actions fails on `python -m pytest tests -q` with `No module named pytest` because `.github/workflows/tests.yml` runs only `python -m pip install -e .`, while `pytest>=8,<9` is listed in `requirements.txt` and not in `pyproject.toml`.
- **Minimal fix in `.github/workflows/tests.yml`**: add `- run: python -m pip install -r requirements.txt` immediately before `- run: python -m pip install -e .`.

### For Contributor 1 (`src/reco_mvp/data/synthetic.py` — synthetic signal audit finding)
- **Problem**: In `make_synthetic_bundle()` (bootstrap SHA `f4e2859`), all 12 future transactions (`2026-06-10..2026-06-19`) buy `P06`, which has `available_from=None` and zero pre-cutoff sales. Under Shared Contract v1's conservative catalog rule, all 12 future purchases are excluded as `unobserved_without_available_from`, yielding `0` valid cross-sell targets (`Precision@3 = 0.0` for every strategy). Additionally, all 60 historical buyers have `>=2` orders (`one_purchase=0`), and `P14` has `available_from="2026-05-15T00:00:00Z"` (before cutoff) rather than a post-cutoff launch date.
- **Minimal optional fix (if meaningful non-zero synthetic demo metrics are desired)**:
  1. Include pre-cutoff purchases/co-purchases of `P06` (and/or set `P06` + additional addon products with pre-cutoff history and cross-sell patterns in `[2026-06-01, 2026-07-01)`).
  2. Give a subset of historical customers exactly 1 pre-cutoff `order_id` so `one_purchase` is non-empty.
  3. Set one catalog product to `available_from="2026-06-15T00:00:00Z"` (`> as_of`) to exercise post-cutoff product exclusion directly in the default synthetic bundle.

### Integration Notes for Contributor 2 & Contributor 4
- **Contributor 2 (`reco_mvp.recommender`)**: `evaluate()` passes a deep-copied, point-in-time `train_bundle` to `Recommender(config).fit(train_bundle, as_of=as_of)`. For main cohort customers (present in `train_bundle["customers"]`), `evaluate()` calls `recommend(customer_id, k=3, strategy=strategy)`; for `simulated_cold` customers (removed from `train_bundle["customers"]`), `evaluate()` calls `recommend(customer_id, segment=known_segment, k=3, strategy=strategy)`.
- **Contributor 4 (`app/`)**: `recommendations.jsonl` contains unmodified model responses for the **main cohort** only (one line per `(customer_id, strategy)`), avoiding collisions with history-masked `simulated_cold` queries. Full `simulated_cold` metrics are in `metrics.json` / `metrics.csv` under `cohort="simulated_cold"`.
