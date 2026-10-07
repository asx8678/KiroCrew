"""USE-9: usage rows name the credit rate multiplier that produced their credits.

Credits are already the billed (multiplied) amount; the additive
rate_multiplier field closes the attribution gap. None for unknown models.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from kiro_crew.dashboard.handlers.usage import _build_token_record


def _event(credits: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(
            credits=credits,
            input_tokens=10,
            output_tokens=5,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=0.0,
            num_turns=1,
            duration_ms=0,
        ),
        stop_reason="end_turn",
    )


def _with_catalog(rows: list[dict]):
    return patch(
        "kiro_crew.dashboard.handlers.agents._catalog_cache",
        SimpleNamespace(models=rows),
    )


def test_a_catalog_model_writes_its_multiplier() -> None:
    rows = [{"modelId": "m1", "rateMultiplier": 2.2}]
    with _with_catalog(rows):
        rec = _build_token_record("s", "m1", _event(), "acp", __import__("datetime").datetime.now())
    assert rec["rate_multiplier"] == 2.2


def test_the_snake_case_spelling_is_read_too() -> None:
    rows = [{"modelId": "m1", "rate_multiplier": 0.05}]
    with _with_catalog(rows):
        rec = _build_token_record("s", "m1", _event(), "acp", __import__("datetime").datetime.now())
    assert rec["rate_multiplier"] == 0.05


def test_an_unknown_model_writes_none() -> None:
    rows = [{"modelId": "m1", "rateMultiplier": 2.2}]
    with _with_catalog(rows):
        rec = _build_token_record(
            "s", "other", _event(), "acp", __import__("datetime").datetime.now()
        )
    assert rec["rate_multiplier"] is None


def test_a_cold_cache_writes_none() -> None:
    with _with_catalog(None):
        rec = _build_token_record("s", "m1", _event(), "acp", __import__("datetime").datetime.now())
    assert rec["rate_multiplier"] is None


def test_a_non_numeric_badge_writes_none() -> None:
    rows = [{"modelId": "m1", "rateMultiplier": "2.2x"}]
    with _with_catalog(rows):
        rec = _build_token_record("s", "m1", _event(), "acp", __import__("datetime").datetime.now())
    assert rec["rate_multiplier"] is None


def test_every_legacy_reader_still_gets_its_fields() -> None:
    """The field is additive: existing readers must be unaffected."""
    import datetime

    with _with_catalog([{"modelId": "m1", "rateMultiplier": 2.2}]):
        rec = _build_token_record(
            "s", "m1", _event(), "acp", datetime.datetime.now(), surface="dashboard", agent="a"
        )
    for key in (
        "_type",
        "ts",
        "slot",
        "provider",
        "model",
        "input",
        "output",
        "credits",
        "surface",
        "agent",
        "stop_reason",
    ):
        assert key in rec, key
