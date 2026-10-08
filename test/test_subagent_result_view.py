"""Tests for the subagent completion-event summary and spawn_status paged reads.

Covers:
- context_management.summarize_result — the summary + result_path body injected
  into the parent when the completion copy dropped content.
- dashboard.handlers.messaging._spawn_result_view — line-oriented offset/limit/
  grep slicing and the tail view the spawn_status no-argument default requests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

from kiro_crew.context_management import summarize_result
from kiro_crew.dashboard.handlers.messaging import _apply_result_view, _spawn_result_view
from kiro_crew.mcp_tools import spawn as spawn_tools


class TestSummarizeResult:
    def test_small_result_inlined_whole(self):
        out = summarize_result("short answer", "/tmp/does-not-exist.txt", words=200)
        assert "short answer" in out
        assert "Full transcript:" in out
        assert "/tmp/does-not-exist.txt" in out

    def test_large_result_previews_head_and_tail(self):
        body = " ".join(f"w{i}" for i in range(1000))
        out = summarize_result(body, "/tmp/x.txt", words=20)
        assert "w0" in out  # head preserved
        assert "w999" in out  # tail preserved
        assert "middle truncated" in out
        assert "spawn_status" in out  # steers to on-demand read, not re-run

    def test_closing_segment_rides_the_envelope_whole(self):
        """EVT-2 done-when: a long narrated transcript ending in an answer
        produces an envelope that contains the whole answer, even when the kept
        copy is head-only (the default keep mode) and dropped it."""
        head = "narration about tool calls " * 300  # the head-kept copy
        # No trailing whitespace: the seam strips the segment, and the assert
        # must hold against exactly what the envelope carries.
        answer = ("ANSWER: " + "concrete item " * 130).strip()  # ~2k chars
        out = summarize_result(head, "/tmp/x.txt", words=200, final_segment=answer)
        assert answer in out, "the whole closing answer must reach the parent"
        assert "Closing output (the run's final answer, preserved whole):" in out
        # The head preview still stands beside it.
        assert "Preview (first+last 100 words):" in out

    def test_closing_segment_already_in_the_copy_is_not_duplicated(self):
        """tail/both keep modes retain the closing segment in the copy, so the
        envelope must not emit a second, duplicate section for it."""
        copy = "narration … ANSWER: ship it"
        out = summarize_result(copy, "/tmp/x.txt", words=200, final_segment="ANSWER: ship it")
        assert "Closing output" not in out
        assert out.count("ANSWER: ship it") == 1

    def test_size_annotation_when_file_exists(self, tmp_path):
        p = tmp_path / "result.txt"
        p.write_text("x" * 1234, encoding="utf-8")
        out = summarize_result("preview", str(p), words=200)
        assert "1,234 bytes" in out


class TestSpawnResultView:
    @staticmethod
    def _text(n: int) -> str:
        return "\n".join(f"line{i}" for i in range(n))

    def test_offset_limit_slices_lines(self):
        view, meta = _spawn_result_view(self._text(100), offset=10, limit=5, grep="")
        assert view.splitlines() == [f"line{i}" for i in range(10, 15)]
        assert meta["offset"] == 10
        assert meta["returned_lines"] == 5
        assert meta["total_lines"] == 100
        assert meta["has_more"] is True

    def test_limit_zero_returns_to_end(self):
        view, meta = _spawn_result_view(self._text(50), offset=0, limit=0, grep="")
        assert meta["returned_lines"] == 50
        assert meta["has_more"] is False

    def test_grep_filters_matching_lines_case_insensitive(self):
        text = "alpha\nBETA\ngamma\nbeta again\n"
        view, meta = _spawn_result_view(text, offset=0, limit=0, grep="beta")
        assert view.splitlines() == ["BETA", "beta again"]
        assert meta["matched_lines"] == 2
        assert meta["total_lines"] == 4

    def test_grep_then_offset_limit(self):
        text = "\n".join("hit" if i % 2 == 0 else "miss" for i in range(10))
        view, meta = _spawn_result_view(text, offset=2, limit=2, grep="hit")
        assert view.splitlines() == ["hit", "hit"]  # 5 hits, skip 2, take 2
        assert meta["matched_lines"] == 5
        assert meta["offset"] == 2
        assert meta["returned_lines"] == 2
        assert meta["has_more"] is True

    def test_bad_grep_regex_returns_error(self):
        view, meta = _spawn_result_view("a\nb", offset=0, limit=0, grep="(")
        assert view == ""
        assert "grep_error" in meta

    def test_offset_past_end_returns_empty(self):
        view, meta = _spawn_result_view(self._text(5), offset=100, limit=10, grep="")
        assert view == ""
        assert meta["returned_lines"] == 0
        assert meta["has_more"] is False

    # ── Tail view (the spawn_status no-argument default) ──

    def test_tail_returns_last_lines_with_paging_meta(self):
        view, meta = _spawn_result_view(self._text(100), 0, 0, "", tail=20)
        assert view.splitlines() == [f"line{i}" for i in range(80, 100)]
        assert meta["offset"] == 80
        assert meta["returned_lines"] == 20
        assert meta["total_lines"] == 100
        assert meta["has_more"] is True
        assert meta["tail_view"] is True

    def test_tail_keeps_the_newest_line_under_the_char_budget(self):
        # Alternating huge/small lines: whole lines drop from the front until
        # the view fits 12k chars, but the newest line is always returned.
        text = (
            "\n".join(("x" * 5_000) if i % 2 == 0 else f"line{i}" for i in range(100))
            + "\nthe closing answer"
        )
        view, meta = _spawn_result_view(text, 0, 0, "", tail=200)
        assert view.splitlines()[-1] == "the closing answer"
        assert len(view) <= 12_000
        assert meta["has_more"] is True
        assert meta["tail_view"] is True

    def test_tail_short_text_returns_everything(self):
        view, meta = _spawn_result_view(self._text(5), 0, 0, "", tail=200)
        assert meta["returned_lines"] == 5
        assert meta["offset"] == 0
        assert meta["has_more"] is False

    def test_tail_with_grep_filters_first(self):
        text = "\n".join("hit" if i % 2 == 0 else "miss" for i in range(10))
        view, meta = _spawn_result_view(text, 0, 0, "hit", tail=3)
        assert view.splitlines() == ["hit", "hit", "hit"]
        assert meta["matched_lines"] == 5
        assert meta["returned_lines"] == 3
        assert meta["has_more"] is True

    def test_route_no_params_still_returns_the_full_transcript(self):
        # The dashboard ActivityViewer, the CLI poll and spawn_sub_agents all read
        # GET /api/spawn/{id} with no view params and need the full transcript.
        req = make_mocked_request("GET", "/api/spawn/x")
        text = "\n".join(f"line{i}" for i in range(500))
        view, meta = asyncio.run(_apply_result_view(req, text))
        assert view == text
        assert meta == {}

    def test_route_tail_param_slices_from_the_end(self):
        req = make_mocked_request("GET", "/api/spawn/x?tail=20")
        view, meta = asyncio.run(_apply_result_view(req, self._text(100)))
        assert view.splitlines() == [f"line{i}" for i in range(80, 100)]
        assert meta["tail_view"] is True


class TestSpawnStatusMcpDefault:
    """The no-argument spawn_status requests the bounded tail view, and an
    explicit offset/limit/grep keeps today's exact paging behaviour."""

    @staticmethod
    def _big_transcript() -> str:
        # 400 KB: far past the transport's 100k head-only cut.
        return "\n".join(f"row {i} " + "y" * 400 for i in range(1_000)) + "\nTHE CLOSING ANSWER"

    def test_no_argument_call_requests_the_tail_and_keeps_the_answer(self):
        text = self._big_transcript()
        requested: dict[str, str] = {}

        def fake_get(path: str):
            requested["path"] = path
            assert "tail=200" in path
            view, meta = _spawn_result_view(text, 0, 0, "", tail=200)
            return {"done": True, "result": view, "result_meta": meta}

        with patch.object(spawn_tools.mcp_core, "_get", side_effect=fake_get):
            out = spawn_tools.spawn_status("spawn_status", {"agent_id": "abc123"})
        assert "tail=200" in requested["path"]
        assert "THE CLOSING ANSWER" in out
        assert "earlier lines available" in out
        assert "showing lines" in out
        # The whole tool response (header + bounded tail) stays small.
        assert len(out) <= 12_000 + 400

    def test_explicit_offset_keeps_the_paging_contract(self):
        seen: dict[str, str] = {}

        def fake_get(path: str):
            seen["path"] = path
            view, meta = _spawn_result_view("a\nb\nc", offset=1, limit=1, grep="")
            return {"done": True, "result": view, "result_meta": meta}

        with patch.object(spawn_tools.mcp_core, "_get", side_effect=fake_get):
            out = spawn_tools.spawn_status(
                "spawn_status", {"agent_id": "abc123", "offset": 1, "limit": 1}
            )
        assert "tail=" not in seen["path"]
        assert "offset=1" in seen["path"] and "limit=1" in seen["path"]
        assert "more available — call again with offset=" in out
