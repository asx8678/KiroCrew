"""An expired sign-in never charges a cron job toward auto-pause (LOOP-24).

``AcpAuthRequired`` is non-transient (``transient`` is False, and respawning
the backend process hits the same wall), so it skips the transient retry ladder
in the gateway's cron failure handler. Before the auth arm it reached
``record_failure()``: five sign-in lapses auto-paused a healthy schedule, and a
paused job never fires, so every LLM cron that woke during an
expired-credential window stayed disabled until the user re-enabled each one by
hand. The arm alerts once through the SAME dedup anchor every other failure
alert uses, marks the run ``error``, and leaves the counter untouched.

The per-job ``auto_pause_after`` field (None = global default, 0 = never) is
pinned here too, at the model layer where the threshold arithmetic lives.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiro_crew.acp.client import AcpAuthRequired
from kiro_crew.cron import CronJob, CronSchedule
from kiro_crew.cron_service.fields import apply_job_update, build_job


def _make_gw() -> Any:
    """GatewayOrchestrator via __new__ (see test_cron_acp_retry for the pattern)."""
    from kiro_crew.slack.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.sessions = MagicMock()
    gw.ctx_builder = MagicMock()
    gw.slack = MagicMock()
    gw.slack.post_message = AsyncMock()
    gw._open_dm_with_retry = AsyncMock(return_value="D123")
    gw._deliver_cron_to_channel = AsyncMock(return_value=False)
    gw.conv_log = None
    gw.dashboard_state = MagicMock()
    gw.dashboard_state.get_slot = MagicMock(return_value=None)
    gw.dashboard_state.has_slot = MagicMock(return_value=False)
    gw.dashboard_state.notify = MagicMock()
    gw.dashboard_state.get_active_session = MagicMock(return_value=None)
    gw._owner_id = "U000"
    gw.subagent_mgr = None
    gw._cron_injecting = {}
    gw._running_script_ids = set()
    gw._no_crons = False
    gw.cron_svc = MagicMock()
    gw.cron_svc.remove_job_async = AsyncMock(return_value=True)
    gw._cfg = MagicMock()
    gw._cfg.agent.provider = "acp"
    gw._cfg.hooks = {}
    gw._approval_mode = None
    gw.sessions.release = MagicMock()
    gw.sessions.reset = AsyncMock()
    gw.sessions.set_thread = AsyncMock()
    gw.sessions.set_channel = AsyncMock()
    gw.sessions.get_channel = MagicMock(return_value=None)
    gw.sessions.get_pid = MagicMock(return_value=None)
    provider_mock = MagicMock()
    gw.sessions.get_or_create = AsyncMock(return_value=(provider_mock, True, False))
    gw.ctx_builder.build_message = MagicMock(return_value=("full prompt", None))
    gw.ctx_builder.hooks = MagicMock()
    gw._interactive_approval = MagicMock(return_value="cb")
    return gw


def _make_llm_job(**overrides: Any) -> CronJob:
    defaults = dict(
        id="auth-j1",
        name="auth-job",
        message="Run the daily check",
        schedule=CronSchedule(kind="every", every_secs=3600),
    )
    defaults.update(overrides)
    return CronJob(**defaults)


async def _fire(gw: Any, job: CronJob, stream_side_effect: Any, *, fires: int) -> None:
    """Run the ONE callback the gateway built ``fires`` times, as successive
    schedule slots would; exceptions raised by the arm are swallowed here
    because the assertions are on the job's state, not on the raise."""
    captured_cb: list[Any] = []

    def capture_cron(on_job: Any = None, **kw: Any) -> MagicMock:
        captured_cb.append(on_job)
        svc = MagicMock()
        svc.start = AsyncMock()
        svc.remove_job_async = AsyncMock(return_value=True)
        return svc

    with (
        patch("kiro_crew.slack.gateway.CronService") as mock_cron_cls,
        patch("kiro_crew.slack.gateway.run_in_embed_pool", AsyncMock(return_value=("p", None))),
        patch(
            "kiro_crew.slack.gateway.stream_and_collect", AsyncMock(side_effect=stream_side_effect)
        ),
        patch("kiro_crew.slack.gateway.sel"),
        patch("kiro_crew.slack.gateway.build_cron_session_context") as mock_ctx,
    ):
        mock_ctx.return_value = (f"cron:{job.id}", job.message)
        mock_cron_cls.create = AsyncMock(side_effect=capture_cron)
        await gw._init_cron()
        cb = captured_cb[0]
        assert cb is not None
        for _ in range(fires):
            try:
                await cb(job)
            except AcpAuthRequired:
                pass  # the arm re-raises so the run history stays truthful
            except RuntimeError:
                pass


