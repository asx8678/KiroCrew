"""MSG-1: one sender's queued Slack messages drain as one turn."""

from __future__ import annotations

from kiro_crew.slack.events import take_queued_burst


class _Sessions:
    def __init__(self, items):
        self.items = list(items)
        self.front = []

    def dequeue(self, key):
        if self.front:
            return self.front.pop(0)
        if not self.items:
            return None
        return self.items.pop(0)

    def requeue_front(self, key, ts, text, **kwargs):
        self.front.insert(0, (ts, text, kwargs))
        return True


class _Orch:
    def __init__(self, items):
        self.sessions = _Sessions(items)
        self._pending_queue = {}


def test_same_sender_collapses_and_a_different_sender_stays():
    orch = _Orch(
        [
            ("1", "one", {"sender_id": "U1", "channel": "C", "thread_ts": "T"}),
            ("2", "two", {"sender_id": "U1", "channel": "C", "thread_ts": "T"}),
            ("3", "other", {"sender_id": "U2", "channel": "C", "thread_ts": "T"}),
        ]
    )
    burst = take_queued_burst(orch, "slack:C:T")
    assert burst is not None
    ts, text, kwargs = burst
    assert ts == "1"
    assert text == "one\ntwo"
    assert kwargs["_collapsed_ts"] == ["2"]
    assert orch.sessions.dequeue("slack:C:T")[0] == "3"
