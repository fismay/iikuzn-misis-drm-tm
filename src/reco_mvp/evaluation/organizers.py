"""Точная реализация протокола проверки организаторов (лист «Протокол_проверки»).

Правила (воспроизведены и сверены с опубликованной таблицей baseline):

- обучение: строки с ``order_week <= train_end`` (по умолчанию 2026-08-31);
- проверка: строки с ``order_week >= test_start`` (по умолчанию 2026-09-07), при
  необходимости с ограничением ``test_end``;
- корзина — пара ``(order_id, order_week)``;
- цель — новые для клиента ``item_id`` периода проверки (товары истории обучения
  не считаются);
- ровно 3 ``item_id`` на клиента без товаров истории; меньше 3 — промах;
- Precision@3 клиента = угаданные / 3 (фиксированный знаменатель), итог — среднее
  по группе; Fill Rate — доля клиентов группы с хотя бы одной рекомендацией;
- группа A — клиенты с покупками обучения и хотя бы одним новым товаром проверки;
  группа B — подмножество A с ровно одним товаром в истории обучения;
- попадание-вариант: ``product_group`` угаданного товара есть в истории клиента.

Модуль не читает файл сам: строки передаёт вызывающий код (``load_official_workbook``
даёт готовое чтение листа «Продажи»/«Клиенты»). Это сохраняет чистоту границ и
тестируемость без внешних зависимостей.
"""
from __future__ import annotations

from collections import defaultdict

TRAIN_END = "2026-08-31"
TEST_START = "2026-09-07"
K = 3


def load_official_workbook(path: str) -> tuple[list[dict], list[dict]]:
    """Читает листы «Продажи» и «Клиенты» книги организаторов в list[dict]."""
    import pandas as pd

    sales = pd.read_excel(path, sheet_name="Продажи", dtype=str)
    customers = pd.read_excel(path, sheet_name="Клиенты", dtype=str)
    sales = sales.dropna(subset=["item_id", "client_id", "order_week", "order_id",
                                 "product_group"])
    sales_rows = [{"order_id": str(r.order_id), "client_id": str(r.client_id),
                   "order_week": str(r.order_week), "item_id": str(r.item_id),
                   "sku": r.sku, "item_name": str(r.item_name),
                   "product_group": str(r.product_group)} for r in sales.itertuples()]
    client_rows = [{"client_id": str(r.client_id), "client_group": str(r.client_group)}
                   for r in customers.itertuples()]
    return sales_rows, client_rows


class OfficialDataset:
    """Официальное разбиение, цели и справочные счётчики для скоринга."""

    def __init__(self, sales_rows, client_rows, *, train_end=TRAIN_END,
                 test_start=TEST_START, test_end=None):
        if test_end is not None and test_end < test_start:
            raise ValueError("test_end раньше test_start")
        self.train_end, self.test_start, self.test_end = train_end, test_start, test_end
        train = [r for r in sales_rows if r["order_week"] <= train_end]
        test = [r for r in sales_rows if r["order_week"] >= test_start]
        if test_end is not None:
            test = [r for r in test if r["order_week"] <= test_end]
        self.train_rows = train
        self.history: dict[str, set[str]] = defaultdict(set)
        self.history_groups: dict[str, set[str]] = defaultdict(set)
        for r in train:
            self.history[r["client_id"]].add(r["item_id"])
            self.history_groups[r["client_id"]].add(r["product_group"])
        future: dict[str, set[str]] = defaultdict(set)
        for r in test:
            future[r["client_id"]].add(r["item_id"])
        self.targets = {}
        for cid, items in future.items():
            fresh = items - self.history.get(cid, set())
            if fresh and cid in self.history:
                self.targets[cid] = fresh
        self.client_group = {r["client_id"]: (r["client_group"]
                          if r["client_group"] in ("B2B", "B2C") else "unknown")
                          for r in client_rows}
        self.group_a = sorted(self.targets)
        self.group_b = sorted(c for c in self.group_a if len(self.history[c]) == 1)
        self.item_group = {r["item_id"]: r["product_group"] for r in sales_rows}
        self.item_name = {}
        for r in sales_rows:
            self.item_name.setdefault(r["item_id"], r["item_name"])
        self.all_items = sorted({r["item_id"] for r in sales_rows})
        popularity: dict[str, set[str]] = defaultdict(set)
        baskets: dict[tuple[str, str], set[str]] = defaultdict(set)
        for r in train:
            popularity[r["item_id"]].add(r["client_id"])
            baskets[(r["order_id"], r["order_week"])].add(r["item_id"])
        self.popularity = {p: len(clients) for p, clients in popularity.items()}
        self.basket_count: dict[str, int] = defaultdict(int)
        self.basket_co: dict[str, dict[str, int]] = defaultdict(dict)
        for _, items in baskets.items():
            for a in items:
                self.basket_count[a] += 1
            for a in sorted(items):
                for b in sorted(items):
                    if a != b:
                        row = self.basket_co[a]
                        row[b] = row.get(b, 0) + 1

    def members(self, group: str) -> list[str]:
        return {"A": self.group_a, "B": self.group_b}[group]


