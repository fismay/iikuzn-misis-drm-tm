"""Pure metric computation and statistical comparison utilities."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable, Sequence

SEGMENTS: tuple[str, ...] = ("all", "B2B", "B2C", "unknown")
COHORTS: tuple[str, ...] = ("all", "one_purchase", "warm", "natural_cold", "simulated_cold")
MAIN_COHORTS: tuple[str, ...] = ("one_purchase", "warm", "natural_cold")

METRIC_COLUMNS: tuple[str, ...] = (
    "strategy",
    "segment",
    "cohort",
    "n_customers",
    "n_with_future_purchase",
    "precision_at_3",
    "hit_rate_at_3",
    "fill_rate_at_3",
    "full_list_rate",
    "catalog_coverage",
)


@dataclass(frozen=True)
class CustomerEvalRecord:
    """Single evaluated recommendation request with ground-truth cross-sell targets."""

    customer_id: str
    segment: str
    cohort: str
    recommended_product_ids: tuple[str, ...]
    target_product_ids: frozenset[str]
    invalid_items_count: int = 0


def customer_precision_at_k(
    recommended_product_ids: Sequence[str],
    target_product_ids: frozenset[str] | set[str],
    *,
    k: int = 3,
) -> float:
    """Precision@k with fixed denominator k, regardless of list length."""
    if k <= 0:
        raise ValueError("k must be positive")
    top_k = set(recommended_product_ids[:k])
    hits = len(top_k & target_product_ids)
    return hits / float(k)


def compute_slice_metrics(
    *,
    strategy: str,
    segment: str,
    cohort: str,
    records: Sequence[CustomerEvalRecord],
    eligible_catalog_ids: frozenset[str] | set[str],
    k: int = 3,
) -> dict:
    """Compute contract-compliant metrics for one (strategy, segment, cohort) slice.

    Empty groups always return n_customers=0 and None for every metric field.
    Zero catalog denominator always returns catalog_coverage=None.
    """
    n_customers = len(records)
    if n_customers == 0:
        return {
            "strategy": strategy,
            "segment": segment,
            "cohort": cohort,
            "n_customers": 0,
            "n_with_future_purchase": 0,
            "precision_at_3": None,
            "hit_rate_at_3": None,
            "fill_rate_at_3": None,
            "full_list_rate": None,
            "catalog_coverage": None,
        }

    precision_sum = 0.0
    hit_count = 0
    valid_position_count = 0
    full_list_count = 0
    n_with_future_purchase = 0
    distinct_recommended: set[str] = set()

    for rec in records:
        top_k = rec.recommended_product_ids[:k]
        if rec.target_product_ids:
            n_with_future_purchase += 1
        hits = len(set(top_k) & rec.target_product_ids)
        precision_sum += hits / float(k)
        if hits > 0:
            hit_count += 1
        valid_position_count += len(top_k)
        if len(top_k) == k:
            full_list_count += 1
        for pid in top_k:
            if pid in eligible_catalog_ids:
                distinct_recommended.add(pid)

    catalog_size = len(eligible_catalog_ids)
    catalog_coverage = (
        None if catalog_size == 0 else len(distinct_recommended) / float(catalog_size)
    )

    return {
        "strategy": strategy,
        "segment": segment,
        "cohort": cohort,
        "n_customers": n_customers,
        "n_with_future_purchase": n_with_future_purchase,
        "precision_at_3": precision_sum / float(n_customers),
        "hit_rate_at_3": hit_count / float(n_customers),
        "fill_rate_at_3": valid_position_count / float(k * n_customers),
        "full_list_rate": full_list_count / float(n_customers),
        "catalog_coverage": catalog_coverage,
    }


def compute_metric_delta(
    candidate: float | None,
    baseline: float | None,
) -> dict:
    """Compare candidate vs baseline, separating percentage points from relative lift.

    When baseline == 0 (or either metric is None), relative lift is undefined (None)
    and never reported as infinite growth.
    """
    if candidate is None or baseline is None:
        return {
            "candidate": candidate,
            "baseline": baseline,
            "absolute_diff": None,
            "delta_pp": None,
            "relative_lift_pct": None,
            "relative_lift_defined": False,
            "note": "undefined (missing metric)",
        }

    diff = candidate - baseline
    delta_pp = diff * 100.0
    if baseline == 0.0:
        return {
            "candidate": candidate,
            "baseline": baseline,
            "absolute_diff": diff,
            "delta_pp": delta_pp,
            "relative_lift_pct": None,
            "relative_lift_defined": False,
            "note": "relative lift undefined (baseline = 0)",
        }

    relative_lift_pct = (diff / baseline) * 100.0
    return {
        "candidate": candidate,
        "baseline": baseline,
        "absolute_diff": diff,
        "delta_pp": delta_pp,
        "relative_lift_pct": relative_lift_pct,
        "relative_lift_defined": True,
        "note": None,
    }


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile on a pre-sorted non-empty sequence."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    weight = pos - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def bootstrap_precision_diff_ci(
    records_candidate: Iterable[CustomerEvalRecord],
    records_baseline: Iterable[CustomerEvalRecord],
    *,
    n_resamples: int = 500,
    seed: int = 42,
    alpha: float = 0.05,
    k: int = 3,
) -> dict:
    """Paired customer-level bootstrap CI for Precision@k(candidate) - Precision@k(baseline)."""
    cand_map = {r.customer_id: r for r in records_candidate}
    base_map = {r.customer_id: r for r in records_baseline}
    common_ids = sorted(cand_map.keys() & base_map.keys())
    n = len(common_ids)

    if n < 2 or n_resamples <= 0:
        return {
            "n_customers": n,
            "n_resamples": n_resamples,
            "seed": seed,
            "confidence_level": 1.0 - alpha,
            "observed_diff": None if n == 0 else (
                sum(
                    customer_precision_at_k(
                        cand_map[cid].recommended_product_ids,
                        cand_map[cid].target_product_ids,
                        k=k,
                    )
                    - customer_precision_at_k(
                        base_map[cid].recommended_product_ids,
                        base_map[cid].target_product_ids,
                        k=k,
                    )
                    for cid in common_ids
                )
                / float(n)
            ),
            "ci_low": None,
            "ci_high": None,
            "ci_low_pp": None,
            "ci_high_pp": None,
            "note": "insufficient customers or disabled resampling for bootstrap CI",
        }

    diffs = [
        customer_precision_at_k(
            cand_map[cid].recommended_product_ids,
            cand_map[cid].target_product_ids,
            k=k,
        )
        - customer_precision_at_k(
            base_map[cid].recommended_product_ids,
            base_map[cid].target_product_ids,
            k=k,
        )
        for cid in common_ids
    ]
    observed_diff = sum(diffs) / float(n)

    rng = random.Random(seed)
    boot_means: list[float] = []
    for _ in range(n_resamples):
        sample_sum = 0.0
        for _ in range(n):
            sample_sum += diffs[rng.randrange(n)]
        boot_means.append(sample_sum / float(n))
    boot_means.sort()

    ci_low = _percentile(boot_means, alpha / 2.0)
    ci_high = _percentile(boot_means, 1.0 - alpha / 2.0)

    if all(d == diffs[0] for d in diffs):
        note = "zero observed variance across customers; interval is degenerate"
    else:
        note = "paired customer-level percentile bootstrap; offline metric only"

    return {
        "n_customers": n,
        "n_resamples": n_resamples,
        "seed": seed,
        "confidence_level": 1.0 - alpha,
        "observed_diff": observed_diff,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_low_pp": ci_low * 100.0,
        "ci_high_pp": ci_high * 100.0,
        "note": note,
    }
