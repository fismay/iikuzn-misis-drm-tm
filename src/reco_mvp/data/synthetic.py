from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts.validate import validate_bundle


def make_synthetic_bundle(seed: int = 42) -> dict:
    """Build deterministic, non-production data with temporal cross-sell signals."""
    # seed is part of the API; this construction is deliberately deterministic.
    _ = seed
    customers = []
    for i in range(60):
        segment = "B2B" if i % 3 == 0 else "B2C" if i % 3 == 1 else "unknown"
        customers.append({"customer_id": f"C{i+1:03d}", "segment": segment, "known_since": None})
    customers.append({"customer_id": "C061", "segment": "B2C", "known_since": "2026-05-20T00:00:00Z"})
    products = []
    for i in range(15):
        products.append({
            "product_id": f"P{i+1:02d}", "name": f"Synthetic product {i+1}",
            "category": "core" if i < 5 else "addon", "eligible_segments": ["B2B"] if i == 10 else None,
            "active": False if i == 14 else True,
            "available_from": "2026-05-15T00:00:00Z" if i == 13 else None,
        })
    transactions = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    n = 0
    for i in range(60):
        cid = f"C{i+1:03d}"
        segment_offset = 0 if i % 3 == 0 else 1
        purchased = [1 + (i % 5), 3 + segment_offset]
        if i % 4 == 0:
            purchased.append(5)
        for j, product_no in enumerate(purchased):
            n += 1
            transactions.append({"transaction_id": f"T{n:04d}", "order_id": f"O{i+1:03d}-{j+1}",
                "customer_id": cid, "product_id": f"P{product_no:02d}",
                "purchased_at": (base + timedelta(days=i * 2 + j)).isoformat().replace("+00:00", "Z"),
                "quantity": 1.0})
        if i % 5 == 0:
            n += 1
            transactions.append({"transaction_id": f"T{n:04d}", "order_id": f"O{i+1:03d}-future",
                "customer_id": cid, "product_id": "P06",
                "purchased_at": (datetime(2026, 6, 10, tzinfo=timezone.utc) + timedelta(days=i % 10)).isoformat().replace("+00:00", "Z"),
                "quantity": 1.0})
    bundle = {"schema_version": "1.0", "data_mode": "synthetic", "customers": customers,
        "products": products, "transactions": transactions,
        "metadata": {"observation_end": "2026-07-01T00:00:00Z", "warnings": [
            "Synthetic data only; no business effect is inferred.",
            "Catalog history is incomplete by design."], "segment_source": "synthetic",
            "catalog_history_available": False}}
    return validate_bundle(bundle)

