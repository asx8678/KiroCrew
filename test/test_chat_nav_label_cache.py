"""UI-1: the same URL and context labels once."""

from unittest.mock import AsyncMock, patch

import pytest

from kiro_crew.dashboard import chat_nav


@pytest.mark.asyncio
async def test_the_same_url_and_context_calls_the_model_once():
    chat_nav._LABEL_CACHE.clear()
    link = {"url": "https://example.com/a?x=1", "context": "see this"}
    with patch(
        "kiro_crew.dashboard.chat_nav.run_bg_oneliner",
        new=AsyncMock(return_value="Example doc"),
    ) as model:
        state = type("S", (), {"sessions": object()})()
        first = await chat_nav._resolve_link_summaries(state, [link])
        second = await chat_nav._resolve_link_summaries(state, [link])
    assert first == ["Example doc"]
    assert second == ["Example doc"]
    assert model.await_count == 1
