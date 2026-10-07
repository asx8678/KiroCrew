"""SPEC-5: pptx-maker agents preload only what every turn needs.

The composer declared 4 file:// resources (compose workflow, review workflow,
slide-json spec, grid guide — 50,710 B on the pinned engine snapshot) while
its tools already include @sdpm/read_workflows, @sdpm/read_guides and
@sdpm/grid. The structural budget: an agent whose tools include the read
tools must not PRELOAD the files they reach, and the composer preloads only
the slide-json spec (24,075 B), the one thing every turn needs.
"""

from __future__ import annotations

import json
from pathlib import Path

_AGENTS = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "kiro_crew"
    / "apps"
    / "builtins"
    / "pptx_maker"
    / "agents"
)


def _spec(name: str) -> dict:
    return json.loads((_AGENTS / f"{name}.json").read_text(encoding="utf-8"))


def test_the_composer_preloads_only_the_slide_json_spec() -> None:
    spec = _spec("pptx-maker-composer")
    assert spec["resources"] == ["file://{ENGINE_ROOT}/skill/references/slide-json-spec.md"]
    # The read tools that cover everything dropped:
    for tool in ("@sdpm/read_workflows", "@sdpm/read_guides", "@sdpm/grid"):
        assert tool in spec["tools"], tool


def test_the_vibe_keeps_its_outline_and_studio_only() -> None:
    """SPEC-5 step 3: the same review for the vibe (19,198 B of preloaded
    workflow docs dropped; read_workflows covers them on demand)."""
    spec = _spec("pptx-maker-vibe")
    assert "@sdpm/read_workflows" in spec["tools"]
    assert spec["resources"] == [
        "file://{ENGINE_ROOT}/skill/references/workflows/create-new-1-outline.md",
        "file://{APP_PROMPTS}/spec-studio.md",
    ]


def test_every_spec_still_parses_and_keeps_its_prompt() -> None:
    for name in ("pptx-maker-composer", "pptx-maker-vibe", "pptx-maker-style", "pptx-maker-spec"):
        spec = _spec(name)
        assert spec["prompt"].startswith("file://")
        assert spec["mcpServers"]["sdpm"]["command"] == "{UV_BIN}"
