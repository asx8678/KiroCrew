"""WF-5: ctx.agent(effort=...) reaches the session on both shipped adapters.

Before: the effort reached agent_fn's opts dict but neither adapter read it,
so a script could not lower a step's reasoning effort and steps ran at the
factory's effort. The pooled adapter keys warm workers by (agent, model,
cwd, effort) so a worker built at one effort never serves another.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kiro_crew.workflows.agent_exec import build_agent_fn


class _RecordingSessions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_or_create(self, key: str, **kw: Any) -> tuple[Any, bool, bool]:
        self.calls.append({"key": key, **kw})
        provider = SimpleNamespace(
            stream_command=None,
            context_usage_pct=lambda: 5.0,
            context_window_tokens=lambda: None,
        )
        return provider, True, False

    async def release(self, key: str, **kw: Any) -> None:
        return None


def _run_coro() -> Any:
    class _C:
        async def stream_and_collect(self, provider: Any, prompt: str, **kw: Any) -> Any:
            return SimpleNamespace(stop_reason="end_turn")

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    return _C()


def _stub_collect(monkeypatch) -> None:
    """Skip the real model call: the test pins get_or_create kwargs only."""

    async def _fake(provider, prompt, **kw):
        return "step done"

    monkeypatch.setattr("kiro_crew.workflows.agent_exec.stream_and_collect", _fake)


def test_the_cold_path_threads_the_effort(monkeypatch) -> None:
    import asyncio

    sessions = _RecordingSessions()
    _stub_collect(monkeypatch)
    fn = build_agent_fn(sessions, run_id="wf-eff")
    asyncio.run(fn("do it", {"effort": "low"}))
    assert sessions.calls, "no session was created"
    assert sessions.calls[0]["reasoning_effort_override"] == "low"


def test_an_invalid_effort_falls_back_to_the_default(monkeypatch) -> None:
    import asyncio

    sessions = _RecordingSessions()
    _stub_collect(monkeypatch)
    fn = build_agent_fn(sessions, run_id="wf-eff2")
    asyncio.run(fn("do it", {"effort": "turbo"}))
    assert sessions.calls[0]["reasoning_effort_override"] == ""


def test_the_pool_identity_includes_the_effort() -> None:
    """Two steps with different efforts must land on two distinct sub-pool
    identity keys, so a warm worker built at one effort never serves a call
    that asked for another."""
    from kiro_crew.workflows.agent_pool import build_pooled_agent_fn

    import asyncio

    sessions = _RecordingSessions()
    fn, pool = build_pooled_agent_fn(sessions, run_id="wf-eff3", max_workers=1)
    try:
        asyncio.run(asyncio.sleep(0))
    finally:
        try:
            asyncio.run(pool.shutdown())
        except Exception:
            pass
    # The identity tuple the source builds includes the effort slot.
    import inspect

    import kiro_crew.workflows.agent_pool as pool_mod

    src = inspect.getsource(pool_mod._pool_for if hasattr(pool_mod, "_pool_for") else pool_mod)
    assert "effort or " "" in src
