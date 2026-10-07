"""SEC-21: every computer-use action route funnels through tools.dispatch_tool.

The in-band chokepoint AGENTS.md names is ``computer_use/tools.py``'s
``dispatch_tool``: it enforces computer use's own refusals (keystone enable,
KiroCrew-self policy, click_method), and computer use is deliberately NOT
governed elsewhere. Until this pin, only the CLI had a structural test; a new
action route could reach the service or a platform driver directly and ship
without passing the chokepoint. This is a structural AST union over ``src/``
(the shape ``test_sandbox_governance_mask.py`` uses), not a regex over call
text.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "kiro_crew"
_CU = _SRC / "computer_use"

#: Modules that carry the action surface. Importing any of these outside
#: ``computer_use/`` reaches a driver, the FFI or the service directly.
_ACTION_MODULES = frozenset(
    {
        "kiro_crew.computer_use.service",
        "kiro_crew.computer_use.macos_driver",
        "kiro_crew.computer_use.windows_driver",
        "kiro_crew.computer_use.linux_driver",
        "kiro_crew.computer_use.macos_ffi",
        "kiro_crew.computer_use.windows_ffi",
        "kiro_crew.computer_use.macos_skylight",
        "kiro_crew.computer_use.backend",
    }
)

#: The only modules that may construct the service for dispatch.
_SHARED_SERVICE_CALLERS = frozenset({(_CU / "tools.py").resolve(), (_CU / "cli.py").resolve()})

#: ``backend`` carries BOTH the action surface and the read-only probes the
#: dashboard handler and the test fake legitimately use. Importing the probe
#: names is allowed anywhere; anything else from ``backend`` (the action
#: methods) must stay inside ``computer_use/``.
_BACKEND_PROBE_NAMES = frozenset(
    {"get_shared_backend", "platform_id_for_current_os", "ComputerUseBackend"}
)


def _iter_files():
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        yield path.resolve(), tree


def test_no_module_outside_computer_use_reaches_the_action_surface() -> None:
    offenders: list[str] = []
    for path, tree in _iter_files():
        if _CU in path.parents:
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "kiro_crew.computer_use.backend":
                    bad = [a.name for a in node.names if a.name not in _BACKEND_PROBE_NAMES]
                    if bad:
                        offenders.append(
                            f"{path.relative_to(_SRC.parent)}:{node.lineno} "
                            f"imports backend action name(s) {bad}"
                        )
                    continue
                targets = [node.module]
            for target in targets:
                if target in _ACTION_MODULES or (
                    target.startswith("kiro_crew.computer_use.")
                    and target.split(".")[-1] in {m.split(".")[-1] for m in _ACTION_MODULES}
                ):
                    offenders.append(
                        f"{path.relative_to(_SRC.parent)}:{node.lineno} imports {target}"
                    )
    assert not offenders, (
        "computer-use action surface imported outside computer_use/ "
        "(must go through tools.dispatch_tool): " + "; ".join(offenders)
    )


def test_get_shared_service_is_called_only_by_the_chokepoint_and_the_cli() -> None:
    offenders: list[str] = []
    for path, tree in _iter_files():
        if path in _SHARED_SERVICE_CALLERS:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "get_shared_service")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "get_shared_service")
            ):
                offenders.append(f"{path.relative_to(_SRC.parent)}:{node.lineno}")
    assert not offenders, (
        "get_shared_service() called outside tools.py/cli.py "
        "(the in-band chokepoint is dispatch_tool): " + "; ".join(offenders)
    )


def test_the_dashboard_computer_use_handler_dispatches_through_the_chokepoint() -> None:
    """The dashboard/MCP shim's one action entry must call dispatch_tool."""
    handler = _SRC / "dashboard" / "handlers" / "computer_use.py"
    tree = ast.parse(handler.read_text(encoding="utf-8"), filename=str(handler))
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ] + [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "dispatch_tool" in calls, (
        "dashboard/handlers/computer_use.py must route actions through "
        "computer_use.tools.dispatch_tool (the in-band chokepoint)"
    )
