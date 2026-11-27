"""Unit tests for pure metric formulas, manual hand-calculated examples, and deltas."""
from __future__ import annotations

import math

import pytest

from reco_mvp.evaluation.metrics import (
    CustomerEvalRecord,
    bootstrap_precision_diff_ci,
    compute_metric_delta,
    compute_slice_metrics,
)


def test_hand_calculated_three_customer_example():
    """Verify fixed denominator 3, averaging across all customers, FillRate, FullListRate, Coverage.

    Manual calculation (eligible catalog = {P1, P2, P3, P4, P5}, size = 5):
    - C1: recs=(P1, P2, P3) [len 3], targets={P2, P4} -> 1 hit -> Precision@3 = 1/3, Hit = 1, Full = 1
    - C2: recs=(P1, P4)     [len 2], targets={P1, P4} -> 2 hits -> Precision@3 = 2/3 (NOT 2/2!), Hit = 1, Full = 0
    - C3: recs=(P2, P3, P5) [len 3], targets=set()    -> 0 hits -> Precision@3 = 0/3 (included!), Hit = 0, Full = 1

    Aggregates over all 3 customers:
    - n_customers = 3
    - n_with_future_purchase = 2
    - Precision@3 = (1/3 + 2/3 + 0/3) / 3 = 1/3
    - HitRate@3 = (1 + 1 + 0) / 3 = 2/3
    - FillRate@3 = (3 + 2 + 3) / (3 * 3) = 8/9
    - FullListRate = (1 + 0 + 1) / 3 = 2/3
    - CatalogCoverage = |{P1, P2, P3, P4, P5}| / 5 = 5/5 = 1.0
    """
    records = [
        CustomerEvalRecord("C1", "B2B", "warm", ("P1", "P2", "P3"), frozenset({"P2", "P4"})),
        CustomerEvalRecord("C2", "B2C", "one_purchase", ("P1", "P4"), frozenset({"P1", "P4"})),
        CustomerEvalRecord("C3", "unknown", "warm", ("P2", "P3", "P5"), frozenset()),
    ]
    catalog = frozenset({"P1", "P2", "P3", "P4", "P5"})

    row = compute_slice_metrics(
        strategy="hybrid",
        segment="all",
        cohort="all",
        records=records,
        eligible_catalog_ids=catalog,
    )

    assert row["n_customers"] == 3
    assert row["n_with_future_purchase"] == 2
    assert row["precision_at_3"] == pytest.approx(1.0 / 3.0)
    assert row["hit_rate_at_3"] == pytest.approx(2.0 / 3.0)
    assert row["fill_rate_at_3"] == pytest.approx(8.0 / 9.0)
    assert row["full_list_rate"] == pytest.approx(2.0 / 3.0)
    assert row["catalog_coverage"] == pytest.approx(1.0)


def test_empty_cohort_returns_null_metrics_not_zeros():
    row = compute_slice_metrics(
        strategy="popular",
        segment="B2B",
        cohort="natural_cold",
        records=[],
        eligible_catalog_ids=frozenset({"P1", "P2"}),
    )

    assert row["n_customers"] == 0
    assert row["n_with_future_purchase"] == 0
    assert row["precision_at_3"] is None
    assert row["hit_rate_at_3"] is None
    assert row["fill_rate_at_3"] is None
    assert row["full_list_rate"] is None
    assert row["catalog_coverage"] is None


def test_completely_empty_recommendations_and_zero_catalog_denominator():
    records = [
        CustomerEvalRecord("C1", "B2C", "natural_cold", (), frozenset({"P1"})),
        CustomerEvalRecord("C2", "B2C", "natural_cold", (), frozenset()),
    ]
    non_empty_catalog_row = compute_slice_metrics(
        strategy="graph_only",
        segment="B2C",
        cohort="natural_cold",
        records=records,
        eligible_catalog_ids=frozenset({"P1", "P2"}),
    )
    assert non_empty_catalog_row["n_customers"] == 2
    assert non_empty_catalog_row["n_with_future_purchase"] == 1
    assert non_empty_catalog_row["precision_at_3"] == 0.0
    assert non_empty_catalog_row["hit_rate_at_3"] == 0.0
    assert non_empty_catalog_row["fill_rate_at_3"] == 0.0
    assert non_empty_catalog_row["full_list_rate"] == 0.0
    assert non_empty_catalog_row["catalog_coverage"] == 0.0

    zero_catalog_row = compute_slice_metrics(
        strategy="graph_only",
        segment="B2C",
        cohort="natural_cold",
        records=records,
        eligible_catalog_ids=frozenset(),
    )
    assert zero_catalog_row["catalog_coverage"] is None


def test_metric_delta_handles_zero_baseline_and_pp_vs_relative():
    zero_base = compute_metric_delta(0.15, 0.0)
    assert zero_base["absolute_diff"] == pytest.approx(0.15)
    assert zero_base["delta_pp"] == pytest.approx(15.0)
    assert zero_base["relative_lift_pct"] is None
    assert zero_base["relative_lift_defined"] is False
    assert "baseline = 0" in (zero_base["note"] or "")

    positive_base = compute_metric_delta(0.12, 0.10)
    assert positive_base["absolute_diff"] == pytest.approx(0.02)
    assert positive_base["delta_pp"] == pytest.approx(2.0)
    assert positive_base["relative_lift_pct"] == pytest.approx(20.0)
    assert positive_base["relative_lift_defined"] is True
    assert math.isfinite(positive_base["relative_lift_pct"])

    missing_base = compute_metric_delta(None, 0.10)
    assert missing_base["delta_pp"] is None
    assert missing_base["relative_lift_defined"] is False


def test_bootstrap_precision_diff_ci_is_deterministic():
    cand = [
        CustomerEvalRecord("C1", "B2B", "warm", ("P1", "P2", "P3"), frozenset({"P1"})),
        CustomerEvalRecord("C2", "B2B", "warm", ("P1", "P2", "P3"), frozenset({"P2"})),
        CustomerEvalRecord("C3", "B2C", "warm", ("P1", "P2", "P3"), frozenset()),
        CustomerEvalRecord("C4", "B2C", "warm", ("P1", "P2", "P3"), frozenset({"P3"})),
    ]
    base = [
        CustomerEvalRecord("C1", "B2B", "warm", ("P4", "P5", "P6"), frozenset({"P1"})),
        CustomerEvalRecord("C2", "B2B", "warm", ("P2", "P5", "P6"), frozenset({"P2"})),
        CustomerEvalRecord("C3", "B2C", "warm", ("P4", "P5", "P6"), frozenset()),
        CustomerEvalRecord("C4", "B2C", "warm", ("P4", "P5", "P6"), frozenset({"P3"})),
    ]
    ci_1 = bootstrap_precision_diff_ci(cand, base, n_resamples=200, seed=42)
    ci_2 = bootstrap_precision_diff_ci(cand, base, n_resamples=200, seed=42)
    assert ci_1 == ci_2
    assert ci_1["observed_diff"] == pytest.approx((2.0 / 3.0) / 4.0)
    assert ci_1["ci_low"] <= ci_1["observed_diff"] <= ci_1["ci_high"]
