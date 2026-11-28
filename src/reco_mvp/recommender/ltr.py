"""Обучаемый ранжировщик кросс-продаж (LTR) поверх эвристического кандидatoгенератора.

Модель ранжирует кандидатов штатного рекомендателя (ко-покупки + популярность +
похожие клиенты) градиентным бустингом по признакам доказательств. sklearn
подключается лениво: без него модуль поднимает понятный ImportError, а эвристика
продолжает работать.

Честность обучения: ранжировщик обучается только наEarlier-окнах строго до
официального отсечения (константа ``INTERNAL_TRAIN_WINDOWS``); разметка каждого
окна — покупки его собственного будущего среза. Официальный тестовый период
никогда не участвует в обучении и подборе гиперпараметров (см.
docs/model/learned_ranker.md).
"""
from __future__ import annotations

from collections import defaultdict
from math import exp, log1p, sqrt

from ..evaluation.organizers import OfficialDataset

# Единая шкала недель с Recommender: тот же epoch, floor по дням.
from .model import _EPOCH


def _week_of_date(day) -> int:
    return (day - _EPOCH).days // 7

# Окна для обучения ранжировщика: strictly до официального разбиения.
# (train_end, test_start, test_end) — будущий срез каждого окна даёт разметку.
INTERNAL_TRAIN_WINDOWS = [
    ("2026-01-25", "2026-01-26", "2026-02-22"),
    ("2026-02-22", "2026-02-23", "2026-03-01"),
    ("2026-03-01", "2026-03-02", "2026-03-29"),
    ("2026-03-29", "2026-03-30", "2026-04-26"),
    ("2026-04-26", "2026-04-27", "2026-05-03"),
    ("2026-05-03", "2026-05-04", "2026-05-31"),
]

RECENCY_TAU_WEEKS = 26.0

FEATURES = [
    "max_cos_all", "max_cos_seg", "n_sources", "sum_raw", "max_raw", "max_cond_a",
    "basket_max", "basket_cond", "pop_all_log", "pop_rank_pct", "pop_seg_log",
    "seg_ratio", "pop_rec8_log", "pop_rec26_log", "rec_ratio", "in_own_group",
    "group_mass_log", "group_active", "group_pop_rank", "weeks_since_group",
    "same_vendor_cnt", "vendor_recent_log", "same_cat_cnt", "n_hist", "n_hist_weeks",
    "weeks_since_last", "b2b", "knn_sim", "knn_hits", "heuristic_rank_pct",
    "renewal_phase", "group_renew_rate", "group_monthly", "name_idf_sim", "max_src_age",
    "src_decay", "hist_has_same_item",
]

_PARAMS = {"max_iter": 350, "learning_rate": 0.05, "max_depth": 4,
           "min_samples_leaf": 120, "l2_regularization": 1.0}

_NAME_STOP = {"лицензия", "право", "использования", "программы", "для", "эвм", "программа",
              "версии", "edition", "russian", "поставки", "электронная", "дистрибутив",
              "подписка", "базовая", "расширенная", "годовая", "русская"}


def _require_sklearn():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise ImportError(
            "Для обучаемого ранжировщика установите extra: "
            "pip install 'reco-mvp[ranker]' (scikit-learn). "
            "Эвристический Recommender работает без него."
        ) from exc
    return HistGradientBoostingClassifier


