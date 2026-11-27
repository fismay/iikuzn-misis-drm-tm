from __future__ import annotations

import json
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
        raise NotImplementedError("real adapters are pending explicit source schema and mapping")
    (out / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"data_mode": bundle["data_mode"], "customers": len(bundle["customers"]),
              "products": len(bundle["products"]), "transactions": len(bundle["transactions"]),
              "observation_end": bundle["metadata"]["observation_end"],
              "warnings": bundle["metadata"]["warnings"], "timezone_assumption": "UTC in synthetic data",
              "catalog_history_available": bundle["metadata"]["catalog_history_available"]}
    (out / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle

