"""Tests for merge_queued_messages feature."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dashboard_owner_helpers import as_owner

from kiro_crew.dashboard.chat import _dequeue_next_message
from kiro_crew.dashboard.chat_utils import (
    CRON_NOTIFICATION_KIND,
    SUBAGENT_COMPLETION_KIND,
)
from kiro_crew.dashboard.state import (
    CRON_NOTIFY_PREFIX,
    SUBAGENT_COMPLETION_PREFIX,
    DashboardState,
    _ChatSlot,
)

# ── Unit tests: _dequeue_next_message ──


class TestDequeueNextMessage:
    """Tests for the extracted _dequeue_next_message helper."""

    def test_merge_two_plus_messages_when_enabled(self):
        """When enabled and 2+ messages queued, they are joined with \\n\\n."""
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": "fix the bug"},
            {"id": "b", "content": "also add tests"},
            {"id": "c", "content": "use junit5"},
        ]
        for item in list(slot._queue):
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert (
            next_msg == "[3 queued messages merged]\n\nfix the bug\n\nalso add tests\n\nuse junit5"
        )
        assert [c["content"] for c in consumed] == ["fix the bug", "also add tests", "use junit5"]
        assert len(slot._queue) == 0

    def test_single_message_pops_normally_when_enabled(self):
        """When enabled but only 1 message queued, pop normally (no merge)."""
        slot = _ChatSlot("s1")
        slot._queue = [{"id": "a", "content": "single message"}]
        slot.append("queued", "single message", "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == "single message"
        assert [c["content"] for c in consumed] == ["single message"]
        assert len(slot._queue) == 0

    def test_synthetic_recovery_entry_breaks_merge(self):
        """A runner-injected synthetic recovery instruction (empty-response
        nudge / post-transient CONTINUE) must NEVER be folded into a
        "[N queued messages merged]" user turn: merged, the internal text
        would drain with the user role (persisted as user-authored history and
        mirrored to linked channels). Classification is STRUCTURAL — the
        entry's kind tag set at queue_insert time — so it drains ALONE,
        exactly like sub-agent and cron injections."""
        from kiro_crew.dashboard.chat_utils import (
            _SYNTHETIC_RECOVERY_MSGS,
            SYNTHETIC_RECOVERY_KIND,
        )

        for synthetic in _SYNTHETIC_RECOVERY_MSGS:
            slot = _ChatSlot("s1")
            slot.queue_insert(0, "a genuine user message")
            slot.queue_insert(0, synthetic, kind=SYNTHETIC_RECOVERY_KIND)
            next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)
            assert next_msg == synthetic
            assert [c["content"] for c in consumed] == [synthetic]
            # The user message stays queued for its own (user-role) turn.
            assert [i["content"] for i in slot._queue] == ["a genuine user message"]

    def test_refusal_recovery_entry_breaks_merge(self):
        """A tool-refusal recovery injection (built dynamically, not a constant
        in _SYNTHETIC_RECOVERY_MSGS) must carry the structural kind tag so it
        drains ALONE rather than folding into a user-role merged turn — the
        regression behind the composer leak, where an untagged refusal-recovery
        entry classified as user speech AND rendered as an editable queue card.
        Mirrors the chat_runner call site, which now passes
        kind=SYNTHETIC_RECOVERY_KIND."""
        from kiro_crew.dashboard.chat_utils import SYNTHETIC_RECOVERY_KIND
        from kiro_crew.dashboard.state import (
            REFUSAL_RECOVERY_PREFIX,
            build_refusal_recovery_prompt,
        )

        body = build_refusal_recovery_prompt([("write /tmp/x", "not on read-only allowlist")])
        injection = f"{REFUSAL_RECOVERY_PREFIX}\n{body}"

        slot = _ChatSlot("s1")
        slot.queue_insert(0, "a genuine user message")
        slot.queue_insert(0, injection, kind=SYNTHETIC_RECOVERY_KIND)
        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)
        assert next_msg == injection
        assert [c["content"] for c in consumed] == [injection]
        assert [i["content"] for i in slot._queue] == ["a genuine user message"]

    def test_user_pasted_recovery_text_is_plain_user_content(self):
        """A user PASTING the transcript-visible recovery text verbatim is a
        plain user message: without the structural kind tag it must merge and
        classify as user speech — content equality is deliberately NOT a
        classification mechanism (attribution correctness)."""
        from kiro_crew.dashboard.chat_utils import _EMPTY_AUTO_CONTINUE_MSG

        slot = _ChatSlot("s1")
        slot.queue_append(_EMPTY_AUTO_CONTINUE_MSG)  # no kind: user-typed
        slot.queue_append("and my follow-up question")
        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)
        assert next_msg.startswith("[2 queued messages merged]")
        assert len(consumed) == 2

    def test_multiple_messages_fifo_when_disabled(self):
        """When disabled, only first message is popped (original FIFO)."""
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": "first"},
            {"id": "b", "content": "second"},
            {"id": "c", "content": "third"},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=False)

        assert next_msg == "first"
        assert [c["content"] for c in consumed] == ["first"]
        assert [q["content"] for q in slot._queue] == ["second", "third"]

    def test_empty_queue_after_single_pop(self):
        """Single message in queue pops cleanly."""
        slot = _ChatSlot("s1")
        slot._queue = [{"id": "a", "content": "only one"}]
        slot.append("queued", "only one", "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=False)

        assert next_msg == "only one"
        assert [c["content"] for c in consumed] == ["only one"]
        assert len(slot._queue) == 0

    def test_cron_message_not_merged(self):
        """Cron-prefixed messages are never merged — popped individually."""
        cron_msg = f"{CRON_NOTIFY_PREFIX}daily-check]: run report"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": "user msg"},
            {"id": "b", "content": cron_msg, "kind": CRON_NOTIFICATION_KIND},
            {"id": "c", "content": "another user msg"},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        # First dequeue: only "user msg" pops (cron breaks the merge)
        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == "user msg"
        assert [c["content"] for c in consumed] == ["user msg"]
        assert [q["content"] for q in slot._queue] == [cron_msg, "another user msg"]

    def test_partial_merge_before_cron(self):
        """Multiple user messages before a cron are merged; cron and later messages stay."""
        cron_msg = f"{CRON_NOTIFY_PREFIX}daily]: run report"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": "msg1"},
            {"id": "b", "content": "msg2"},
            {"id": "c", "content": cron_msg, "kind": CRON_NOTIFICATION_KIND},
            {"id": "d", "content": "msg3"},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == "[2 queued messages merged]\n\nmsg1\n\nmsg2"
        assert [c["content"] for c in consumed] == ["msg1", "msg2"]
        assert [q["content"] for q in slot._queue] == [cron_msg, "msg3"]

    def test_cron_first_in_queue_pops_individually(self):
        """If cron message is first, it pops as single (no merge)."""
        cron_msg = f"{CRON_NOTIFY_PREFIX}hourly]: check status"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": cron_msg, "kind": CRON_NOTIFICATION_KIND},
            {"id": "b", "content": "user follow-up"},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == cron_msg
        assert [c["content"] for c in consumed] == [cron_msg]
        assert [q["content"] for q in slot._queue] == ["user follow-up"]

    def test_subagent_completion_not_merged(self):
        """Subagent completions are never merged — popped individually like crons."""
        subagent_msg = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `abc123` completed ✅\nResult text"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": "user msg"},
            {"id": "b", "content": subagent_msg, "kind": SUBAGENT_COMPLETION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == "user msg"
        assert [c["content"] for c in consumed] == ["user msg"]
        assert [q["content"] for q in slot._queue] == [subagent_msg]

    def test_subagent_first_in_queue_pops_individually(self):
        """If subagent completion is first, it pops as single (no merge)."""
        subagent_msg = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `xyz` completed ✅\nDone"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": subagent_msg, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": "user follow-up"},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == subagent_msg
        assert [c["content"] for c in consumed] == [subagent_msg]
        assert [q["content"] for q in slot._queue] == ["user follow-up"]

    def test_consecutive_subagent_completions_merge_into_one_drain(self):
        """EVT-1: each queued completion was its own full-context parent turn;
        consecutive same-kind entries now drain as ONE turn (no synthetic
        header — the first announce's structural prefix still leads, so the
        row keeps classifying by it)."""
        sa1 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1` completed ✅\nResult 1"
        sa2 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a2` completed ✅\nResult 2"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": sa1, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": sa2, "kind": SUBAGENT_COMPLETION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert next_msg == f"{sa1}\n\n{sa2}"
        assert [c["content"] for c in consumed] == [sa1, sa2]
        assert len(slot._queue) == 0

    def test_a_different_system_kind_breaks_the_merge_run(self):
        """Only SAME-kind runs fold: a cron notice behind a completion drains on
        its own turn, not inside the completion's."""
        sa = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1` completed ✅\nResult 1"
        cron = f"{CRON_NOTIFY_PREFIX} job nightly: ok"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": sa, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": cron, "kind": CRON_NOTIFICATION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert [c["content"] for c in consumed] == [sa]
        assert next_msg == sa
        assert [q["content"] for q in slot._queue] == [cron]

    def test_a_user_message_breaks_the_merge_run(self):
        """A held user entry between two completions keeps them on separate
        turns — order is preserved and nothing drains past the user."""
        sa1 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1` completed ✅\nR1"
        sa2 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a2` completed ✅\nR2"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": sa1, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": "user follow-up"},
            {"id": "c", "content": sa2, "kind": SUBAGENT_COMPLETION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert [c["content"] for c in consumed] == [sa1]
        assert [q["content"] for q in slot._queue] == ["user follow-up", sa2]

    def test_the_system_drain_merges_past_held_user_entries(self):
        """The hold_users path (subagents running) drains the same-kind RUN:
        completions on either side of nothing still fold, while the held user
        entries stay queued."""
        from kiro_crew.dashboard.chat_utils import _dequeue_next_system_message

        sa1 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1` completed ✅\nR1"
        sa2 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a2` completed ✅\nR2"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "u", "content": "held user text"},
            {"id": "a", "content": sa1, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": sa2, "kind": SUBAGENT_COMPLETION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        next_msg, consumed = _dequeue_next_system_message(slot)

        assert next_msg == f"{sa1}\n\n{sa2}"
        assert [c["content"] for c in consumed] == [sa1, sa2]
        assert [q["content"] for q in slot._queue] == ["held user text"]

    def test_the_merge_respects_the_body_budget(self, monkeypatch):
        """The fold stops at MERGED_INJECTION_MAX_CHARS; the entries beyond it
        stay queued for the next turn instead of flooding one prompt."""
        from kiro_crew.dashboard import chat_utils

        sa1 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a1` completed ✅\nR1"
        sa2 = f"{SUBAGENT_COMPLETION_PREFIX}\nAgent `a2` completed ✅\nR2"
        slot = _ChatSlot("s1")
        slot._queue = [
            {"id": "a", "content": sa1, "kind": SUBAGENT_COMPLETION_KIND},
            {"id": "b", "content": sa2, "kind": SUBAGENT_COMPLETION_KIND},
        ]
        for item in slot._queue:
            slot.append("queued", item["content"], "msg msg-queued")

        monkeypatch.setattr(chat_utils, "MERGED_INJECTION_MAX_CHARS", len(sa1) + 1)
        next_msg, consumed = _dequeue_next_message(slot, merge_enabled=True)

        assert [c["content"] for c in consumed] == [sa1]
        assert [q["content"] for q in slot._queue] == [sa2]


