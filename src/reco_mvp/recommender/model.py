"""Standard-library recommendation engine for Bundle contract v1."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from math import sqrt

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
        if set(config) - {"min_support"}:
            raise ValueError(f"Неизвестные параметры: {sorted(set(config) - {'min_support'})}")
        support = config.get("min_support", 2)
        if type(support) is not int or support < 1:
            raise ValueError("min_support должен быть положительным целым числом")
        self.min_support = support
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
            products[pid] = {
                "name": row["name"], "active": active, "eligible_segments": eligible,
                "available_from": _timestamp(row["available_from"], "available_from")
                if row.get("available_from") is not None else None,
            }

        history = defaultdict(set)
        for row in bundle["transactions"]:
            # Future rows are neither counted nor retained in the fitted state.
            if _timestamp(row["purchased_at"], "purchased_at") >= cutoff:
                continue
            cid, pid = row["customer_id"], row["product_id"]
            if cid not in customers or pid not in products:
                raise ValueError("Покупка ссылается на неизвестного клиента или товар")
            history[cid].add(pid)

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

        self._cutoff = cutoff
        self._as_of = cutoff.isoformat().replace("+00:00", "Z")
        self._data_mode = bundle["data_mode"]
        self._products = products
        self._segments = customer_segments
        self._history = {cid: frozenset(pids) for cid, pids in history.items()}
        self._buyers = buyers
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

    def _graph(self, purchased: frozenset, segment: str) -> list[dict]:
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
            result.append({
                "product_id": pid, "product_name": self._products[pid]["name"],
                "score": state["score"], "reason_type": "co_purchase",
                "reason_text": f"Товар покупали {count} клиентов, приобретавших "
                f"хотя бы один из товаров: {names} ({scope}).",
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
        selected = self._graph(purchased, resolved)[:k] if strategy != "popular" and k else []
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
