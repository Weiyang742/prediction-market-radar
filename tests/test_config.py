"""Config validation fail-fast tests."""
import pytest

from data.config import Config


def test_default_config_is_valid():
    Config().validate()


def test_rejects_non_positive_rate_limit():
    with pytest.raises(ValueError, match="kalshi_rate_limit_rps"):
        Config(kalshi_rate_limit_rps=0).validate()


def test_rejects_unknown_aggregation_method():
    with pytest.raises(ValueError, match="aggregation_method"):
        Config(aggregation_method="mean").validate()


def test_rejects_out_of_range_abstain_pp():
    with pytest.raises(ValueError, match="dual_model_abstain_pp"):
        Config(dual_model_abstain_pp=150.0).validate()
