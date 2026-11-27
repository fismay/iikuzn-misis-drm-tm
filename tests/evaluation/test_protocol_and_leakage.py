"""Protocol tests for temporal leakage, boundaries, split orders, catalog rules, and cold-start."""
from __future__ import annotations

import pytest

from contracts.validate import validate_bundle
from reco_mvp.evaluation.protocol import (
    build_point_in_time_snapshot,
    build_simulated_cold_split,
    parse_utc_timestamp,
)


def _make_protocol_bundle() -> dict:
    """Small hand-crafted bundle exercising every temporal and eligibility edge case."""
    bundle = {
        "schema_version": "1.0",
        "data_mode": "synthetic",
        "customers": [
            # C1: 1 historical order -> one_purchase (B2B)
            {"customer_id": "001", "segment": "B2B", "known_since": None},
            # C2: 2 historical orders -> warm (B2C)
            {"customer_id": "002", "segment": "B2C", "known_since": "2026-01-10T00:00:00Z"},
            # C3: 1 historical order -> one_purchase (unknown segment)
            {"customer_id": "003", "segment": "unknown", "known_since": None},
            # C4: 0 historical orders, known before cutoff -> natural_cold (B2C)
            {"customer_id": "004", "segment": "B2C", "known_since": "2026-05-15T00:00:00Z"},
            # C5: first appears AFTER cutoff -> must NOT enter main cohort
            {"customer_id": "005", "segment": "B2B", "known_since": "2026-06-05T00:00:00Z"},
            # C6: known_since is None and only future transaction -> must NOT enter main cohort
            {"customer_id": "006", "segment": "B2C", "known_since": None},
        ],
        "products": [
            # P1: sold before cutoff, no available_from -> admitted conservatively
            {
                "product_id": "P1",
                "name": "Core A",
                "category": "core",
                "eligible_segments": None,
                "active": True,
                "available_from": None,
            },
            # P2: sold before cutoff, B2B only -> admitted for B2B only (blocked for B2C/unknown)
            {
                "product_id": "P2",
                "name": "B2B Addon",
                "category": "addon",
                "eligible_segments": ["B2B"],
                "active": True,
                "available_from": None,
            },
            # P3: available_from <= cutoff, 0 pre-cutoff sales -> ADMITTED despite zero sales
            {
                "product_id": "P3",
                "name": "Known Launch Addon",
                "category": "addon",
                "eligible_segments": ["B2B", "B2C", "unknown"],
                "active": True,
                "available_from": "2026-05-20T00:00:00Z",
            },
            # P4: available_from > cutoff -> LATE product, must be excluded from train & targets
            {
                "product_id": "P4",
                "name": "Future Product",
                "category": "addon",
                "eligible_segments": None,
                "active": True,
                "available_from": "2026-06-10T00:00:00Z",
            },
            # P5: available_from=None, never sold before cutoff -> conservatively excluded
            {
                "product_id": "P5",
                "name": "Unobserved Null Avail",
                "category": "addon",
                "eligible_segments": None,
                "active": True,
                "available_from": None,
            },
            # P6: inactive product -> excluded from catalog and targets
            {
                "product_id": "P6",
                "name": "Discontinued",
                "category": "core",
                "eligible_segments": None,
                "active": False,
                "available_from": "2026-01-01T00:00:00Z",
            },
            # P7: sold ONLY by C3 before cutoff, unknown-eligible explicitly
            {
                "product_id": "P7",
                "name": "Niche Unknown Allowed",
                "category": "addon",
                "eligible_segments": ["unknown", "B2C"],
                "active": None,
                "available_from": None,
            },
        ],
        "transactions": [
            # --- Historical (< 2026-06-01T00:00:00Z) ---
            # C1 order O1 (also has a split line after cutoff!)
            {
                "transaction_id": "T01",
                "order_id": "O1",
                "customer_id": "001",
                "product_id": "P1",
                "purchased_at": "2026-05-31T23:59:59Z",
                "quantity": 1.0,
            },
            # C2 order O2a and O2b (using +03:00 timezone offset: 2026-06-01T02:00:00+03:00 == 2026-05-31T23:00:00Z < cutoff!)
            {
                "transaction_id": "T02",
                "order_id": "O2a",
                "customer_id": "002",
                "product_id": "P1",
                "purchased_at": "2026-03-01T10:00:00Z",
                "quantity": 1.0,
            },
            {
                "transaction_id": "T03",
                "order_id": "O2b",
                "customer_id": "002",
                "product_id": "P2",
                "purchased_at": "2026-06-01T02:00:00+03:00",
                "quantity": 1.0,
            },
            # C3 single order O3 buying P7 (unique to C3) and P6 (inactive today, historically bought)
            {
                "transaction_id": "T04",
                "order_id": "O3",
                "customer_id": "003",
                "product_id": "P7",
                "purchased_at": "2026-04-01T12:00:00Z",
                "quantity": 1.0,
            },
            {
                "transaction_id": "T05",
                "order_id": "O3",
                "customer_id": "003",
                "product_id": "P6",
                "purchased_at": "2026-04-01T12:00:00Z",
                "quantity": 1.0,
            },
            # --- Future window [2026-06-01T00:00:00Z, 2026-07-01T00:00:00Z) ---
            # Exact boundary as_of: belongs to future window, NOT train; valid target for C1 (P2 is B2B-eligible!)
            {
                "transaction_id": "T06",
                "order_id": "O1-future",
                "customer_id": "001",
                "product_id": "P2",
                "purchased_at": "2026-06-01T00:00:00Z",
                "quantity": 1.0,
            },
            # Split order O1 straddling cutoff -> must be excluded from future targets!
            {
                "transaction_id": "T07",
                "order_id": "O1",
                "customer_id": "001",
                "product_id": "P3",
                "purchased_at": "2026-06-01T00:01:00Z",
                "quantity": 1.0,
            },
            # Repeat purchase of P1 by C1 -> excluded as repeat_purchase
            {
                "transaction_id": "T08",
                "order_id": "O1-rep",
                "customer_id": "001",
                "product_id": "P1",
                "purchased_at": "2026-06-15T00:00:00Z",
                "quantity": 1.0,
            },
            # Late product P4 bought by C2 -> excluded as late_product_available_after_cutoff
            {
                "transaction_id": "T09",
                "order_id": "O2-late",
                "customer_id": "002",
                "product_id": "P4",
                "purchased_at": "2026-06-12T00:00:00Z",
                "quantity": 1.0,
            },
            # Unobserved null-avail product P5 bought by C2 -> excluded as unobserved_without_available_from
            {
                "transaction_id": "T10",
                "order_id": "O2-unobs",
                "customer_id": "002",
                "product_id": "P5",
                "purchased_at": "2026-06-13T00:00:00Z",
                "quantity": 1.0,
            },
            # Valid cross-sell target for C2: P3 (available_from <= as_of despite 0 pre-cutoff sales)
            {
                "transaction_id": "T11",
                "order_id": "O2-valid",
                "customer_id": "002",
                "product_id": "P3",
                "purchased_at": "2026-06-20T00:00:00Z",
                "quantity": 1.0,
            },
            # Inactive product P6 bought by C2 -> excluded as inactive_product
            {
                "transaction_id": "T12",
                "order_id": "O2-inact",
                "customer_id": "002",
                "product_id": "P6",
                "purchased_at": "2026-06-21T00:00:00Z",
                "quantity": 1.0,
            },
            # Segment-ineligible purchase: C3 (unknown) buys P2 (B2B only) -> excluded as segment_ineligible
            {
                "transaction_id": "T13",
                "order_id": "O3-inelig",
                "customer_id": "003",
                "product_id": "P2",
                "purchased_at": "2026-06-10T00:00:00Z",
                "quantity": 1.0,
            },
            # Valid cross-sell target for C3 (unknown): P1 (eligible_segments=None)
            {
                "transaction_id": "T14",
                "order_id": "O3-valid",
                "customer_id": "003",
                "product_id": "P1",
                "purchased_at": "2026-06-18T00:00:00Z",
                "quantity": 1.0,
            },
            # Valid cross-sell target for C4 (natural_cold, B2C): P7 (eligible for B2C)
            {
                "transaction_id": "T15",
                "order_id": "O4-valid",
                "customer_id": "004",
                "product_id": "P7",
                "purchased_at": "2026-06-25T00:00:00Z",
                "quantity": 1.0,
            },
            # Future new customer C5 and C6 transactions -> excluded as not_in_main_cohort
            {
                "transaction_id": "T16",
                "order_id": "O5-new",
                "customer_id": "005",
                "product_id": "P1",
                "purchased_at": "2026-06-06T00:00:00Z",
                "quantity": 1.0,
            },
            {
                "transaction_id": "T17",
                "order_id": "O6-new",
                "customer_id": "006",
                "product_id": "P1",
                "purchased_at": "2026-06-07T00:00:00Z",
                "quantity": 1.0,
            },
            # --- Post-window boundary (== horizon_end 2026-07-01T00:00:00Z): must be ignored ---
            {
                "transaction_id": "T18",
                "order_id": "O1-post",
                "customer_id": "001",
                "product_id": "P3",
                "purchased_at": "2026-07-01T00:00:00Z",
                "quantity": 1.0,
            },
        ],
        "metadata": {
            "observation_end": "2026-07-01T00:00:00Z",
            "warnings": [],
            "segment_source": "synthetic",
            "catalog_history_available": False,
        },
    }
    return validate_bundle(bundle)


