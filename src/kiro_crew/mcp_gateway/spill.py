"""Spill-to-file for oversized MCP tool responses.

When a tool/call result exceeds ``response_spill_threshold_bytes`` (by default
the one tool-result budget, ``tool_result_cap.MAX_TOOL_RESULT_CHARS``), the full
frame is written to a sidecar file under ``<config_dir>/mcp_spill/`` and each
text item is cut in the middle -- head and tail kept, the same cut first-party
results get -- with a marker telling the agent where the full content is, until
the whole frame fits the threshold. Non-tool-result frames, errors, and small
responses pass through untouched.

A failed sidecar write (disk full, permission denied, a linked spill dir) still
caps the frame, with a marker saying the full copy was not saved. A frame that
does not parse, or a rewrite that itself fails, is forwarded unmodified.
"""

from __future__ import annotations

import json
import logging
import re
import stat as stat_mod
import time
from pathlib import Path

from kiro_crew import platform_compat
from kiro_crew.config.paths import config_dir
from kiro_crew.json_line import parse_json_object_line
from kiro_crew.tool_result_cap import (
    SPILL_DIR_NAME,
    cut_head_tail,
    spill_filename,
    write_spill_file,
)

logger = logging.getLogger(__name__)

# The smallest per-text-item budget the frame rewrite shrinks to. Below this the
# kept head and tail would be too small to tell the agent whether to read the file.
_MIN_TEXT_BUDGET = 1024

# Spill directory (created mode 0700 on first use) -- shared with the
# first-party tool-result spill in ``validation.build_tool_response``.
_SPILL_DIR_NAME = SPILL_DIR_NAME

# Max age (seconds) for spill files — older ones are cleaned on startup.
_SPILL_MAX_AGE_SECS = 24 * 60 * 60  # 24 hours


def _spill_dir() -> Path:
    """Return the spill directory path (does NOT create it)."""
    return config_dir() / _SPILL_DIR_NAME


def _sanitize_server_name(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)[:64]


def cleanup_old_spill_files() -> int:
    """Delete spill files older than 24h. Returns count deleted. Best-effort.

    Link-hardened: ``is_dir()``/``is_file()``/``stat()`` all FOLLOW
    symlinks, so an agent that swapped the spill dir (or planted a link
    inside it) could otherwise aim this sweep at an arbitrary directory and
    have the gateway delete 24h-old files there (confused deputy). The dir
    itself and every entry are checked with lstat semantics; links are
    skipped, never followed.

    ``is_link_or_junction``, not ``Path.is_symlink()``: a Windows directory
    JUNCTION is a reparse point ``is_symlink()`` answers False for while
    ``is_dir()`` answers True, and it is the only directory link an
    unprivileged Windows writer can plant. An ``is_symlink()`` guard let a
    junction at the spill dir name through, so the sweep followed it and
    deleted 24h-old files under the junction's target.
    """
    spill_dir = _spill_dir()
    if platform_compat.is_link_or_junction(spill_dir) or not spill_dir.is_dir():
        return 0
    now = time.time()
    deleted = 0
    try:
        for entry in spill_dir.iterdir():
            try:
                st = entry.lstat()  # never follows — a symlink reports itself
                if not stat_mod.S_ISREG(st.st_mode):
                    continue  # skip symlinks / dirs / anything non-regular
                if (now - st.st_mtime) > _SPILL_MAX_AGE_SECS:
                    entry.unlink()
                    deleted += 1
            except OSError:
                continue
    except OSError:
        pass
    if deleted:
        logger.info("spill cleanup: removed %d files older than 24h", deleted)
    return deleted


