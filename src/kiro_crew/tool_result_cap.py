"""The one inline budget for a tool result, and the head+tail cut that keeps to it.

Every tool result Kiro Crew can reach -- each first-party stdio MCP server's
frame (``validation.build_tool_response``), the auto-improvement app's own
server, and a stubbed third-party server's frame in the MCP broker
(``mcp_gateway.spill``) -- is held to :data:`MAX_TOOL_RESULT_CHARS`. Over it, the
text is cut in the MIDDLE: the head and the tail are kept, and a note between
them says how much was omitted and, when the full text could be saved, the path
of a spill file that holds all of it. The tail is kept because tail-anchored
payloads live there: a session directive's marker is the last line of its
result, and a log or build output's newest lines are its end.

A leaf (it imports only ``platform_compat``), so the broker and the validation
layer can both import it without pulling in each other. The spill WRITE lives in
:func:`write_spill_file` here too, so both callers share one hardened writer;
the directory comes from the caller (``config_dir()`` is resolved there), never
from module state, which keeps the MCP servers stateless.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from kiro_crew import platform_compat

#: The inline budget for one tool result: 48 KiB, measured in CHARACTERS of the
#: text the model receives. The broker compares a frame's UTF-8 byte length
#: against the same number, which is the stricter reading (a character is at
#: least one byte), so neither path can deliver more than this.
MAX_TOOL_RESULT_CHARS = 48 * 1024

#: Spill directory name under the data home (created owner-only on first use and
#: swept of files older than 24h by ``mcp_gateway.spill.cleanup_old_spill_files``).
SPILL_DIR_NAME = "mcp_spill"

#: Share of the budget the TAIL gets. A third of 48 KiB is ~16 KiB, which holds a
#: whole session-directive marker line (at most ``MAX_TOOL_RESULT_CHARS`` of
#: ``session_directive``, 8,000 chars) several times over.
_TAIL_SHARE = 3

_UNSAFE_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_\-]")


def tool_json(obj: Any, *, sort_keys: bool = False) -> str:
    """*obj* as the compact UTF-8 JSON a model-facing tool result carries.

    The ONE serializer for JSON a tool hands the model: no indentation (it is
    whitespace the model pays for in tokens and reads nothing from) and
    ``ensure_ascii=False`` (a ``\\uXXXX`` escape costs six characters, and
    several tokens, for one character of CJK or emoji). A tool that bounds its
    result measures THIS string, so the size it budgets is the size it sends.

    Not for files on disk or wire frames: those keep their own encoders. Hidden
    characters (bidi controls, zero-width marks) that ``ensure_ascii=False`` now
    leaves literal are stripped at egress by ``validation.build_tool_response``,
    which every Kiro Crew MCP server frames its results through.
    """
    return json.dumps(
        obj, separators=(",", ":"), ensure_ascii=False, default=str, sort_keys=sort_keys
    )


def safe_name_part(value: object, limit: int = 64) -> str:
    """*value* reduced to filename-safe characters, at most *limit* long."""
    return _UNSAFE_NAME_CHARS.sub("_", str(value))[:limit]


def truncation_note(total: int, omitted: int, spill_path: str | None) -> str:
    """The line placed between the kept head and tail."""
    where = (
        f"the full text is saved at {spill_path} -- read it with your file tools "
        "(head, tail, grep, sed) or a narrower call"
        if spill_path
        else "the full text was not saved; narrow the call to see the rest"
    )
    return (
        f"\n…[response truncated: {omitted} of {total} chars omitted from the middle; "
        f"{where}]\n"
    )


def cut_head_tail(
    text: str,
    max_chars: int,
    note: Callable[[int], str],
) -> str:
    """*text* cut to at most *max_chars*, keeping its head and its tail.

    *note* renders the separator from the omitted-character count. Each cut is
    moved to a line boundary when one is near (so neither side starts or ends in
    the middle of a line), and only ever INWARD, so the budget holds. A budget too
    small to carry the note degrades to a plain head slice.
    """
    if len(text) <= max_chars:
        return text
    # Size the note for the widest count it can carry, so the real one fits.
    budget = max_chars - len(note(len(text)))
    if budget <= 0:
        return text[:max_chars]
    tail_len = budget // _TAIL_SHARE
    head_len = budget - tail_len
    head_end = head_len
    nl = text.rfind("\n", head_len - head_len // 4, head_len)
    if nl > 0:
        head_end = nl
    tail_start = len(text) - tail_len
    nl = text.find("\n", tail_start, tail_start + tail_len // 2)
    if nl >= 0:
        tail_start = nl + 1
    omitted = tail_start - head_end
    return text[:head_end] + note(omitted) + text[tail_start:]


def write_spill_file(directory: Path, filename: str, data: bytes) -> str:
    """Write *data* to a NEW owner-only file in *directory*; return its absolute path.

    Raises ``OSError`` (or ``ValueError`` for a linked directory) on any failure;
    the caller decides how to degrade. Never operates through a linked spill dir
    (agent-swappable): a planted link would redirect the write outside the data
    home. ``is_link_or_junction``, not ``Path.is_symlink()``, because a Windows
    directory JUNCTION answers False to the latter. The file is opened
    ``O_EXCL`` (refusing any pre-existing entry, a planted symlink included) and
    ``O_NOFOLLOW`` where the platform has it.
    """
    if platform_compat.is_link_or_junction(directory):
        raise ValueError("spill dir is a link")
    # Not a bare mkdir(mode=0o700): that is umask-masked, is ignored for an
    # already-existing directory, and is inert on Windows -- yet this directory
    # holds spilled tool responses, which are exactly the payloads that may
    # carry secrets.
    platform_compat.make_owner_only_dir(directory)
    path = directory / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)
    return str(path.resolve())


def spill_filename(label: str, suffix: str = ".txt") -> str:
    """A fresh spill filename for one call: its label plus a random token.

    Random rather than a timestamp or a counter: no clock and no module state, and
    two results of one call label can never collide on ``O_EXCL``.
    """
    return f"{safe_name_part(label or 'tool')}-{uuid.uuid4().hex}{suffix}"
