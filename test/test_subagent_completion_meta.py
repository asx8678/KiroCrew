"""Structured completion facts ride on the message meta, not the prose.

A finished sub-agent's completion is injected as the parent's next turn and
rendered by the dashboard as a card. Recovering the card's header facts
(outcome, tallies, which agent) by re-parsing the English prose the gateway
composes is fragile: a reword can silently break rendering with no failing test.

The gateway now stamps those facts as a structured dict on the injected row's
``meta[SUBAGENT_COMPLETION_META_KEY]``. These tests pin the helper shapes and
prove the queue-drain path carries the meta onto the row — as a dict on a
single completion, as a LIST when several completions drain merged (EVT-1) —
and does not invent it for a plain user message.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from chat_test_helpers import _make_state

from kiro_crew.constants import SUBAGENT_COMPLETION_META_KEY
from kiro_crew.dashboard.chat_runner import _start_next_queued_turn
from kiro_crew.dashboard.chat_utils import SUBAGENT_COMPLETION_KIND
from kiro_crew.dashboard.state import (
    SUBAGENT_BATCH_COMPLETION_PREFIX,
    SUBAGENT_COMPLETION_PREFIX,
)
from kiro_crew.subagent_completion_meta import (
    OUTCOME_FAILED,
    OUTCOME_INTERRUPTED,
    OUTCOME_OK,
    OUTCOME_STOPPED,
    single_completion_meta,
    wave_chunk_meta,
    wave_final_meta,
)

SINGLE = (
    f"{SUBAGENT_COMPLETION_PREFIX}\n"
    "Agent `a1` (kirocrew) completed ✅\n"
    "Task: add a label\n"
    "\n"
    "done."
)
WAVE = (
    f"{SUBAGENT_BATCH_COMPLETION_PREFIX}\n"
    "Batch results 1/1 — wave finished: 8 ✅ · 1 ❌ · 0 ⏹ of 9 agents. "
    "All results delivered.\n"
    "This run is complete.\n"
    "\n"
    "— `a1` ✅ first"
)


def _swallow_turn(_state, _slot, coro):
    """Stand-in for ``spawn_guarded_turn`` that never runs the turn.

    The drain hands a live ``_run_chat`` coroutine to the spawner; a bare
    ``MagicMock`` would drop it un-awaited and the interpreter reports that at
    garbage collection, against whichever later test happens to trigger it.
    Closing it here settles the coroutine without running a model turn.
    """
    coro.close()
    return MagicMock()


class TestMetaHelperShapes:
    """The dict shape is a wire contract with subagentCompletion.ts; keep the
    field names and the outcome tokens in lockstep with the ParsedSingleCompletion
    / ParsedBatchCompletion interfaces there."""

    def test_single_carries_outcome_agent_and_task(self) -> None:
        m = single_completion_meta(
            agent_id="a1", outcome=OUTCOME_OK, agent_name="kirocrew", task="add a label"
        )
        assert m == {
            "kind": "single",
            "agentId": "a1",
            "agentName": "kirocrew",
            "outcome": "ok",
            "task": "add a label",
            "note": "",
            "requestedModel": "",
            "resolvedModel": "",
        }

    def test_single_carries_requested_and_resolved_model(self) -> None:
        # The served model is auditable against the requested pin: the
        # card shows the resolved id and can flag a downgrade when the two differ.
        m = single_completion_meta(
            agent_id="a1",
            outcome=OUTCOME_OK,
            agent_name="kirocrew",
            task="review the diff",
            requested_model="claude-opus-4.8",
            resolved_model="claude-opus-4.7",
        )
        assert m["requestedModel"] == "claude-opus-4.8"
        assert m["resolvedModel"] == "claude-opus-4.7"

    def test_single_model_fields_default_empty_when_unknown(self) -> None:
        # A provider that cannot report a served model leaves both "" — the card
        # must treat that as "don't show", never as a wildcard.
        m = single_completion_meta(agent_id="a1", outcome=OUTCOME_OK)
        assert m["requestedModel"] == "" and m["resolvedModel"] == ""

    def test_single_note_carries_the_only_explanation_on_orphan_shapes(self) -> None:
        m = single_completion_meta(
            agent_id="a1", outcome=OUTCOME_INTERRUPTED, note="orphaned by gateway restart"
        )
        assert m["outcome"] == "interrupted"
        assert m["note"] == "orphaned by gateway restart"

    def test_outcome_tokens_match_the_frontend_union(self) -> None:
        assert (OUTCOME_OK, OUTCOME_FAILED, OUTCOME_STOPPED, OUTCOME_INTERRUPTED) == (
            "ok",
            "failed",
            "stopped",
            "interrupted",
        )

    def test_wave_final_carries_tallies_not_progress(self) -> None:
        m = wave_final_meta(chunk=1, chunks=1, ok=8, failed=1, stopped=0, total=9)
        assert m["kind"] == "batch"
        assert m["final"] is True
        assert (m["ok"], m["failed"], m["stopped"], m["total"]) == (8, 1, 0, 9)
        # delivered/running are implied by final and left for the frontend.
        assert "delivered" not in m and "running" not in m

    def test_wave_chunk_carries_progress_not_tallies(self) -> None:
        m = wave_chunk_meta(chunk=1, chunks=3, delivered=10, total=30, running=20)
        assert m["final"] is False
        assert (m["delivered"], m["total"], m["running"]) == (10, 30, 20)
        assert "ok" not in m and "failed" not in m


class TestDrainStampsMetaOntoRow:
    @pytest.mark.asyncio
    async def test_single_completion_meta_lands_on_the_subagent_row(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-single")
        slot.queue_append(
            SINGLE,
            meta={
                SUBAGENT_COMPLETION_META_KEY: single_completion_meta(
                    agent_id="a1", outcome=OUTCOME_OK, agent_name="kirocrew", task="add a label"
                )
            },
        )
        with patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_swallow_turn):
            started = await _start_next_queued_turn(state, slot)

        assert started is True
        row = [m for m in slot.messages if m["role"] == "subagent"][0]
        stamped = row["meta"][SUBAGENT_COMPLETION_META_KEY]
        assert stamped["kind"] == "single"
        assert stamped["agentId"] == "a1"
        assert stamped["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_wave_digest_meta_lands_on_the_subagent_row(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-wave")
        slot.queue_append(
            WAVE,
            meta={
                SUBAGENT_COMPLETION_META_KEY: wave_final_meta(
                    chunk=1, chunks=1, ok=8, failed=1, stopped=0, total=9
                )
            },
        )
        with patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_swallow_turn):
            await _start_next_queued_turn(state, slot)

        row = [m for m in slot.messages if m["role"] == "subagent"][0]
        stamped = row["meta"][SUBAGENT_COMPLETION_META_KEY]
        assert stamped["kind"] == "batch"
        assert stamped["final"] is True
        assert stamped["ok"] == 8 and stamped["failed"] == 1

    @pytest.mark.asyncio
    async def test_a_plain_user_message_gets_no_completion_meta(self, tmp_path) -> None:
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-user")
        slot.queue_append("please fix the bug")
        with patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_swallow_turn):
            await _start_next_queued_turn(state, slot)

        row = [m for m in slot.messages if m["role"] == "user"][0]
        meta = row.get("meta") or {}
        assert SUBAGENT_COMPLETION_META_KEY not in meta

    @pytest.mark.asyncio
    async def test_a_merged_completion_drain_carries_each_meta_as_a_list(self, tmp_path) -> None:
        """EVT-1: several completions drain as ONE turn now, and the row carries
        each member's structured facts as a LIST under the one key — a plain
        last-writer-wins merge would silently keep only the final member's
        dict. The frontend reads the dict shape on single rows and degrades to
        the prose parse on anything else, so the list is safe to stamp."""
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-merged")
        for i in (1, 2, 3):
            slot.queue_append(
                f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a{i}` (kirocrew) completed ✅\n"
                f"Task: thing {i}\n\ndone {i}",
                kind=SUBAGENT_COMPLETION_KIND,
                meta={
                    SUBAGENT_COMPLETION_META_KEY: single_completion_meta(
                        agent_id=f"a{i}", outcome=OUTCOME_OK
                    )
                },
            )
        with patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_swallow_turn):
            await _start_next_queued_turn(state, slot)

        row = [m for m in slot.messages if m["role"] == "subagent"][0]
        stamped = row["meta"][SUBAGENT_COMPLETION_META_KEY]
        assert isinstance(stamped, list) and len(stamped) == 3
        assert [m["agentId"] for m in stamped] == ["a1", "a2", "a3"]

    @pytest.mark.asyncio
    async def test_five_completions_drain_as_one_turn_and_settle_all_five_debts(
        self, tmp_path
    ) -> None:
        """EVT-1's done-when: 5 queued SUBAGENT_COMPLETION_KIND entries drain as
        ONE _run_chat carrying all 5 announces, every member's delivery debt
        settles through that single turn, and the drain counts ONE completion
        turn for the synthesis fire gate."""
        from kiro_crew.subagent import SubagentDelivery

        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-five")
        announces = []
        for i in range(1, 6):
            announce = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `m{i}` completed ✅\nresult {i}"
            announces.append(announce)
            slot.queue_append(
                announce,
                kind=SUBAGENT_COMPLETION_KIND,
                meta={
                    SUBAGENT_COMPLETION_META_KEY: single_completion_meta(
                        agent_id=f"m{i}", outcome=OUTCOME_OK
                    )
                },
            )
            slot.note_pending_subagent_delivery(announce, [SubagentDelivery(f"m{i}", 1.0, 0.1)])

        started: list = []
        settles: list = []

        def _record_turn(_state, _slot, coro):
            started.append(coro)
            coro.close()
            return MagicMock()

        with (
            patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_record_turn),
            patch(
                "kiro_crew.dashboard.chat_runner._arm_queued_delivery_settlement",
                side_effect=lambda *a, **k: settles.append(a[3]),
            ),
        ):
            assert await _start_next_queued_turn(state, slot) is True

        assert len(started) == 1, "5 completions must dispatch ONE turn"
        row = [m for m in slot.messages if m["role"] == "subagent"][0]
        for announce in announces:
            assert announce in row["content"], "the merged turn must carry every announce"
        assert sorted(settles[0]) == sorted(announces), "every debt must settle"
        assert slot._synthesis_completion_turns == 1, "a merged drain counts once"

    @pytest.mark.asyncio
    async def test_meta_stays_off_a_user_row_drained_ahead_of_a_completion(self, tmp_path) -> None:
        """A user message drains first (a completion never merges with it), so
        the first drained row is the user's and must carry no completion meta
        even though a completion with meta sits behind it in the queue."""
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("meta-merge")
        slot.queue_append("do a thing")
        slot.queue_append(
            SINGLE,
            meta={
                SUBAGENT_COMPLETION_META_KEY: single_completion_meta(
                    agent_id="a1", outcome=OUTCOME_OK
                )
            },
        )
        with patch("kiro_crew.dashboard.chat_runner.spawn_guarded_turn", side_effect=_swallow_turn):
            await _start_next_queued_turn(state, slot)

        drained = [m for m in slot.messages if m["role"] in ("user", "subagent")][0]
        assert drained["role"] == "user"
        assert SUBAGENT_COMPLETION_META_KEY not in (drained.get("meta") or {})
