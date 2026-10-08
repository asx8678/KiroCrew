"""Per-domain MCP tool descriptors for the ``kirocrew-core`` server.

``build_tool_list`` is what ``mcp_core._list_tools`` answers ``tools/list``
from. Domain modules are imported lazily inside it so this package stays a
leaf at import time: ``mcp_core`` reads ``_limits`` from here at module
level, and an eager import of the domain modules would close that loop.

Adding a tool means adding its descriptor to the domain module's
``schemas()`` and its handler to that module's ``HANDLERS``; nothing here needs
to change.

Two modules in this package are not domains, and nothing here imports them:
``table`` (:class:`~kiro_crew.mcp_tools.table.ToolTable`, the one-row-per-tool
server shape ``kirocrew-dashboard`` is built on) and ``dashboard_client`` (the
``DashboardClient`` port a table's tools reach the gateway through).
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

# Descriptor modules, in the order their tools are advertised.
DOMAIN_MODULES: tuple[str, ...] = (
    "spawn",
    "learn",
    "ledger",
    "skills",
    "logs",
    "control",
    "messaging",
    "artifacts",
    "knowledge",
    "sessions",
    "workflows",
    "apps",
    "browser",
)


#: Tools served by the opt-in ``kirocrew-ops`` server instead of the default
#: ``kirocrew-core`` set (TOOL-2). A default session spends nothing on them; an
#: agent whose spec grants the ``kirocrew-ops`` set gets them. None of these
#: emits a directive, so the gateway's directive path (keyed on ``kirocrew-core``)
#: does not apply to them.
OPS_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # workflows
        "workflow_author",
        "workflow_run",
        "workflow_library_list",
        "workflow_status",
        "workflow_result",
        "workflow_list",
        "workflow_cancel",
        "workflow_rerun_subtree",
        # knowledge
        "local_knowledge_search",
        "knowledge_list_sources",
        "knowledge_add_document",
        "knowledge_dedup",
        # browser and logs
        "browser",
        "kiro_cli_logs",
    }
)


def _advertised_tools(keep: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Descriptors from every domain whose name passes *keep*, concatenated by domain."""
    tools: list[dict[str, Any]] = []
    for name in DOMAIN_MODULES:
        # Imported here, not at module scope. Every domain module imports
        # ``mcp_core``, and ``mcp_core`` imports this package -- so hoisting these
        # to the top would close that loop and turn it into an import-time
        # failure on the gateway boot path. The laziness is load-bearing.
        module = importlib.import_module(f"{__name__}.{name}")
        # A domain may advertise a subset of what it declares: ``apps`` withholds
        # a tool whose app is not installed and enabled (``advertised_schemas``).
        advertise = getattr(module, "advertised_schemas", None) or module.schemas
        tools.extend(t for t in advertise() if keep(t["name"]))
    return tools


def build_tool_list() -> list[dict[str, Any]]:
    """Every ``kirocrew-core`` tool descriptor, concatenated by domain.

    The opt-in set in :data:`OPS_TOOL_NAMES` is withheld here; it is served by
    :func:`build_ops_tool_list`.

    Descriptors are rebuilt per call rather than cached: some carry a live
    value (the concurrent sub-agent cap), and a cache would pin the first
    reading for the life of the server process.
    """
    return _advertised_tools(lambda name: name not in OPS_TOOL_NAMES)


def build_ops_tool_list() -> list[dict[str, Any]]:
    """The ``kirocrew-ops`` tool descriptors: exactly the opt-in set."""
    return _advertised_tools(lambda name: name in OPS_TOOL_NAMES)


def dispatch(name: str, args: dict[str, Any]) -> str:
    """Run the handler for *name*, or report the tool as unknown.

    Domains are searched in the order they are advertised. A name is claimed by
    exactly one domain -- ``test_mcp_tool_registry`` fails on a collision -- so
    the order decides nothing beyond how soon the lookup stops.
    """
    for domain in DOMAIN_MODULES:
        module = importlib.import_module(f"{__name__}.{domain}")
        handler = module.HANDLERS.get(name)
        if handler is not None:
            return handler(name, args)
    return f"Unknown tool: {name}"
