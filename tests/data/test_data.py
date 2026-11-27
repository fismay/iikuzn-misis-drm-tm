import json

import pytest

from reco_mvp.data import load_bundle, make_synthetic_bundle, prepare_dataset


def test_synthetic_is_deterministic_and_referentially_valid():
    first = make_synthetic_bundle()
    second = make_synthetic_bundle()
    assert first == second
    assert len(first["customers"]) >= 60
    assert len(first["products"]) >= 15
    assert {row["segment"] for row in first["customers"]} == {"B2B", "B2C", "unknown"}
    assert all(isinstance(row["customer_id"], str) for row in first["customers"])


def test_roundtrip_and_explicit_real_mode(tmp_path):
    prepare_dataset(None, str(tmp_path), synthetic=True)
    loaded = load_bundle(str(tmp_path / "bundle.json"))
    assert loaded["data_mode"] == "synthetic"
    with pytest.raises(ValueError, match="mapping_path"):
        prepare_dataset(str(tmp_path), str(tmp_path / "real"))

