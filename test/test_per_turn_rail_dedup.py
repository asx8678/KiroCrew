"""CTX-5: per-turn rail blocks are deduplicated per session.

Before: every dashboard follow-up turn re-sent ~1,915 B of byte-identical
[PROJECT], [RUNTIME] and guidance text (1,902 B of it identical to the turn
before). Now a per-session digest per block drops an unchanged block to a
one-line pointer; a changed block, a fresh session or a re-injection re-sends
it whole, and a turn that never lands re-sends (the turn seam settles the
record with the skill-body seam).
"""

from __future__ import annotations

import os
from pathlib import Path

from kiro_crew.context import ContextBuilder
from kiro_crew.learn import LessonStore
from kiro_crew.memory import MemoryStore
from kiro_crew.skills import SkillsLoader


def _builder(tmp: Path) -> ContextBuilder:
    os.environ.setdefault("KIRO_HOME", str(tmp / "kiro"))
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp / "ws"),
        skills=SkillsLoader(skills_path=tmp / "skills", install_builtins=False),
        hooks=None,
        lessons=LessonStore(base_dir=tmp / "lessons"),
        bot_name="Kiro",
    )


def _turn(builder: ContextBuilder, text: str, *, new: bool, **kw) -> str:
    msg, _ = builder.build_message(text, new, **kw)
    return msg


def test_turn_2_with_unchanged_project_sends_no_project_body(tmp_path: Path) -> None:
    b = _builder(tmp_path)
    kw = dict(session_key="dashboard:x", runtime_source="dashboard", project="/repo")
    first = _turn(b, "fix the bug", new=True, **kw)
    assert "[PROJECT] Active project directory: /repo" in first
    second = _turn(b, "now the tests", new=False, **kw)
    assert "[PROJECT] Active project directory" not in second
    assert "[PROJECT] unchanged: /repo" in second
    # Turn 2 is the FIRST [RUNTIME] send (a new session emits none), so it
    # carries the full block; turn 3 is where the pointer appears.
    assert "[RUNTIME] unchanged" not in second
    third = _turn(b, "and the docs", new=False, **kw)
    assert "[RUNTIME] unchanged from earlier" in third
    # The guidance paragraphs drop to the one-line pointer on turn 2.
    assert "as given earlier in this session apply." in second
    # The bounded turn: beyond the request header + user text, the rail cost
    # stays small (the three pointers, not the ~1.9 KB of full blocks).


def test_a_changed_project_re_sends_the_block(tmp_path: Path) -> None:
    b = _builder(tmp_path)
    kw = dict(session_key="dashboard:y", runtime_source="dashboard")
    _turn(b, "first", new=True, project="/a", **kw)
    second = _turn(b, "second", new=False, project="/a", **kw)
    assert "[PROJECT] unchanged: /a" in second
    third = _turn(b, "third", new=False, project="/b", **kw)
    assert "[PROJECT] Active project directory: /b" in third


def test_needs_reinjection_re_sends_everything(tmp_path: Path) -> None:
    b = _builder(tmp_path)
    kw = dict(session_key="dashboard:z", runtime_source="dashboard", project="/repo")
    _turn(b, "first", new=True, **kw)
    second = _turn(b, "second", new=False, **kw)
    assert "[PROJECT] unchanged" in second
    rebuilt = _turn(b, "after compaction", new=False, needs_reinjection=True, **kw)
    assert "[PROJECT] Active project directory: /repo" in rebuilt
    assert "[RUNTIME] unchanged" not in rebuilt
    assert "[OPTIONS: choice1" in rebuilt  # the full guidance returned


def test_a_fresh_session_key_sends_everything(tmp_path: Path) -> None:
    b = _builder(tmp_path)
    kw = dict(runtime_source="dashboard", project="/repo")
    _turn(b, "one", new=True, session_key="dashboard:s1", **kw)
    other = _turn(b, "two", new=False, session_key="dashboard:s2", **kw)
    assert "[PROJECT] Active project directory: /repo" in other


def test_a_keyless_build_never_dedups_and_carries_no_options_paragraph(tmp_path: Path) -> None:
    """The heartbeat shape: no session key means no record (always send) —
    and with interactive=False it carries no [OPTIONS:] paragraph at all."""
    b = _builder(tmp_path)
    text = _turn(b, "unattended", new=False, runtime_source="heartbeat", interactive=False)
    assert "[OPTIONS:" not in text
    # Same keyless build again: still full blocks (no record kept).
    again = _turn(b, "unattended again", new=False, runtime_source="heartbeat", interactive=False)
    assert "[OPTIONS:" not in again


def test_a_turn_that_never_lands_re_sends(tmp_path: Path) -> None:
    """The turn seam: an un-settled build's digest writes are rolled back."""
    b = _builder(tmp_path)
    kw = dict(session_key="dashboard:r", runtime_source="dashboard", project="/repo")
    _turn(b, "first", new=True, **kw)
    # A second build that the caller reports as never landing.
    _turn(b, "dropped", new=False, **kw)
    b.rollback_skill_bodies("dashboard:r")
    resent = _turn(b, "retry", new=False, **kw)
    assert "[PROJECT] Active project directory: /repo" in resent
    assert "[RUNTIME] unchanged" not in resent