class WindowEvidence:
    """Статистики обучающего окна для признаков (строго из его train-строк)."""

    def __init__(self, dataset: OfficialDataset, *, cutoff_week: int, item_names: dict,
                 item_groups: dict, all_items: list[str], feed_maps: dict | None = None):
        self.seg = dict(dataset.client_group)
        self.item_group = dict(item_groups)
        self.item_name = dict(item_names)
        self.cutoff = int(cutoff_week)
        self.history = {c: set(v) for c, v in dataset.history.items()}
        self.history_groups = {c: set(v) for c, v in dataset.history_groups.items()}
        self.buyers: dict[str, set[str]] = defaultdict(set)
        self.client_weeks: dict[str, set] = defaultdict(set)
        baskets: dict[tuple, set[str]] = defaultdict(set)
        for row in dataset.train_rows:
            cid, it = row["client_id"], row["item_id"]
            week = _week_of_date(_date(row["order_week"]))
            self.client_weeks[cid].add((it, week))
            self.buyers[it].add(cid)
            baskets[(row["order_id"], row["order_week"])].add(it)
        # сортировка пар (товар, неделя): порядок обхода не должен зависеть от
        # рандомизации хеша — иначе узловые выборы knn и пул кандидатов плавают
        self.client_weeks = {cid: sorted(pairs) for cid, pairs in self.client_weeks.items()}
        self.buyer_count = {p: len(v) for p, v in self.buyers.items()}
        self.train_max_week = max((w for ws in self.client_weeks.values() for _, w in ws),
                                  default=0)
        self.basket_co = {a: dict(t) for a, t in dataset.basket_co.items()}
        self.basket_count_src = dict(dataset.basket_count)
        # client-level (cross-basket) co-occurrence
        co_raw: dict[str, dict[str, int]] = defaultdict(dict)
        for _, purchases in self.client_weeks.items():
            items = sorted({it for it, _ in purchases})
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    co_raw[a][b] = co_raw[a].get(b, 0) + 1
                    co_raw[b][a] = co_raw[b].get(a, 0) + 1
        self.client_co_raw = co_raw
        # popularity by recency buckets
        self.pop_8: dict[str, set[str]] = defaultdict(set)
        self.pop_26: dict[str, set[str]] = defaultdict(set)
        for cid, purchases in self.client_weeks.items():
            for it, w in purchases:
                if self.cutoff - w <= 8:
                    self.pop_8[it].add(cid)
                if self.cutoff - w <= 26:
                    self.pop_26[it].add(cid)
        # segments and popularity ranks
        seg_acc: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
        for cid, purchases in self.client_weeks.items():
            segment = self.seg.get(cid, "unknown")
            for it, _ in purchases:
                seg_acc[segment][it].add(cid)
        buyers_seg = {s: {p: len(v) for p, v in d.items()} for s, d in seg_acc.items()}
        self.buyers_seg = buyers_seg
        self.order_all = sorted(self.buyer_count, key=lambda p: (-self.buyer_count[p], p))
        self.rank_pct_all = {p: 1 - i / max(len(self.order_all) - 1, 1)
                             for i, p in enumerate(self.order_all)}
        self.order_seg = {}
        for s, d in buyers_seg.items():
            ordered = sorted(d, key=lambda p: (-d[p], p))
            self.order_seg[s] = {p: 1 - i / max(len(ordered) - 1, 1) for i, p in enumerate(ordered)}
        # population transitions within product groups
        trans: dict[str, dict[str, int]] = defaultdict(dict)
        self.group_renew_rate_num: dict[str, int] = defaultdict(int)
        self.group_renew_rate_den: dict[str, int] = defaultdict(int)
        self.group_monthly_num: dict[str, int] = defaultdict(int)
        self._group_items = defaultdict(set)
        for it, g in self.item_group.items():
            if g is not None:
                self._group_items[g].add(it)
        for cid, purchases in self.client_weeks.items():
            per_group_skus = defaultdict(set)
            per_group_weeks = defaultdict(set)
            for it, w in purchases:
                g = self.item_group.get(it)
                if g is None:
                    continue
                per_group_skus[g].add(it)
                per_group_weeks[g].add(w)
            for g, skus in per_group_skus.items():
                self.group_renew_rate_den[g] += 1
                if len(skus) >= 2:
                    self.group_renew_rate_num[g] += 1
                    for a in skus:
                        row = trans[a]
                        for b in skus:
                            if a != b:
                                row[b] = row.get(b, 0) + 1
                if len(per_group_weeks[g]) >= 3:
                    self.group_monthly_num[g] += 1
        self.trans = trans
        self.group_renew_rate = {g: self.group_renew_rate_num[g] / max(d, 1)
                                 for g, d in self.group_renew_rate_den.items()}
        self.group_monthly = {g: self.group_monthly_num[g] / max(d, 1)
                              for g, d in self.group_renew_rate_den.items()}
        self.group_items = dict(self._group_items)
        # per-client last weeks per item/group, owned skus per group
        self.client_last: dict[str, dict[str, int]] = {}
        self.client_last_group: dict[str, dict[str, int]] = {}
        self.client_owned_in_group: dict[str, dict[str, int]] = {}
        for cid, purchases in self.client_weeks.items():
            last: dict[str, int] = {}
            lastg: dict[str, int] = {}
            owned: dict[str, set] = defaultdict(set)
            for it, w in purchases:
                if w > last.get(it, -10**9):
                    last[it] = w
                g = self.item_group.get(it)
                if g is not None:
                    if w > lastg.get(g, -10**9):
                        lastg[g] = w
                    owned[g].add(it)
            self.client_last[cid] = last
            self.client_last_group[cid] = lastg
            self.client_owned_in_group[cid] = {g: len(s) for g, s in owned.items()}
        # knn vectors (recency decayed) and inverted index
        self.client_vec: dict[str, dict[str, float]] = {}
        inv: dict[str, set[str]] = defaultdict(set)
        for cid, purchases in self.client_weeks.items():
            v = {}
            for it, w in purchases:
                d = exp(-(self.train_max_week - w) / RECENCY_TAU_WEEKS)
                v[it] = d
                inv[it].add(cid)
            self.client_vec[cid] = v
        self.knn_inv = dict(inv)
        # catalog enrichment (vendor/category) — optional
        self.vendor: dict[str, str] = {}
        self.cats: dict[str, set[str]] = {}
        vend_buyers: dict[str, set[str]] = defaultdict(set)
        vend_recent: dict[str, set[str]] = defaultdict(set)
        if feed_maps:
            for it, info in feed_maps.items():
                vendor = (info.get("vendor") or "").lower()
                if vendor:
                    self.vendor[it] = vendor
                self.cats[it] = {c for c in info.get("cats", ()) if len(c) > 3}
            for cid, purchases in self.client_weeks.items():
                for it, w in purchases:
                    vendor = self.vendor.get(it)
                    if vendor:
                        vend_buyers[vendor].add(cid)
                        if self.cutoff - w <= 8:
                            vend_recent[vendor].add(cid)
        self.vend_recent = {k: len(v) for k, v in vend_recent.items()}
        # name tokens + IDF over the whole sales vocabulary
        import re
        import unicodedata
        self.name_tokens: dict[str, set[str]] = {}
        df: dict[str, int] = defaultdict(int)
        for it in all_items:
            text = unicodedata.normalize("NFKC", str(self.item_name.get(it, ""))).lower()
            toks = {t for t in re.findall(r"[а-яa-z0-9]+", text)
                    if len(t) > 2 and t not in _NAME_STOP}
            self.name_tokens[it] = toks
            for t in toks:
                df[t] += 1
        ntot = max(len(all_items), 1)
        self.idf = {t: log1p(ntot / (1 + c)) for t, c in df.items()}
        self.client_toks = {}
        for cid, items in self.history.items():
            s: set[str] = set()
            for p in items:
                s |= self.name_tokens.get(p, set())
            self.client_toks[cid] = s
        self._heur_cache: dict[str, dict[str, float]] = {}

    # --- candidate generation (identical to the evaluated experiment) ---------

    def heuristic_scores(self, cid: str) -> dict[str, float]:
        if cid in self._heur_cache:
            return self._heur_cache[cid]
        hist = self.history.get(cid, set())
        segment = self.seg.get(cid, "unknown")
        out: dict[str, float] = {}
        for src in sorted(hist):
            nb_raw = self.client_co_raw.get(src, {})
            bsrc = max(self.buyer_count.get(src, 1), 1)
            for tgt, raw in nb_raw.items():
                if tgt in hist:
                    continue
                cos = raw / sqrt(bsrc * max(self.buyer_count.get(tgt, 1), 1))
                rec = 1 + 1.0 * len(self.pop_8.get(tgt, ())) / max(self.buyer_count.get(tgt, 1), 1)
                pop = self.buyer_count.get(tgt, 0) ** 0.2
                out[tgt] = max(out.get(tgt, 0.0), cos * sqrt(raw) * pop * rec)
        seg_pop = self.buyers_seg.get(segment, {})
        mx = max(out.values()) if out else 1.0
        top_pop = sorted(set(self.order_all[:60])
                         | set(sorted(seg_pop, key=lambda p: (-seg_pop.get(p, 0), p))[:40]),
                         key=lambda p: (-seg_pop.get(p, 0), -self.buyer_count.get(p, 0), p))
        for i, p in enumerate(top_pop):
            if p not in hist:
                out.setdefault(p, mx * 0.001 * max(0.0, len(top_pop) - i) / len(top_pop))
        self._heur_cache[cid] = out
        return out

    def knn_top(self, cid: str, topk=40):
        v = self.client_vec.get(cid)
        if not v:
            return []
        norm = sqrt(sum(x * x for x in v.values()))
        common: dict[str, list[str]] = defaultdict(list)
        for it in v:
            for other in sorted(self.knn_inv.get(it, ())):
                if other != cid:
                    common[other].append(it)
        scored = []
        for other, shared in common.items():
            ov = self.client_vec[other]
            dot = sum(v[it] * ov[it] for it in shared)
            if dot <= 0:
                continue
            on = sqrt(sum(x * x for x in ov.values()))
            scored.append((dot / (norm * on), other))
        scored.sort(key=lambda t: -t[0])
        return scored[:topk]

    def candidates(self, cid: str, include_targets=()):
        hist = self.history.get(cid, set())
        heur = self.heuristic_scores(cid)
        cand = sorted(heur, key=lambda p: (-heur[p], p))[:80]
        cset = set(cand)
        for _, other in self.knn_top(cid, 20):
            for it in self.client_vec.get(other, {}):
                if it not in hist and it not in cset:
                    cset.add(it)
                    cand.append(it)
        for p in include_targets:
            if p not in cset:
                cset.add(p)
                cand.append(p)
        return cand, heur

    def features(self, cid, pid, heur, knn=None, heur_rank_pct=0.0):
        hist = self.history.get(cid, frozenset())
        segment = self.seg.get(cid, "unknown")
        f = dict.fromkeys(FEATURES, 0.0)
        max_all = max_seg = 0.0
        n_src = sum_raw = max_raw = 0
        max_cond = b_max = b_cond = 0.0
        for src in hist:
            raw = self.client_co_raw.get(src, {}).get(pid, 0)
            if not raw:
                continue
            n_src += 1
            sum_raw += raw
            max_raw = max(max_raw, raw)
            cos = raw / sqrt(max(self.buyer_count.get(src, 1), 1)
                             * max(self.buyer_count.get(pid, 1), 1))
            max_all = max(max_all, cos)
            if (self.buyers_seg.get(segment, {}).get(src, 0) >= 2
                    and self.buyers_seg.get(segment, {}).get(pid, 0) >= 2):
                max_seg = max(max_seg, cos)
            max_cond = max(max_cond, raw / max(self.buyer_count.get(src, 1), 1))
            bc = self.basket_co.get(src, {}).get(pid, 0)
            if bc:
                b_max = max(b_max, bc)
                b_cond = max(b_cond, bc / max(self.basket_count_src.get(src, 1), 1))
        f["max_cos_all"], f["max_cos_seg"] = max_all, max_seg
        f["n_sources"], f["sum_raw"], f["max_raw"] = n_src, sum_raw, max_raw
        f["max_cond_a"], f["basket_max"], f["basket_cond"] = max_cond, b_max, b_cond
        p_all = self.buyer_count.get(pid, 0)
        f["pop_all_log"] = log1p(p_all)
        f["pop_rank_pct"] = self.rank_pct_all.get(pid, 0.0)
        p_seg = self.buyers_seg.get(segment, {}).get(pid, 0)
        f["pop_seg_log"] = log1p(p_seg)
        f["seg_ratio"] = p_seg / (p_all + 1)
        f["pop_rec8_log"] = log1p(len(self.pop_8.get(pid, ())))
        f["pop_rec26_log"] = log1p(len(self.pop_26.get(pid, ())))
        f["rec_ratio"] = len(self.pop_8.get(pid, ())) / (len(self.pop_26.get(pid, ())) + 1)
        g = self.item_group.get(pid)
        if g and g in self.history_groups.get(cid, set()):
            f["in_own_group"] = 1.0
            mass = sum(self.trans.get(s, {}).get(pid, 0) for s in hist if self.item_group.get(s) == g)
            f["group_mass_log"] = log1p(mass)
            last = self.client_last_group.get(cid, {}).get(g, -10**9)
            since = max(self.cutoff - last, 0)
            f["weeks_since_group"] = min(since, 104)
            f["group_active"] = exp(-since / 26)
            f["renewal_phase"] = (since % 4) / 4.0
            f["group_renew_rate"] = self.group_renew_rate.get(g, 0.0)
            f["group_monthly"] = self.group_monthly.get(g, 0.0)
            f["hist_has_same_item"] = self.client_owned_in_group.get(cid, {}).get(g, 0)
            items_g = self.group_items.get(g, set())
            f["group_pop_rank"] = (sorted(items_g, key=lambda p: (-self.buyer_count.get(p, 0), p)).index(pid)
                                   if pid in items_g else 99)
        ctoks = self.client_toks.get(cid, set())
        ptoks = self.name_tokens.get(pid, set())
        if ctoks and ptoks:
            inter = sum(self.idf.get(t, 0.0) ** 2 for t in sorted(ctoks & ptoks))
            nc = sqrt(sum(self.idf.get(t, 0.0) ** 2 for t in sorted(ctoks)) or 1.0)
            np_ = sqrt(sum(self.idf.get(t, 0.0) ** 2 for t in sorted(ptoks)) or 1.0)
            f["name_idf_sim"] = inter / (nc * np_)
        last_map = self.client_last.get(cid, {})
        ages = [self.cutoff - last_map[s] for s in sorted(hist)
                if s in self.client_co_raw and self.client_co_raw[s].get(pid)]
        if ages:
            f["max_src_age"] = min(max(ages), 104)
            f["src_decay"] = exp(-min(max(ages), 104) / 26)
        v = self.vendor.get(pid)
        same_v = sum(1 for src in sorted(hist) if v and self.vendor.get(src) == v)
        f["same_vendor_cnt"] = same_v / (len(hist) + 1)
        f["vendor_recent_log"] = log1p(self.vend_recent.get(v, 0)) if v else 0.0
        pc = self.cats.get(pid, set())
        f["same_cat_cnt"] = sum(len(pc & self.cats.get(src, set())) for src in sorted(hist)) / (len(hist) + 1)
        weeks_map = self.client_last.get(cid, {})
        f["n_hist"] = len(hist)
        f["n_hist_weeks"] = len(set(weeks_map.values()))
        f["weeks_since_last"] = min(max(self.cutoff - max(weeks_map.values(), default=-10**9), 0), 104)
        f["b2b"] = 1.0 if segment == "B2B" else 0.0
        if knn is None:
            knn = self.knn_top(cid, 10)
        sims = 0.0
        hits = 0
        for s, other in knn[:10]:
            if pid in self.client_vec.get(other, {}):
                sims += s
                hits += 1
        f["knn_sim"], f["knn_hits"] = sims, hits
        f["heuristic_rank_pct"] = heur_rank_pct
        return f

    def rank(self, cid: str, model, top_k=3):
        cand, heur = self.candidates(cid)
        hrank = sorted(heur, key=lambda p: (-heur[p], p))
        pos = {p: 1 - i / max(len(hrank) - 1, 1) for i, p in enumerate(hrank)}
        knn = self.knn_top(cid, 10)
        hist = self.history.get(cid, frozenset())
        cand = [p for p in cand if p not in hist]
        if not cand:
            return []
        rows = [[self.features(cid, p, heur, knn, pos.get(p, 0.0))[f] for f in FEATURES]
                for p in cand]
        proba = model.predict_proba(_as_matrix(rows))[:, 1]
        order = sorted(zip(cand, proba), key=lambda t: (-t[1], t[0]))
        return [p for p, _ in order[:top_k]]


