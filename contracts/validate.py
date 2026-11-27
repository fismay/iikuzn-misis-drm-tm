"""Small standard-library validator for the shared Bundle contract."""
from __future__ import annotations

from datetime import datetime


class ContractError(ValueError):
    pass


def _utc(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field} is not ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field} must include a timezone")


def validate_bundle(bundle: dict) -> dict:
    if not isinstance(bundle, dict):
        raise ContractError("bundle must be an object")
    required = {"schema_version", "data_mode", "customers", "products", "transactions", "metadata"}
    missing = required - bundle.keys()
    if missing:
        raise ContractError(f"missing bundle fields: {sorted(missing)}")
    if bundle["schema_version"] != "1.0":
        raise ContractError("schema_version must be '1.0'")
    if bundle["data_mode"] not in {"synthetic", "real"}:
        raise ContractError("data_mode must be synthetic or real")
    customer_ids = set()
    for row in bundle["customers"]:
        cid = row.get("customer_id")
        if not isinstance(cid, str) or not cid:
            raise ContractError("customer_id must be a non-empty string")
        customer_ids.add(cid)
        if row.get("segment") not in {"B2B", "B2C", "unknown"}:
            raise ContractError(f"invalid segment for {cid}")
        if row.get("known_since") is not None:
            _utc(row["known_since"], "known_since")
    product_ids = set()
    for row in bundle["products"]:
        pid = row.get("product_id")
        if not isinstance(pid, str) or not pid:
            raise ContractError("product_id must be a non-empty string")
        product_ids.add(pid)
        if row.get("available_from") is not None:
            _utc(row["available_from"], "available_from")
    transaction_ids = set()
    for row in bundle["transactions"]:
        tid = row.get("transaction_id")
        if not isinstance(tid, str) or tid in transaction_ids:
            raise ContractError(f"transaction_id must be unique: {tid!r}")
        transaction_ids.add(tid)
        if row.get("customer_id") not in customer_ids:
            raise ContractError(f"unknown transaction customer: {row.get('customer_id')}")
        if row.get("product_id") not in product_ids:
            raise ContractError(f"unknown transaction product: {row.get('product_id')}")
        _utc(row.get("purchased_at"), "purchased_at")
    _utc(bundle["metadata"].get("observation_end"), "observation_end")
    return bundle

