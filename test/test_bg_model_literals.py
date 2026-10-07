"""MOD-3: no background one-liner may pass a string-literal model.

A literal model="auto" is forwarded to set_model and replaces an operator's
agent.role_models.background pin at the wire. Every background call site either
names no model (inheriting the pinned spec model) or resolves through
resolve_model. This AST gate keeps a new call site from reintroducing the
literal.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "kiro_crew"


def test_no_background_oneliner_passes_a_string_literal_model() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "run_bg_oneliner":
                continue
            for kw in node.keywords:
                if kw.arg == "model" and isinstance(kw.value, ast.Constant):
                    offenders.append(f"{path.relative_to(_SRC.parent)}:{node.lineno}")
    assert not offenders, (
        "run_bg_oneliner call sites must not pass a string-literal model= "
        "(it overrides the operator's role_models pin at the wire): " + ", ".join(offenders)
    )


def test_tips_model_default_is_the_background_role() -> None:
    from kiro_crew.config.loader import KiroCrewConfig

    cfg = KiroCrewConfig()
    assert cfg.dashboard.tips_model == ""
    # Empty resolves to the background role ("auto" when unpinned).
    assert cfg.agent.resolve_model("background")
