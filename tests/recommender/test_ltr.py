"""Смоук-тесты обучаемого ранжировщика на синтетике (skip, если нет scikit-learn)."""
import random

import pytest

pytest.importorskip("sklearn")

from reco_mvp.evaluation.organizers import OfficialDataset  # noqa: E402
from reco_mvp.recommender.ltr import LtrAgent, INTERNAL_TRAIN_WINDOWS  # noqa: E402

sklearn = pytest.importorskip("sklearn")


def _synthetic_book(seed=7):
    """Мини-книга: клиенты, товары с семействами, недели; детерминированно."""
    rng = random.Random(seed)
    items = [f"P{i:02d}" for i in range(20)]
    groups = {p: f"G{i % 5}" for i, p in enumerate(items)}
    names = {p: f"Товар {p} семейства {groups[p]}" for p in items}
    rows = []
    seq = 0
    weeks = [f"2026-0{m}-01" for m in range(1, 7)]
    for c in range(40):
        cid = f"C{c:03d}"
        for _ in range(rng.randint(1, 4)):
            if rng.random() < 0.6:
                base = rng.choice(items[:10])
                pool = [p for p in items[:10] if groups[p] == groups[base]]
            else:
                pool = items
            pid = rng.choice(pool)
            rows.append({"client_id": cid, "item_id": pid, "sku": pid,
                         "item_name": names[pid], "product_group": groups[pid],
                         "order_id": f"O{c}_{seq}", "order_week": rng.choice(weeks[:5])})
            seq += 1
    test_rows = []
    for c in range(12):
        cid = f"C{c:03d}"
        hist = {r["item_id"] for r in rows if r["client_id"] == cid}
        fresh = [p for p in items if p not in hist][:3]
        for pid in fresh:
            test_rows.append({"client_id": cid, "item_id": pid, "sku": pid,
                              "item_name": names[pid], "product_group": groups[pid],
                              "order_id": f"T{c}_{pid}", "order_week": weeks[5]})
    client_rows = [{"client_id": f"C{c:03d}", "client_group": "B2B" if c % 2 else "B2C"}
                   for c in range(40)]
    return rows + test_rows, client_rows, names, groups, items


@pytest.fixture(scope="module")
def book():
    return _synthetic_book()


def _datasets(book):
    sales, clients, *_ = book
    windows = [
        ("2026-01-01", "2026-01-08", "2026-02-01"),
        ("2026-02-01", "2026-02-08", "2026-03-01"),
        ("2026-03-01", "2026-03-08", "2026-04-01"),
    ]
    train = [OfficialDataset(sales, clients, train_end=te, test_start=ts, test_end=te2)
             for te, ts, te2 in windows]
    target = OfficialDataset(sales, clients, train_end="2026-04-01",
                             test_start="2026-04-08", test_end="2026-05-01")
    return train, target


def test_ltr_trains_and_recommends_deterministically(tmp_path, book):
    _, _, names, groups, items = book
    train, target = _datasets(book)
    if not any(t for ds in train for t in ds.targets.values()):
        pytest.skip("в синтетике нет целей — тест требует размеченных окон")
    kwargs = dict(item_names=names, item_groups=groups, all_items=items)
    agent = LtrAgent(target, **kwargs)
    agent.train(train, **kwargs)
    assert agent.meta["positives"] > 0
    members = target.group_a[:5]
    first = [agent.recommend(cid) for cid in members]
    second = [agent.recommend(cid) for cid in members]
    assert first == second
    for cid, reco in zip(members, first):
        assert len(reco) <= 3
        assert not set(reco) & target.history.get(cid, set())


def test_ltr_save_load_roundtrip(tmp_path, book):
    _, _, names, groups, items = book
    train, target = _datasets(book)
    kwargs = dict(item_names=names, item_groups=groups, all_items=items)
    agent = LtrAgent(target, **kwargs)
    agent.train(train, **kwargs)
    path = tmp_path / "ranker.pkl"
    agent.save(str(path))
    model, meta = LtrAgent.load_model(str(path))
    revived = LtrAgent(target, **kwargs)
    revived.use_model(model, meta)
    for cid in target.group_a[:3]:
        assert agent.recommend(cid) == revived.recommend(cid)


def test_ltr_requires_trained_model(book):
    _, _, names, groups, items = book
    _, target = _datasets(book)
    agent = LtrAgent(target, item_names=names, item_groups=groups, all_items=items)
    with pytest.raises(ValueError):
        agent.recommend("C001")


def test_ltr_feed_mismatch_guard(tmp_path, book):
    _, _, names, groups, items = book
    train, target = _datasets(book)
    kwargs = dict(item_names=names, item_groups=groups, all_items=items)
    agent = LtrAgent(target, feed_maps={p: {"vendor": "v", "cats": set()} for p in items}, **kwargs)
    agent.train(train, feed_maps={p: {"vendor": "v", "cats": set()} for p in items}, **kwargs)
    plain = LtrAgent(target, **kwargs)
    with pytest.raises(ValueError):
        plain.use_model(agent._model, agent.meta)


def test_internal_windows_precede_official_cutoff():
    from reco_mvp.evaluation.organizers import TEST_START
    assert all(te2 < TEST_START for _, _, te2 in INTERNAL_TRAIN_WINDOWS)
