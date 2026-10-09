#!/usr/bin/env python3
"""Every model turn must write its usage row (USE-1).

Two checks, one walk of ``src/kiro_crew`` with ``ast``:

1. ``stream_and_collect(`` call sites. ``stream_and_collect`` writes one usage
   row only when it is given a ``usage_surface``; a call that passes none writes
   nothing, so its spend is invisible on the usage page.
2. Direct drives: a ``.stream(`` / ``.prompt(`` call on a model handle (a
   receiver named ``client``, ``provider``, ``session``, ``handle``, …) outside
   the provider implementations themselves. A path that drives the provider by
   hand writes no row unless it says so, which is how a whole app's spend once
   stayed off the usage store with this gate green.

Either kind of site passes when it is:

* labelled: it passes a ``usage_surface=`` value that is not the empty string
  (check 1 only);
* inside ``async with background_turn(...)`` or ``metered_turn(...)``, which
  write the row themselves on every exit;
* listed in ``ALLOWED`` / ``DRIVE_ALLOWED`` below, with the reason its row is
  written elsewhere -- on EVERY exit, not only after a successful stream.

Run: ``python3 scripts/check_usage_surface.py``. Exit 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "kiro_crew"

# The call names that drive a model turn and take ``usage_surface``. The JSON
# variant forwards the label, so an unlabelled call to it writes nothing either.
TARGETS = frozenset(
    {
        "stream_and_collect",
        "_facade_stream_and_collect",
        "stream_and_collect_json",
        "_facade_stream_and_collect_json",
    }
)

# Context managers that write the usage row themselves on every exit.
SELF_METERED = frozenset({"background_turn", "metered_turn"})

# (path relative to src/kiro_crew, enclosing function) -> why the row is written
# elsewhere. Keep this list short: a new entry needs a real reason.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "slack/gateway_runtime/cron_dispatch.py",
        "_cron_stream_with_posttoken_resume",
    ): "passes **stream_kwargs; the cron callback writes the row on every exit after "
    "dispatch (_record_cron_turn_row, USE-1)",
    (
        "slack/gateway.py",
        "_heartbeat_task",
    ): "the heartbeat writes its own row via _persist_turn_row on every exit after "
    "dispatch (USE-1)",
    (
        "slack/gateway.py",
        "_fire_slack_nudge",
    ): "the autonudge/monitor turn writes its own row via _persist_turn_row on every "
    "exit after dispatch (USE-1)",
    (
        "history_consolidation.py",
        "_facade_stream_and_collect",
    ): "forwarding wrapper; every caller of it is checked as a target",
    (
        "history_consolidation.py",
        "_facade_stream_and_collect_json",
    ): "forwarding wrapper; every caller of it is checked as a target",
    (
        "history_consolidation.py",
        "_call_llm",
    ): "background_turn writes the row; it is entered through an AsyncExitStack this walk cannot see",
    (
        "llm_helpers.py",
        "stream_and_collect_json",
    ): "forwards usage_surface to stream_and_collect; its callers are checked as targets",
}


# Check 2: the methods that drive a model turn, and the receiver names a model
# handle goes by. Matching on the receiver keeps unrelated ``.stream`` /
# ``.prompt`` calls (an SSE response, a TTS runtime, a CLI prompt) out of scope.
DRIVE_METHODS = frozenset({"stream", "prompt"})
DRIVE_RECEIVERS = frozenset({"client", "provider", "_provider", "session", "handle", "_client"})

# The provider implementations: their ``.stream`` forwards a turn some caller
# above them meters, so a drive inside them is not a turn of its own.
DRIVE_EXEMPT_PREFIXES = ("acp/", "providers/")

# (path relative to src/kiro_crew, enclosing function) -> why the row is written
# elsewhere on every exit. Same bar as ALLOWED: a new entry needs a real reason.
DRIVE_ALLOWED: dict[tuple[str, str], str] = {
    (
        "llm_helpers.py",
        "stream_and_collect",
    ): "the metering helper itself: usage_surface rows, else the published total",
    (
        "llm_helpers.py",
        "_drive",
    ): "run_bg_oneliner's finally writes the row (bg:<sel_source>)",
    (
        "session_background.py",
        "prompt",
    ): "the background-session wrapper run_bg_oneliner drives; its finally writes the row",
    (
        "dashboard/chat_runner.py",
        "_run_chat",
    ): "writes its row at EVENT_COMPLETE and from the abnormal-end seam on every other exit",
    (
        "dashboard/handlers/hooks.py",
        "_run_hook_inner",
    ): "_persist_hook_usage in the stream's finally (webhook:<hook>)",
    (
        "task_executor.py",
        "execute_task",
    ): "_persist_step_usage in the stream's finally (taskrunner:<run>)",
    (
        "subagent_manager/run.py",
        "_stream_with_transient_retry",
    ): "the run total is written once per attempt from SubagentManager._run_inner's finally",
    (
        "apps/builtins/auto_improvement/spine/agent_runner.py",
        "_run_async",
    ): "record_turn_usage in the run's finally (auto_improvement:<agent>)",
    (
        "apps/builtins/code_review_sage/sage_lib/review_pool.py",
        "send",
    ): "record_turn_usage in the send's finally (code_review_sage)",
    (
        "messaging/driver.py",
        "_drive",
    ): "TurnDriver.run wraps the drive in metered_turn when given usage_surface",
    (
        "messaging/auto_title.py",
        "_stream_title",
    ): "called inside background_turn (entered through an AsyncExitStack)",
    (
        "eval/judge.py",
        "_drive_judge",
    ): "called inside metered_turn(surface='eval_judge') by judge_turn",
    (
        "eval/runner.py",
        "_drive_turn",
    ): "called inside metered_turn(surface='eval') by the scenario loop",
    (
        "apps/builtins/aws_control/crew/runtime/container/front/backend.py",
        "generator",
    ): "an httpx streaming proxy to the crew container, not a model handle",
}


def _receiver_name(node: ast.Call) -> str:
    """The last name of a ``x.y.method(...)`` call's receiver (``y``), else ``""``."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return ""
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return ""


def _is_test_module(path: Path) -> bool:
    return path.name.startswith("test_") or any(
        part == "tests" or part.endswith("_tests") for part in path.parts
    )


def _is_drive(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in DRIVE_METHODS
        and _receiver_name(node) in DRIVE_RECEIVERS
    )


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

    # Test modules shipped beside an app drive doubles, not model turns.
    drive_exempt = rel.startswith(DRIVE_EXEMPT_PREFIXES) or _is_test_module(path)

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Call) and _callee_name(node) in TARGETS:
            func = _enclosing_function(stack)
            if not (_has_label(node) or _inside_self_metered(stack) or (rel, func) in ALLOWED):
                problems.append(f"src/kiro_crew/{rel}:{node.lineno} in {func}()")
        elif isinstance(node, ast.Call) and not drive_exempt and _is_drive(node):
            func = _enclosing_function(stack)
            if not (_inside_self_metered(stack) or (rel, func) in DRIVE_ALLOWED):
                problems.append(f"src/kiro_crew/{rel}:{node.lineno} in {func}() [direct drive]")
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
        print("model turns without a usage row (USE-1):")
        for line in problems:
            print(f"  {line}")
        print(
            "Pass usage_surface=, call inside background_turn/metered_turn, or add an "
            "ALLOWED / DRIVE_ALLOWED entry naming where the row is written on every exit."
        )
        return 1
    print("usage rows: every model turn call site is labelled, self-metered or allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