def maybe_spill_response(
    line: bytes,
    server_name: str,
    threshold_bytes: int,
) -> bytes:
    """If ``line`` is an oversized tool/call result, spill it and cut it to fit.

    Returns the (possibly rewritten) line to forward: at most *threshold_bytes*
    for a text result. A threshold of 0 (or less) disables the layer. A failed
    sidecar write still caps; only an unparseable frame or a failed rewrite is
    forwarded unmodified.
    """
    if threshold_bytes <= 0 or len(line) <= threshold_bytes:
        return line

    msg = parse_json_object_line(line)
    if msg is None:
        return line  # not a JSON object — pass through

    # Only spill tool/call results (has result.content list with text items)
    result = msg.get("result")
    if not isinstance(result, dict):
        return line
    content_list = result.get("content")
    if not isinstance(content_list, list):
        return line

    # Check if this looks like an MCP tools/call result
    has_text_items = any(
        isinstance(item, dict) and item.get("type") == "text" and "text" in item
        for item in content_list
    )
    if not has_text_items:
        return line

    msg_id = msg.get("id", "unknown")
    total_bytes = len(line)
    abs_path: str | None = None
    try:
        # Named after the call (server, request id) plus a random token, so two
        # results of one request id can never collide on the O_EXCL create.
        label = f"{_sanitize_server_name(server_name)}-{_sanitize_server_name(str(msg_id))}"
        filename = spill_filename(label, ".json")
        abs_path = write_spill_file(_spill_dir(), filename, line)
    except Exception:
        # The full copy could not be kept (disk full, permissions, a linked
        # spill dir). The frame is STILL cut to the budget -- an over-budget
        # result must not reach the model whole because a sidecar failed -- and
        # the note says the rest was not saved.
        logger.warning(
            "spill: failed to write oversized response for %s; capping without a copy",
            server_name,
            exc_info=True,
        )

    try:
        rewritten_line = _cap_frame(
            msg, result, content_list, threshold_bytes, total_bytes, abs_path
        )
        logger.info(
            "spill: %s response %s capped (%d bytes -> %d inline, full copy %s)",
            server_name,
            msg_id,
            total_bytes,
            len(rewritten_line),
            abs_path or "not saved",
        )
        return rewritten_line

    except Exception:
        # A rewrite failure (not a spill failure) forwards the original line,
        # so the happy path is never broken by this layer's own bug.
        logger.warning(
            "spill: failed to rewrite oversized response for %s; forwarding original",
            server_name,
            exc_info=True,
        )
        return line


def _cap_frame(
    msg: dict,
    result: dict,
    content_list: list,
    threshold_bytes: int,
    total_bytes: int,
    abs_path: str | None,
) -> bytes:
    """The frame re-serialized with each text item cut head+tail to fit the threshold.

    The text budget starts at the threshold and shrinks by the measured overshoot
    until the WHOLE re-serialized line fits (JSON escaping makes a character cost
    up to six bytes, and other items take their share). A frame whose non-text
    items alone exceed the threshold is returned at the floor budget.
    """

    def note(omitted: int) -> str:
        where = (
            f"full {total_bytes}-byte response at {abs_path}. Read with bash: head/grep/jq."
            if abs_path
            else "the full response could not be saved."
        )
        return f"\n\n[KiroCrew: response truncated -- {omitted} chars omitted from the middle; {where}]\n\n"

    budget = threshold_bytes
    while True:
        rewritten_content = []
        for item in content_list:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                rewritten_content.append(
                    {**item, "text": cut_head_tail(item["text"], budget, note)}
                )
            else:
                rewritten_content.append(item)
        rewritten_msg = dict(msg)
        rewritten_msg["result"] = {**result, "content": rewritten_content}
        rewritten_line = json.dumps(rewritten_msg, separators=(",", ":")).encode("utf-8") + b"\n"
        over = len(rewritten_line) - threshold_bytes
        if over <= 0 or budget <= _MIN_TEXT_BUDGET:
            return rewritten_line
        # Shrink in proportion to the overshoot, and always by at least the
        # overshoot itself, so the loop converges in a few passes.
        shrink = max(over, budget * over // max(len(rewritten_line), 1))
        budget = max(_MIN_TEXT_BUDGET, budget - shrink - 64)
