"""A conductor that binds work always has a gated work-ledger patrol (LOOP-19).

Arming was prompt-only, so a conductor that never called monitor_start left
conductor_wake nothing to pull forward: worker reports reached nobody until a
human prompted it. The bind path runs a BACKSTOP through the same authorized
chokepoint monitor_start uses -- only when no active work-ledger loop exists for
the slot, gated, idempotent (a second bind arms nothing).
"""

from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest

CONDUCTOR = "chat-77-abc"


class _State:
    _background_tasks: "set[asyncio.Task[None]]" = set()
    _slots = {CONDUCTOR: SimpleNamespace(key=CONDUCTOR, mode="", workspace="default")}

    def get_slot(self, key: str) -> Any:
        return None  # no transcript window here; the notice is best-effort


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.05)


class TestBindArmsAPatrol:
    @pytest.mark.asyncio
    async def test_a_slot_with_no_loop_gets_exactly_one_gated_patrol(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("KIROCREW_AUTONUDGE", "1")
        from kiro_crew import autonudge
        from kiro_crew.conductor_wake import work_ledger_loop_id
        from kiro_crew.dashboard.handlers import work_ledger as wl

        svc = autonudge.AutoNudgeService(base_dir=tmp_path / "an")
        await svc.start()
        try:
            assert work_ledger_loop_id(svc, CONDUCTOR) == ""
            wl._arm_conductor_patrol_backstop(_State(), CONDUCTOR)
            await _settle()
            loop_id = work_ledger_loop_id(svc, CONDUCTOR)
            assert loop_id, "the backstop did not arm"
            loop = svc.get_by_slot(CONDUCTOR)
            assert str(getattr(loop.monitor, "kind", "")) == "work-ledger"
            assert loop.gate is True

            # A second bind arms nothing new.
            wl._arm_conductor_patrol_backstop(_State(), CONDUCTOR)
            await _settle()
            assert svc.get_by_slot(CONDUCTOR).id == loop_id
        finally:
            svc.stop()
