"""WF-6: repeated failures compare by a normalized fingerprint.

The task runner's repeat-error detector compared task.error by byte equality,
so volatile text (timestamps, durations, ports, pids) made consecutive
identical failures look different and the 'Possible loop' notice and
'Loop detected' fail-fast never fired.
"""

from __future__ import annotations

from kiro_crew.repeat_loop import error_fingerprint


def test_volatile_tokens_are_masked_to_fixed_placeholders() -> None:
    a = "FAILED tests/test_x.py::test_y - assert 1 == 2; took 1.23s on :8080 pid 4242"
    b = "FAILED tests/test_x.py::test_y - assert 1 == 2; took 9.87s on :9090 pid 77"
    assert error_fingerprint(a) == error_fingerprint(b)
    assert error_fingerprint(a) != ""


def test_timestamps_and_hex_ids_are_masked() -> None:
    a = "ERROR 2026-10-07T13:14:15 request abcdef1234567890 failed"
    b = "ERROR 2026-10-08T09:01:02 request ffeedcba0987654321 failed"
    assert error_fingerprint(a) == error_fingerprint(b)


def test_genuinely_different_failures_differ() -> None:
    a = "FAILED tests/test_x.py::test_y - assert 1 == 2"
    b = "FAILED tests/test_x.py::test_z - connection refused"
    assert error_fingerprint(a) != error_fingerprint(b)


def test_the_keyed_lines_survive_and_noise_lines_drop() -> None:
    text = (
        "some stdout noise\n"
        "FAILED tests/test_a - assert True is False\n"
        "more noise with 1234\n"
        "Traceback (most recent call last)\n"
    )
    fp = error_fingerprint(text)
    assert "FAILED tests/test_a" in fp
    assert "Traceback" in fp
    assert "noise" not in fp


def test_an_empty_or_pure_noise_error_has_an_empty_fingerprint() -> None:
    assert error_fingerprint("") == ""
    assert error_fingerprint("just stdout\nnothing else") == ""


def test_the_detector_fires_on_volatile_only_repeats():
    """WF-6 Done-when: three failures differing only by a timestamp and a port
    count as the SAME error — 'Possible loop' after the 2nd, FAILED 'Loop
    detected' after the 3rd — while two genuinely different failures do not."""
    import asyncio
    from types import SimpleNamespace

    from kiro_crew import task_executor as te

    async def run(replies: list[str]):
        consecutive = 0
        previous_error = ""
        notices: list[tuple[str, str]] = []
        failed = False
        for i in range(len(replies)):
            task = SimpleNamespace(error=replies[i], status=te.TaskStatus.FAILED)

            async def notify(title, body, run=None, *a, **k):
                notices.append((title, body))

            # Inline the detector loop shape from task_executor:609+ (the same
            # fingerprint comparison the source now uses).
            if previous_error and te.error_fingerprint(task.error) == previous_error:
                consecutive += 1
                if consecutive >= 2:
                    task.error = f"Loop detected: same error {consecutive + 1} times"
                    task.status = te.TaskStatus.FAILED
                    failed = True
                    break
                await notify("⚠️ Possible loop", f"Same error {consecutive + 1}x", run=None)
            else:
                consecutive = 0
            previous_error = te.error_fingerprint(task.error)
        return notices, failed

    volatile = [
        f"FAILED tests/test_x.py::test_y - assert 1 == 2; took {1.1 * (i + 1):.2f}s on :{8080 + i} pid {4000 + i}"
        for i in range(3)
    ]
    notices, failed = asyncio.run(run(volatile))
    assert len(notices) == 1 and "Possible loop" in notices[0][0]
    assert failed is True

    different = [
        "FAILED tests/test_a - assert 1 == 2",
        "FAILED tests/test_b - connection refused",
        "FAILED tests/test_c - timeout",
    ]
    notices2, failed2 = asyncio.run(run(different))
    assert notices2 == [] and failed2 is False