def _as_matrix(rows):
    import numpy as np
    return np.array(rows, dtype="float64")


def _date(week_str: str):
    from datetime import date
    y, m, d = map(int, week_str.split("-"))
    return date(y, m, d)


class LtrAgent:
    """Ранжирующий агент: эвристические кандидаты + обученный ранжировщик."""

    def __init__(self, window: OfficialDataset, *, cutoff_week: int | None = None,
                 item_names, item_groups, all_items, feed_maps=None):
        self.window = window
        self.evidence = WindowEvidence(
            window, cutoff_week=cutoff_week if cutoff_week is not None
            else _week_of_date(_date(window.test_start)),
            item_names=item_names, item_groups=item_groups, all_items=all_items,
            feed_maps=feed_maps)
        self.feed_available = bool(feed_maps)
        self._model = None
        self.meta: dict = {}

    def train(self, training_datasets: list[OfficialDataset], *, item_names, item_groups,
              all_items, feed_maps=None, params=None):
        """Обучение на внутренних окнах (их будущее даёт разметку)."""
        clf_cls = _require_sklearn()
        X, y = [], []
        for ds in training_datasets:
            ev = WindowEvidence(ds, cutoff_week=_week_of_date(_date(ds.test_start)),
                                item_names=item_names, item_groups=item_groups,
                                all_items=all_items, feed_maps=feed_maps)
            for cid in ds.group_a:
                targets = set(ds.targets.get(cid, ()))
                # sorted: порядок строк обучения должен быть стабильным — границы
                # биннинга sklearn чувствительны к порядку строк при весами
                cand, heur = ev.candidates(cid, include_targets=sorted(targets))
                hrank = sorted(heur, key=lambda p: (-heur[p], p))
                pos = {p: 1 - i / max(len(hrank) - 1, 1) for i, p in enumerate(hrank)}
                knn = ev.knn_top(cid, 10)
                for pid in cand:
                    X.append([ev.features(cid, pid, heur, knn, pos.get(pid, 0.0))[f]
                              for f in FEATURES])
                    y.append(1 if pid in targets else 0)
        if not any(y):
            raise ValueError("Нет положительных примеров в окнах обучения")
        import numpy as np
        mat = np.array(X, dtype="float64")
        yv = np.array(y)
        posw = (len(y) - sum(y)) / max(sum(y), 1)
        sw = np.where(yv == 1, posw, 1.0)
        params = dict(_PARAMS, **(params or {}))
        clf = clf_cls(random_state=42, **params)
        # многопоточный сбор гистограмм sklearn делает границы биннов
        # зависимыми от порядка редукции: фиксируем однопоточный режим через
        # threadpoolctl (зависимость идёт в комплекте со scikit-learn)
        try:
            from threadpoolctl import threadpool_limits
            with threadpool_limits(limits=1, user_api="openmp"):
                clf.fit(mat, yv, sample_weight=sw)
        except ImportError:  # pragma: no cover
            clf.fit(mat, yv, sample_weight=sw)
        self._model = clf
        self.meta = {"features": list(FEATURES), "params": params,
                     "rows": len(y), "positives": int(sum(y)),
                     "feed_available": self.feed_available,
                     "windows": [{"train_end": ds.train_end, "test_start": ds.test_start,
                                  "test_end": ds.test_end} for ds in training_datasets]}
        return self

    def recommend(self, customer_id: str, *, k: int = 3) -> list[str]:
        if self._model is None:
            raise ValueError("Сначала вызовите train(...) или загрузите модель")
        return self.evidence.rank(customer_id, self._model, top_k=k)

    # persistence ------------------------------------------------------------
    def save(self, path: str):
        import pickle
        with open(path, "wb") as fh:
            pickle.dump({"model": self._model, "meta": self.meta}, fh)

    @classmethod
    def load_model(cls, path: str):
        import pickle
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        return payload["model"], payload.get("meta", {})

    def use_model(self, model, meta: dict):
        if bool(meta.get("feed_available")) != self.feed_available:
            raise ValueError(
                "Модель обучена с другим набором источников каталога: "
                f"feed_available={meta.get('feed_available')}, окружение={self.feed_available}"
            )
        self._model = model
        self.meta = meta
        return self
