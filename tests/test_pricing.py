import pytest

from pricing import estimate_cost


@pytest.fixture(autouse=True)
def clear_pricing_overrides(monkeypatch):
    for category in ("INPUT", "OUTPUT", "CACHED_INPUT", "CACHE_WRITE"):
        monkeypatch.delenv(f"AZURE_OPENAI_{category}_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("DECISION_USD_PER_MILLION", raising=False)


def test_cached_and_cache_write_tokens_are_not_double_counted():
    result = estimate_cost("openai", "gpt-6.1-sol-2026-09-29", {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cached_input_tokens": 400,
        "cache_write_tokens": 100,
    })
    assert result["cost_usd"] == pytest.approx((500 * 2 + 400 * 0.1 + 100 * 2.5 + 100 * 10) / 1_000_000)
    assert result["pricing"]["checked_on"] == "2026-11-17"
    assert result["pricing"]["source"] == "https://prices.azure.com/api/retail/prices"


def test_custom_rates_support_unknown_models(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("AZURE_OPENAI_OUTPUT_USD_PER_MILLION", "3")
    result = estimate_cost("openai", "custom-model", {"input_tokens": 100, "output_tokens": 10})
    assert result["cost_usd"] == pytest.approx(130 / 1_000_000)
    assert result["pricing"]["checked_on"] is None


def test_decision_override_applies_to_both_token_types(monkeypatch):
    monkeypatch.setenv("DECISION_USD_PER_MILLION", "0.05")
    result = estimate_cost("decision", "custom-decision", {"input_tokens": 100, "output_tokens": 2})
    assert result["cost_usd"] == pytest.approx(102 * 0.05 / 1_000_000)


@pytest.mark.parametrize("model", [
    "microsoft-decision-1.1",
    "Microsoft-Decision-1.1",
    "microsoft-decision-1.1-2026-11-10",
    "dev-microsoft-decision-1.1",
    "Dev-Microsoft-Decision-1.1",
    "dev-microsoft-decision-1.1-2026-11-10",
])
def test_decision_1_1_uses_same_rate_as_decision_1(model):
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    original = estimate_cost("decision", "microsoft-decision-1", usage)
    result = estimate_cost("decision", model, usage)
    assert result["cost_usd"] == pytest.approx(0.0462)
    assert result["cost_usd"] == original["cost_usd"]
    assert result["cost_unavailable_reason"] is None
    assert result["pricing"]["basis"] == "Microsoft-Decision-1.1, Global Standard token rate"
    assert result["pricing"]["checked_on"] == "2026-11-17"
    assert result["pricing"]["source"] == "https://prices.azure.com/api/retail/prices"


@pytest.mark.parametrize("model", ["microsoft-decision-1.1", "dev-microsoft-decision-1.1"])
def test_decision_1_1_rate_can_be_overridden(monkeypatch, model):
    monkeypatch.setenv("DECISION_USD_PER_MILLION", "0.05")
    result = estimate_cost("decision", model, {
        "input_tokens": 1_000_000, "output_tokens": 100_000,
    })
    assert result["cost_usd"] == pytest.approx(0.055)
    assert result["pricing"]["basis"] == "Configured USD pricing overrides"


def test_decision_data_zone_override_is_not_reported_as_verified_global_pricing(monkeypatch):
    monkeypatch.setenv("DECISION_USD_PER_MILLION", "0.046")
    result = estimate_cost("decision", "microsoft-decision-1.1", {
        "input_tokens": 1_000_000, "output_tokens": 100_000,
    })
    assert result["cost_usd"] == pytest.approx(0.0506)
    assert result["pricing"]["checked_on"] is None
    assert result["pricing"]["source"] is None


@pytest.mark.parametrize("model", ["microsoft-decision-1.2", "dev-microsoft-decision-1.2"])
def test_decision_1_1_price_is_not_applied_to_other_versions(model):
    result = estimate_cost("decision", model, {
        "input_tokens": 100, "output_tokens": 1,
    })
    assert result["cost_usd"] is None
    assert "No verified price" in result["cost_unavailable_reason"]


@pytest.mark.parametrize("usage", [
    None,
    [],
    {"input_tokens": 10},
    {"input_tokens": -1, "output_tokens": 1},
    {"input_tokens": True, "output_tokens": 1},
    {"input_tokens": 10, "output_tokens": 1, "cached_input_tokens": 11},
])
def test_missing_or_invalid_usage_is_not_zero_cost(usage, caplog):
    result = estimate_cost("openai", "gpt-6.1-sol", usage)
    assert result["cost_usd"] is None
    assert result["cost_unavailable_reason"]
    assert "Cost unavailable" in caplog.text


def test_unknown_model_is_not_assigned_an_unrelated_price():
    result = estimate_cost("openai", "gpt-6.1-sol-other", {"input_tokens": 100, "output_tokens": 1})
    assert result["cost_usd"] is None
    assert "No verified price" in result["cost_unavailable_reason"]


@pytest.mark.parametrize("rate", ["nan", "inf", "-1", "not-a-price"])
def test_invalid_override_is_reported_without_losing_classification(rate, monkeypatch):
    monkeypatch.setenv("DECISION_USD_PER_MILLION", rate)
    result = estimate_cost("decision", "microsoft-decision-1", {"input_tokens": 10, "output_tokens": 1})
    assert result["cost_usd"] is None
    assert "Invalid pricing configuration" in result["cost_unavailable_reason"]


def test_incomplete_overrides_do_not_mix_with_snapshot_prices(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_INPUT_USD_PER_MILLION", "1")
    result = estimate_cost("openai", "gpt-6.1-sol", {"input_tokens": 10, "output_tokens": 1})
    assert result["cost_usd"] is None
    assert "both input and output" in result["cost_unavailable_reason"]


def test_cached_usage_requires_a_cached_override(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("AZURE_OPENAI_OUTPUT_USD_PER_MILLION", "3")
    result = estimate_cost("openai", "custom-model", {
        "input_tokens": 100, "output_tokens": 1, "cached_input_tokens": 10,
    })
    assert result["cost_usd"] is None
    assert "price is missing" in result["cost_unavailable_reason"]


def test_overflow_is_reported_instead_of_serializing_infinite_cost(monkeypatch):
    monkeypatch.setenv("DECISION_USD_PER_MILLION", "1e308")
    result = estimate_cost("decision", "microsoft-decision-1", {"input_tokens": 100, "output_tokens": 1})
    assert result["cost_usd"] is None
    assert "non-finite cost" in result["cost_unavailable_reason"]
