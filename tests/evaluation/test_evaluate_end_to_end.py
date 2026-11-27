"""End-to-end evaluation tests using test-only stubs (no stubs exist in production code)."""
from __future__ import annotations

import csv
import json
from collections import Counter

import pytest

from reco_mvp.data import make_synthetic_bundle
from reco_mvp.evaluation import evaluate
from reco_mvp.evaluation.metrics import COHORTS, METRIC_COLUMNS, SEGMENTS
from reco_mvp.evaluation.protocol import is_product_eligible_for_segment, parse_utc_timestamp


class _TestOnlyRecommenderStub:
    """Minimal deterministic test stub used ONLY inside tests/evaluation."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.seen_bundles: list[dict] = []
        self._as_of: str = ""
        self._data_mode: str = "synthetic"
        self._customers: dict[str, dict] = {}
        self._products: dict[str, dict] = {}
        self._bought_by_cust: dict[str, set[str]] = {}
        self._global_pop: Counter[str] = Counter()
        self._seg_pop: dict[str, Counter[str]] = {"B2B": Counter(), "B2C": Counter(), "unknown": Counter()}
        self._co_buyers: dict[tuple[str, str], set[str]] = {}

    def fit(self, bundle: dict, *, as_of: str) -> "_TestOnlyRecommenderStub":
        self.seen_bundles.append(bundle)
        self._as_of = as_of
        self._data_mode = bundle["data_mode"]
        self._customers = {c["customer_id"]: c for c in bundle["customers"]}
        self._products = {p["product_id"]: p for p in bundle["products"]}
        self._bought_by_cust = {cid: set() for cid in self._customers}
        self._global_pop = Counter()
        self._seg_pop = {"B2B": Counter(), "B2C": Counter(), "unknown": Counter()}
        self._co_buyers = {}

        buyers_by_prod: dict[str, set[str]] = {}
        for tx in bundle["transactions"]:
            cid = tx["customer_id"]
            pid = tx["product_id"]
            self._bought_by_cust.setdefault(cid, set()).add(pid)
            buyers_by_prod.setdefault(pid, set()).add(cid)

        for pid, buyers in buyers_by_prod.items():
            self._global_pop[pid] = len(buyers)
            for cid in buyers:
                seg = self._customers[cid]["segment"]
                self._seg_pop[seg][pid] += 1

        for cid, bought in self._bought_by_cust.items():
            for a in bought:
                for b in bought:
                    if a != b:
                        self._co_buyers.setdefault((a, b), set()).add(cid)
        return self

    def recommend(
        self,
        customer_id: str,
        *,
        segment: str | None = None,
        k: int = 3,
        strategy: str = "hybrid",
    ) -> dict:
        if customer_id in self._customers:
            known_seg = self._customers[customer_id]["segment"]
            if segment is not None and segment != known_seg:
                raise ValueError("contradicting segment")
            resolved_seg = known_seg
        else:
            resolved_seg = segment or "unknown"

        bought = self._bought_by_cust.get(customer_id, set())
        candidates = [
            pid
            for pid, prod in self._products.items()
            if prod.get("active") is not False
            and is_product_eligible_for_segment(prod, resolved_seg)
            and pid not in bought
        ]

        items: list[dict] = []
        used: set[str] = set()

        if strategy in {"graph_only", "hybrid"} and bought:
            graph_scores: list[tuple[int, str, list[str]]] = []
            for pid in candidates:
                union_buyers: set[str] = set()
                sources: list[str] = []
                for src in sorted(bought):
                    sup = self._co_buyers.get((src, pid), set())
                    if sup:
                        sources.append(src)
                        union_buyers |= sup
                if union_buyers:
                    graph_scores.append((len(union_buyers), pid, sources))
            graph_scores.sort(key=lambda x: (-x[0], x[1]))
            for sup_cnt, pid, sources in graph_scores[:k]:
                used.add(pid)
                items.append(
                    {
                        "product_id": pid,
                        "product_name": self._products[pid]["name"],
                        "rank": len(items) + 1,
                        "score": float(sup_cnt),
                        "reason_type": "co_purchase",
                        "reason_text": f"Co-purchased with {', '.join(sources)}",
                        "source_product_ids": sources,
                        "support_count": sup_cnt,
                        "is_fallback": False,
                    }
                )

        if strategy in {"popular", "hybrid"} and len(items) < k:
            rem = [pid for pid in candidates if pid not in used]
            seg_counts = self._seg_pop.get(resolved_seg, Counter())
            rem.sort(key=lambda pid: (-seg_counts[pid], -self._global_pop[pid], pid))
            for pid in rem:
                if len(items) >= k:
                    break
                if seg_counts[pid] > 0:
                    rtype = "segment_popularity"
                    scnt = int(seg_counts[pid])
                else:
                    rtype = "global_popularity"
                    scnt = int(self._global_pop[pid])
                used.add(pid)
                items.append(
                    {
                        "product_id": pid,
                        "product_name": self._products[pid]["name"],
                        "rank": len(items) + 1,
                        "score": float(scnt),
                        "reason_type": rtype,
                        "reason_text": "Popularity fallback",
                        "source_product_ids": [],
                        "support_count": scnt,
                        "is_fallback": strategy == "hybrid",
                    }
                )

        return {
            "schema_version": "1.0",
            "customer_id": customer_id,
            "segment": resolved_seg,
            "strategy": strategy,
            "as_of": self._as_of,
            "data_mode": self._data_mode,
            "recommendations": items,
            "warnings": [],
        }


def test_production_evaluate_has_no_hidden_stub_when_recommender_missing(tmp_path, monkeypatch):
    """Production evaluate() MUST fail clearly if the recommender is absent."""
    import sys

    monkeypatch.setitem(sys.modules, "reco_mvp.recommender", None)
    bundle = make_synthetic_bundle()
    with pytest.raises(ImportError, match="reco_mvp.recommender"):
        evaluate(
            bundle,
            as_of="2026-06-01T00:00:00Z",
            horizon_days=30,
            output_dir=str(tmp_path / "out"),
        )


def test_end_to_end_synthetic_artifacts_and_cold_start_isolation(tmp_path):
    bundle = make_synthetic_bundle()
    as_of = "2026-06-01T00:00:00Z"
    as_of_dt = parse_utc_timestamp(as_of, "as_of")

    fitted_instances: list[_TestOnlyRecommenderStub] = []

    def factory(cfg):
        inst = _TestOnlyRecommenderStub(cfg)
        fitted_instances.append(inst)
        return inst

    out_dir = tmp_path / "eval_out"
    result = evaluate(
        bundle,
        as_of=as_of,
        horizon_days=30,
        output_dir=str(out_dir),
        recommender_factory=factory,
        bootstrap_resamples=100,
    )

    # 1. Both main fit and simulated_cold refit received strictly pre-cutoff transactions
    assert len(fitted_instances) == 2
    for inst in fitted_instances:
        for tx in inst.seen_bundles[0]["transactions"]:
            assert parse_utc_timestamp(tx["purchased_at"], "purchased_at") < as_of_dt

    # 2. All 4 contract artifacts exist and parse cleanly
    metrics_json = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics_json == result
    assert result["schema_version"] == "1.0"
    assert result["data_mode"] == "synthetic"
    assert len(result["rows"]) == 3 * len(SEGMENTS) * len(COHORTS)

    with (out_dir / "metrics.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert tuple(csv_rows[0].keys()) == METRIC_COLUMNS
    assert len(csv_rows) == len(result["rows"])
    # Empty slices (e.g., natural_cold in B2B) must be blank in CSV, not "0.0"
    empty_csv = [r for r in csv_rows if r["n_customers"] == "0"]
    assert len(empty_csv) > 0
    assert all(r["precision_at_3"] == "" for r in empty_csv)

    jsonl_lines = [
        json.loads(line)
        for line in (out_dir / "recommendations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Main cohort has 61 customers * 3 strategies = 183 responses (simulated_cold not duplicated here)
    assert len(jsonl_lines) == 61 * 3

    report_md = (out_dir / "evaluation_report.md").read_text(encoding="utf-8")
    assert "DATA MODE: SYNTHETIC" in report_md
    assert "Excluded Purchases" in report_md
    assert "Proposed Next Online Experiment" in report_md

    # 3. Verify all-cohort never mixes simulated_cold and cold-start fill behavior
    by_key = {(r["strategy"], r["segment"], r["cohort"]): r for r in result["rows"]}
    for strat in ("popular", "graph_only", "hybrid"):
        assert by_key[(strat, "all", "all")]["n_customers"] == 61
        assert by_key[(strat, "all", "simulated_cold")]["n_customers"] == 12
        # Main cohort partition sum equals all
        assert (
            by_key[(strat, "all", "one_purchase")]["n_customers"]
            + by_key[(strat, "all", "warm")]["n_customers"]
            + by_key[(strat, "all", "natural_cold")]["n_customers"]
            == by_key[(strat, "all", "all")]["n_customers"]
        )

    # On simulated_cold and natural_cold, graph_only returns empty (FillRate=0) while hybrid/popular fill
    assert by_key[("graph_only", "all", "simulated_cold")]["fill_rate_at_3"] == 0.0
    assert by_key[("hybrid", "all", "simulated_cold")]["fill_rate_at_3"] == 1.0
    assert by_key[("popular", "all", "simulated_cold")]["fill_rate_at_3"] == 1.0
    assert by_key[("graph_only", "all", "natural_cold")]["fill_rate_at_3"] == 0.0
    assert by_key[("hybrid", "all", "natural_cold")]["fill_rate_at_3"] == 1.0


def test_model_error_is_never_masked_and_inadmissible_items_are_not_credited(tmp_path):
    bundle = make_synthetic_bundle()

    class _CrashingModel(_TestOnlyRecommenderStub):
        def recommend(self, customer_id, *, segment=None, k=3, strategy="hybrid"):
            if customer_id == "C005":
                raise RuntimeError("boom on C005")
            return super().recommend(customer_id, segment=segment, k=k, strategy=strategy)

    with pytest.raises(RuntimeError, match="boom on C005"):
        evaluate(
            bundle,
            as_of="2026-06-01T00:00:00Z",
            horizon_days=30,
            output_dir=str(tmp_path / "crash"),
            recommender_factory=_CrashingModel,
        )

    class _LeakyFilterBugModel(_TestOnlyRecommenderStub):
        """Returns already-bought P01 for C001 to verify evaluator catches filter violation."""

        def recommend(self, customer_id, *, segment=None, k=3, strategy="hybrid"):
            resp = super().recommend(customer_id, segment=segment, k=k, strategy=strategy)
            if customer_id == "C001" and strategy == "popular":
                resp["recommendations"] = [
                    {
                        "product_id": "P01",  # C001 already bought P01!
                        "product_name": "Synthetic product 1",
                        "rank": 1,
                        "score": 99.0,
                        "reason_type": "global_popularity",
                        "reason_text": "Buggy repeat recommendation",
                        "source_product_ids": [],
                        "support_count": 10,
                        "is_fallback": False,
                    }
                ]
            return resp

    res = evaluate(
        bundle,
        as_of="2026-06-01T00:00:00Z",
        horizon_days=30,
        output_dir=str(tmp_path / "violation"),
        strategies=("popular",),
        recommender_factory=_LeakyFilterBugModel,
    )
    assert res["protocol"]["filter_violations"]["total_invalid_items"] >= 1
    assert any("filter violation" in w for w in res["warnings"])


def test_end_to_end_hand_calculated_three_customer_backtest_and_simulated_cold_no_renewal_hit(tmp_path):
    """Full evaluate() run on a 3-customer bundle verifying exact hand-calculated metrics.

    Customers (as_of = 2026-06-01T00:00:00Z, horizon = 30d):
    - C1 (B2B, one_purchase): bought P1 before cutoff; buys P2 (valid) + P1 (repeat, excluded) after cutoff.
    - C2 (B2C, warm): bought P1 & P3 before cutoff; buys P4 (valid) after cutoff; gets only 2 recs (P2, P4).
    - C3 (unknown, one_purchase): bought P4 before cutoff; buys nothing after cutoff.
    Eligible catalog at cutoff: {P1, P2, P3, P4} (size 4).
    """
    bundle = {
        "schema_version": "1.0",
        "data_mode": "real",
        "customers": [
            {"customer_id": "C1", "segment": "B2B", "known_since": None},
            {"customer_id": "C2", "segment": "B2C", "known_since": None},
            {"customer_id": "C3", "segment": "unknown", "known_since": None},
        ],
        "products": [
            {"product_id": "P1", "name": "Prod 1", "category": "core", "eligible_segments": None, "active": True, "available_from": None},
            {"product_id": "P2", "name": "Prod 2", "category": "addon", "eligible_segments": None, "active": True, "available_from": None},
            {"product_id": "P3", "name": "Prod 3", "category": "addon", "eligible_segments": None, "active": True, "available_from": None},
            {"product_id": "P4", "name": "Prod 4", "category": "addon", "eligible_segments": None, "active": True, "available_from": None},
        ],
        "transactions": [
            # Pre-cutoff
            {"transaction_id": "T1", "order_id": "O1", "customer_id": "C1", "product_id": "P1", "purchased_at": "2026-04-01T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T2", "order_id": "O2a", "customer_id": "C2", "product_id": "P1", "purchased_at": "2026-04-02T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T3", "order_id": "O2b", "customer_id": "C2", "product_id": "P3", "purchased_at": "2026-04-10T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T4", "order_id": "O3", "customer_id": "C3", "product_id": "P4", "purchased_at": "2026-04-15T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T5", "order_id": "O2c", "customer_id": "C2", "product_id": "P2", "purchased_at": "2026-04-20T00:00:00Z", "quantity": 1.0},
            # Future window
            {"transaction_id": "T6", "order_id": "O1f", "customer_id": "C1", "product_id": "P2", "purchased_at": "2026-06-10T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T7", "order_id": "O1rep", "customer_id": "C1", "product_id": "P1", "purchased_at": "2026-06-11T00:00:00Z", "quantity": 1.0},
            {"transaction_id": "T8", "order_id": "O2f", "customer_id": "C2", "product_id": "P4", "purchased_at": "2026-06-12T00:00:00Z", "quantity": 1.0},
        ],
        "metadata": {
            "observation_end": "2026-07-01T00:00:00Z",
            "warnings": [],
            "segment_source": "mapped",
            "catalog_history_available": False,
        },
    }

    class _FixedScriptedStub(_TestOnlyRecommenderStub):
        def recommend(self, customer_id, *, segment=None, k=3, strategy="hybrid"):
            if customer_id not in self._customers:
                # Simulated cold customer: recommends P1 (which C1 originally bought as a repeat!)
                return {
                    "schema_version": "1.0",
                    "customer_id": customer_id,
                    "segment": segment or "unknown",
                    "strategy": strategy,
                    "as_of": self._as_of,
                    "data_mode": self._data_mode,
                    "recommendations": [
                        {
                            "product_id": "P1",
                            "product_name": "Prod 1",
                            "rank": 1,
                            "score": 1.0,
                            "reason_type": "global_popularity",
                            "reason_text": "Global popular",
                            "source_product_ids": [],
                            "support_count": 1,
                            "is_fallback": strategy == "hybrid",
                        }
                    ],
                    "warnings": [],
                }
            scripted = {
                # C1 (targets={P2}): 3 recs -> 1 hit (P2) -> P@3 = 1/3, Full = 1
                "C1": ["P2", "P3", "P4"],
                # C2 (bought P1,P2,P3; targets={P4}): only 1 eligible left -> [P4] -> P@3 = 1/3 (denom 3!), Full = 0
                "C2": ["P4"],
                # C3 (bought P4; targets={}): 2 recs [P1, P2] -> 0 hits -> P@3 = 0/3, Full = 0
                "C3": ["P1", "P2"],
            }
            pids = scripted[customer_id]
            return {
                "schema_version": "1.0",
                "customer_id": customer_id,
                "segment": self._customers[customer_id]["segment"],
                "strategy": strategy,
                "as_of": self._as_of,
                "data_mode": self._data_mode,
                "recommendations": [
                    {
                        "product_id": pid,
                        "product_name": self._products[pid]["name"],
                        "rank": idx,
                        "score": float(10 - idx),
                        "reason_type": "global_popularity",
                        "reason_text": "Scripted",
                        "source_product_ids": [],
                        "support_count": 1,
                        "is_fallback": False,
                    }
                    for idx, pid in enumerate(pids, start=1)
                ],
                "warnings": [],
            }

    out_dir = tmp_path / "manual_3"
    res = evaluate(
        bundle,
        as_of="2026-06-01T00:00:00Z",
        horizon_days=30,
        output_dir=str(out_dir),
        strategies=("popular", "hybrid"),
        recommender_factory=_FixedScriptedStub,
        simulated_cold_fraction=0.34,
        simulated_cold_seed=42,
    )

    by_key = {(r["strategy"], r["segment"], r["cohort"]): r for r in res["rows"]}
    all_row = by_key[("hybrid", "all", "all")]
    # Manual check:
    # n_customers=3, n_with_future_purchase=2
    # Precision@3 = (1/3 + 1/3 + 0/3) / 3 = 2/9
    # HitRate@3 = 2/3
    # FillRate@3 = (3 + 1 + 2) / (3 * 3) = 6/9 = 2/3
    # FullListRate = 1/3
    # CatalogCoverage = |{P1, P2, P3, P4}| / 4 = 1.0
    assert all_row["n_customers"] == 3
    assert all_row["n_with_future_purchase"] == 2
    assert all_row["precision_at_3"] == pytest.approx(2.0 / 9.0)
    assert all_row["hit_rate_at_3"] == pytest.approx(2.0 / 3.0)
    assert all_row["fill_rate_at_3"] == pytest.approx(2.0 / 3.0)
    assert all_row["full_list_rate"] == pytest.approx(1.0 / 3.0)
    assert all_row["catalog_coverage"] == pytest.approx(1.0)

    # natural_cold is empty (no known_since) -> n=0 and all metrics None
    nat_cold = by_key[("hybrid", "all", "natural_cold")]
    assert nat_cold["n_customers"] == 0
    assert nat_cold["precision_at_3"] is None
    assert nat_cold["fill_rate_at_3"] is None

    # simulated_cold has 1 customer, recommends P1; even if C1 was selected, P1 is a repeat -> 0 hits!
    sim_cold = by_key[("hybrid", "all", "simulated_cold")]
    assert sim_cold["n_customers"] == 1
    assert sim_cold["precision_at_3"] == 0.0
    assert sim_cold["hit_rate_at_3"] == 0.0

    report_md = (out_dir / "evaluation_report.md").read_text(encoding="utf-8")
    assert "DATA MODE: REAL" in report_md


def test_structural_contract_violations_raise_clear_errors(tmp_path):
    bundle = make_synthetic_bundle()

    class _DuplicateProductStub(_TestOnlyRecommenderStub):
        def recommend(self, customer_id, *, segment=None, k=3, strategy="hybrid"):
            resp = super().recommend(customer_id, segment=segment, k=k, strategy=strategy)
            if resp["recommendations"]:
                dup = dict(resp["recommendations"][0])
                dup["rank"] = 2
                resp["recommendations"] = [resp["recommendations"][0], dup]
            return resp

    with pytest.raises(ValueError, match="duplicate product_id"):
        evaluate(
            bundle,
            as_of="2026-06-01T00:00:00Z",
            horizon_days=30,
            output_dir=str(tmp_path / "dup"),
            strategies=("popular",),
            recommender_factory=_DuplicateProductStub,
        )

    class _NonFiniteScoreStub(_TestOnlyRecommenderStub):
        def recommend(self, customer_id, *, segment=None, k=3, strategy="hybrid"):
            resp = super().recommend(customer_id, segment=segment, k=k, strategy=strategy)
            if resp["recommendations"]:
                resp["recommendations"][0]["score"] = float("nan")
            return resp

    with pytest.raises(ValueError, match="finite number"):
        evaluate(
            bundle,
            as_of="2026-06-01T00:00:00Z",
            horizon_days=30,
            output_dir=str(tmp_path / "nan"),
            strategies=("popular",),
            recommender_factory=_NonFiniteScoreStub,
        )


def test_real_recommender_fit_ignores_future_transactions_when_available():
    """Direct fit-level leakage guard against Recommender once merged."""
    recommender_mod = pytest.importorskip(
        "reco_mvp.recommender",
        reason="reco_mvp.recommender is not available",
    )
    Recommender = recommender_mod.Recommender

    base_bundle = make_synthetic_bundle()
    as_of = "2026-06-01T00:00:00Z"
    as_of_dt = parse_utc_timestamp(as_of, "as_of")

    # Strip future transactions manually for ground-truth pre-cutoff model
    pre_only = {
        **base_bundle,
        "transactions": [
            tx
            for tx in base_bundle["transactions"]
            if parse_utc_timestamp(tx["purchased_at"], "purchased_at") < as_of_dt
        ],
    }

    m_full = Recommender().fit(base_bundle, as_of=as_of)
    m_pre = Recommender().fit(pre_only, as_of=as_of)

    for strat in ("popular", "graph_only", "hybrid"):
        for cid in ("C001", "C006", "C061"):
            r_full = m_full.recommend(cid, k=3, strategy=strat)
            r_pre = m_pre.recommend(cid, k=3, strategy=strat)
            assert [x["product_id"] for x in r_full["recommendations"]] == [
                x["product_id"] for x in r_pre["recommendations"]
            ]