# ── API tests: /api/dashboard/config ──


def _make_state(tmp_path):
    state = DashboardState.__new__(DashboardState)
    state._slots = {}
    state._background_tasks = set()
    state._pending_approvals = {}
    state.conversation_log = None
    state.slack_client = None
    state.sessions = None
    state.subagents = None
    return state


def _make_config_app(tmp_path):
    from kiro_crew.dashboard.handlers import api_dashboard_config

    state = _make_state(tmp_path)
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/dashboard/config", api_dashboard_config)
    app.router.add_put("/api/dashboard/config", api_dashboard_config)
    return as_owner(app)


class TestDashboardConfigMergeQueued:
    @pytest.mark.asyncio
    async def test_get_includes_merge_queued_messages(self, tmp_path, monkeypatch):
        """GET /api/dashboard/config returns merge_queued_messages field."""
        monkeypatch.setattr("kiro_crew.config.loader.config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        with patch("kiro_crew.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/dashboard/config")
                assert resp.status == 200
                data = await resp.json()
                assert "merge_queued_messages" in data
                assert data["merge_queued_messages"] is False  # default

    @pytest.mark.asyncio
    async def test_put_persists_merge_queued_messages(self, tmp_path, monkeypatch):
        """PUT merge_queued_messages=true persists to config.json."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr("kiro_crew.config.loader.config_path", lambda: cfg_file)
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        with patch("kiro_crew.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"merge_queued_messages": True},
                )
                assert resp.status == 200

                # Verify persisted
                assert cfg_file.exists()
                saved = json.loads(cfg_file.read_text(encoding="utf-8"))
                assert saved["dashboard"]["merge_queued_messages"] is True

                # Verify GET reflects the change
                resp = await client.get("/api/dashboard/config")
                data = await resp.json()
                assert data["merge_queued_messages"] is True

    @pytest.mark.asyncio
    async def test_put_rejects_non_dict_body(self, tmp_path, monkeypatch):
        """PUT with a non-object JSON body returns 400."""
        monkeypatch.setattr("kiro_crew.config.loader.config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        with patch("kiro_crew.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put("/api/dashboard/config", json=[1, 2])
                assert resp.status == 400
                data = await resp.json()
                assert "JSON object" in data["error"]

    @pytest.mark.asyncio
    async def test_put_rejects_unknown_fields(self, tmp_path, monkeypatch):
        """PUT with unknown fields returns 400."""
        monkeypatch.setattr("kiro_crew.config.loader.config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        with patch("kiro_crew.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put("/api/dashboard/config", json={"bogus_field": True})
                assert resp.status == 400
                data = await resp.json()
                assert "Unknown fields" in data["error"]

    @pytest.mark.asyncio
    async def test_put_rejects_non_boolean_merge_queued(self, tmp_path, monkeypatch):
        """PUT merge_queued_messages with non-boolean returns 400."""
        monkeypatch.setattr("kiro_crew.config.loader.config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        with patch("kiro_crew.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config", json={"merge_queued_messages": "yes"}
                )
                assert resp.status == 400
                data = await resp.json()
                assert "must be a boolean" in data["error"]
