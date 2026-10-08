"""MCP server ``kirocrew-ops`` -- the opt-in workflow, knowledge, browser and log tools.

TOOL-2 moved these out of the default ``kirocrew-core`` list so a default session
no longer spends context on schemas it rarely uses. The server is ``opt_in`` in
``agent._MANAGED_MCP_SERVERS``: a default agent's spec carries neither the entry
nor an ``@kirocrew-ops`` reference, and only an agent whose spec grants the set
reaches these tools.

Same call path as ``kirocrew-core``
-----------------------------------
Calls go through :func:`kiro_crew.mcp_core._call_tool`, the wrapper core uses, so
argument validation, the channel-agent deny, the call-in-flight record and the
audit are the same for both servers. Only the advertised list differs
(:func:`kiro_crew.mcp_tools.build_ops_tool_list`). Reusing the wrapper also means
the audit labels these calls ``kirocrew-core`` as ``downstream_service``: that is
the existing label for the whole shared call path, and changing it here would
split one path's attribution in two.
"""

from __future__ import annotations

from typing import Any

from kiro_crew import mcp_core
from kiro_crew.mcp_core import _call_tool
from kiro_crew.mcp_shared import run_mcp_stdio_loop
from kiro_crew.mcp_tools import OPS_TOOL_NAMES, build_ops_tool_list

SERVER_NAME = "kirocrew-ops"
SERVER_VERSION = "1.0.0"


def _list_tools() -> list[dict[str, Any]]:
    """The opt-in tool surface. Reaching this process means the set was granted."""
    return build_ops_tool_list()


def run_mcp_server() -> None:
    """Run the MCP stdio server -- reads JSON-RPC from stdin, writes to stdout."""
    # Before the loop, so no call is served while this process still answers as
    # the default core: only the opt-in set is runnable here (see mcp_core).
    mcp_core._SERVED_TOOL_NAMES = OPS_TOOL_NAMES
    run_mcp_stdio_loop(
        SERVER_NAME,
        SERVER_VERSION,
        _list_tools,
        _call_tool,
        # Same identity contract as kirocrew-core, which this server's calls
        # share: it consumes the per-call ``kirocrew.caller`` identity and may be
        # pooled. ``mcp_discovery._MANAGED_SERVERS_CALLER_AWARE`` must list it.
        advertise_caller_identity=True,
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    run_mcp_server()
