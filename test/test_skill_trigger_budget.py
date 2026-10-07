"""SKL-6: triggered skill bodies are capped per body and per turn, and a
confined project skill's repeat match sends a name-only pointer instead of
re-pasting its whole body.

Before: the trigger path loaded a matched GLOBAL body with no byte cap (a
153,062 B body injected whole), there was no per-turn total, and confined
skills were excluded from the per-session dedup, so they re-injected on
every matching turn (measured 19,442 B on each of 3 consecutive matches).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.context import ContextBuilder
from kiro_crew.learn import LessonStore
from kiro_crew.memory import MemoryStore
from kiro_crew.skills import SkillsLoader


def _seeds(skills_dir: Path) -> None:
    os.environ.setdefault("KIRO_HOME", tempfile.mkdtemp(prefix="kc-skl6-"))


def _builder(skills_dir: Path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=skills_dir / "ws"),
        skills=SkillsLoader(skills_path=skills_dir, install_builtins=False),
        hooks=None,
        lessons=LessonStore(base_dir=skills_dir / "lessons"),
        bot_name="Kiro",
    )


def _seed_skill(skills_dir: Path, name: str, body: str, triggers: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} test skill\n"
        f"triggers: {triggers}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _enable_triggers(monkeypatch, cap: int = 5) -> None:
    cfg = KiroCrewConfig()
    cfg.skills.max_triggered = cap
    monkeypatch.setattr(KiroCrewConfig, "load", lambda *a, **k: cfg)


def test_an_oversized_global_body_yields_a_pointer_not_150k(tmp_path, monkeypatch):
    _enable_triggers(monkeypatch)
    _seed_skill(tmp_path, "huge", "x" * 150_000, "alpha, bravo")
    builder = _builder(tmp_path)
    text, _ = builder.build_message("run alpha bravo", True, session_key="dashboard:s1")
    assert "x" * 1_000 not in text
    assert "huge" in text  # the pointer names it


def test_bodies_past_the_per_turn_total_arrive_as_pointers(tmp_path, monkeypatch):
    _enable_triggers(monkeypatch)
    # Three ~40k bodies: two fit the 99k budget, the third is a pointer.
    for i in range(3):
        _seed_skill(tmp_path, f"bulk{i}", f"body{i} " + "y" * 40_000, f"alpha{i}, bravo")
    builder = _builder(tmp_path)
    text, _ = builder.build_message("bravo alpha0 alpha1 alpha2", True, session_key="dashboard:s2")
    assert text.count("[Skill: bulk0]") + text.count("[Skill: bulk1]") >= 1
    assert "y" * 40_000 in text  # at least one body delivered
    assert "bulk2" in text


def test_a_confined_repeat_match_sends_a_name_only_pointer(tmp_path, monkeypatch):
    """A trusted project's confined skill, matched on two consecutive turns of
    one session: body once, then a name-only pointer; a needs_reinjection turn
    re-sends the body."""
    _enable_triggers(monkeypatch)
    project = tmp_path / "proj"
    (project / ".kiro" / "skills" / "conf").mkdir(parents=True)
    (project / ".kiro" / "skills" / "conf" / "SKILL.md").write_text(
        "---\nname: conf\ndescription: confined test skill\ntriggers: gamma\n---\n\n"
        "CONFined BODY text.\n",
        encoding="utf-8",
    )
    builder = _builder(tmp_path)
    from kiro_crew import skill_trust

    if skill_trust.project_skill_traversal_supported():
        skill_trust.grant_project_trust(project)
    else:  # pragma: no cover — the confined reader still runs on real bytes
        builder_skills = builder.skills
        root = str(project)
        builder_skills._trusted_project_key = lambda project_dir: (
            root if project_dir is not None and Path(project_dir) == project else ""
        )
    first, _ = builder.build_message(
        "do gamma", True, session_key="dashboard:s3", project=str(project)
    )
    assert "CONFined BODY text." in first
    second, _ = builder.build_message(
        "more gamma", False, session_key="dashboard:s3", project=str(project)
    )
    assert "CONFined BODY text." not in second
    assert "[Skill: conf]" in second  # the name-only pointer
    third, _ = builder.build_message(
        "after compaction gamma",
        False,
        session_key="dashboard:s3",
        project=str(project),
        needs_reinjection=True,
    )
    assert "CONFined BODY text." in third


def test_an_unconfined_repeat_match_demotes_via_trigger_hint(tmp_path, monkeypatch):
    _enable_triggers(monkeypatch)
    _seed_skill(tmp_path, "repeat", "REPEAT BODY.", "delta")
    builder = _builder(tmp_path)
    first, _ = builder.build_message("do delta", True, session_key="dashboard:s4")
    assert "REPEAT BODY." in first
    second, _ = builder.build_message("more delta", False, session_key="dashboard:s4")
    assert "REPEAT BODY." not in second
    assert "repeat" in second
