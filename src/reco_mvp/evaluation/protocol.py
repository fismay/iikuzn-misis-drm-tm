"""Point-in-time backtest protocol, leakage prevention, and contract checks."""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from contracts.validate import validate_bundle

VALID_SEGMENTS: frozenset[str] = frozenset({"B2B", "B2C", "unknown"})
VALID_STRATEGIES: frozenset[str] = frozenset({"popular", "graph_only", "hybrid"})
VALID_REASON_TYPES: frozenset[str] = frozenset(
    {"co_purchase", "segment_popularity", "global_popularity"}
)
POPULARITY_REASON_TYPES: frozenset[str] = frozenset(
    {"segment_popularity", "global_popularity"}
)


def parse_utc_timestamp(value: str, field: str) -> datetime:
    """Parse a timezone-aware ISO 8601 timestamp string and normalize to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty ISO 8601 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not valid ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset or 'Z': {value!r}")
    return parsed.astimezone(timezone.utc)


def format_utc_timestamp(dt: datetime) -> str:
    """Format a timezone-aware datetime as canonical UTC ISO 8601 with 'Z'."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def is_product_eligible_for_segment(product: dict, segment: str) -> bool:
    """Evaluate segment eligibility strictly according to Shared Contract v1.

    eligible_segments=None means the source supplied no restriction (allowed,
    with a warning). When eligible_segments is provided, unknown customers are
    permitted only if 'unknown' is explicitly listed.
    """
    eligible = product.get("eligible_segments")
    if eligible is None:
        return True
    return segment in eligible


@dataclass(frozen=True)
class PointInTimeSnapshot:
    """Leak-free train/test split and point-in-time catalog at as_of."""

    data_mode: str
    as_of_dt: datetime
    as_of_iso: str
    horizon_days: int
    horizon_end_dt: datetime
    horizon_end_iso: str
    observation_end_dt: datetime
    observation_end_iso: str
    train_bundle: dict
    main_customers: tuple[dict, ...]
    customer_cohorts: dict[str, str]
    true_train_products_by_customer: dict[str, frozenset[str]]
    future_targets_by_customer: dict[str, frozenset[str]]
    eligible_catalog_products: dict[str, dict]
    eligible_catalog_by_segment: dict[str, frozenset[str]]
    order_count_method: str
    catalog_summary: dict
    cohort_counts: dict
    excluded_future_purchases: dict
    warnings: list[str]
    segment_limitations: list[str]


