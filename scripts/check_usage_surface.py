#!/usr/bin/env python3
"""Every ``stream_and_collect(`` call site must write its usage row (USE-1).

``stream_and_collect`` writes one usage row only when it is given a
``usage_surface``. A call that passes none writes nothing, so its spend is
invisible on the usage page. This gate walks ``src/kiro_crew`` with ``ast`` and
fails when a call site is none of:

* labelled: it passes a ``usage_surface=`` value that is not the empty string;
* inside ``async with background_turn(...)`` or ``metered_turn(...)``, which
  write the row themselves on every exit;
* listed in ``ALLOWED`` below, with the reason its row is written elsewhere.

Run: ``python3 scripts/check_usage_surface.py``. Exit 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "kiro_crew"

# The call names that drive a model turn and take ``usage_surface``.
TARGETS = frozenset({"stream_and_collect", "_facade_stream_and_collect"})

# Context managers that write the usage row themselves on every exit.
SELF_METERED = frozenset({"background_turn", "metered_turn"})

# (path relative to src/kiro_crew, enclosing function) -> why the row is written
# elsewhere. Keep this list short: a new entry needs a real reason.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "slack/gateway_runtime/cron_dispatch.py",
        "_cron_stream_with_posttoken_resume",
    ): "passes **stream_kwargs; the gateway cron callback writes the row (USE-1)",
    (
        "slack/gateway.py",
        "_heartbeat_task",
    ): "the heartbeat writes its own row from provider_last_turn_usage (USE-1)",
    (
        "slack/gateway.py",
        "_fire_slack_nudge",
    ): "the autonudge/monitor turn writes its own row from provider_last_turn_usage (USE-1)",
    (
        "history_consolidation.py",
        "_facade_stream_and_collect",
    ): "forwarding wrapper; every caller of it is checked as a target",
}


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _has_label(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "usage_surface":
            value = kw.value
            return not (isinstance(value, ast.Constant) and value.value == "")
    return False


def _inside_self_metered(stack: list[ast.AST]) -> bool:
    for node in stack:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call) and _callee_name(expr) in SELF_METERED:
                    return True
    return False


def _enclosing_function(stack: list[ast.AST]) -> str:
    for node in reversed(stack):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(SOURCE).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    stack: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Call) and _callee_name(node) in TARGETS:
            func = _enclosing_function(stack)
            if not (_has_label(node) or _inside_self_metered(stack) or (rel, func) in ALLOWED):
                problems.append(f"src/kiro_crew/{rel}:{node.lineno} in {func}()")
        stack.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)
        stack.pop()

    visit(tree)
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        problems.extend(check_file(path))
    if problems:
        print("stream_and_collect call sites without a usage row (USE-1):")
        for line in problems:
            print(f"  {line}")
        print(
            "Pass usage_surface=, call inside background_turn/metered_turn, or add an ALLOWED entry."
        )
        return 1
    print("usage rows: every stream_and_collect call site is labelled or self-metered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
