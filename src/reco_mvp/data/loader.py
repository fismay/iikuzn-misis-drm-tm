from __future__ import annotations

import json
import csv
from datetime import datetime, timezone
from pathlib import Path

from contracts.validate import validate_bundle
from .synthetic import make_synthetic_bundle


def load_bundle(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        return validate_bundle(json.load(handle))


def prepare_dataset(input_dir: str | None, output_dir: str, *, synthetic: bool = False,
                    mapping_path: str | None = None) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if synthetic:
        bundle = make_synthetic_bundle()
    else:
        if not input_dir:
            raise ValueError("real mode requires input_dir and an explicit mapping")
        if not mapping_path:
            raise ValueError("real mode requires mapping_path; synthetic fallback is disabled")
        bundle = _read_real(Path(input_dir), Path(mapping_path))
    (out / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"data_mode": bundle["data_mode"], "customers": len(bundle["customers"]),
              "products": len(bundle["products"]), "transactions": len(bundle["transactions"]),
              "observation_end": bundle["metadata"]["observation_end"],
              "warnings": bundle["metadata"]["warnings"], "timezone_assumption": "UTC in synthetic data",
              "catalog_history_available": bundle["metadata"]["catalog_history_available"]}
    (out / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    if path.suffix.lower() in {".xlsx", ".xls"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("XLSX input requires pandas/openpyxl") from exc
        return pd.read_excel(path, dtype=str).fillna("").to_dict("records")
    raise ValueError(f"unsupported input format: {path.suffix}; use CSV or XLSX")


def _required(row: dict, name: str, source: str) -> str:
    value = row.get(name)
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing required field {name!r} in {source}")
    return str(value).strip()


def _timestamp(value: str, source: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {value!r} in {source}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_real(input_dir: Path, mapping_path: Path) -> dict:
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    source_rows = {}
    for kind in ("customers", "products", "transactions"):
        spec = mapping.get(kind)
        if not isinstance(spec, dict) or not spec.get("file"):
            raise ValueError(f"mapping must define {kind}.file")
        source = input_dir / spec["file"]
        if not source.exists():
            raise FileNotFoundError(f"mapped source does not exist: {source}")
        source_rows[kind] = (spec, _read_rows(source), source.name)
    customers = []
    for spec, rows, source in [source_rows["customers"]]:
        for row in rows:
            cid = _required(row, spec.get("customer_id", "customer_id"), source)
            segment = str(row.get(spec.get("segment", "segment"), "")).strip() or "unknown"
            if segment not in {"B2B", "B2C", "unknown"}:
                segment = "unknown"
            known = row.get(spec.get("known_since", "known_since")) or None
            customers.append({"customer_id": cid, "segment": segment,
                              "known_since": _timestamp(known, source) if known else None})
    products = []
    for spec, rows, source in [source_rows["products"]]:
        for row in rows:
            pid = _required(row, spec.get("product_id", "product_id"), source)
            active_value = row.get(spec.get("active", "active"))
            active = None if active_value in (None, "") else str(active_value).lower() in {"1", "true", "yes"}
            products.append({"product_id": pid, "name": _required(row, spec.get("name", "name"), source),
                "category": row.get(spec.get("category", "category")) or None,
                "eligible_segments": None, "active": active, "available_from": None})
    transactions = []
    for spec, rows, source in [source_rows["transactions"]]:
        for index, row in enumerate(rows, 1):
            transactions.append({"transaction_id": _required(row, spec.get("transaction_id", "transaction_id"), source),
                "order_id": row.get(spec.get("order_id", "order_id")) or None,
                "customer_id": _required(row, spec.get("customer_id", "customer_id"), source),
                "product_id": _required(row, spec.get("product_id", "product_id"), source),
                "purchased_at": _timestamp(_required(row, spec.get("purchased_at", "purchased_at"), source), source),
                "quantity": float(row.get(spec.get("quantity", "quantity"), 1) or 1)})
    dates = [row["purchased_at"] for row in transactions]
    observation_end = max(dates) if dates else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    bundle = {"schema_version": "1.0", "data_mode": "real", "customers": customers,
        "products": products, "transactions": transactions,
        "metadata": {"observation_end": observation_end,
            "warnings": ["Observation end inferred from maximum transaction timestamp; verify export coverage."],
            "segment_source": "mapped source", "catalog_history_available": False}}
    return validate_bundle(bundle)