class TestAuthFailureNeverCharges:
    """AcpAuthRequired alerts once and never moves the auto-pause counter."""

    @pytest.mark.asyncio
    async def test_six_auth_fires_leave_the_job_enabled_with_one_alert(self) -> None:
        gw = _make_gw()
        job = _make_llm_job()
        await _fire(
            gw,
            job,
            AcpAuthRequired("kiro-cli sign-in expired; re-authenticate"),
            fires=6,
        )
        # The counter never moved: an expired sign-in is the operator's state,
        # not this job's defect.
        assert job.consecutive_failures == 0
        assert job.auto_paused is False
        assert job.enabled is True
        # The run itself is recorded as failed, for truthful history.
        assert job.last_status == "error"
        assert "sign-in expired" in job.last_error
        # Exactly one alert across six fires: the dedup anchor (the SAME
        # last_failure_hash/last_failure_at pair every other failure alert
        # uses) suppresses the repeats inside the reminder window.
        assert gw.dashboard_state.notify.call_count == 1
        assert gw.slack.post_message.await_count == 1

    @pytest.mark.asyncio
    async def test_non_auth_failures_still_pause_at_the_default_threshold(self) -> None:
        """The exemption is auth-only: a genuinely failing job still reaches
        auto-pause at the global default (5)."""
        gw = _make_gw()
        job = _make_llm_job()
        await _fire(gw, job, RuntimeError("backend exploded"), fires=5)
        assert job.consecutive_failures == 5
        assert job.auto_paused is True
        assert job.enabled is False


class TestAutoPauseAfterField:
    """The per-job threshold override: None = default, 0 = never."""

    def test_default_job_pauses_at_five(self) -> None:
        job = build_job(name="d", message="m", every_secs=60)
        assert job.auto_pause_after is None
        for _ in range(4):
            job.record_failure()
        assert not job.auto_paused
        job.record_failure()
        assert job.auto_paused and not job.enabled

    def test_zero_never_pauses_after_ten(self) -> None:
        job = build_job(name="z", message="m", every_secs=60, auto_pause_after=0)
        for _ in range(10):
            job.record_failure()
        assert job.consecutive_failures == 10
        assert job.auto_paused is False
        assert job.enabled is True

    def test_custom_threshold_pauses_there(self) -> None:
        job = build_job(name="c", message="m", every_secs=60, auto_pause_after=3)
        for _ in range(3):
            job.record_failure()
        assert job.auto_paused

    def test_invalid_values_are_rejected(self) -> None:
        for bad in (True, 1.5, -1, 10001):
            with pytest.raises(ValueError):
                build_job(name="b", message="m", every_secs=60, auto_pause_after=bad)

    def test_update_sets_and_resets(self) -> None:
        job = build_job(name="u", message="m", every_secs=60)
        apply_job_update(job, {"auto_pause_after": 2}, None)
        assert job.auto_pause_after == 2
        job.record_failure()
        job.record_failure()
        assert job.auto_paused
        # An explicit null RESETS to the global default.
        apply_job_update(job, {"auto_pause_after": None}, None)
        assert job.auto_pause_after is None

    def test_update_rejects_garbage(self) -> None:
        job = build_job(name="u2", message="m", every_secs=60)
        for bad in (True, "x", -2, 20000, 2.5):
            with pytest.raises(ValueError):
                apply_job_update(job, {"auto_pause_after": bad}, None)
