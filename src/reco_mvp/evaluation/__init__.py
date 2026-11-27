"""Independent time-based cross-sell evaluation and backtest package."""

from .metrics import (
    COHORTS,
    MAIN_COHORTS,
    METRIC_COLUMNS,
    SEGMENTS,
    CustomerEvalRecord,
    bootstrap_precision_diff_ci,
    compute_metric_delta,
    compute_slice_metrics,
    customer_precision_at_k,
)
from .protocol import (
    PointInTimeSnapshot,
    SimulatedColdSplit,
    build_point_in_time_snapshot,
    build_simulated_cold_split,
    is_product_eligible_for_segment,
    parse_utc_timestamp,
    validate_recommendation_response,
)
from .runner import evaluate

__all__ = [
    "COHORTS",
    "MAIN_COHORTS",
    "METRIC_COLUMNS",
    "SEGMENTS",
    "CustomerEvalRecord",
    "PointInTimeSnapshot",
    "SimulatedColdSplit",
    "bootstrap_precision_diff_ci",
    "build_point_in_time_snapshot",
    "build_simulated_cold_split",
    "compute_metric_delta",
    "compute_slice_metrics",
    "customer_precision_at_k",
    "evaluate",
    "is_product_eligible_for_segment",
    "parse_utc_timestamp",
    "validate_recommendation_response",
]