@dataclass(frozen=True)
class SimulatedColdSplit:
    """Controlled holdout simulation where selected customers' histories are removed."""

    enabled: bool
    seed: int
    fraction: float
    selected_customers: tuple[dict, ...]
    sim_train_bundle: dict | None
    n_remaining_train_customers: int
    note: str


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_point_in_time_snapshot(
    bundle: dict,
    *,
    as_of: str,
    horizon_days: int = 30,
) -> PointInTimeSnapshot:
    """Construct a physically leak-free point-in-time evaluation snapshot.

    All features, customers, catalog items, and transactions exposed to fit()
    strictly precede as_of (transactions.purchased_at < as_of).
    """
    validate_bundle(bundle)

    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days <= 0:
        raise ValueError(f"horizon_days must be a positive integer, got {horizon_days!r}")

    as_of_dt = parse_utc_timestamp(as_of, "as_of")
    as_of_iso = format_utc_timestamp(as_of_dt)
    horizon_end_dt = as_of_dt + timedelta(days=horizon_days)
    horizon_end_iso = format_utc_timestamp(horizon_end_dt)

    metadata = bundle["metadata"]
    observation_end_dt = parse_utc_timestamp(metadata["observation_end"], "observation_end")
    observation_end_iso = format_utc_timestamp(observation_end_dt)

    if observation_end_dt < horizon_end_dt:
        raise ValueError(
            "observation_end does not cover the full evaluation window "
            f"[{as_of_iso}, {horizon_end_iso}): observation_end={observation_end_iso}. "
            "Cannot run a valid final backtest on an incomplete horizon."
        )

    warnings: list[str] = list(metadata.get("warnings") or [])
    segment_limitations: list[str] = []

    if not metadata.get("catalog_history_available", False):
        warnings.append(
            "Catalog history is unavailable (catalog_history_available=false): "
            "current active and eligible_segments flags are used as an approximation "
            "and cannot be treated as a complete historical snapshot."
        )

    products_by_id: dict[str, dict] = {p["product_id"]: p for p in bundle["products"]}
    customers_by_id: dict[str, dict] = {c["customer_id"]: c for c in bundle["customers"]}

    # Partition transactions strictly using parsed UTC timestamps.
    train_txs: list[dict] = []
    window_txs: list[dict] = []
    post_window_txs_count = 0

    for tx in bundle["transactions"]:
        tx_dt = parse_utc_timestamp(tx["purchased_at"], "purchased_at")
        if tx_dt < as_of_dt:
            train_txs.append(tx)
        elif tx_dt < horizon_end_dt:
            window_txs.append(tx)
        else:
            post_window_txs_count += 1

    train_txs.sort(
        key=lambda t: (
            parse_utc_timestamp(t["purchased_at"], "purchased_at"),
            t["transaction_id"],
        )
    )

    train_txs_by_customer: dict[str, list[dict]] = {}
    true_train_products_by_customer_mut: dict[str, set[str]] = {}
    train_order_keys: set[tuple[str, str]] = set()
    sold_before_as_of_ids: set[str] = set()

    for tx in train_txs:
        cid = tx["customer_id"]
        pid = tx["product_id"]
        train_txs_by_customer.setdefault(cid, []).append(tx)
        true_train_products_by_customer_mut.setdefault(cid, set()).add(pid)
        sold_before_as_of_ids.add(pid)
        oid = tx.get("order_id")
        if isinstance(oid, str) and oid != "":
            train_order_keys.add((cid, oid))

    # Build conservative point-in-time historical catalog.
    eligible_catalog_products: dict[str, dict] = {}
    excluded_inactive = 0
    excluded_late_available_from = 0
    excluded_unobserved_without_available_from = 0
    n_unknown_active_eligible = 0
    n_null_eligible_segments = 0
    n_null_available_from_eligible = 0
    anomalous_late_sold_before_cutoff: list[str] = []

    for pid in sorted(products_by_id):
        product = products_by_id[pid]
        if product.get("active") is False:
            excluded_inactive += 1
            continue

        avail_raw = product.get("available_from")
        if avail_raw is not None:
            avail_dt = parse_utc_timestamp(avail_raw, f"products[{pid}].available_from")
            if avail_dt > as_of_dt:
                excluded_late_available_from += 1
                if pid in sold_before_as_of_ids:
                    anomalous_late_sold_before_cutoff.append(pid)
                continue
        else:
            if pid not in sold_before_as_of_ids:
                excluded_unobserved_without_available_from += 1
                continue
            n_null_available_from_eligible += 1

        eligible_catalog_products[pid] = product
        if product.get("active") is None:
            n_unknown_active_eligible += 1
        if product.get("eligible_segments") is None:
            n_null_eligible_segments += 1

    if n_unknown_active_eligible > 0:
        warnings.append(
            f"{n_unknown_active_eligible} eligible catalog product(s) have active=null "
            "(unknown active status; retained in catalog)."
        )
    if n_null_eligible_segments > 0:
        msg = (
            f"{n_null_eligible_segments} eligible catalog product(s) have eligible_segments=null "
            "(no segment restriction supplied by source; universal compatibility is not proved)."
        )
        warnings.append(msg)
        segment_limitations.append(msg)
    if n_null_available_from_eligible > 0 or excluded_unobserved_without_available_from > 0:
        warnings.append(
            "Conservative historical catalog rule applied for products with available_from=null: "
            f"{n_null_available_from_eligible} product(s) admitted via pre-cutoff sales, "
            f"{excluded_unobserved_without_available_from} unobserved product(s) excluded."
        )
    if anomalous_late_sold_before_cutoff:
        warnings.append(
            "Anomalous product(s) with available_from > as_of appeared in pre-cutoff transactions "
            f"({sorted(anomalous_late_sold_before_cutoff)}); marked inactive in train snapshot "
            "and excluded from eligible catalog."
        )

    eligible_by_seg_concrete: dict[str, frozenset[str]] = {}
    for seg in ("B2B", "B2C", "unknown"):
        eligible_by_seg_concrete[seg] = frozenset(
            pid
            for pid, prod in eligible_catalog_products.items()
            if is_product_eligible_for_segment(prod, seg)
        )
    eligible_catalog_by_segment: dict[str, frozenset[str]] = {
        "all": frozenset().union(*eligible_by_seg_concrete.values()),
        **eligible_by_seg_concrete,
    }

    # Build main cohort (strictly customers known before as_of via past purchase or known_since).
    main_customers_list: list[dict] = []
    customer_cohorts: dict[str, str] = {}
    excluded_future_new_customers = 0
    any_known_since = False
    any_missing_order_id = False
    any_present_order_id = False
    known_since_after_purchase_count = 0

    for cid in sorted(customers_by_id):
        cust = customers_by_id[cid]
        known_raw = cust.get("known_since")
        known_dt: datetime | None = None
        if known_raw is not None:
            any_known_since = True
            known_dt = parse_utc_timestamp(known_raw, f"customers[{cid}].known_since")

        cust_train_txs = train_txs_by_customer.get(cid, [])
        has_train_tx = len(cust_train_txs) > 0
        known_before_cutoff = known_dt is not None and known_dt < as_of_dt

        if not has_train_tx and not known_before_cutoff:
            excluded_future_new_customers += 1
            continue

        if has_train_tx and known_dt is not None and known_dt >= as_of_dt:
            known_since_after_purchase_count += 1

        main_customers_list.append(cust)
        if not has_train_tx:
            customer_cohorts[cid] = "natural_cold"
        else:
            order_keys: set[tuple[str, str]] = set()
            for tx in cust_train_txs:
                oid = tx.get("order_id")
                if isinstance(oid, str) and oid != "":
                    any_present_order_id = True
                    order_keys.add(("order", oid))
                else:
                    any_missing_order_id = True
                    tx_date = parse_utc_timestamp(tx["purchased_at"], "purchased_at").date().isoformat()
                    order_keys.add(("date", tx_date))
            n_orders = len(order_keys)
            customer_cohorts[cid] = "one_purchase" if n_orders == 1 else "warm"

    if any_missing_order_id and any_present_order_id:
        order_count_method = "mixed_order_id_and_utc_date_approx"
        warnings.append(
            "Some historical transactions lack order_id; distinct UTC purchase dates were used "
            "as an approximate order count for those rows."
        )
    elif any_missing_order_id:
        order_count_method = "distinct_utc_date_approx"
        warnings.append(
            "order_id is absent in historical transactions; one_purchase vs warm cohorts use "
            "distinct UTC purchase dates as an approximate replacement."
        )
    else:
        order_count_method = "distinct_order_id"

    if not any_known_since:
        warnings.append(
            "No customer registry timestamps (known_since) are present; main evaluation cohort "
            "is based exclusively on historical buyers prior to as_of (natural_cold is empty)."
        )
    if known_since_after_purchase_count > 0:
        warnings.append(
            f"{known_since_after_purchase_count} customer(s) have pre-cutoff purchases despite "
            "known_since >= as_of; retained in cohort based on observed purchase history."
        )

    main_customer_ids = {c["customer_id"] for c in main_customers_list}
    unknown_seg_count = sum(1 for c in main_customers_list if c["segment"] == "unknown")
    if unknown_seg_count > 0:
        segment_limitations.append(
            f"{unknown_seg_count} main-cohort customer(s) have segment='unknown' "
            f"(segment_source={metadata.get('segment_source', 'unspecified')!r})."
        )

    # Build physically leak-free train_bundle for Recommender.fit().
    train_products_list: list[dict] = []
    for pid in sorted(products_by_id):
        if pid in eligible_catalog_products:
            train_products_list.append(copy.deepcopy(eligible_catalog_products[pid]))
        elif pid in sold_before_as_of_ids:
            # Keep historically purchased non-eligible product for referential integrity,
            # explicitly marked active=False so no model may recommend it.
            non_rec = copy.deepcopy(products_by_id[pid])
            non_rec["active"] = False
            train_products_list.append(non_rec)

    train_bundle = validate_bundle(
        {
            "schema_version": bundle["schema_version"],
            "data_mode": bundle["data_mode"],
            "customers": [copy.deepcopy(c) for c in main_customers_list],
            "products": train_products_list,
            "transactions": [copy.deepcopy(tx) for tx in train_txs],
            "metadata": {
                "observation_end": as_of_iso,
                "warnings": list(metadata.get("warnings") or []),
                "segment_source": metadata.get("segment_source", "unknown"),
                "catalog_history_available": bool(
                    metadata.get("catalog_history_available", False)
                ),
            },
        }
    )

    # Compute cross-sell future targets in [as_of, horizon_end) and account for EVERY excluded row.
    true_train_products_by_customer: dict[str, frozenset[str]] = {
        c["customer_id"]: frozenset(true_train_products_by_customer_mut.get(c["customer_id"], set()))
        for c in main_customers_list
    }
    future_targets_mut: dict[str, set[str]] = {c["customer_id"]: set() for c in main_customers_list}
    customers_with_any_raw_future_tx: set[str] = set()

    excluded_by_reason: dict[str, int] = {
        "not_in_main_cohort": 0,
        "split_order_across_cutoff": 0,
        "repeat_purchase": 0,
        "inactive_product": 0,
        "late_product_available_after_cutoff": 0,
        "unobserved_without_available_from": 0,
        "segment_ineligible": 0,
    }
    valid_target_transactions = 0

    for tx in window_txs:
        cid = tx["customer_id"]
        pid = tx["product_id"]

        if cid not in main_customer_ids:
            excluded_by_reason["not_in_main_cohort"] += 1
            continue

        customers_with_any_raw_future_tx.add(cid)

        oid = tx.get("order_id")
        if isinstance(oid, str) and oid != "" and (cid, oid) in train_order_keys:
            excluded_by_reason["split_order_across_cutoff"] += 1
            continue

        if pid in true_train_products_by_customer[cid]:
            excluded_by_reason["repeat_purchase"] += 1
            continue

        product = products_by_id[pid]
        if product.get("active") is False:
            excluded_by_reason["inactive_product"] += 1
            continue

        avail_raw = product.get("available_from")
        if avail_raw is not None:
            if parse_utc_timestamp(avail_raw, f"products[{pid}].available_from") > as_of_dt:
                excluded_by_reason["late_product_available_after_cutoff"] += 1
                continue
        elif pid not in sold_before_as_of_ids:
            excluded_by_reason["unobserved_without_available_from"] += 1
            continue

        cust_segment = customers_by_id[cid]["segment"]
        if not is_product_eligible_for_segment(product, cust_segment):
            excluded_by_reason["segment_ineligible"] += 1
            continue

        valid_target_transactions += 1
        future_targets_mut[cid].add(pid)

    if excluded_by_reason["split_order_across_cutoff"] > 0:
        warnings.append(
            f"{excluded_by_reason['split_order_across_cutoff']} future window transaction(s) "
            "belonged to order_id(s) already present before as_of and were excluded so no order "
            "is split between train and test."
        )

    future_targets_by_customer: dict[str, frozenset[str]] = {
        cid: frozenset(pids) for cid, pids in future_targets_mut.items()
    }
    total_excluded_transactions = sum(excluded_by_reason.values())

    cohort_counts = {
        "main_all": len(main_customers_list),
        "one_purchase": sum(1 for v in customer_cohorts.values() if v == "one_purchase"),
        "warm": sum(1 for v in customer_cohorts.values() if v == "warm"),
        "natural_cold": sum(1 for v in customer_cohorts.values() if v == "natural_cold"),
        "excluded_future_new_customers": excluded_future_new_customers,
        "main_customers_with_any_raw_future_tx": len(customers_with_any_raw_future_tx),
        "main_customers_with_valid_cross_sell_target": sum(
            1 for pids in future_targets_by_customer.values() if pids
        ),
    }

    catalog_summary = {
        "total_products_in_bundle": len(products_by_id),
        "eligible_at_cutoff_all": len(eligible_catalog_by_segment["all"]),
        "eligible_by_segment": {
            seg: len(pids) for seg, pids in eligible_catalog_by_segment.items()
        },
        "excluded_inactive": excluded_inactive,
        "excluded_late_available_from": excluded_late_available_from,
        "excluded_unobserved_without_available_from": excluded_unobserved_without_available_from,
        "unknown_active_status_in_eligible": n_unknown_active_eligible,
        "null_eligible_segments_in_eligible": n_null_eligible_segments,
    }

    excluded_future_purchases = {
        "total_future_transactions_in_window": len(window_txs),
        "post_window_transactions_ignored": post_window_txs_count,
        "valid_target_transactions": valid_target_transactions,
        "unique_customer_target_pairs": sum(len(v) for v in future_targets_by_customer.values()),
        "total_excluded_transactions": total_excluded_transactions,
        "by_reason": excluded_by_reason,
    }

    return PointInTimeSnapshot(
        data_mode=bundle["data_mode"],
        as_of_dt=as_of_dt,
        as_of_iso=as_of_iso,
        horizon_days=horizon_days,
        horizon_end_dt=horizon_end_dt,
        horizon_end_iso=horizon_end_iso,
        observation_end_dt=observation_end_dt,
        observation_end_iso=observation_end_iso,
        train_bundle=train_bundle,
        main_customers=tuple(main_customers_list),
        customer_cohorts=customer_cohorts,
        true_train_products_by_customer=true_train_products_by_customer,
        future_targets_by_customer=future_targets_by_customer,
        eligible_catalog_products=eligible_catalog_products,
        eligible_catalog_by_segment=eligible_catalog_by_segment,
        order_count_method=order_count_method,
        catalog_summary=catalog_summary,
        cohort_counts=cohort_counts,
        excluded_future_purchases=excluded_future_purchases,
        warnings=_dedupe_preserve_order(warnings),
        segment_limitations=_dedupe_preserve_order(segment_limitations),
    )


