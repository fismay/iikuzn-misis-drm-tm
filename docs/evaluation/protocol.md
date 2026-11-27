# Evaluation Protocol (`reco_mvp.evaluation`)

This document specifies the independent time-based cross-sell backtest protocol implemented by Contributor 3 (`src/reco_mvp/evaluation/`) under Shared Contract v1 (`contracts/CONTRACT.md`).

## 1. Public API and Artifacts

```python
from reco_mvp.evaluation import evaluate

metrics = evaluate(
    bundle,
    as_of="2026-06-01T00:00:00Z",
    horizon_days=30,
    output_dir="artifacts/demo",
    strategies=("popular", "graph_only", "hybrid"),
)
```

`evaluate(...)` requires the real `reco_mvp.recommender.Recommender` from Contributor 2 (`feature/02-recommender`). Production code contains **no hidden fallback stub**: if `reco_mvp.recommender` is absent, `evaluate()` raises a clear `ImportError`.

### Generated Artifacts in `output_dir` (Git-ignored)
1. `metrics.json` — full contract output (`schema_version`, `data_mode`, `protocol`, `warnings`, `rows`).
2. `metrics.csv` — UTF-8 CSV table of `rows` (empty groups serialize `null` metrics as blank cells `""`, never `0`).
3. `recommendations.jsonl` — UTF-8 JSONL containing unmodified `Recommender.recommend()` responses for every evaluated **main-cohort** `(customer_id, strategy)` request (one line per pair; `simulated_cold` responses are never mixed into this file).
4. `evaluation_report.md` — Markdown report with a prominent `DATA MODE` header, limitations, main/segment/cohort tables, cold-start fill-rate comparison, excluded future purchases breakdown, scope boundaries, and next online experiment proposal.

---

## 2. Temporal Split and Leakage Prevention

All timestamp comparisons use parsed timezone-aware UTC `datetime` objects (`ISO 8601` with `Z` or explicit offset).

- **Training history**: strictly `purchased_at < as_of`.
- **Evaluation horizon**: `[as_of, as_of + horizon_days)`.
- **Post-window**: `purchased_at >= as_of + horizon_days` (excluded from both train and targets).
- **Observation coverage guard**: `metadata.observation_end >= as_of + horizon_days` is mandatory; otherwise `evaluate()` raises `ValueError` rather than silently reporting an incomplete backtest.
- **Physical isolation of `fit()`**: before calling `Recommender.fit(train_bundle, as_of=as_of)`, the evaluator constructs a deep-copied, schema-validated `train_bundle` containing:
  - only transactions with `purchased_at < as_of`;
  - only main-cohort customers known prior to `as_of`;
  - only point-in-time eligible catalog products (plus historically purchased non-eligible products forced to `active=False` solely for referential integrity);
  - `metadata.observation_end` clamped to `as_of`.
- **No split orders across cutoff**: if an `order_id` appears in `purchased_at < as_of`, any transaction of the same `(customer_id, order_id)` occurring in `[as_of, as_of + horizon_days)` is excluded from future targets (`split_order_across_cutoff`) with an explicit warning.

---

## 3. Point-in-Time Historical Catalog Rules

1. Products with `active=False` are excluded. Products with `active=None` are retained with an explicit warning.
2. Products with explicit `available_from`: admitted iff `available_from <= as_of` (including products with zero historical sales). Products with `available_from > as_of` are physically removed from `train_bundle` and excluded from targets (`late_product_available_after_cutoff`).
3. Products with `available_from=None`: conservatively admitted iff observed in at least one transaction strictly before `as_of`; unobserved products are excluded (`unobserved_without_available_from`).
4. Segment eligibility:
   - `eligible_segments=None` means no restriction was supplied by the source (admitted with a warning; universal compatibility is not claimed).
   - Explicit `eligible_segments` permits a segment only if present in the list (`unknown` customers are permitted only when `"unknown"` is explicitly listed).
5. When `metadata.catalog_history_available=False`, the report explicitly notes that current `active` and `eligible_segments` flags approximate historical state and do not constitute a full historical catalog snapshot.

---

## 4. Cohorts and Order Counting

### Main Cohort (`cohort="all"`)
Includes:
- all customers with at least one transaction strictly before `as_of`, plus
- customers with zero historical transactions who have `known_since < as_of` (`natural_cold`).

