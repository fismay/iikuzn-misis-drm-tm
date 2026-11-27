"""Artifact writers for metrics.csv, metrics.json, recommendations.jsonl, and evaluation_report.md."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Sequence

from .metrics import METRIC_COLUMNS, SEGMENTS


def write_metrics_json(path: Path, metrics: dict) -> None:
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_metrics_csv(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(METRIC_COLUMNS))
        writer.writeheader()
        for row in rows:
            serialized = {
                col: "" if row.get(col) is None else row.get(col)
                for col in METRIC_COLUMNS
            }
            writer.writerow(serialized)


def write_recommendations_jsonl(path: Path, responses: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for response in responses:
            handle.write(json.dumps(response, ensure_ascii=False) + "\n")


def _fmt_metric(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:.4f}"


def _fmt_pp(value: float | None) -> str:
    if value is None:
        return "null"
    return f"{value:+.2f} pp"


def _fmt_rel(delta: dict) -> str:
    if not delta.get("relative_lift_defined"):
        return delta.get("note") or "undefined"
    return f"{delta['relative_lift_pct']:+.2f}%"


def _index_rows(rows: Sequence[dict]) -> dict[tuple[str, str, str], dict]:
    return {(r["strategy"], r["segment"], r["cohort"]): r for r in rows}


def _render_metric_table(rows: Sequence[dict]) -> list[str]:
    lines = [
        "| Strategy | Segment | Cohort | n_customers | n_with_future_purchase | "
        "Precision@3 | HitRate@3 | FillRate@3 | FullListRate | CatalogCoverage |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['strategy']}` | `{row['segment']}` | `{row['cohort']}` | "
            f"{row['n_customers']} | {row['n_with_future_purchase']} | "
            f"{_fmt_metric(row['precision_at_3'])} | {_fmt_metric(row['hit_rate_at_3'])} | "
            f"{_fmt_metric(row['fill_rate_at_3'])} | {_fmt_metric(row['full_list_rate'])} | "
            f"{_fmt_metric(row['catalog_coverage'])} |"
        )
    return lines


def render_evaluation_report(metrics: dict) -> str:
    """Render an auditable Markdown evaluation report covering all required sections."""
    data_mode = metrics["data_mode"]
    protocol = metrics["protocol"]
    warnings = metrics["warnings"]
    rows = metrics["rows"]
    strategies: list[str] = protocol["strategies"]
    row_index = _index_rows(rows)

    lines: list[str] = []

    # 1. Prominent data mode banner
    if data_mode == "synthetic":
        lines.extend(
            [
                "# Evaluation Report — DATA MODE: SYNTHETIC",
                "",
                "> **DATA MODE: `synthetic`** — All figures below come from synthetic test data only. "
                "No conclusions about real customers, conversion uplift, or business revenue may be drawn.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "# Evaluation Report — DATA MODE: REAL",
                "",
                "> **DATA MODE: `real`** — Offline historical cross-sell backtest on provided real data. "
                "See limitations below; offline purchase logs do not measure impressions or monetary revenue.",
                "",
            ]
        )

    # 2. Periods, cohorts, and limitations
    cohort_counts = protocol["cohort_counts"]
    catalog_summary = protocol["catalog_summary"]
    lines.extend(
        [
            "## 1. Period, Cohorts, and Limitations",
            "",
            f"- **Schema version**: `{metrics['schema_version']}`",
            f"- **Data mode**: `{data_mode}`",
            f"- **Cutoff (`as_of`)**: `{protocol['as_of']}`",
            f"- **Horizon**: `{protocol['horizon_days']} days` (`[{protocol['as_of']}, {protocol['horizon_end']})`)",
            f"- **Observation end**: `{protocol['observation_end']}` (covers full horizon)",
            f"- **Evaluated strategies**: {', '.join(f'`{s}`' for s in strategies)}",
            f"- **Order counting method**: `{protocol['order_count_method']}`",
            f"- **Segment source**: `{protocol['segment_source']}`",
            f"- **Catalog history available**: `{protocol['catalog_history_available']}`",
            f"- **Main cohort size (`all`)**: `{cohort_counts['main_all']}` "
            f"(`one_purchase={cohort_counts['one_purchase']}`, "
            f"`warm={cohort_counts['warm']}`, "
            f"`natural_cold={cohort_counts['natural_cold']}`; "
            f"excluded future new customers: `{cohort_counts['excluded_future_new_customers']}`)",
            f"- **Simulated cold holdout size (separate from `all`)**: `{cohort_counts['simulated_cold']}`",
            f"- **Eligible historical catalog at cutoff**: `{catalog_summary['eligible_at_cutoff_all']}` "
            f"of `{catalog_summary['total_products_in_bundle']}` products "
            f"(`B2B={catalog_summary['eligible_by_segment']['B2B']}`, "
            f"`B2C={catalog_summary['eligible_by_segment']['B2C']}`, "
            f"`unknown={catalog_summary['eligible_by_segment']['unknown']}`)",
            "",
            "### Recorded Warnings & Limitations",
            "",
        ]
    )
    if warnings:
        for w in warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- None.")

    filter_violations = protocol.get("filter_violations", {})
    if filter_violations.get("total_invalid_items", 0) > 0:
        lines.append(
            f"- **Model filter violations detected**: `{filter_violations['total_invalid_items']}` "
            "inadmissible recommended item(s) were excluded from valid fill/hit counts."
        )
    lines.append("")

    # 3. Main results table + baseline comparison
    lines.extend(["## 2. Main Backtest Results (`segment=all`, `cohort=all`)", ""])
    main_rows = [row_index[(s, "all", "all")] for s in strategies if (s, "all", "all") in row_index]
    lines.extend(_render_metric_table(main_rows))
    lines.append("")

    baseline_comp = protocol.get("baseline_comparison") or {}
    if baseline_comp:
        lines.extend(
            [
                "### Comparison Against `popular` Baseline (Main `all` Cohort)",
                "",
                "| Candidate | Metric | Candidate | Baseline (`popular`) | Absolute Change (pp) | Relative Lift |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for cand_strategy, metric_deltas in baseline_comp.items():
            for metric_name, delta in metric_deltas.items():
                lines.append(
                    f"| `{cand_strategy}` | `{metric_name}` | "
                    f"{_fmt_metric(delta['candidate'])} | {_fmt_metric(delta['baseline'])} | "
                    f"{_fmt_pp(delta['delta_pp'])} | {_fmt_rel(delta)} |"
                )
        lines.append("")

    boot_ci = protocol.get("bootstrap_ci") or {}
    if boot_ci:
        lines.append("### Paired Customer-Level Bootstrap 95% CI (`Precision@3` Difference vs `popular`)")
        lines.append("")
        for pair_name, ci in boot_ci.items():
            if ci.get("ci_low") is not None and ci.get("ci_high") is not None:
                lines.append(
                    f"- **`{pair_name}`**: observed diff = `{ci['observed_diff']:+.4f}` "
                    f"(`{ci['observed_diff'] * 100.0:+.2f} pp`), "
                    f"{int(round(ci['confidence_level'] * 100))}% CI = "
                    f"`[{ci['ci_low']:+.4f}, {ci['ci_high']:+.4f}]` "
                    f"(`[{ci['ci_low_pp']:+.2f} pp, {ci['ci_high_pp']:+.2f} pp]`, "
                    f"n={ci['n_customers']}, resamples={ci['n_resamples']}, seed={ci['seed']}). "
                    f"Note: {ci['note']}."
                )
            else:
                lines.append(
                    f"- **`{pair_name}`**: bootstrap CI unavailable ({ci.get('note', 'n/a')})."
                )
        lines.append("")

    # 4. Segment & historical cohort tables
    lines.extend(["## 3. Segment and Cohort Breakdowns", ""])
    lines.append("### By Segment (`cohort=all`)")
    lines.append("")
    seg_rows = [
        row_index[(s, seg, "all")]
        for s in strategies
        for seg in ("B2B", "B2C", "unknown")
        if (s, seg, "all") in row_index
    ]
    lines.extend(_render_metric_table(seg_rows))
    lines.append("")

    lines.append("### By Historical Cohort (`segment=all`)")
    lines.append("")
    cohort_rows = [
        row_index[(s, "all", coh)]
        for s in strategies
        for coh in ("one_purchase", "warm", "natural_cold")
        if (s, "all", coh) in row_index
    ]
    lines.extend(_render_metric_table(cohort_rows))
    lines.append("")

    lines.append("### Segment x Historical Cohort (`one_purchase`, `warm`, `natural_cold`)")
    lines.append("")
    cross_rows = [
        row_index[(s, seg, coh)]
        for s in strategies
        for seg in ("B2B", "B2C", "unknown")
        for coh in ("one_purchase", "warm", "natural_cold")
        if (s, seg, coh) in row_index
    ]
    lines.extend(_render_metric_table(cross_rows))
    lines.append("")

    # 5. Cold start & fill rate
    sim_info = protocol["simulated_cold"]
    lines.extend(["## 4. Cold Start and Fill-Rate Demonstration", ""])
    if cohort_counts["natural_cold"] == 0:
        lines.append(
            "- **Observed `natural_cold`**: `0` customers (no pre-cutoff customer registry dates "
            "`known_since < as_of` without prior orders). Absence of observed cold-start customers "
            "is a data limitation; simulation below is **not** a substitute for real cold-start observation."
        )
    else:
        lines.append(
            f"- **Observed `natural_cold`**: `{cohort_counts['natural_cold']}` customer(s) "
            "with `known_since < as_of` and zero historical orders."
        )

    if sim_info["enabled"]:
        lines.append(
            f"- **Offline `simulated_cold` holdout**: `{sim_info['n_selected']}` historical customer(s) "
            f"held out deterministically (`seed={sim_info['seed']}`, `fraction={sim_info['fraction']}`), "
            f"refitting on the remaining `{sim_info['n_remaining_train_customers']}` train customer(s) "
            "with the original point-in-time catalog frozen and cross-sell labels anchored to true original history. "
            "**Excluded from main `all` metrics.**"
        )
    else:
        lines.append(f"- **Offline `simulated_cold` holdout**: disabled ({sim_info['note']}).")
    lines.append("")

    cold_rows = [
        row_index[(s, seg, coh)]
        for coh in ("natural_cold", "simulated_cold")
        for seg in SEGMENTS
        for s in strategies
        if (s, seg, coh) in row_index
    ]
    lines.extend(_render_metric_table(cold_rows))
    lines.append("")

    # Honest FillRate commentary for cold start (never claim fallback beats full baseline)
    for coh in ("natural_cold", "simulated_cold"):
        g_row = row_index.get(("graph_only", "all", coh))
        h_row = row_index.get(("hybrid", "all", coh))
        p_row = row_index.get(("popular", "all", coh))
        if g_row and h_row and p_row and h_row["n_customers"] > 0:
            g_fill = g_row["fill_rate_at_3"]
            h_fill = h_row["fill_rate_at_3"]
            p_fill = p_row["fill_rate_at_3"]
            if g_fill is not None and h_fill is not None and p_fill is not None:
                if h_fill <= p_fill:
                    baseline_note = (
                        f"`popular` already achieves FillRate@3={p_fill:.4f}, so `hybrid` fallback "
                        f"({h_fill:.4f}) matches/does not exceed `popular` on fill rate."
                    )
                else:
                    baseline_note = (
                        f"`hybrid` achieves FillRate@3={h_fill:.4f} vs `popular` {p_fill:.4f}."
                    )
                lines.append(
                    f"- **`{coh}` FillRate@3 summary (`segment=all`, n={h_row['n_customers']})**: "
                    f"`graph_only`={g_fill:.4f}, `hybrid`={h_fill:.4f}, `popular`={p_fill:.4f}. "
                    f"{baseline_note}"
                )
    lines.append("")

    # 6. Excluded future purchases
    excl = protocol["excluded_future_purchases"]
    by_reason = excl["by_reason"]
    lines.extend(
        [
            "## 5. Future Window Transactions and Excluded Purchases",
            "",
            f"- **Total transactions in `[as_of, horizon_end)`**: `{excl['total_future_transactions_in_window']}`",
            f"- **Valid cross-sell target transactions**: `{excl['valid_target_transactions']}` "
            f"(`{excl['unique_customer_target_pairs']}` unique `(customer, product)` targets)",
            f"- **Total excluded future transactions**: `{excl['total_excluded_transactions']}`",
            f"- **Post-horizon transactions (`>= horizon_end`, ignored)**: `{excl['post_window_transactions_ignored']}`",
            "",
            "| Exclusion Reason | Excluded Transactions |",
            "| --- | ---: |",
        ]
    )
    for reason, count in by_reason.items():
        lines.append(f"| `{reason}` | {count} |")
    lines.append("")

    # 7. What results prove and do not prove
    lines.extend(
        [
            "## 6. What These Results Prove and Do NOT Prove",
            "",
            "### What this backtest checks",
            "- Temporal separation (`transactions.purchased_at < as_of`) with future records physically withheld from `fit()`.",
            "- Consistent comparison of `popular`, `graph_only`, and `hybrid` on the same cohort, target rules, and eligibility filters.",
            "- Fill-rate behavior when co-purchase graph evidence is absent (`natural_cold` and/or `simulated_cold`).",
            "",
            "### What this backtest does NOT prove",
        ]
    )
    if data_mode == "synthetic":
        lines.append(
            "- **Not real customer evidence**: `data_mode=synthetic` verifies mechanics only; "
            "no conclusions about real customer preferences or live store performance are valid."
        )
    lines.extend(
        [
            "- **No impression / exposure fidelity**: offline purchase history lacks personal-account visit, impression, and click logs.",
            "- **No monetary or revenue effect**: product prices are absent; revenue lift, AOV lift, and ROI cannot be calculated.",
            "- **Business hypotheses remain unverified**: stated targets (+15–25% repeat purchases, +10–15% average check) are unproved hypotheses, not measured outcomes.",
            "- **No causal effect**: offline observational cross-sell hits do not prove incremental demand caused by showing recommendations.",
            "- **No renewal or upsell claims**: without contract terms, expiration dates, or product hierarchy, only cross-sell of new-to-customer items is evaluated.",
            "",
        ]
    )

    # 8. Next online test proposal
    lines.extend(
        [
            "## 7. Proposed Next Online Experiment (No Fabricated Revenue Forecast)",
            "",
            "- **Design**: Customer-level randomized A/B test in the personal account (control: current UI or `popular` baseline; treatment: `hybrid` Top-3 recommendations with explanation badges).",
            "- **Required instrumentation before launch**: log personal-account session visits, widget impressions (eligible/rendered items), clicks, add-to-cart events, and attributed orders.",
            "- **Primary online metrics**: recommendation CTR, click-to-new-product order conversion rate, cross-sell order rate per exposed customer, and widget fill rate.",
            "- **Guardrails**: page latency, empty-state rate, B2B/B2C segment-eligibility compliance, and customer support / return rate.",
            "- **Decision rule**: pre-register sample size and minimum detectable effect using real personal-account traffic once impression logging is live; do not project revenue lift until verified price data is integrated.",
            "",
        ]
    )

    return "\n".join(lines)
