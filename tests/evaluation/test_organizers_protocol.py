"""Golden tests for the organizers' official protocol scorer.

Without the organizers' workbook only the synthetic unit checks run; the golden
reproduction of the published baseline table requires ``data/raw/source.xlsx``
(git-ignored) and is skipped elsewhere.
"""
import os
from pathlib import Path

import pytest

from reco_mvp.evaluation.organizers import (
    OfficialDataset,
    baseline_copurchase,
    baseline_popularity,
    score_recommendations,
)

WORKBOOK = Path(__file__).resolve().parents[2] / "data" / "raw" / "source.xlsx"
HAS_WORKBOOK = os.environ.get("RECO_OFFICIAL_WORKBOOK") or WORKBOOK.exists()


def _rows(triples):
    """triples: (client, item, week, order, group)"""
    return [{"client_id": c, "item_id": i, "order_week": w, "order_id": o,
             "product_group": g, "sku": i, "item_name": i} for c, i, w, o, g in triples]


def _dataset(triples, groups):
    client_rows = [{"client_id": c, "client_group": g} for c, g in groups.items()]
    return OfficialDataset(_rows(triples), client_rows, train_end="2026-01-05",
                           test_start="2026-01-12", test_end="2026-01-31")


def test_cohorts_and_targets_follow_official_rules():
    # history: C1 buys A,B (order O1) before cutoff; C2 buys A; C3 buys nothing before cutoff
    # test: C1 buys C (new) and A (repeat, not a target); C2 buys B (new); C4 everything after
    ds = _dataset([
        ("C1", "A", "2026-01-05", "O1", "GA"),
        ("C1", "B", "2026-01-05", "O1", "GB"),
        ("C2", "A", "2026-01-05", "O2", "GA"),
        ("C1", "C", "2026-01-12", "O3", "GC"),
        ("C1", "A", "2026-01-12", "O4", "GA"),
        ("C2", "B", "2026-01-19", "O5", "GB"),
        ("C3", "C", "2026-01-12", "O6", "GC"),
        ("C2", "D", "2026-02-02", "O7", "GD"),  # beyond test_end below via explicit window
    ], {"C1": "B2B", "C2": "B2C", "C3": "B2B"})
    assert set(ds.group_a) == {"C1", "C2"}          # C3 has no training history
    assert ds.targets["C1"] == {"C"}                # repeat A excluded
    assert ds.targets["C2"] == {"B"}
    # group B = clients with exactly one historical item
    assert set(ds.group_b) == {"C2"}
    # basket semantics: same order_id in different weeks are different baskets
    ds2 = OfficialDataset(_rows([("C1", "X", "2026-01-05", "O9", "GX"),
                                 ("C1", "Y", "2026-01-12", "O9", "GY")]),
                          [{"client_id": "C1", "client_group": "B2B"}],
                          train_end="2026-01-12", test_start="2026-01-19")
    assert ds2.basket_co.get("X", {}).get("Y") is None  # different baskets across weeks


def test_metrics_hand_checked():
    ds = _dataset([
        ("C1", "A", "2026-01-05", "O1", "GA"),
        ("C2", "A", "2026-01-05", "O2", "GA"),
        ("C1", "B", "2026-01-12", "O3", "GB"),   # variant hit: GB not in history? no — GA in history, GB new group
        ("C1", "A2", "2026-01-12", "O4", "GA"),  # variant: group GA already owned
        ("C2", "B", "2026-01-12", "O5", "GB"),
    ], {"C1": "B2B", "C2": "B2B"})
    # hand-crafted recommender: C1 gets [A2, B, Z]; C2 gets [B, junk1, junk2]
    def rec(cid):
        return {"C1": ["A2", "B", "Z"], "C2": ["B", "junk1", "junk2"]}[cid]
    res = score_recommendations(ds, rec)
    a = res["A"]["all"]
    assert a["n_customers"] == 2
    # hits: C1 2/3, C2 1/3 -> 3/(2*3) = 0.5
    assert abs(a["precision_at_3"] - 0.5) < 1e-12
    assert abs(a["fill_rate"] - 1.0) < 1e-12
    # variants: only A2 (group GA owned); B is a new group for both -> 1 variant / 3 hits
    assert abs(a["variant_share_of_hits"] - 1 / 3) < 1e-12


def test_short_list_counts_as_miss_and_fill_penalized():
    ds = _dataset([
        ("C1", "A", "2026-01-05", "O1", "GA"),
        ("C1", "B", "2026-01-12", "O2", "GB"),
    ], {"C1": "B2B"})
    res = score_recommendations(ds, lambda cid: [])
    assert res["A"]["all"]["precision_at_3"] == 0.0
    assert res["A"]["all"]["fill_rate"] == 0.0
    assert res["A"]["all"]["full_list_rate"] == 0.0


def test_dedup_repeated_recommendation_items():
    ds = _dataset([
        ("C1", "A", "2026-01-05", "O1", "GA"),
        ("C1", "B", "2026-01-12", "O2", "GB"),
    ], {"C1": "B2B"})
    # duplicate B must not count twice
    res = score_recommendations(ds, lambda cid: ["B", "B", "B"])
    assert res["A"]["all"]["precision_at_3"] == pytest.approx(1 / 3)


@pytest.mark.skipif(not HAS_WORKBOOK, reason="книга организаторов не найдена")
def test_golden_reproduction_of_published_baseline_table():
    from reco_mvp.evaluation.organizers import load_official_workbook

    path = os.environ.get("RECO_OFFICIAL_WORKBOOK") or str(WORKBOOK)
    sales_rows, client_rows = load_official_workbook(path)
    ds = OfficialDataset(sales_rows, client_rows)
    assert len(ds.group_a) == 337 and len(ds.group_b) == 172
    pop = score_recommendations(ds, baseline_popularity(ds))
    co = score_recommendations(ds, baseline_copurchase(ds))
    def close(got, want):
        assert abs(got - want) < 0.0002
    # Published organizers' table (rounding 0.01pp)
    close(pop["A"]["all"]["precision_at_3"], 0.0544)
    close(pop["A"]["B2B"]["precision_at_3"], 0.0375)
    close(pop["A"]["B2C"]["precision_at_3"], 0.20)
    close(pop["B"]["all"]["precision_at_3"], 0.0736)
    close(pop["B"]["B2B"]["precision_at_3"], 0.0522)
    close(co["A"]["all"]["precision_at_3"], 0.0564)
    close(co["A"]["B2B"]["precision_at_3"], 0.0453)
    close(co["A"]["B2C"]["precision_at_3"], 0.1524)
    close(co["B"]["all"]["precision_at_3"], 0.0601)
    close(co["B"]["B2B"]["precision_at_3"], 0.0454)
    close(co["B"]["B2C"]["precision_at_3"], 0.1467)
    assert abs(pop["A"]["all"]["fill_rate"] - 1.0) < 1e-9
    assert abs(co["A"]["all"]["fill_rate"] - 0.9228) < 0.001
    assert abs(co["B"]["all"]["fill_rate"] - 0.8721) < 0.001
    # доля вариантов среди попаданий baseline «Совместные покупки»
    close(co["A"]["all"]["variant_share_of_hits"], 0.1053)
    close(co["A"]["B2B"]["variant_share_of_hits"], 0.1220)
    close(co["B"]["all"]["variant_share_of_hits"], 0.0645)
    assert pop["A"]["all"]["variant_share_of_hits"] == 0.0
