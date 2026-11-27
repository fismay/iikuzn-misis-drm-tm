import json
from datetime import datetime

import pytest

from reco_mvp.data import load_bundle, make_synthetic_bundle, prepare_dataset


def test_synthetic_is_deterministic_and_referentially_valid():
    first = make_synthetic_bundle()
    second = make_synthetic_bundle()
    assert first == second
    assert len(first["customers"]) >= 60
    assert len(first["products"]) >= 15
    assert {row["segment"] for row in first["customers"]} == {"B2B", "B2C", "unknown"}
    assert all(isinstance(row["customer_id"], str) for row in first["customers"])


def test_roundtrip_and_explicit_real_mode(tmp_path):
    prepare_dataset(None, str(tmp_path), synthetic=True)
    loaded = load_bundle(str(tmp_path / "bundle.json"))
    assert loaded["data_mode"] == "synthetic"
    with pytest.raises(ValueError, match="mapping_path"):
        prepare_dataset(str(tmp_path), str(tmp_path / "real"))


def test_synthetic_contains_warm_targets_and_a_separate_future_only_case():
    bundle = make_synthetic_bundle()
    cutoff = datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    transactions = bundle["transactions"]

    def before(product_id):
        return [row for row in transactions
                if row["product_id"] == product_id
                and datetime.fromisoformat(row["purchased_at"].replace("Z", "+00:00")) < cutoff]

    def after(product_id):
        return [row for row in transactions
                if row["product_id"] == product_id
                and datetime.fromisoformat(row["purchased_at"].replace("Z", "+00:00")) >= cutoff]

    assert len({row["customer_id"] for row in before("P07")}) > 0
    assert len(after("P07")) > 0
    assert len(after("P06")) > 0
    assert before("P06") == []
    assert sum(1 for cid in {row["customer_id"] for row in transactions}
               if sum(row["customer_id"] == cid for row in transactions) == 1) > 0


def test_real_loader_preserves_mapping_and_reports_discarded_transactions(tmp_path):
    (tmp_path / "customers.csv").write_text(
        "client_id,segment\nC1,B2B\n", encoding="utf-8")
    (tmp_path / "products.csv").write_text(
        "sku,name,eligible,available\nP1,Widget,B2B,2026-01-01T00:00:00Z\n",
        encoding="utf-8")
    (tmp_path / "sales.csv").write_text(
        "line_id,order_id,client_id,sku,created_at,quantity,status\n"
        "T1,O1,C1,P1,2026-01-02T00:00:00Z,1,completed\n"
        "T2,O2,C1,P1,2026-01-03T00:00:00Z,0,completed\n"
        "T3,O3,C1,P1,2026-01-04T00:00:00Z,1,cancelled\n",
        encoding="utf-8")
    mapping = {
        "customers": {"file": "customers.csv", "customer_id": "client_id", "segment": "segment"},
        "products": {"file": "products.csv", "product_id": "sku", "name": "name",
                      "eligible_segments": "eligible", "available_from": "available"},
        "transactions": {"file": "sales.csv", "transaction_id": "line_id", "order_id": "order_id",
                          "customer_id": "client_id", "product_id": "sku", "purchased_at": "created_at",
                          "quantity": "quantity", "status": "status"},
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    prepare_dataset(str(tmp_path), str(tmp_path / "out"), mapping_path=str(mapping_path))
    report = json.loads((tmp_path / "out" / "quality_report.json").read_text(encoding="utf-8"))
    bundle = json.loads((tmp_path / "out" / "bundle.json").read_text(encoding="utf-8"))

    assert bundle["products"][0]["eligible_segments"] == ["B2B"]
    assert bundle["products"][0]["available_from"] == "2026-01-01T00:00:00Z"
    assert len(bundle["transactions"]) == 1
    assert report["transactions_dropped"] == 2
    assert any("2" in warning for warning in report["warnings"])
