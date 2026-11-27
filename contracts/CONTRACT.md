# Shared contract v1

This document is the source of truth for contributors 1–4. Do not add mandatory fields
or rename public functions without agreement from all owners.

## Conventions

- Every ID is a string; leading zeroes are preserved.
- Timestamps are timezone-aware UTC ISO 8601 (`Z` or an explicit UTC offset).
- Empty segments become `unknown`; they are never inferred from names or orders.
- `active=false` products are excluded. `active=null` means unknown.
- `eligible_segments=null` means that the source supplied no restriction, not that
  universal compatibility was proved.

## Bundle

```json
{
  "schema_version": "1.0",
  "data_mode": "synthetic",
  "customers": [{"customer_id":"0001","segment":"B2B","known_since":null}],
  "products": [{"product_id":"P01","name":"Example","category":null,
    "eligible_segments":null,"active":true,"available_from":null}],
  "transactions": [{"transaction_id":"T01","order_id":null,"customer_id":"0001",
    "product_id":"P01","purchased_at":"2026-01-01T00:00:00Z","quantity":1.0}],
  "metadata": {"observation_end":"2026-07-01T00:00:00Z","warnings":[],
    "segment_source":"synthetic","catalog_history_available":false}
}
```

`transactions` contains normalized successful purchases after available cleanup.
Repeated purchases are not removed merely because customer/product/date match.

## Data API

```python
prepare_dataset(input_dir, output_dir, *, synthetic=False, mapping_path=None) -> dict
load_bundle(path) -> dict
make_synthetic_bundle(seed=42) -> dict
```

Synthetic mode is explicit only. Real mode must fail with a useful error when
required mapping or columns are missing; it must not silently fall back to data.

## Recommendation and evaluation APIs

Contributors 2 and 3 implement the exact signatures from the prompts:
`Recommender.fit/recommend` and `evaluate`. Their output must retain
`schema_version`, `data_mode`, warnings, deterministic ranks, and honest nulls
for empty metric groups. The complete original role prompts are preserved in
`C:\Users\heckl\hackathon\prompts` on the working host.

