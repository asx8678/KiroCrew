"""OUT-4: the resource_status pre-check is conditional, not unconditional.

On full-context sessions the per-turn [RESOURCES] line already carries the
signal (and is silent when everything is clear), so a routine pre-check is
redundant; only minimal-context sessions — which get prompt.md but no
[RESOURCES] line — need the conditional pre-check before genuinely heavy work.
"""

from __future__ import annotations

from pathlib import Path

import kiro_crew.mcp_tools.spawn as spawn_tools

_PROMPT = (
    Path(__file__).resolve().parent.parent / "src" / "kiro_crew" / "config" / "prompt.md"
).read_text(encoding="utf-8")


def test_prompt_carries_no_unconditional_pre_check() -> None:
    # The old wording: "resource_status`: BEFORE full tests, large builds ..."
    assert "resource_status`: BEFORE" not in _PROMPT
    # The new conditional shape, in the tool roster bullet.
    assert "only when a `[RESOURCES]` line is present" in _PROMPT
    # The orchestration paragraph keeps a conditional check for minimal-context runs.
    assert "in a run with no `[RESOURCES]` line, check `resource_status`" in _PROMPT


def test_tool_description_carries_no_unconditional_pre_check() -> None:
    descriptions = {t["name"]: t["description"] for t in spawn_tools.schemas()}
    desc = descriptions["resource_status"]
    assert "BEFORE starting a heavy step" not in desc
    assert "when a reading is needed" in desc
    assert "[RESOURCES]" in desc
