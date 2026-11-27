"""Main evaluation entrypoint implementing the Shared Contract v1 evaluate() API."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from .metrics import (
    COHORTS,
    MAIN_COHORTS,
    SEGMENTS,
    CustomerEvalRecord,
    bootstrap_precision_diff_ci,
    compute_metric_delta,
    compute_slice_metrics,
)
from .protocol import (
    VALID_STRATEGIES,
    build_point_in_time_snapshot,
    build_simulated_cold_split,
    validate_recommendation_response,
)
from .report import (
    render_evaluation_report,
    write_metrics_csv,
    write_metrics_json,
    write_recommendations_jsonl,
)


def _default_recommender_factory(config: dict | None = None) -> Any:
    """Resolve the real Agent 2 Recommender without any hidden production stub."""
    try:
        from reco_mvp.recommender import Recommender
    except ImportError as exc:
        raise ImportError(
            "reco_mvp.recommender.Recommender is not available in this checkout "
            "(depends on Agent 2 branch agent/02-recommender). "
            "Production evaluation does not use a stub fallback."
        ) from exc
    return Recommender(config)


def evaluate(
    bundle: dict,
    *,
    as_of: str,
    horizon_days: int = 30,
    output_dir: str,
    strategies: tuple[str, ...] = ("popular", "graph_only", "hybrid"),
    recommender_factory: Callable[[dict | None], Any] | None = None,
    recommender_config: dict | None = None,
    simulated_cold_fraction: float = 0.2,
    simulated_cold_seed: int = 42,
    bootstrap_resamples: int = 500,
    bootstrap_seed: int = 42,
) -> dict:
    """Run point-in-time cross-sell backtest and emit auditable artifacts.

    Writes metrics.json, metrics.csv, recommendations.jsonl, and
    evaluation_report.md into output_dir and returns the metrics dict.
    """
    strategies_tuple = tuple(strategies)
    if not strategies_tuple:
        raise ValueError("strategies must contain at least one strategy")
    unknown_strategies = [s for s in strategies_tuple if s not in VALID_STRATEGIES]
    if unknown_strategies:
        raise ValueError(
            f"unsupported strategies {unknown_strategies!r}; "
            f"allowed: {sorted(VALID_STRATEGIES)}"
        )
    if len(set(strategies_tuple)) != len(strategies_tuple):
        raise ValueError(f"strategies must not contain duplicates: {strategies_tuple!r}")

    snapshot = build_point_in_time_snapshot(bundle, as_of=as_of, horizon_days=horizon_days)
    factory = recommender_factory or _default_recommender_factory

    # Fit main model strictly on a copy of point-in-time train_bundle (< as_of).
    main_model = factory(recommender_config)
    fitted_main = main_model.fit(
        copy.deepcopy(snapshot.train_bundle), as_of=snapshot.as_of_iso
    )
    if fitted_main is not None:
        main_model = fitted_main

    main_records_by_strategy: dict[str, list[CustomerEvalRecord]] = {
        s: [] for s in strategies_tuple
    }
    main_responses_for_jsonl: list[dict] = []
    violation_messages: list[str] = []
    violations_by_strategy: dict[str, int] = {s: 0 for s in strategies_tuple}

    for strategy in strategies_tuple:
        for customer in snapshot.main_customers:
            cid = customer["customer_id"]
            seg = customer["segment"]
            cohort = snapshot.customer_cohorts[cid]
            response = main_model.recommend(cid, k=3, strategy=strategy)

            admissible_for_customer = (
                snapshot.eligible_catalog_by_segment[seg]
                - snapshot.true_train_products_by_customer[cid]
            )
            valid_pids, item_violations = validate_recommendation_response(
                response,
                expected_customer_id=cid,
                expected_segment=seg,
                expected_strategy=strategy,
                expected_as_of_dt=snapshot.as_of_dt,
                expected_data_mode=snapshot.data_mode,
                k=3,
                admissible_product_ids=admissible_for_customer,
            )
            if item_violations:
                violations_by_strategy[strategy] += len(item_violations)
                violation_messages.extend(item_violations)

            main_responses_for_jsonl.append(response)
            main_records_by_strategy[strategy].append(
                CustomerEvalRecord(
                    customer_id=cid,
                    segment=seg,
                    cohort=cohort,
                    recommended_product_ids=valid_pids,
                    target_product_ids=snapshot.future_targets_by_customer[cid],
                    invalid_items_count=len(item_violations),
                )
            )

    # Separate simulated_cold holdout evaluation (never mixed into main 'all').
    sim_split = build_simulated_cold_split(
        snapshot,
        fraction=simulated_cold_fraction,
        seed=simulated_cold_seed,
    )
    sim_records_by_strategy: dict[str, list[CustomerEvalRecord]] = {
        s: [] for s in strategies_tuple
    }

    if sim_split.enabled and sim_split.sim_train_bundle is not None:
        sim_model = factory(recommender_config)
        fitted_sim = sim_model.fit(
            copy.deepcopy(sim_split.sim_train_bundle), as_of=snapshot.as_of_iso
        )
        if fitted_sim is not None:
            sim_model = fitted_sim

        for strategy in strategies_tuple:
            for customer in sim_split.selected_customers:
                cid = customer["customer_id"]
                seg = customer["segment"]
                response = sim_model.recommend(cid, segment=seg, k=3, strategy=strategy)

                # For simulated cold customers, history was hidden from the model;
                # admissibility reflects segment-eligible frozen catalog, while
                # targets strictly exclude the customer's original true history.
                admissible_for_cold = snapshot.eligible_catalog_by_segment[seg]
                valid_pids, item_violations = validate_recommendation_response(
                    response,
                    expected_customer_id=cid,
                    expected_segment=seg,
                    expected_strategy=strategy,
                    expected_as_of_dt=snapshot.as_of_dt,
                    expected_data_mode=snapshot.data_mode,
                    k=3,
                    admissible_product_ids=admissible_for_cold,
                )
                if item_violations:
                    violations_by_strategy[strategy] += len(item_violations)
                    violation_messages.extend(item_violations)

                sim_records_by_strategy[strategy].append(
                    CustomerEvalRecord(
                        customer_id=cid,
                        segment=seg,
                        cohort="simulated_cold",
                        recommended_product_ids=valid_pids,
                        target_product_ids=snapshot.future_targets_by_customer[cid],
                        invalid_items_count=len(item_violations),
                    )
                )

    # Assemble full contract metric grid (non-overlapping definitions; never mix simulated_cold into all).
    rows: list[dict] = []
    for strategy in strategies_tuple:
        main_recs = main_records_by_strategy[strategy]
        sim_recs = sim_records_by_strategy[strategy]
        for segment in SEGMENTS:
            eligible_catalog = snapshot.eligible_catalog_by_segment[segment]
            for cohort in COHORTS:
                if cohort == "simulated_cold":
                    source = sim_recs
                elif cohort == "all":
                    source = [r for r in main_recs if r.cohort in MAIN_COHORTS]
                else:
                    source = [r for r in main_recs if r.cohort == cohort]

                if segment != "all":
                    sliced = [r for r in source if r.segment == segment]
                else:
                    sliced = source

                rows.append(
                    compute_slice_metrics(
                        strategy=strategy,
                        segment=segment,
                        cohort=cohort,
                        records=sliced,
                        eligible_catalog_ids=eligible_catalog,
                        k=3,
                    )
                )

    # Baseline comparisons & customer-level paired bootstrap CI against 'popular'.
    row_lookup = {(r["strategy"], r["segment"], r["cohort"]): r for r in rows}
    baseline_comparison: dict[str, dict[str, dict]] = {}
    bootstrap_ci: dict[str, dict] = {}
    if "popular" in strategies_tuple:
        base_all = row_lookup[("popular", "all", "all")]
        for cand in strategies_tuple:
            if cand == "popular":
                continue
            cand_all = row_lookup[(cand, "all", "all")]
            baseline_comparison[cand] = {
                metric_name: compute_metric_delta(
                    cand_all[metric_name], base_all[metric_name]
                )
                for metric_name in (
                    "precision_at_3",
                    "hit_rate_at_3",
                    "fill_rate_at_3",
                    "full_list_rate",
                    "catalog_coverage",
                )
            }
            bootstrap_ci[f"{cand} - popular"] = bootstrap_precision_diff_ci(
                main_records_by_strategy[cand],
                main_records_by_strategy["popular"],
                n_resamples=bootstrap_resamples,
                seed=bootstrap_seed,
                k=3,
            )

    warnings = list(snapshot.warnings)
    if violation_messages:
        warnings.append(
            f"Detected {len(violation_messages)} model admissibility filter violation(s); "
            "invalid items were excluded from valid fill/hit/coverage counts."
        )

    cohort_counts = {
        **snapshot.cohort_counts,
        "simulated_cold": len(sim_split.selected_customers),
    }

    protocol = {
        "as_of": snapshot.as_of_iso,
        "horizon_days": snapshot.horizon_days,
        "horizon_end": snapshot.horizon_end_iso,
        "observation_end": snapshot.observation_end_iso,
        "strategies": list(strategies_tuple),
        "cohort_definition": (
            "Main cohort includes all customers with >=1 transaction strictly before as_of "
            "plus customers with known_since < as_of and 0 transactions (natural_cold). "
            "Customers first appearing at or after as_of are excluded. "
            "simulated_cold is evaluated separately and excluded from 'all'."
        ),
        "order_count_method": snapshot.order_count_method,
        "cross_sell_target_rule": (
            "Unique products bought in [as_of, as_of + horizon_days) that are not in the "
            "customer's pre-cutoff purchase history, belong to the point-in-time eligible "
            "catalog at as_of for the customer's segment, and do not belong to an order_id "
            "already started before as_of."
        ),
        "catalog_rule": (
            "Products with active=false are excluded. Products with available_from are "
            "admitted iff available_from <= as_of (including zero-sales products). Products "
            "with available_from=null are admitted conservatively iff observed in transactions "
            "strictly before as_of."
        ),
        "catalog_history_available": bool(
            bundle["metadata"].get("catalog_history_available", False)
        ),
        "segment_source": bundle["metadata"].get("segment_source", "unknown"),
        "segment_limitations": snapshot.segment_limitations,
        "recommender_config": recommender_config or {},
        "catalog_summary": snapshot.catalog_summary,
        "cohort_counts": cohort_counts,
        "excluded_future_purchases": snapshot.excluded_future_purchases,
        "filter_violations": {
            "total_invalid_items": len(violation_messages),
            "by_strategy": violations_by_strategy,
            "sample_messages": violation_messages[:10],
        },
        "simulated_cold": {
            "enabled": sim_split.enabled,
            "seed": sim_split.seed,
            "fraction": sim_split.fraction,
            "n_selected": len(sim_split.selected_customers),
            "n_remaining_train_customers": sim_split.n_remaining_train_customers,
            "catalog_fixed_from_original_train_snapshot": True,
            "cross_sell_targets_use_original_history": True,
            "excluded_from_overall_all": True,
            "note": sim_split.note,
        },
        "baseline_comparison": baseline_comparison,
        "bootstrap_ci": bootstrap_ci,
    }

    metrics = {
        "schema_version": "1.0",
        "data_mode": snapshot.data_mode,
        "protocol": protocol,
        "warnings": warnings,
        "rows": rows,
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_metrics_json(out / "metrics.json", metrics)
    write_metrics_csv(out / "metrics.csv", rows)
    write_recommendations_jsonl(out / "recommendations.jsonl", main_responses_for_jsonl)
    (out / "evaluation_report.md").write_text(
        render_evaluation_report(metrics), encoding="utf-8"
    )

    return metrics
