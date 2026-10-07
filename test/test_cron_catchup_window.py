"""A cron boundary the host slept through fires once on wake (LOOP-32).

Only cron-EXPRESSION jobs ever missed a boundary: ``is_due`` was true only
while the expression matched the current minute, so a host waking at 09:01+
never fired that day's ``0 9 * * *`` and re-armed for the next day. ``every``
and ``at`` jobs already catch up (due whenever ``now >= last_run +
interval`` / ``at_ts``). The catch-up window (``agent.cron_catchup_window_secs``,
default 6h, 0 = the historical off) closes that gap using the job's PERSISTED
``last_run_ts`` as the marker, so a restart cannot lose the occurrence.
"""

from __future__ import annotations

import datetime
import zoneinfo

from kiro_crew.cron_service.model import CronJob, CronSchedule
from kiro_crew.cron_service.schedule import is_due

TZ = zoneinfo.ZoneInfo("UTC")
WINDOW = 6 * 3600


def _ts(hour: int, minute: int = 0) -> float:
    return datetime.datetime(2026, 3, 10, hour, minute, tzinfo=TZ).timestamp()


def _cron_job(last_run: float = 0.0, skip: list[str] | None = None) -> CronJob:
    return CronJob(
        id="j",
        name="j",
        message="m",
        schedule=CronSchedule(kind="cron", cron_expr="0 9 * * *"),
        last_run_ts=last_run,
        skip_dates=skip or [],
        timezone="UTC",
    )


class TestCatchUp:
    def test_a_boundary_the_host_slept_through_fires_once(self) -> None:
        job = _cron_job(last_run=_ts(9) - 86400)
        assert is_due(job, _ts(9, 30), catchup_window_secs=WINDOW) is True

    def test_no_double_fire_after_the_catchup_ran(self) -> None:
        job = _cron_job(last_run=_ts(9, 30))
        assert is_due(job, _ts(9, 31), catchup_window_secs=WINDOW) is False

    def test_the_window_edge_is_honoured(self) -> None:
        job = _cron_job(last_run=_ts(9) - 86400)
        assert is_due(job, _ts(15, 0), catchup_window_secs=WINDOW) is True  # exactly 6h
        assert is_due(job, _ts(15, 30), catchup_window_secs=WINDOW) is False  # 6.5h

    def test_zero_and_default_keep_the_historical_behaviour(self) -> None:
        job = _cron_job(last_run=_ts(9) - 86400)
        assert is_due(job, _ts(9, 30), catchup_window_secs=0) is False
        assert is_due(job, _ts(9, 30)) is False

    def test_a_skip_dated_day_is_never_caught_up(self) -> None:
        job = _cron_job(last_run=_ts(9) - 86400, skip=["2026-03-10"])
        assert is_due(job, _ts(9, 30), catchup_window_secs=WINDOW) is False

    def test_an_on_time_fire_and_the_same_minute_dedup_are_unchanged(self) -> None:
        job = _cron_job(last_run=_ts(9) - 86400)
        assert is_due(job, _ts(9) + 30, catchup_window_secs=WINDOW) is True
        dedup = _cron_job(last_run=_ts(9) + 10)
        assert is_due(dedup, _ts(9) + 40, catchup_window_secs=WINDOW) is False


class TestEveryJobUnchanged:
    def test_interval_jobs_keep_their_own_catchup_semantics(self) -> None:
        job = CronJob(
            id="e",
            name="e",
            message="m",
            schedule=CronSchedule(kind="every", every_secs=3600),
            last_run_ts=_ts(8),
        )
        assert is_due(job, _ts(9, 30), catchup_window_secs=WINDOW) is True
        assert is_due(job, _ts(8, 30), catchup_window_secs=WINDOW) is False
