"""The suggestions parser emits {text, kind} and never an unknown kind."""

from __future__ import annotations

import json

import pytest

from kiro_crew.suggestions import (
    _FALLBACK_SUGGESTIONS,
    SUGGESTION_KINDS,
    SuggestionsCache,
    _parse_suggestions,
    _redact_suggestions,
)


@pytest.mark.asyncio
async def test_an_unchanged_context_costs_no_second_model_call(monkeypatch):
    """LOOP-10: two refreshes past the interval with an UNCHANGED context make
    one model call — the second bumps generated_at on a matching digest — while
    a changed context regenerates, and force=1 always does."""
    from types import SimpleNamespace

    from kiro_crew import suggestions as s

    calls = {"n": 0}
    contexts = {"i": 0}
    built = ["x" * 120]

    monkeypatch.setattr(s, "_build_context", lambda state: built[contexts["i"]], raising=False)

    async def fake_generate(context, sessions=None):
        calls["n"] += 1
        return s._fallback()

    monkeypatch.setattr(s, "_generate_from_context", fake_generate)
    import time as _time

    real_time = _time.time
    clock = {"t": 1_000.0}
    monkeypatch.setattr(s.time, "time", lambda: clock["t"])

    cache = s.SuggestionsCache()
    cache.generated_at = 0.0  # stale: the interval has passed
    state = SimpleNamespace(_background_tasks=set())

    await s.refresh_suggestions(state, cache)
    assert calls["n"] == 1
    first_digest = cache.context_digest
    assert first_digest, "a generation records the digest it was built from"

    # 31 minutes pass with no context change: the interval upper bound is met,
    # but the digest matches, so no second model call — only the stamp moves.
    clock["t"] = real_time() and 1_000.0 + 31 * 60
    await s.refresh_suggestions(state, cache)
    assert calls["n"] == 1
    assert cache.generated_at == 1_000.0 + 31 * 60

    # The recent-activity input changes: the digest differs, so it regenerates.
    built[0] = "y" * 120
    await s.refresh_suggestions(state, cache)
    assert calls["n"] == 2

    # force always regenerates, digest or not.
    await s.refresh_suggestions(state, cache, force=True)
    assert calls["n"] == 3
    assert real_time() > 0


def test_object_items_keep_a_known_kind() -> None:
    raw = json.dumps([{"text": "Review the auth PR", "kind": "review"}])
    assert _parse_suggestions(raw) == [{"text": "Review the auth PR", "kind": "review"}]


def test_kind_is_case_and_whitespace_normalized() -> None:
    raw = json.dumps([{"text": "Fix the build", "kind": " OPS "}])
    assert _parse_suggestions(raw)[0]["kind"] == "ops"


def test_unknown_or_missing_kind_becomes_general() -> None:
    raw = json.dumps([{"text": "a", "kind": "banana"}, {"text": "b"}, {"text": "c", "kind": 3}])
    assert [s["kind"] for s in _parse_suggestions(raw)] == ["general"] * 3


def test_legacy_string_items_are_still_accepted() -> None:
    assert _parse_suggestions('["Draft notes"]') == [{"text": "Draft notes", "kind": "general"}]


def test_malformed_blank_and_overlong_items_are_dropped() -> None:
    raw = json.dumps([{"kind": "code"}, {"text": "   "}, {"text": "x" * 81}, 5, {"text": "ok"}])
    assert _parse_suggestions(raw) == [{"text": "ok", "kind": "general"}]


def test_fenced_response_and_six_item_cap() -> None:
    items = [{"text": f"s{i}", "kind": "code"} for i in range(9)]
    parsed = _parse_suggestions("```json\n" + json.dumps(items) + "\n```")
    assert len(parsed) == 6


def test_unparseable_response_yields_empty() -> None:
    assert _parse_suggestions("not json") == []


def test_redaction_keeps_kind() -> None:
    out = _redact_suggestions([{"text": "Deploy with AKIAIOSFODNN7EXAMPLE", "kind": "ops"}])
    assert out[0]["kind"] == "ops"
    assert "AKIAIOSFODNN7EXAMPLE" not in out[0]["text"]


def test_fallbacks_use_only_known_kinds_and_are_copied() -> None:
    assert all(s["kind"] in SUGGESTION_KINDS for s in _FALLBACK_SUGGESTIONS)
    cache = SuggestionsCache()
    cache.suggestions[0]["text"] = "mutated"
    assert _FALLBACK_SUGGESTIONS[0]["text"] != "mutated"
