"""CTX-31: every replay row is clipped, and the replay never exceeds its budget.

Before the per-row cap the budget loop admitted the NEWEST row whole whatever
its size (a 300,000-char row produced a 300,006-char replay against an 80,000
budget) and a large OLDER row ended the scan, keeping only the rows after it.
"""

from __future__ import annotations

from kiro_crew.context_assembly import replay


def _rows(n: int, size: int = 40) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "r" + str(i) + " " * (size - 4)}
        for i in range(n)
    ]


def test_the_newest_row_is_clipped_to_the_budget():
    messages = _rows(40) + [{"role": "assistant", "content": "Z" * 300_000}]
    text = replay.replay_text(messages, None)
    assert len(text) <= 80_000
    assert text.endswith("…[truncated]")
    # The clipped head of the newest row is still the newest content.
    assert text.rsplit("\n\n", 1)[-1].startswith("Assistant: " + "Z" * 100)


def test_a_small_window_scales_the_cap_down():
    messages = _rows(40) + [{"role": "assistant", "content": "Z" * 300_000}]
    text = replay.replay_text(messages, 200_000)
    assert len(text) <= 16_000
    assert text.endswith("…[truncated]")


def test_a_large_older_row_no_longer_ends_the_scan():
    big = {"role": "assistant", "content": "B" * 100_000}
    messages = _rows(30) + [big] + _rows(30)
    text = replay.replay_text(messages, None)
    # Rows OLDER than the big one are present again (r0 is the oldest row).
    assert "r0 " in text
    assert "…[truncated]" in text
    assert len(text) <= 80_000


def test_small_rows_are_unchanged():
    messages = _rows(10)
    text = replay.replay_text(messages, None)
    assert "…[truncated]" not in text
    for i in range(10):
        assert f"r{i} " in text
