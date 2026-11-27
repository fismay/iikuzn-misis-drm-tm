import pytest

from contracts.validate import ContractError, validate_bundle
from reco_mvp.data import make_synthetic_bundle


def test_invalid_timezone_is_rejected():
    bundle = make_synthetic_bundle()
    bundle["metadata"]["observation_end"] = "2026-07-01T00:00:00"
    with pytest.raises(ContractError, match="timezone"):
        validate_bundle(bundle)

