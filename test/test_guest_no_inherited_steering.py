"""The guest agent's view carries no operator steering (SEC-22).

With workspace inheritance at its default, the projection appended the
operator's global steering (``~/.kiro/steering``), workspace ``.kiro/steering``
and ``AGENTS.md`` to EVERY agent view -- including ``kirocrew-guest``, the
tool-less agent a NON-operator channel sender talks to: a trust boundary that
mounts nothing, carrying operator-authored instruction content an admitted
non-operator can ask about. The guest is exempted through a Crew-side set
(``_NO_INHERITED_STEERING_AGENTS``), never a spec key (the spec format denies
unknown fields, and the guest's config is shipped, not user-authored).
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest


def _projection_specs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Materialize the shipped agents, seed steering, and project the views."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    steering = pathlib.Path.home() / ".kiro" / "steering"
    steering.mkdir(parents=True, exist_ok=True)
    (steering / "ops.md").write_text("Operator-only steering. SECRET-CALLSIGN-42")

    from kiro_crew import agent as agent_mod
    from kiro_crew.agent_materialization import service_agents

    agent_mod.kiro_agents_dir_path().mkdir(parents=True, exist_ok=True)
    service_agents._install_guest_agent()
    service_agents._install_knowledge_agent()

    import kiro_crew.acp.skill_projection as sp

    ws = tmp_path / "ws"
    ws.mkdir()
    out = sp.prepare_native_skill_projection(work_dir=ws)
    return out.specs


class TestGuestViewCarriesNoSteering:
    def test_the_guest_view_has_no_inherited_steering_or_agents_md(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        specs = _projection_specs(tmp_path, monkeypatch)
        assert "kirocrew-guest" in specs
        resources = list(specs["kirocrew-guest"].get("resources", []))
        assert not any("steering" in str(r) or "AGENTS.md" in str(r) for r in resources), resources

    def test_other_agents_keep_the_operators_inheritance(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        specs = _projection_specs(tmp_path, monkeypatch)
        others = [n for n in specs if n != "kirocrew-guest"]
        assert others, "expected at least one other materialized agent view"
        assert any(
            "steering" in str(r) or "AGENTS.md" in str(r)
            for n in others
            for r in specs[n].get("resources", [])
        ), "the exemption widened past the guest"

    def test_the_exemption_set_names_only_the_shipped_guest(self) -> None:
        from kiro_crew.acp.skill_projection import _NO_INHERITED_STEERING_AGENTS

        assert _NO_INHERITED_STEERING_AGENTS == frozenset({"kirocrew-guest"})
