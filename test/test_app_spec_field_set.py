"""SPEC-7: materialized app specs use only fields kiro-cli accepts.

A template `skills` array is not a spec field — kiro-cli validates with
deny_unknown_fields, so a spec carrying it can be refused whole and the agent
silently falls back to the DEFAULT agent. The bridge maps skills to
`skill://` resource entries and drops the key before writing.
"""

from __future__ import annotations

import json
from pathlib import Path

from kiro_crew.apps.bridges import _register_agents

#: The accepted kiro-cli field set — the inventory in agent-spec-fields.md.
_ACCEPTED = {
    "name",
    "model",
    "description",
    "prompt",
    "tools",
    "allowedTools",
    "mcpServers",
    "resources",
    "permissions",
    "includeMcpJson",
    "welcomeMessage",
    "toolsSettings",
    "managedToolPolicy",
    "$schema",
}


def _materialize(tmp_path: Path, spec: dict) -> dict:
    """Run the real _register_agents over one template and read the spec back."""
    app_root = tmp_path / "app"
    agents_dir = app_root / "agents"
    agents_dir.mkdir(parents=True)
    tpl = agents_dir / "probe.json"
    tpl.write_text(json.dumps(spec), encoding="utf-8")

    class _Manifest:
        agents = ["agents/probe.json"]

    import kiro_crew.apps.bridges as bridges

    home = tmp_path / "home"
    home.mkdir()
    real_agents_dir = bridges._kiro_agents_dir
    bridges._kiro_agents_dir = lambda: home / ".kiro" / "agents"
    try:
        _register_agents("probe-app", _Manifest(), app_root)  # type: ignore[arg-type]
        written = sorted((home / ".kiro" / "agents").glob("*.json"))
        assert len(written) == 1, written
        return json.loads(written[0].read_text(encoding="utf-8"))
    finally:
        bridges._kiro_agents_dir = real_agents_dir


def test_a_template_skills_array_is_mapped_and_dropped(tmp_path: Path) -> None:
    out = _materialize(
        tmp_path,
        {
            "name": "probe",
            "model": "auto",
            "prompt": "do things",
            "skills": ["ai-discover", "metric-design"],
        },
    )
    assert "skills" not in out, "the unaccepted key must not reach the spec"
    assert out["resources"] == ["skill://ai-discover", "skill://metric-design"]
    # agent_discovery reads skills from resources — the mapping is lossless.
    from kiro_crew.agent_discovery import _extract_skills

    assert _extract_skills(out) == ["ai-discover", "metric-design"]


def test_every_materialized_key_is_accepted(tmp_path: Path) -> None:
    out = _materialize(
        tmp_path,
        {
            "name": "probe2",
            "model": "auto",
            "description": "d",
            "prompt": "p",
            "tools": [],
            "skills": ["s1"],
            "welcomeMessage": "hi",
        },
    )
    assert set(out) <= _ACCEPTED, sorted(set(out) - _ACCEPTED)


def test_a_template_without_skills_writes_no_resources(tmp_path: Path) -> None:
    out = _materialize(tmp_path, {"name": "probe3", "model": "auto", "prompt": "p"})
    assert "resources" not in out
