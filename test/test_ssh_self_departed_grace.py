"""The ssh-self own-address set shrinks when an address departs (SEC-17).

The own-host set used to be only ever UNIONED -- startup, netlink publication,
the 300s refresh -- so an address the host once held (an old DHCP lease, a
detached VPN) marked remote hosts resolving to it as "self" for the rest of a
long-running gateway's life, and the DENY verdicts that produced were permanent.
``_apply_own_set_update`` now ledgers departures: a departed address stays
"self" only for the grace window, the effective-set change bumps the own-set
generation, and a deny recorded against an older generation is revalidated
instead of served forever. Loopback/unspecified stays self whatever the set
holds (generation -1: permanent, fail-closed direction kept).
"""

from __future__ import annotations

import pytest

import kiro_crew.security.argv_floor as _argv_floor


class _Clock:
    """A frozen monotonic clock the ledger reads instead of the real one."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture()
def frozen(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    ck = _Clock()
    monkeypatch.setattr(_argv_floor.time, "monotonic", ck.monotonic)
    monkeypatch.setattr(_argv_floor, "_OWN_HOST_NAMES_CACHE", frozenset())
    monkeypatch.setattr(_argv_floor, "_OWN_HOST_DEPARTED", {})
    monkeypatch.setattr(_argv_floor, "_OWN_SET_GENERATION", 0)
    return ck


class TestDepartedAddressLedger:
    def test_a_departed_address_stays_self_only_for_the_grace_window(self, frozen: _Clock) -> None:
        _argv_floor._apply_own_set_update(frozenset({"10.0.0.5"}))
        assert "10.0.0.5" in _argv_floor._OWN_HOST_NAMES_CACHE
        gen_held = _argv_floor._OWN_SET_GENERATION

        # The interface drops the address: it ledgers as departed, still self.
        _argv_floor._apply_own_set_update(frozenset())
        assert "10.0.0.5" in _argv_floor._OWN_HOST_NAMES_CACHE, "grace must hold it"

        # Past the grace window the next update prunes it: no longer self,
        # and the effective-set change bumped the generation a deny can compare.
        frozen.now += _argv_floor._OWN_HOST_DEPARTED_GRACE + 1.0
        _argv_floor._apply_own_set_update(frozenset())
        assert "10.0.0.5" not in _argv_floor._OWN_HOST_NAMES_CACHE
        assert _argv_floor._OWN_SET_GENERATION > gen_held

    def test_a_returning_address_leaves_the_ledger_without_a_generation_bump(
        self, frozen: _Clock
    ) -> None:
        _argv_floor._apply_own_set_update(frozenset({"10.0.0.5"}))
        gen_held = _argv_floor._OWN_SET_GENERATION

        # Drop and re-add INSIDE the grace window: the effective set never
        # changes (live plus in-grace departures stays {10.0.0.5}), so no
        # generation bump -- no needless deny revalidation for a flapping
        # interface.
        _argv_floor._apply_own_set_update(frozenset())
        _argv_floor._apply_own_set_update(frozenset({"10.0.0.5"}))
        assert _argv_floor._OWN_SET_GENERATION == gen_held
        assert _argv_floor._OWN_HOST_DEPARTED == {}

    def test_the_fail_closed_direction_is_kept_for_loopback(self) -> None:
        # Whatever the own set holds, loopback is always self: its denies carry
        # generation -1 and never revalidate.
        assert _argv_floor._host_is_self("127.0.0.1") is True
        assert _argv_floor._host_is_self("localhost") is True


class TestStaleGenerationDeny:
    """A deny recorded against an older own-set generation is revalidated.

    The revalidation runs on the single-flight worker (``_HOST_VERDICT_PENDING``);
    this pins the DECISION: a stale-generation deny re-arms the pending mark,
    while a current-generation deny is served as-is.
    """

    def _armed(self, monkeypatch: pytest.MonkeyPatch, recorded_gen: int | None) -> bool:
        import threading

        spawned: list[object] = []

        class _NoThread:
            def __init__(self, *a: object, **k: object) -> None:
                spawned.append(a)

            def start(self) -> None:
                pass

        monkeypatch.setattr(_argv_floor.threading, "Thread", _NoThread)
        monkeypatch.setattr(_argv_floor, "_OWN_HOST_NAMES_CACHE", frozenset())
        monkeypatch.setattr(_argv_floor, "_OWN_SET_GENERATION", 7)
        monkeypatch.setattr(_argv_floor, "_HOST_VERDICT_CACHE", {"remote.example": True})
        monkeypatch.setattr(_argv_floor, "_HOST_VERDICT_STAMP", {"remote.example": 0.0})
        gen_map = {} if recorded_gen is None else {"remote.example": recorded_gen}
        monkeypatch.setattr(_argv_floor, "_HOST_VERDICT_GEN", gen_map)
        monkeypatch.setattr(_argv_floor, "_HOST_VERDICT_PENDING", set())
        _argv_floor._resolved_host_verdict("remote.example")
        return "remote.example" in _argv_floor._HOST_VERDICT_PENDING and bool(spawned)

    def test_a_deny_from_an_older_generation_revalidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._armed(monkeypatch, recorded_gen=6) is True

    def test_a_current_generation_deny_is_served_as_is(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._armed(monkeypatch, recorded_gen=7) is False

    def test_a_permanent_loopback_deny_never_revalidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._armed(monkeypatch, recorded_gen=-1) is False