Customers with no pre-cutoff transactions and `known_since is None` or `known_since >= as_of` are excluded retroactively (`excluded_future_new_customers`).

Historical order count determines sub-cohorts:
- `natural_cold`: 0 historical orders (`known_since < as_of`).
- `one_purchase`: 1 distinct historical order.
- `warm`: 2 or more distinct historical orders.

Distinct orders are counted by non-empty `order_id`. If `order_id` is missing, distinct UTC purchase dates are used as a documented approximation (`order_count_method` + warning).

### Simulated Cold Start (`cohort="simulated_cold"`)
Evaluated **separately** from main `all` metrics:
1. Deterministically hold out a stratified fraction (default `fraction=0.2`, `seed=42`) of historical buyers (`one_purchase` / `warm`), leaving remaining buyers for refitting.
2. Physically remove held-out customers and all their pre-cutoff transactions from `sim_train_bundle`.
3. **Freeze the catalog** from the original `train_bundle["products"]` snapshot before hiding histories (a product bought only by held-out customers remains in the catalog).
4. Refit a fresh model on `sim_train_bundle` and query `recommend(customer_id, segment=known_segment, k=3, strategy=strategy)`.
5. Keep cross-sell targets anchored to each customer's **original true pre-cutoff history** so hiding history never turns repeat purchases into cross-sell hits.
6. Never include `simulated_cold` rows in `cohort="all"`.

---

## 5. Cross-Sell Target Definition & Metric Formulas

For customer $i$ in the evaluated cohort, the target set $T_i$ consists of unique products bought in `[as_of, as_of + horizon_days)` that:
- were not purchased by customer $i$ before `as_of`;
- do not belong to a split pre-cutoff `order_id`;
- belong to the point-in-time eligible catalog at `as_of` for customer $i$'s segment.

Every excluded future transaction is counted by reason (`not_in_main_cohort`, `split_order_across_cutoff`, `repeat_purchase`, `inactive_product`, `late_product_available_after_cutoff`, `unobserved_without_available_from`, `segment_ineligible`).

Let $R_i$ be the ordered list of up to 3 **admissible** unique recommended product IDs returned for customer $i$ (structural contract errors raise `ValueError`; inadmissible items are excluded from $R_i$ and logged in `filter_violations`). Over a slice of $N$ customers with slice-eligible catalog $C$:

- **Empty slice ($N = 0$)**: `n_customers = 0`, `n_with_future_purchase = 0`, all five metrics are `null` (`None`).
- **Non-empty slice ($N > 0$)**:
  - $\text{Precision@3} = \frac{1}{N} \sum_{i=1}^{N} \frac{|R_i \cap T_i|}{3}$ (fixed denominator 3; customers with $T_i = \varnothing$ contribute $0$).
  - $\text{HitRate@3} = \frac{1}{N} \sum_{i=1}^{N} \mathbb{I}[|R_i \cap T_i| > 0]$.
  - $\text{FillRate@3} = \frac{1}{3N} \sum_{i=1}^{N} |R_i|$.
  - $\text{FullListRate} = \frac{1}{N} \sum_{i=1}^{N} \mathbb{I}[|R_i| = 3]$.
  - $\text{CatalogCoverage} = \frac{|\bigcup_{i=1}^{N} (R_i \cap C)|}{|C|}$ if $|C| > 0$, else `null`.

### Baseline Comparison & Uncertainty
- Changes vs `popular` report both **absolute difference in percentage points (pp)** and **relative lift (%)**. When `baseline == 0`, relative lift is `null` (`undefined (baseline = 0)`), never infinity.
- Paired customer-level percentile bootstrap (default 500 resamples, `seed=42`) estimates a 95% confidence interval for $\text{Precision@3}(\text{candidate}) - \text{Precision@3}(\text{popular})$ on the main `all` cohort.

---

## 6. Cutoff Policy for Synthetic vs Real Data

- **Synthetic demo**: fixed cutoff `2026-06-01T00:00:00Z`, `horizon_days=30`, `observation_end=2026-07-01T00:00:00Z`.
- **Real data**: use a pre-registered cutoff covering `[as_of, as_of + horizon_days) <= observation_end`. If hyperparameters are tuned, use an earlier validation cutoff and reserve the final test cutoff for a single un-tuned check; never select a cutoff that maximizes reported metrics.