def test_window_boundaries_split_orders_late_products_and_future_customers():
    bundle = _make_protocol_bundle()
    as_of = "2026-06-01T00:00:00Z"
    snap = build_point_in_time_snapshot(bundle, as_of=as_of, horizon_days=30)

    # 1. Cohort membership: C5 and C6 excluded; C1=one_purchase, C2=warm, C3=one_purchase, C4=natural_cold
    assert [c["customer_id"] for c in snap.main_customers] == ["001", "002", "003", "004"]
    assert snap.customer_cohorts == {
        "001": "one_purchase",
        "002": "warm",
        "003": "one_purchase",
        "004": "natural_cold",
    }
    assert snap.cohort_counts["excluded_future_new_customers"] == 2

    # 2. Physical temporal isolation of train_bundle
    as_of_dt = parse_utc_timestamp(as_of, "as_of")
    assert all(
        parse_utc_timestamp(tx["purchased_at"], "purchased_at") < as_of_dt
        for tx in snap.train_bundle["transactions"]
    )
    # T03 (+03:00 offset -> 2026-05-31T23:00:00Z) MUST be in train; T06 (exact as_of) MUST NOT
    train_tx_ids = {tx["transaction_id"] for tx in snap.train_bundle["transactions"]}
    assert train_tx_ids == {"T01", "T02", "T03", "T04", "T05"}
    # Future customers C5/C6 physically removed from train_bundle
    assert {c["customer_id"] for c in snap.train_bundle["customers"]} == {"001", "002", "003", "004"}
    # Late product P4 and unobserved null-avail P5 physically absent from train_bundle["products"]
    train_prod_ids = {p["product_id"] for p in snap.train_bundle["products"]}
    assert "P4" not in train_prod_ids
    assert "P5" not in train_prod_ids
    # Historically bought inactive product P6 remains inactive in train_bundle for referential integrity
    p6_in_train = next(p for p in snap.train_bundle["products"] if p["product_id"] == "P6")
    assert p6_in_train["active"] is False
    # observation_end inside train_bundle is clamped to as_of
    assert snap.train_bundle["metadata"]["observation_end"] == as_of

    # 3. Point-in-time eligible catalog per segment
    assert snap.eligible_catalog_by_segment["all"] == frozenset({"P1", "P2", "P3", "P7"})
    assert snap.eligible_catalog_by_segment["B2B"] == frozenset({"P1", "P2", "P3"})
    assert snap.eligible_catalog_by_segment["B2C"] == frozenset({"P1", "P3", "P7"})
    # unknown cannot access P2 (B2B only), CAN access P1 (null), P3 (explicit), P7 (explicit)
    assert snap.eligible_catalog_by_segment["unknown"] == frozenset({"P1", "P3", "P7"})

    # 4. Cross-sell targets and exact exclusion accounting
    assert snap.future_targets_by_customer == {
        "001": frozenset({"P2"}),  # T06 valid; T07 split order excluded; T08 repeat excluded; T18 post-window ignored
        "002": frozenset({"P3"}),  # T11 valid; T09 late, T10 unobs, T12 inactive excluded
        "003": frozenset({"P1"}),  # T14 valid; T13 segment_ineligible excluded
        "004": frozenset({"P7"}),  # T15 valid
    }
    excl = snap.excluded_future_purchases
    assert excl["total_future_transactions_in_window"] == 12
    assert excl["post_window_transactions_ignored"] == 1
    assert excl["valid_target_transactions"] == 4
    assert excl["total_excluded_transactions"] == 8
    assert excl["by_reason"] == {
        "not_in_main_cohort": 2,
        "split_order_across_cutoff": 1,
        "repeat_purchase": 1,
        "inactive_product": 1,
        "late_product_available_after_cutoff": 1,
        "unobserved_without_available_from": 1,
        "segment_ineligible": 1,
    }