def score_recommendations(dataset: OfficialDataset, recommend_fn, *,
                          groups=("A", "B")) -> dict:
    """Считает официальные метрики для функции ``recommend_fn(client_id) -> list``.

    Возвращает по каждой группе: доля клиентов, Precision@3, Fill Rate и долю
    вариантов среди попаданий — всего и по сегментам B2B/B2C.
    """
    out = {}
    for gname in groups:
        members = dataset.members(gname)
        rows = []
        for cid in members:
            reco = list(recommend_fn(cid))[:K]
            seen = []
            for pid in reco:
                if pid not in seen:
                    seen.append(pid)
            targets = dataset.targets[cid]
            hits = [p for p in seen if p in targets]
            variants = [p for p in hits
                        if dataset.item_group[p] in dataset.history_groups[cid]]
            rows.append({"client_id": cid, "segment": dataset.client_group.get(cid, "unknown"),
                         "n_reco": len(seen), "n_hits": len(hits), "n_variants": len(variants)})
        def block(rows_subset):
            n = len(rows_subset)
            if n == 0:
                return None
            hits = sum(r["n_hits"] for r in rows_subset)
            return {
                "n_customers": n,
                "precision_at_3": hits / (K * n),
                "fill_rate": sum(1 for r in rows_subset if r["n_reco"] > 0) / n,
                "full_list_rate": sum(1 for r in rows_subset if r["n_reco"] == K) / n,
                "variant_share_of_hits": (sum(r["n_variants"] for r in rows_subset) / hits) if hits else None,
            }
        out[gname] = {
            "all": block(rows),
            "B2B": block([r for r in rows if r["segment"] == "B2B"]),
            "B2C": block([r for r in rows if r["segment"] == "B2C"]),
        }
    return out


# --- официальные baseline (для воспроизводимой сверки скорера) ----------------


def baseline_popularity(dataset: OfficialDataset):
    """Популярность: число разных клиентов обучения; ничьи — item_id по алфавиту."""
    def recommend(cid):
        hist = dataset.history.get(cid, set())
        ranked = sorted(dataset.all_items, key=lambda p: (-dataset.popularity.get(p, 0), p))
        return [p for p in ranked if p not in hist][:K]
    return recommend


def baseline_copurchase(dataset: OfficialDataset):
    """Совместные покупки: число корзин обучения с обоими товарами, сумма по истории.

    Ничьи: сначала большая популярность, затем item_id. Кандидаты вне истории;
    пустой список, если пересечений нет (как в baseline организаторов).
    """
    def recommend(cid):
        hist = dataset.history.get(cid, set())
        score: dict[str, int] = defaultdict(int)
        for source in hist:
            for target, count in dataset.basket_co.get(source, {}).items():
                if target not in hist:
                    score[target] += count
        ranked = sorted(score, key=lambda p: (-score[p], -dataset.popularity.get(p, 0), p))
        return ranked[:K]
    return recommend
