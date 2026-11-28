"""Renewal-буст в штатном Recommender: поведение по умолчанию и с включённым параметром."""
import copy

from reco_mvp.recommender import Recommender


def _bundle(products, transactions):
    return {
        "schema_version": "1.0", "data_mode": "synthetic",
        "customers": [{"customer_id": f"C{n}", "segment": "B2B", "known_since": None}
                      for n in range(1, 6)],
        "products": products, "transactions": transactions,
        "metadata": {"observation_end": "2026-03-01T00:00:00Z", "warnings": [],
                     "segment_source": "synthetic", "catalog_history_available": False},
    }


def _week(n):
    return f"2026-01-{n:02d}T00:00:00Z"


def _population_bundle():
    """C1 купил S1 (семейство G). Другие клиенты регулярно покупают S2 вместе с S1."""
    products = [
        {"product_id": "S1", "name": "Базовая подписка", "category": None,
         "eligible_segments": None, "active": True, "available_from": None,
         "product_group": "G"},
        {"product_id": "S2", "name": "Подписка, вариант", "category": None,
         "eligible_segments": None, "active": True, "available_from": None,
         "product_group": "G"},
    ]
    transactions = [{"transaction_id": "t1", "order_id": "o1", "customer_id": "C1",
                     "product_id": "S1", "purchased_at": _week(10), "quantity": 1.0}]
    for i in range(2, 6):
        transactions.append({"transaction_id": f"t{i}a", "order_id": f"o{i}a",
                             "customer_id": f"C{i}", "product_id": "S1",
                             "purchased_at": _week(8), "quantity": 1.0})
        transactions.append({"transaction_id": f"t{i}b", "order_id": f"o{i}b",
                             "customer_id": f"C{i}", "product_id": "S2",
                             "purchased_at": _week(15), "quantity": 1.0})
    return _bundle(products, transactions)


def test_default_config_reproduces_previous_ranking_without_product_group():
    bundle = _population_bundle()
    for row in bundle["products"]:
        row.pop("product_group")
    plain = Recommender().fit(copy.deepcopy(bundle), as_of="2026-02-01T00:00:00Z")
    with_group = Recommender().fit(_population_bundle(), as_of="2026-02-01T00:00:00Z")
    a = plain.recommend("C1", k=3)
    b = with_group.recommend("C1", k=3)
    assert [r["product_id"] for r in a["recommendations"]] == \
           [r["product_id"] for r in b["recommendations"]]


def test_renewal_boost_promotes_same_group_candidate_and_explains():
    off = Recommender({"min_support": 2}).fit(_population_bundle(), as_of="2026-02-01T00:00:00Z")
    on = Recommender({"min_support": 2, "renewal_weight": 4.0, "renewal_tau": 4}).fit(
        _population_bundle(), as_of="2026-02-01T00:00:00Z")
    top_off = off.recommend("C1", k=3)["recommendations"]
    top_on = on.recommend("C1", k=3)["recommendations"]
    score_off = {r["product_id"]: r["score"] for r in top_off}
    score_on = {r["product_id"]: r["score"] for r in top_on}
    # усиление только кандидата того же семейства (S2), никогда уже купленного (S1)
    assert score_on["S2"] > score_off["S2"]
    row = next(r for r in top_on if r["product_id"] == "S2")
    assert "того же семейства" in row["reason_text"]


def test_renewal_config_validation():
    for bad in ({"renewal_weight": -1}, {"renewal_weight": 11}, {"renewal_tau": 0},
                {"group_bonus_cap": 21}):
        try:
            Recommender(bad)
            raise AssertionError(f"ожидался ValueError для {bad}")
        except ValueError:
            pass


def test_product_group_type_validation():
    bundle = _population_bundle()
    bundle["products"][0]["product_group"] = 123
    try:
        Recommender().fit(bundle, as_of="2026-02-01T00:00:00Z")
        raise AssertionError("ожидался ValueError для нестрокового product_group")
    except ValueError:
        pass
