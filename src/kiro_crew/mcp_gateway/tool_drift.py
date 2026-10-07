"""Operator-recorded tool-definition digests for stubbed MCP servers.

The launch approval (:mod:`kiro_crew.mcp_gateway.launch_approval`) binds the
operator's decision to the CONTENT of the launch -- the resolved command and
the declared env. A stubbed server can still change WHAT ITS TOOLS SAY without
touching either: a ``notifications/tools/list_changed`` re-list, or a
package-floating launch (``npx pkg@latest``) whose argv hash never changes
while the code does. New description or inputSchema text then reaches the
model under the old, name-based trust. This leaf pins that half the same way
the launch approval pins its own: by content, recorded in a gateway-written,
read-only-in-sandbox leaf beside the approvals.

For every stubbed server it records one sha256 digest per tool, taken over the
canonical JSON of the tool's ``name``, ``description`` and ``inputSchema`` as
the server declared them. The first complete listing the gateway sees becomes
the baseline (the operator's enabling of the server is what put the gateway in
the observation seat); every later complete listing is compared against it:

* a tool whose digest changed is reported once -- served, not withheld,
  because blocking a working server on a benign update is an owner decision
  this detection deliberately does not take -- and the baseline adopts the new
  digest, so one drift is surfaced once and a reverted change is surfaced
  again;
* a tool the listing added is recorded; a tool it dropped leaves the baseline;
* a paginated or partial listing never stands for the tool set (the same rule
  the tool-surface projection applies) and never reaches this module.

Only servers routed through this gateway are pinnable: kiro-cli talks to
non-stubbed servers directly and Crew has no observation point there.

Standard library only, for the same import-weight reason as
:mod:`kiro_crew.mcp_gateway.hashing`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from kiro_crew.config.paths import config_dir
from kiro_crew.mcp_gateway.launch_approval import APPROVALS_DIR

logger = logging.getLogger(__name__)

#: The drift-baseline leaf, a sibling of the launch ``approvals.json`` in the
#: same protected directory: the file-tool gate denies writes under
#: ``mcp-launch-approvals/`` and the OS sandbox mounts the precreated directory
#: read-only, so an agent cannot edit what its tools were approved to say.
TOOL_DIGESTS_LEAF = f"{APPROVALS_DIR}/tool-digests.json"

_LOCK = threading.Lock()

#: Which declaration fields the digest covers. The name is included even
#: though it is also the map key: a rename that reuses another tool's name is
#: a different declaration and must not compare equal to the old one.
_DIGEST_FIELDS = ("name", "description", "inputSchema")


def tool_definition_digest(entry: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON of the tool's pinned declaration fields.

    ``sort_keys`` + tight separators make the digest independent of the
    server's key ordering; a field absent from the declaration hashes as
    ``None`` so a field deleted from the schema is a change, not a no-op.
    """
    canonical = json.dumps(
        {field: entry.get(field) for field in _DIGEST_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def tool_digests_path(home: str | os.PathLike[str] | None = None) -> Path:
    """Return ``<crew home>/`` :data:`TOOL_DIGESTS_LEAF`, honouring ``KIROCREW_HOME``."""
    env_home = os.environ.get("KIROCREW_HOME")
    root = Path(home) if home else (Path(env_home) if env_home else config_dir())
    return root / TOOL_DIGESTS_LEAF


def _load(path: Path) -> "dict[str, dict[str, str]]":
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        # A corrupt or unreadable store is a detection outage, not a gateway
        # outage: compare nothing rather than guess. The next successful write
        # re-publishes a clean baseline.
        logger.debug("tool digest store unreadable; comparing nothing", exc_info=True)
        return {}
    servers = raw.get("servers") if isinstance(raw, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {
        str(name): {
            str(tool): str(digest) for tool, digest in tools.items() if isinstance(tools, dict)
        }
        for name, tools in servers.items()
        if isinstance(tools, dict)
    }


def _write(path: Path, servers: "dict[str, dict[str, str]]") -> None:
    # Deferred: ``atomic_write`` pulls in platform helpers this leaf does not
    # need at import time (the same reason the approvals leaf defers it).
    from kiro_crew.atomic_write import atomic_write

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps({"version": 1, "servers": servers}, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


def compare_and_record(
    server_name: str,
    digests: Mapping[str, str],
    home: str | os.PathLike[str] | None = None,
) -> "list[tuple[str, str, str]]":
    """Compare *digests* with the recorded baseline; adopt and report changes.

    Returns one ``(tool_name, recorded_digest, current_digest)`` triple per tool
    whose digest changed. The first complete listing for a server (no baseline
    yet) records silently, matching the launch approval's
    at-the-moment-of-the-decision recording. Any change -- drift, addition or
    removal -- rewrites that server's baseline to exactly the listing compared,
    so one drift is reported once and a later revert is reported again.
    Best-effort: a store that cannot be written logs at debug and the drift
    found is still returned.
    """
    with _LOCK:
        path = tool_digests_path(home)
        servers = _load(path)
        baseline = servers.get(server_name)
        if baseline is None or baseline == digests:
            if baseline != digests:
                servers[server_name] = dict(digests)
                try:
                    _write(path, servers)
                except Exception:  # pragma: no cover — detection must never raise
                    logger.debug("tool digest store write failed", exc_info=True)
            return []
        drifted = sorted(
            (tool, baseline[tool], digests[tool])
            for tool in set(baseline) & set(digests)
            if baseline[tool] != digests[tool]
        )
        servers[server_name] = dict(digests)
        try:
            _write(path, servers)
        except Exception:  # pragma: no cover — detection must never raise
            logger.debug("tool digest store write failed", exc_info=True)
        return drifted