def build_simulated_cold_split(
    snapshot: PointInTimeSnapshot,
    *,
    fraction: float = 0.2,
    seed: int = 42,
    selected_customer_ids: set[str] | None = None,
) -> SimulatedColdSplit:
    """Create a deterministic holdout split for simulated_cold evaluation.

    - Only historical buyers (one_purchase / warm) are eligible for holdout.
    - Selected customers and all of their pre-cutoff transactions are physically
      removed from sim_train_bundle.
    - The catalog in sim_train_bundle["products"] is fixed from the ORIGINAL
      train_bundle snapshot before hiding histories.
    - Cross-sell targets remain anchored to each customer's original true history.
    """
    historical_customers = [
        c
        for c in snapshot.main_customers
        if snapshot.customer_cohorts[c["customer_id"]] in {"one_purchase", "warm"}
    ]

    if selected_customer_ids is not None:
        selected = [c for c in historical_customers if c["customer_id"] in selected_customer_ids]
        selected_ids = {c["customer_id"] for c in selected}
    else:
        if fraction <= 0.0 or len(historical_customers) < 2:
            return SimulatedColdSplit(
                enabled=False,
                seed=seed,
                fraction=fraction,
                selected_customers=(),
                sim_train_bundle=None,
                n_remaining_train_customers=len(snapshot.train_bundle["customers"]),
                note=(
                    "simulated_cold skipped: fewer than 2 historical customers "
                    "or simulated_cold_fraction <= 0."
                ),
            )

        rng = random.Random(seed)
        selected_ids: set[str] = set()
        by_seg: dict[str, list[dict]] = {"B2B": [], "B2C": [], "unknown": []}
        for c in historical_customers:
            by_seg[c["segment"]].append(c)

        # Stratified deterministic selection when segments have >= 2 customers.
        for seg in ("B2B", "B2C", "unknown"):
            group = by_seg[seg]
            if len(group) >= 2:
                k_pick = max(1, int(round(len(group) * fraction)))
                k_pick = min(k_pick, len(group) - 1)
                picked = rng.sample([c["customer_id"] for c in group], k_pick)
                selected_ids.update(picked)

        # Fallback to unstratified selection if no single segment had >= 2 customers.
        if not selected_ids:
            k_pick = max(1, int(round(len(historical_customers) * fraction)))
            k_pick = min(k_pick, len(historical_customers) - 1)
            selected_ids.update(
                rng.sample([c["customer_id"] for c in historical_customers], k_pick)
            )

        selected = [c for c in historical_customers if c["customer_id"] in selected_ids]

    if not selected:
        return SimulatedColdSplit(
            enabled=False,
            seed=seed,
            fraction=fraction,
            selected_customers=(),
            sim_train_bundle=None,
            n_remaining_train_customers=len(snapshot.train_bundle["customers"]),
            note="simulated_cold skipped: no historical customers selected.",
        )

    sim_customers = [
        copy.deepcopy(c)
        for c in snapshot.train_bundle["customers"]
        if c["customer_id"] not in selected_ids
    ]
    sim_transactions = [
        copy.deepcopy(tx)
        for tx in snapshot.train_bundle["transactions"]
        if tx["customer_id"] not in selected_ids
    ]
    # Catalog is intentionally preserved verbatim from original train snapshot.
    sim_train_bundle = validate_bundle(
        {
            "schema_version": snapshot.train_bundle["schema_version"],
            "data_mode": snapshot.train_bundle["data_mode"],
            "customers": sim_customers,
            "products": copy.deepcopy(snapshot.train_bundle["products"]),
            "transactions": sim_transactions,
            "metadata": copy.deepcopy(snapshot.train_bundle["metadata"]),
        }
    )

    return SimulatedColdSplit(
        enabled=True,
        seed=seed,
        fraction=fraction,
        selected_customers=tuple(selected),
        sim_train_bundle=sim_train_bundle,
        n_remaining_train_customers=len(sim_customers),
        note=(
            "Controlled offline holdout simulation (not observed natural cold-start); "
            "excluded from main 'all' cohort metrics."
        ),
    )


