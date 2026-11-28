"""Сборка Bundle из строк книги организаторов для штатного Recommender."""
from __future__ import annotations


def bundle_from_official(dataset, item_names: dict, item_groups: dict) -> dict:
    """Bundle из обучающих строк официального окна (каталог — текущий снимок)."""
    customers = [{"customer_id": cid, "segment": dataset.client_group.get(cid, "unknown"),
                  "known_since": None} for cid in sorted(dataset.history)]
    products = [{"product_id": pid, "name": item_names.get(pid, pid), "category": None,
                 "eligible_segments": None, "active": None,
                 "available_from": "2024-01-01T00:00:00Z",
                 "product_group": item_groups.get(pid)}
                for pid in sorted(set(item_names))]
    transactions = [{"transaction_id": f"t{i}", "order_id": f"{row['order_id']}|{row['order_week']}",
                     "customer_id": row["client_id"], "product_id": row["item_id"],
                     "purchased_at": row["order_week"] + "T00:00:00Z", "quantity": 1.0}
                    for i, row in enumerate(dataset.train_rows)]
    return {"schema_version": "1.0", "data_mode": "real", "customers": customers,
            "products": products, "transactions": transactions,
            "metadata": {"observation_end": dataset.test_start + "T00:00:00Z",
                         "warnings": [], "segment_source": "client_group",
                         "catalog_history_available": False}}
