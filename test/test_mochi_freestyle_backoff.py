"""UI-5: a refused freestyle-task spawn retries with exponential backoff.

Before: the QueuePoller retried a REFUSED spawn on the very next 1 s poll with
no backoff — one due task whose spawn raises cost 60 attempts/min, 7 due tasks
420/min. Watch checks (5-min lock) and plans (10-min lock + backoff) already
back off; only freestyle lacked one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kiro_crew.apps.builtins.mochi.queue_file import write_queue_atomic
from kiro_crew.apps.builtins.mochi.queue_poller import QueuePoller


def _queue_with(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tasks": tasks,
        "version": 1,
        "planned_at": "2020-01-01T00:00:00.000Z",
        "planned_until": "2099-01-01T00:00:00.000Z",
    }


class _RefusingSpawner:
    def __init__(self) -> None:
        self.calls = 0

    async def spawn_agent(self, prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("admission refused the spawn")

    def get_due_watch_items(self) -> list:
        return []


@pytest.mark.asyncio
async def test_a_refused_freestyle_spawn_backs_off_exponentially(tmp_path: Path) -> None:
    """One due freestyle task whose spawn always raises: over 10 minutes of
    1 s polls the poller makes at most 6 attempts (30 s, 60 s, 120 s, 240 s,
    480 s, 960 s — the 16-min cap takes over after that)."""
    qpath = tmp_path / "q.json"
    write_queue_atomic(
        str(qpath),
        _queue_with(
            [
                {
                    "id": "t1",
                    "type": "freestyle",
                    "title": "do it",
                    "done": False,
                    "execute_after": "2020-01-01T00:00:00.000Z",
                }
            ]
        ),
    )
    cb = _RefusingSpawner()
    clock = {"now": 1_700_000_000_000}
    poller = QueuePoller(str(qpath), cb, clock=lambda: clock["now"])
    poller.start()
    try:
        for _ in range(600):  # 10 minutes of 1 s polls
            await poller.poll()
            clock["now"] += 1_000
    finally:
        poller.stop()

    assert 1 <= cb.calls <= 6, f"{cb.calls} attempts in 10 min — the backoff is not applied"


@pytest.mark.asyncio
async def test_a_task_stays_due_again_after_its_backoff_expires(tmp_path: Path) -> None:
    qpath = tmp_path / "q.json"
    write_queue_atomic(
        str(qpath),
        _queue_with(
            [
                {
                    "id": "t1",
                    "type": "freestyle",
                    "title": "do it",
                    "done": False,
                    "execute_after": "2020-01-01T00:00:00.000Z",
                }
            ]
        ),
    )
    cb = _RefusingSpawner()
    clock = {"now": 1_700_000_000_000}
    poller = QueuePoller(str(qpath), cb, clock=lambda: clock["now"])
    poller.start()
    try:
        await poller.poll()
        first = cb.calls
        assert first == 1
        # Within the 30 s backoff: no retry.
        for _ in range(5):
            await poller.poll()
            clock["now"] += 1_000
        assert cb.calls == first
        # Past the backoff: the task is due again.
        clock["now"] += 31_000
        await poller.poll()
        assert cb.calls == first + 1
    finally:
        poller.stop()