def test_incomplete_observation_end_raises_clear_error():
    bundle = _make_protocol_bundle()
    bundle["metadata"]["observation_end"] = "2026-06-29T23:59:59Z"
    with pytest.raises(ValueError, match="observation_end does not cover"):
        build_point_in_time_snapshot(bundle, as_of="2026-06-01T00:00:00Z", horizon_days=30)


def test_missing_order_id_falls_back_to_distinct_dates_with_warning():
    bundle = _make_protocol_bundle()
    for tx in bundle["transactions"]:
        tx["order_id"] = None
    snap = build_point_in_time_snapshot(bundle, as_of="2026-06-01T00:00:00Z", horizon_days=30)
    assert snap.order_count_method == "distinct_utc_date_approx"
    assert any("distinct UTC purchase dates" in w for w in snap.warnings)
    # C3 had 2 rows on the same date (2026-04-01) -> 1 distinct date -> one_purchase
    assert snap.customer_cohorts["003"] == "one_purchase"
    # C2 had rows on 2026-03-01 and 2026-05-31 -> 2 distinct dates -> warm
    assert snap.customer_cohorts["002"] == "warm"


def test_simulated_cold_hides_history_freezes_catalog_and_keeps_original_cross_sell_targets():
    bundle = _make_protocol_bundle()
    snap = build_point_in_time_snapshot(bundle, as_of="2026-06-01T00:00:00Z", horizon_days=30)

    # Explicitly hold out C1 (has repeat purchase T08 of P1) and C3 (sole historical buyer of P7)
    sim = build_simulated_cold_split(snap, selected_customer_ids={"001", "003"})
    assert sim.enabled is True
    assert sim.sim_train_bundle is not None
    assert {c["customer_id"] for c in sim.selected_customers} == {"001", "003"}

    # Hidden customers and ALL their pre-cutoff transactions are physically gone from sim_train_bundle
    sim_cust_ids = {c["customer_id"] for c in sim.sim_train_bundle["customers"]}
    sim_tx_cust_ids = {tx["customer_id"] for tx in sim.sim_train_bundle["transactions"]}
    assert "001" not in sim_cust_ids and "003" not in sim_cust_ids
    assert "001" not in sim_tx_cust_ids and "003" not in sim_tx_cust_ids

    # Catalog is frozen from original train snapshot: P7 (only bought by C3) MUST stay in catalog!
    sim_prod_ids = {p["product_id"] for p in sim.sim_train_bundle["products"]}
    assert "P7" in sim_prod_ids
    assert "P7" in snap.eligible_catalog_by_segment["all"]

    # C1's future repeat purchase of P1 (T08) MUST NOT become a cross-sell target after hiding C1!
    assert "P1" not in snap.future_targets_by_customer["001"]
    assert snap.future_targets_by_customer["001"] == frozenset({"P2"})