def validate_recommendation_response(
    response: Any,
    *,
    expected_customer_id: str,
    expected_segment: str,
    expected_strategy: str,
    expected_as_of_dt: datetime,
    expected_data_mode: str,
    k: int,
    admissible_product_ids: frozenset[str],
) -> tuple[tuple[str, ...], list[str]]:
    """Validate Recommender.recommend() output against Shared Contract v1.

    Structural contract errors raise ValueError (never silently masked).
    Item-level admissibility violations (e.g., inactive, wrong segment, late
    catalog item, or already-bought item in main cohort) are excluded from
    valid positions and returned as violation strings for explicit reporting.
    """
    if not isinstance(response, dict):
        raise ValueError(
            f"recommend() for customer {expected_customer_id!r} must return a dict"
        )

    required_top = {
        "schema_version",
        "customer_id",
        "segment",
        "strategy",
        "as_of",
        "data_mode",
        "recommendations",
        "warnings",
    }
    missing = required_top - response.keys()
    if missing:
        raise ValueError(
            f"recommend() response missing fields {sorted(missing)} "
            f"for customer {expected_customer_id!r}"
        )

    if response["schema_version"] != "1.0":
        raise ValueError(f"unexpected schema_version: {response['schema_version']!r}")
    if response["customer_id"] != expected_customer_id:
        raise ValueError(
            f"response customer_id {response['customer_id']!r} != {expected_customer_id!r}"
        )
    if response["segment"] != expected_segment:
        raise ValueError(
            f"response segment {response['segment']!r} != {expected_segment!r} "
            f"for customer {expected_customer_id!r}"
        )
    if response["strategy"] != expected_strategy:
        raise ValueError(
            f"response strategy {response['strategy']!r} != {expected_strategy!r}"
        )
    if response["data_mode"] != expected_data_mode:
        raise ValueError(
            f"response data_mode {response['data_mode']!r} != {expected_data_mode!r}"
        )
    resp_as_of_dt = parse_utc_timestamp(response["as_of"], "response.as_of")
    if resp_as_of_dt != expected_as_of_dt:
        raise ValueError(
            f"response as_of {response['as_of']!r} != expected {format_utc_timestamp(expected_as_of_dt)!r}"
        )
    if not isinstance(response["warnings"], list):
        raise ValueError("response warnings must be a list")

    recs = response["recommendations"]
    if not isinstance(recs, list):
        raise ValueError("response recommendations must be a list")
    if len(recs) > k:
        raise ValueError(
            f"recommend() returned {len(recs)} items (> k={k}) for customer {expected_customer_id!r}"
        )

    required_item_fields = {
        "product_id",
        "product_name",
        "rank",
        "score",
        "reason_type",
        "reason_text",
        "source_product_ids",
        "support_count",
        "is_fallback",
    }

    seen_pids: set[str] = set()
    valid_pids: list[str] = []
    violations: list[str] = []

    for expected_rank, item in enumerate(recs, start=1):
        if not isinstance(item, dict):
            raise ValueError("each recommendation entry must be a dict")
        missing_item = required_item_fields - item.keys()
        if missing_item:
            raise ValueError(
                f"recommendation entry missing fields {sorted(missing_item)} "
                f"for customer {expected_customer_id!r}"
            )
        pid = item["product_id"]
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"invalid product_id {pid!r}")
        if pid in seen_pids:
            raise ValueError(
                f"duplicate product_id {pid!r} in recommendations for {expected_customer_id!r}"
            )
        seen_pids.add(pid)

        if item["rank"] != expected_rank:
            raise ValueError(
                f"non-contiguous rank {item['rank']!r} (expected {expected_rank}) "
                f"for customer {expected_customer_id!r}"
            )

        score = item["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError(
                f"score must be a finite number, got {score!r} for product {pid!r}"
            )

        reason_type = item["reason_type"]
        if reason_type not in VALID_REASON_TYPES:
            raise ValueError(f"invalid reason_type {reason_type!r}")

        source_ids = item["source_product_ids"]
        if not isinstance(source_ids, list) or not all(isinstance(s, str) for s in source_ids):
            raise ValueError("source_product_ids must be a list[str]")
        if reason_type in POPULARITY_REASON_TYPES and len(source_ids) > 0:
            raise ValueError(
                f"source_product_ids must be empty for {reason_type!r}, got {source_ids!r}"
            )

        support_count = item["support_count"]
        if isinstance(support_count, bool) or not isinstance(support_count, int) or support_count < 0:
            raise ValueError(f"support_count must be a non-negative int, got {support_count!r}")

        if not isinstance(item["is_fallback"], bool):
            raise ValueError("is_fallback must be a bool")

        if pid not in admissible_product_ids:
            violations.append(
                f"strategy={expected_strategy} customer={expected_customer_id} "
                f"returned inadmissible product_id={pid!r}"
            )
        else:
            valid_pids.append(pid)

    return tuple(valid_pids), violations
