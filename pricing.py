"""USD token pricing from the official Azure Retail Prices API."""

import logging
import math
import os
import re

CHECKED_ON = "2026-11-17"
SOURCE = "https://prices.azure.com/api/retail/prices"
LOGGER = logging.getLogger(__name__)
RATES = {
    "gpt-6.1-sol": {
        "input": 2.0,
        "cached_input": 0.1,
        "cache_write": 2.5,
        "output": 10.0,
        "basis": "GPT-6.1 Sol, Global Standard, short context",
    },
    "microsoft-decision-1": {
        "input": 0.042,
        "output": 0.042,
        "basis": "Microsoft-Decision-1, Global Standard token rate",
    },
    "microsoft-decision-1.1": {
        "input": 0.042,
        "output": 0.042,
        "basis": "Microsoft-Decision-1.1, Global Standard token rate",
    },
}


def _rates(side, model):
    if side == "decision":
        value = os.getenv("DECISION_USD_PER_MILLION", "").strip()
        overrides = {"input": value, "output": value}
    else:
        overrides = {
            key: os.getenv(f"AZURE_OPENAI_{key.upper()}_USD_PER_MILLION", "").strip()
            for key in ("input", "cached_input", "cache_write", "output")
        }
    if any(overrides.values()):
        if not overrides["input"] or not overrides["output"]:
            raise ValueError("Cost pricing overrides require both input and output rates.")
        rates = {key: float(value) for key, value in overrides.items() if value}
        if any(not math.isfinite(value) or value < 0 for value in rates.values()):
            raise ValueError("Cost pricing rates must be finite, non-negative numbers.")
        return {**rates, "basis": "Configured USD pricing overrides", "source": None, "checked_on": None}
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", (model or "").lower())
    if name == "dev-microsoft-decision-1.1":
        name = "microsoft-decision-1.1"
    rates = RATES.get(name)
    return {"source": SOURCE, "checked_on": CHECKED_ON, **rates} if rates else None


def estimate_cost(side, model, usage):
    """Use reported token counts, never text-length estimates or deployment-name guesses."""
    result = {"usage": usage, "cost_usd": None, "pricing": None, "cost_unavailable_reason": None}

    def unavailable(reason):
        LOGGER.warning("Cost unavailable for %s: %s", side, reason)
        return {**result, "cost_unavailable_reason": reason}

    if usage is None:
        return unavailable("Token usage was not reported by the model.")
    try:
        rates = _rates(side, model)
    except ValueError as exc:
        return unavailable(f"Invalid pricing configuration: {exc}")
    result["pricing"] = rates
    if rates is None:
        return unavailable(f"No verified price for model '{model or 'unknown'}'; configure pricing overrides.")
    if not isinstance(usage, dict):
        return unavailable("Token usage is not an object.")
    counts = {
        "input": usage.get("input_tokens"),
        "output": usage.get("output_tokens"),
        "cached_input": usage.get("cached_input_tokens", 0),
        "cache_write": usage.get("cache_write_tokens", 0),
    }
    if any(type(value) is not int or value < 0 for value in counts.values()):
        return unavailable("Token usage contains missing or invalid counts.")
    counts["input"] -= counts["cached_input"] + counts["cache_write"]
    if counts["input"] < 0:
        return unavailable("Cached token counts exceed reported input tokens.")
    if any(count and key not in rates for key, count in counts.items()):
        return unavailable("A price is missing for a reported token category.")
    cost = sum(count * rates.get(key, 0) for key, count in counts.items()) / 1_000_000
    if not math.isfinite(cost):
        return unavailable("Configured prices produce a non-finite cost.")
    result["cost_usd"] = cost
    return result


def mock_cost():
    return {
        "usage": None,
        "cost_usd": None,
        "pricing": None,
        "cost_unavailable_reason": "Mock models do not incur token charges.",
    }
