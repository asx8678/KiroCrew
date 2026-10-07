"""UI-2: a hidden pet pauses plan/replan/freestyle spawns after a grace period.

Before: the owner loop gated the poller on shell presence alone, so planning
and agent-task spawns continued for as long as the Electron shell ran — whether
or not anyone could see the pet (a deliberate original-parity choice, narrowed
here). Watch checks, missed-notify recovery and reminders stay on shell presence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kiro_crew.apps.builtins.mochi.queue_file import write_queue_atomic
from kiro_crew.apps.builtins.mochi.hooks import _PET_HIDDEN_GRACE_MS
from kiro_crew.apps.builtins.mochi.queue_poller import QueuePoller


def _queue() -> dict[str, Any]:
    # planned_until already expired -> route_poll answers 'plan'; one freestyle
    # task is due; both are gated by the pet flag.
    return {
        "tasks": [
            {
                "id": "t1",
                "type": "freestyle",
                "title": "do it",
                "done": False,
                "execute_after": "2020-01-01T00:00:00.000Z",
            }
        ],
        "version": 1,
        "planned_at": "2020-01-01T00:00:00.000Z",
        "planned_until": "2020-01-02T00:00:00.000Z",
    }


class _CB:
    def __init__(self) -> None:
        self.plans = 0
        self.watch = 0
        self.prompts: list[str] = []
        self.due_watch: list[dict[str, Any]] = []

    async def spawn_agent(self, prompt: str) -> str:
        # Refuse every spawn: an accepted spawn would park _await_spawn on a
        # notify that never comes in a unit test. A REFUSAL still counts as a
        # spawn ATTEMPT, which is what the gates are pinned on, and the
        # watch cooldown keeps it to one per window.
        self.prompts.append(prompt)
        raise RuntimeError("refused")

    async def trigger_plan(self) -> None:
        self.plans += 1

    async def trigger_replan(self) -> None:
        self.plans += 1

    def get_due_watch_items(self) -> list:
        return self.due_watch

    def notify_agent_done(self, spawn_id: str) -> None:
        return None


def _poller(tmp_path: Path, cb: _CB, *, visible: bool, hidden_for_ms: int):
    """A poller whose pet flag reports a pet last SEEN hidden_for_ms ago."""
    qpath = tmp_path / "q.json"
    write_queue_atomic(str(qpath), _queue())
    clock = {"now": 1_700_000_000_000}
    seen_at = {"ms": clock["now"] - hidden_for_ms} if not visible else {"ms": clock["now"]}

    def _pet_visible() -> bool:
        return clock["now"] - seen_at["ms"] <= 90_000 + _PET_HIDDEN_GRACE_MS

    p = QueuePoller(str(qpath), cb, clock=lambda: clock["now"], pet_visible=_pet_visible)
    p.start()
    return p, clock


@pytest.mark.asyncio
async def test_a_sustained_hidden_pet_makes_no_plan_or_freestyle_spawns(tmp_path: Path) -> None:
    cb = _CB()
    cb.due_watch = [{"id": "w1", "label": "watch", "trigger_at": "2020-01-01T00:00:00.000Z"}]
    # Hidden for an hour — far past the 5-minute grace.
    p, clock = _poller(tmp_path, cb, visible=False, hidden_for_ms=60 * 60_000)
    try:
        for _ in range(240):  # 2 h of 30 s beats
            await p.poll()
            clock["now"] += 30_000
    finally:
        p.stop()
    assert cb.plans == 0, "plan/replan spawns ran while the pet was hidden"
    assert not any(
        "do it" in p for p in cb.prompts
    ), "freestyle spawns ran while the pet was hidden"
    assert cb.prompts, "watch checks must stay on SHELL presence"


@pytest.mark.asyncio
async def test_a_visible_pet_keeps_planning(tmp_path: Path) -> None:
    cb = _CB()
    p, clock = _poller(tmp_path, cb, visible=True, hidden_for_ms=0)
    try:
        for _ in range(4):
            await p.poll()
            clock["now"] += 30_000
    finally:
        p.stop()
    assert cb.plans >= 1, "a visible pet must keep planning"


@pytest.mark.asyncio
async def test_a_short_hide_within_the_grace_keeps_planning(tmp_path: Path) -> None:
    """A 2-minute hide (space switch, fullscreen app) is not an off switch."""
    cb = _CB()
    p, clock = _poller(tmp_path, cb, visible=False, hidden_for_ms=2 * 60_000)
    try:
        for _ in range(4):
            await p.poll()
            clock["now"] += 30_000
    finally:
        p.stop()
    assert cb.plans >= 1, "within the grace the poller must keep planning"
