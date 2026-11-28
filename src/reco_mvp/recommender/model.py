"""Standard-library recommendation engine for Bundle contract v1."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from math import exp, log1p, sqrt

_EPOCH = date(2024, 1, 1)


def _week_of(moment: datetime) -> int:
    return (moment.astimezone(timezone.utc).date() - _EPOCH).days // 7

_SEGMENTS = {"B2B", "B2C", "unknown"}
_STRATEGIES = {"popular", "graph_only", "hybrid"}


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field}: ожидается ISO 8601 с часовым поясом")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}: некорректная дата {value!r}") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{field}: требуется часовой пояс")
    return result.astimezone(timezone.utc)


def _segment(value: str | None) -> str:
    result = value or "unknown"
    if result not in _SEGMENTS:
        raise ValueError(f"Неизвестный сегмент: {value!r}")
    return result


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field}: ожидается непустая строка")
    return value


class Recommender:
    """Fit once at a cutoff, then reuse sparse buyer and neighbor indexes.

    Config: ``min_support`` (positive integer, default 2).
    Scores are similarities or unique buyer counts, never probabilities.
    """

    def __init__(self, config: dict | None = None):
        config = dict(config or {})
        allowed = {"min_support", "recency_weight", "recency_days", "popularity_exponent",
                   "renewal_weight", "renewal_tau", "group_bonus_cap"}
        if set(config) - allowed:
            raise ValueError(f"Неизвестные параметры: {sorted(set(config) - allowed)}")
        support = config.get("min_support", 2)
        if type(support) is not int or support < 1:
            raise ValueError("min_support должен быть положительным целым числом")
        self.min_support = support
        renewal = config.get("renewal_weight", 0.0)
        tau = config.get("renewal_tau", 26)
        cap = config.get("group_bonus_cap", 3.0)
        if type(renewal) not in (int, float) or not 0 <= renewal <= 10:
            raise ValueError("renewal_weight должен быть числом от 0 до 10")
        if type(tau) not in (int, float) or tau < 1:
            raise ValueError("renewal_tau должен быть положительным числом недель")
        if type(cap) not in (int, float) or not 0 <= cap <= 20:
            raise ValueError("group_bonus_cap должен быть числом от 0 до 20")
        self.renewal_weight = float(renewal)
        self.renewal_tau = float(tau)
        self.group_bonus_cap = float(cap)
        weight = config.get("recency_weight", 0.0)
        days = config.get("recency_days", 120)
        if type(weight) not in (int, float) or not 0 <= weight <= 10:
            raise ValueError("recency_weight должен быть числом от 0 до 10")
        if type(days) is not int or days < 1:
            raise ValueError("recency_days должен быть положительным целым числом")
        exponent = config.get("popularity_exponent")
        if exponent is not None and (type(exponent) not in (int, float) or not 0 <= exponent <= 1):
            raise ValueError("popularity_exponent должен быть числом от 0 до 1 или None")
        self.popularity_exponent = exponent
        self.recency_weight = weight
        self.recency_days = days
        self._fitted = False

    def fit(self, bundle: dict, *, as_of: str) -> Recommender:
        self._fitted = False
        cutoff = _timestamp(as_of, "as_of")
        if bundle.get("schema_version") != "1.0":
            raise ValueError("Поддерживается только schema_version='1.0'")
        if bundle.get("data_mode") not in {"synthetic", "real"}:
            raise ValueError("data_mode должен быть synthetic или real")
        customers = {}
        known_dates = {}
        for row in bundle["customers"]:
            cid = _identifier(row["customer_id"], "customer_id")
            if cid in customers:
                raise ValueError(f"Повторный customer_id: {cid}")
            customers[cid] = _segment(row.get("segment"))
            known_dates[cid] = (
                _timestamp(row["known_since"], "known_since")
                if row.get("known_since") is not None else None
            )
        products = {}
        for row in bundle["products"]:
            pid = _identifier(row["product_id"], "product_id")
            if pid in products:
                raise ValueError(f"Повторный product_id: {pid}")
            active = row.get("active")
            if active is not None and type(active) is not bool:
                raise ValueError(f"active должен быть bool или null: {pid}")
            eligible = row.get("eligible_segments")
            if eligible is not None:
                if not isinstance(eligible, list) or any(s not in _SEGMENTS for s in eligible):
                    raise ValueError(f"Некорректные eligible_segments: {pid}")
                eligible = frozenset(eligible)
            group = row.get("product_group")
            if group is not None and not isinstance(group, str):
                raise ValueError(f"product_group должен быть строкой или null: {pid}")
            products[pid] = {
                "name": row["name"], "active": active, "eligible_segments": eligible,
                "available_from": _timestamp(row["available_from"], "available_from")
                if row.get("available_from") is not None else None,
                "product_group": group,
            }

        history = defaultdict(set)
        recent_buyers = defaultdict(set)
        renewal_on = self.renewal_weight > 0.0
        last_week: dict[str, dict[str, int]] = defaultdict(dict)
        for row in bundle["transactions"]:
            # Future rows are neither counted nor retained in the fitted state.
            purchased_at = _timestamp(row["purchased_at"], "purchased_at")
            if purchased_at >= cutoff:
                continue
            cid, pid = row["customer_id"], row["product_id"]
            if cid not in customers or pid not in products:
                raise ValueError("Покупка ссылается на неизвестного клиента или товар")
            history[cid].add(pid)
            if purchased_at >= cutoff - timedelta(days=self.recency_days):
                recent_buyers[pid].add(cid)
            if renewal_on:
                week = _week_of(purchased_at)
                stored = last_week[cid]
                if week > stored.get(pid, -10**9):
                    stored[pid] = week

        # An explicitly future registration with no past purchase is not known.
        customer_segments = {
            cid: seg for cid, seg in customers.items()
            if cid in history or known_dates[cid] is None or known_dates[cid] < cutoff
        }
        buyers = {group: defaultdict(set) for group in ("all", "B2B", "B2C", "unknown")}
        for cid, purchased in history.items():
            for pid in purchased:
                buyers["all"][pid].add(cid)
                buyers[customers[cid]][pid].add(cid)

        # Sparse item pairs, built per customer's distinct purchased products.
        pairs = {group: defaultdict(set) for group in buyers}
        for cid, purchased in history.items():
            for pair in combinations(sorted(purchased), 2):
                pairs["all"][pair].add(cid)
                pairs[customers[cid]][pair].add(cid)
        neighbors = {group: defaultdict(dict) for group in buyers}
        for group, group_pairs in pairs.items():
            for (left, right), supporters in group_pairs.items():
                if len(supporters) < self.min_support:
                    continue
                similarity = len(supporters) / sqrt(
                    len(buyers[group][left]) * len(buyers[group][right])
                )
                edge = (similarity, frozenset(supporters))
                neighbors[group][left][right] = edge
                neighbors[group][right][left] = edge

        self._renewal_trans = None
        self._client_group_state: dict = {}
        self._last_week: dict = {}
        if renewal_on:
            trans: dict = defaultdict(dict)
            group_state: dict = defaultdict(dict)
            for cid, purchased in history.items():
                weeks = last_week.get(cid, {})
                by_group = defaultdict(set)
                for pid in purchased:
                    group = products[pid]["product_group"]
                    if group:
                        by_group[group].add(pid)
                for group, pids in by_group.items():
                    state = group_state[cid].setdefault(group, {"last": -10**9, "items": set()})
                    for pid in pids:
                        if weeks.get(pid, -10**9) > state["last"]:
                            state["last"] = weeks[pid]
                        state["items"].add(pid)
                    if len(pids) >= 2:
                        for a in pids:
                            row = trans[a]
                            for b in pids:
                                if a != b:
                                    row[b] = row.get(b, 0) + 1
            self._renewal_trans = trans
            self._client_group_state = {c: dict(g) for c, g in group_state.items()}
            self._last_week = dict(last_week)
        self._cutoff = cutoff
        self._cutoff_week = _week_of(cutoff)
        self._as_of = cutoff.isoformat().replace("+00:00", "Z")
        self._data_mode = bundle["data_mode"]
        self._products = products
        self._segments = customer_segments
        self._history = {cid: frozenset(pids) for cid, pids in history.items()}
        self._buyers = buyers
        self._recent_buyers = recent_buyers
        self._neighbors = neighbors
        self._popularity = {
            group: sorted(group_buyers, key=lambda p: (-len(group_buyers[p]), p))
            for group, group_buyers in buyers.items()
        }
        self._warnings = list(bundle.get("metadata", {}).get("warnings", []))
        if not bundle.get("metadata", {}).get("catalog_history_available", False):
            self._warnings.append(
                "История каталога неполна: текущие active и сегментные ограничения "
                "могут отличаться от состояния на дату отсечения."
            )
        self._fitted = True
        return self

    def _eligible(self, pid: str, segment: str, purchased: frozenset) -> bool:
        product = self._products[pid]
        if pid in purchased or product["active"] is False:
            return False
        allowed = product["eligible_segments"]
        if allowed is not None and segment not in allowed:
            return False
        since = product["available_from"]
        return since <= self._cutoff if since is not None else bool(self._buyers["all"].get(pid))

    def _graph(self, purchased: frozenset, segment: str, cid: str | None = None) -> list[dict]:
        candidates = {}
        for source in sorted(purchased):
            local = self._neighbors[segment].get(source, {})
            global_edges = self._neighbors["all"].get(source, {})
            for pid in sorted(local.keys() | global_edges.keys()):
                if not self._eligible(pid, segment, purchased):
                    continue
                used_global = pid not in local
                score, supporters = global_edges[pid] if used_global else local[pid]
                state = candidates.setdefault(pid, {
                    "score": 0.0, "sources": [], "supporters": set(), "global": False,
                })
                state["score"] = max(state["score"], score)
                state["sources"].append(source)
                state["supporters"].update(supporters)
                state["global"] |= used_global
        result = []
        for pid, state in candidates.items():
            count = len(state["supporters"])
            names = ", ".join(self._products[s]["name"] for s in state["sources"])
            scope = "по всем сегментам" if state["global"] else f"в сегменте {segment}"
            boost = 1.0
            if self.renewal_weight and cid is not None:
                group = self._products[pid]["product_group"]
                group_state = self._client_group_state.get(cid, {}).get(group) if group else None
                if group_state:
                    mass = 0
                    for source in group_state["items"]:
                        mass += self._renewal_trans.get(source, {}).get(pid, 0)
                    if mass:
                        action = exp(-max(self._cutoff_week - group_state["last"], 0) / self.renewal_tau)
                        boost = 1.0 + min(self.renewal_weight * log1p(mass) * action,
                                          self.group_bonus_cap)
            reason = (f"Товар покупали {count} клиентов, приобретавших "
                      f"хотя бы один из товаров: {names} ({scope}).")
            if boost > 1.0:
                reason += " Этот товар чаще покупали вместе с уже купленными Вами из того же семейства."
            result.append({
                "product_id": pid, "product_name": self._products[pid]["name"],
                "score": state["score"] * sqrt(count) * (log1p(len(self._buyers["all"][pid])) if self.popularity_exponent is None else len(self._buyers["all"][pid]) ** self.popularity_exponent) * (1 + self.recency_weight * len(self._recent_buyers[pid]) / len(self._buyers["all"][pid])) * boost, "reason_type": "co_purchase",
                "reason_text": reason,
                "source_product_ids": state["sources"], "support_count": count,
                "is_fallback": state["global"],
            })
        return sorted(result, key=lambda row: (-row["score"], row["product_id"]))

    def recommend(self, customer_id: str, *, segment: str | None = None,
                  k: int = 3, strategy: str = "hybrid") -> dict:
        if not self._fitted:
            raise ValueError("Сначала вызовите fit(bundle, as_of=...)")
        _identifier(customer_id, "customer_id")
        if type(k) is not int or k < 0:
            raise ValueError("k должен быть неотрицательным целым числом")
        if strategy not in _STRATEGIES:
            raise ValueError(f"Неизвестная strategy: {strategy!r}")
        explicit = _segment(segment) if segment is not None else None
        known = self._segments.get(customer_id)
        if known is not None and explicit is not None and known != explicit:
            raise ValueError(f"Сегмент клиента {known} противоречит аргументу {explicit}")
        resolved = known if known is not None else (explicit or "unknown")
        purchased = self._history.get(customer_id, frozenset())
        warnings = list(self._warnings)
        selected = self._graph(purchased, resolved, cid=customer_id)[:k] if strategy != "popular" and k else []
        seen = {row["product_id"] for row in selected}
        if strategy != "graph_only":
            for group in (resolved, "all"):
                for pid in self._popularity[group]:
                    if len(selected) >= k:
                        break
                    if pid in seen or not self._eligible(pid, resolved, purchased):
                        continue
                    global_popularity = group == "all"
                    count = len(self._buyers[group][pid])
                    selected.append({
                        "product_id": pid, "product_name": self._products[pid]["name"],
                        "score": float(count),
                        "reason_type": "global_popularity" if global_popularity else "segment_popularity",
                        "reason_text": f"Популярен во всех сегментах: {count} покупателей."
                        if global_popularity else f"Популярен среди клиентов {resolved}: {count} покупателей.",
                        "source_product_ids": [], "support_count": count,
                        "is_fallback": strategy == "hybrid" or global_popularity,
                    })
                    seen.add(pid)
        for rank, row in enumerate(selected, 1):
            row["rank"] = rank
        if len(selected) < k:
            warnings.append(f"Недостаточно допустимых кандидатов для {strategy}: "
                            f"выдано {len(selected)} из {k}.")
        if any(row["reason_type"] == "co_purchase" and row["is_fallback"] for row in selected):
            warnings.append("Часть связей рассчитана по всем сегментам: "
                            "сегментная поддержка ниже min_support.")
        if any(self._products[r["product_id"]]["active"] is None for r in selected):
            warnings.append("Для части рекомендованных товаров active неизвестен; наличие не подтверждено.")
        if any(self._products[r["product_id"]]["eligible_segments"] is None for r in selected):
            warnings.append("Для части рекомендованных товаров нет данных о сегментных ограничениях; "
                            "совместимость не подтверждена.")
        return {
            "schema_version": "1.0", "customer_id": customer_id, "segment": resolved,
            "strategy": strategy, "as_of": self._as_of, "data_mode": self._data_mode,
            "recommendations": selected, "warnings": list(dict.fromkeys(warnings)),
        }
