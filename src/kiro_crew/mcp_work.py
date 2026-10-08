"""The work-ledger MCP server — the narrow write path from a worker back to the
conductor that dispatched it, and the conductor's structured read of its own fleet.

A conductor session dispatches work to child sessions and otherwise learns what
happened by reading their transcripts. These five tools replace that inference
with a record: a worker writes a schema-bounded status against the ONE work item
it was bound to, and the conductor reads that record as data.

Why this is a store and not a message. ``session_send`` would let a worker push a
line into the conductor's session, and it is withheld from workers for a reason
that does not weaken with care: the text becomes the conductor's next prompt,
executed under the conductor's grants. So the requirement here is not "a channel
from worker to conductor" but a channel that CANNOT carry an instruction. Nothing
on this server reaches ``enqueue_or_run_prompt``; ``work_report`` writes JSON
fields into a file the conductor reads.

Its own server, and ``opt_in`` rather than always-on. ``kirocrew-core`` is in
every agent's spec, and almost no session is a conductor or a worker — for those
the only reachable answer is ``not_bound`` or ``no_ledger``, so mounting these
schemas everywhere would charge every session context for a refusal. kiro-cli
loads a server only when something references it, so a spec that wants the set
hand-builds the ``mcpServers`` entry and adds ``@kirocrew-work`` to ``tools``.
The refusal itself is kept: it is still what an unbound caller here gets.

**No ``autoApprove`` key, and none may be added.** An autoApproved MCP tool is
approved inside kiro-cli and emits no permission request, so ``hooks.on_tool_call``
— the PreToolUse gate carrying the deny floor, the sensitive-path check and the
governance ceiling — is never reached for it. A store that writes agent-authored
text into a record the user reads is not the place to break that.

All five tools are advertised to every caller and DISPATCH BY RESOLVED IDENTITY at
call time, because a session can be a worker to its parent and a conductor to its
own children:

* a binding file, no ledger directory → the worker pair answers, the conductor
  trio returns ``no_ledger``
* a ledger directory, no binding file → the conductor trio answers, the worker
  pair returns ``not_bound``
* both — a second-level conductor → all five answer
* neither → ``not_bound`` / ``no_ledger``

Splitting the worker half onto a server of its own would express the same rule in
the specs instead and buy nothing: a second-level conductor mounts both halves
anyway, so the split would have to be rejoined for exactly the case the depth cap
exists to permit.

Identity is resolved, never asserted. Every tool routes through
``mcp_core.require_strict_session_key`` — the gateway-injected caller block,
``KIROCREW_SESSION_KEY``, or the HMAC host-pid sidecar, and explicitly NOT the
lenient resolver's ``/proc`` ancestor walk, under which a subagent resolves to its
PARENT's identity and could report against its parent's item. The key that passed
the gate is the key sent on the wire; re-resolving at the request would check one
identity and act as another. No tool takes a session key, a conductor id, or an
item id from the worker side — the server derives all three from the caller's own
binding.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

# Same cross-module reuse as ``mcp_dashboard``: the authenticated loopback client
# to the gateway lives in ``mcp_core``, and its heavy dependencies are
# function-local so importing it here is cheap.
from kiro_crew.mcp_core import _get, _post, _resolve_session_key, require_strict_session_key
from kiro_crew.mcp_shared import call_tool_with_logging, run_mcp_stdio_loop
from kiro_crew.platform import redact_via_context as redact
from kiro_crew.tool_result_cap import tool_json
from kiro_crew.validation import (
    MAX_RESPONSE_LEN,
    MCP_WORK_SCHEMAS,
    clamp_report,
    sanitize_string,
    validate_tool_args,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "kirocrew-work"
SERVER_VERSION = "1.0.0"

#: The worker half. Named as a tuple so the channel-agent block list and the
#: registration tests can enumerate the surface without parsing the definitions.
WORKER_TOOLS: tuple[str, ...] = ("work_brief", "work_report")

#: The conductor half.
CONDUCTOR_TOOLS: tuple[str, ...] = ("work_ledger_read", "work_ledger_rebuild", "work_ledger_record")

#: Read off the schema that ENFORCES it rather than spelled again here. The
#: ``work_report`` reply and the tool description both quote this number back to
#: the worker, and a local copy could drift from the cap actually applied.
SUMMARY_CAP: int = next(
    f.max_len for f in MCP_WORK_SCHEMAS["work_report"].fields if f.name == "summary"
)

WORK_TOOLS: tuple[str, ...] = WORKER_TOOLS + CONDUCTOR_TOOLS

_BRIEF_PATH = "/api/work-ledger/brief"
_REPORT_PATH = "/api/work-ledger/report"
_READ_PATH = "/api/work-ledger"
_RECORD_PATH = "/api/work-ledger/record"
_REBUILD_PATH = "/api/work-ledger/rebuild"

#: Fields ``work_ledger_record`` forwards. The action selects which of them it
#: REQUIRES; a field an action has no use for is generally IGNORED rather than
#: refused, so a caller cannot infer from a 200 that every field it sent was read.
#: The store refuses only where a wrong field would change meaning — a ``round`` on
#: an action that does not carry one is ``invalid_value``, and a ``verdict`` or
#: ``state`` outside its vocabulary likewise — while a stray ``title`` on a
#: ``bind`` is simply dropped.
_RECORD_FIELDS: tuple[str, ...] = (
    "action",
    "item_id",
    "title",
    "acceptance",
    "worker_session_key",
    "decision",
    "verdict",
    "state",
    "goal",
    "round",
    "fails",
)

#: Fields ``work_ledger_read`` forwards as a query string. Same defence as
#: :data:`_RECORD_FIELDS`: a key that got past the schema never reaches the wire.
_READ_FIELDS: tuple[str, ...] = ("events", "item_id", "state", "since", "compact")

#: The most characters a ``work_ledger_read`` reply may be, measured on the text
#: the model receives (:func:`_render`). ``build_tool_response`` cuts a result
#: over ``MAX_RESPONSE_LEN`` (48 KiB) in the middle; a cut tears the JSON and
#: drops part of the board. The margin leaves room for the runtime's own framing.
_READ_BUDGET_CHARS = MAX_RESPONSE_LEN - 4_000

#: An acceptance larger than this, serialized, is replaced by an elision marker
#: when a read is over budget. Real bars are a few hundred chars; one bloated bar
#: must not cost the read every OTHER row, so this runs before any row is dropped.
_ACCEPTANCE_CAP_CHARS = 4_000

_TRUNCATION_HINT = (
    "Over the read budget. Event tails were emptied, then oversized acceptances "
    "elided (accept_eval.py answers error for an elided bar; shrink it with an "
    "accept write), then rows dropped: closed items first, then open items "
    "oldest-created first. The newest open item is always kept. Re-read with "
    "item_id=<id> for one item, state=open, or compact=true."
)

_STAMP_FLOOR = datetime.min.replace(tzinfo=timezone.utc)


def _read_query(args: dict[str, Any]) -> dict[str, Any]:
    """The caller's read filters as query-string values (``compact`` as true/false)."""
    return {
        k: (str(v).lower() if isinstance(v, bool) else v)
        for k, v in args.items()
        if k in _READ_FIELDS and v is not None
    }


def _render(doc: Any) -> str:
    """*doc* as the model receives it.

    The same passes the reply goes through on its way out: redaction here, then
    ``build_tool_response``'s ``sanitize_string``. Applying the second here too is
    harmless (it is idempotent) and makes ``len(_render(doc))`` the delivered
    length, so the budget is measured on what egress actually emits.
    """
    return sanitize_string(redact(tool_json(doc)))


def _created(row: dict[str, Any]) -> datetime | None:
    """A row's ``created_at`` as an aware moment, or ``None``.

    The store writes local time with an offset, so text order is not time order
    across a DST change; this compares moments. Parsed here rather than through
    the store module, which this tool reaches only over the dashboard API.
    """
    try:
        moment = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _drop_rank(row: dict[str, Any]) -> tuple[bool, bool, datetime, str]:
    """Sort key, first-dropped first: closed rows, then open rows oldest-created.

    A ``created_at`` that will not parse ranks oldest rather than failing the read.
    """
    moment = _created(row)
    return (
        row.get("state") == "open",
        moment is not None,
        moment or _STAMP_FLOOR,
        str(row.get("item_id", "")),
    )


def _fit_ledger(doc: dict[str, Any], budget: int = _READ_BUDGET_CHARS) -> str:
    """Render the ledger read under *budget* chars, always as valid JSON.

    Each stage runs only while the text is still over budget, and a trimmed reply
    says what went (``truncated``, ``dropped_events_for``,
    ``elided_acceptance_for``, ``omitted_items``):

    1. empty every event tail;
    2. elide each acceptance (row ``acceptance``, batch ``accept``) larger than
       :data:`_ACCEPTANCE_CAP_CHARS` -- before any row goes, so one oversized bar
       cannot evict its siblings;
    3. drop rows in :func:`_drop_rank` order -- closed first, then open
       oldest-created -- each with its batch entry, keeping the last one, so the
       newest open item survives;

    and when even that does not fit, one minimal envelope that names the ids.
    """
    text = _render(doc)
    if len(text) <= budget:
        return text
    rows: list[dict[str, Any]] = [r for r in doc.get("items") or [] if isinstance(r, dict)]
    batch_doc = doc.get("accept_batch")
    batch: list[dict[str, Any]] = (
        [e for e in batch_doc["items"] if isinstance(e, dict)]
        if isinstance(batch_doc, dict) and isinstance(batch_doc.get("items"), list)
        else []
    )
    all_ids = [str(r.get("item_id", "")) for r in rows]
    all_ids += [str(e.get("id", "")) for e in batch if str(e.get("id", "")) not in all_ids]
    doc["truncated"] = True
    doc["truncation_hint"] = _TRUNCATION_HINT

    # 1. Event tails.
    emptied = [str(r.get("item_id", "")) for r in rows if r.get("events")]
    for row in rows:
        if row.get("events"):
            row["events"] = []
    if emptied:
        doc["dropped_events_for"] = emptied
    text = _render(doc)
    if len(text) <= budget:
        return text

    # 2. Oversized acceptances, before any row is dropped.
    elided: list[str] = []
    for holder, field, id_key in [(r, "acceptance", "item_id") for r in rows] + [
        (e, "accept", "id") for e in batch
    ]:
        chars = len(json.dumps(holder.get(field), ensure_ascii=False))
        if chars > _ACCEPTANCE_CAP_CHARS:
            holder[field] = {
                "elided": True,
                "chars": chars,
                "reason": "acceptance exceeds the read budget; shrink it with an accept write",
            }
            if str(holder.get(id_key, "")) not in elided:
                elided.append(str(holder.get(id_key, "")))
    if elided:
        doc["elided_acceptance_for"] = elided
        text = _render(doc)
        if len(text) <= budget:
            return text

    # 3. Rows, with their batch entries. Batch entries for ids not among the rows
    #    (a filtered read keeps the whole board's batch) go first.
    row_ids = {str(r.get("item_id", "")) for r in rows}
    victims = [str(e.get("id", "")) for e in batch if str(e.get("id", "")) not in row_ids]
    victims += [str(r.get("item_id", "")) for r in sorted(rows, key=_drop_rank)][:-1]

    def _drop(count: int) -> str:
        # Render with the first ``count`` victims dropped. Fewer rows never
        # renders larger, so the smallest fitting count is found by bisection:
        # O(log n) renders instead of one per dropped row.
        gone = set(victims[:count])
        doc["items"] = [r for r in rows if str(r.get("item_id", "")) not in gone]
        if isinstance(batch_doc, dict) and batch:
            batch_doc["items"] = [e for e in batch if str(e.get("id", "")) not in gone]
        doc["omitted_items"] = victims[:count]
        return _render(doc)

    if victims and len(fitted := _drop(len(victims))) <= budget:
        low, high = 1, len(victims)
        while low < high:
            mid = (low + high) // 2
            if len(_drop(mid)) <= budget:
                high = mid
            else:
                low = mid + 1
        return fitted if low == len(victims) else _drop(low)

    # Nothing left to trim that fits: a minimal envelope, ids as many as fit.
    ids = all_ids
    while True:
        envelope = {
            "truncated": True,
            "unfittable": True,
            "item_count": len(all_ids),
            "omitted_items": ids,
            "truncation_hint": (
                "The ledger cannot be shown under the read budget even trimmed. "
                "Read one item with item_id=<id>, or compact=true."
            ),
        }
        text = _render(envelope)
        if len(text) <= budget or not ids:
            return text
        ids = ids[: len(ids) // 2]


def _tool_definitions() -> list[dict[str, Any]]:
    """The tool surface this server advertises."""
    return [
        {
            "name": "work_brief",
            "description": (
                "Read the ONE work item this session was dispatched for: its title, its "
                "acceptance condition, the round it belongs to, the conductor's latest "
                "decision, and your own last reported status. Call it before starting "
                "work and treat title + acceptance as the definition of done. Takes no "
                "arguments — which item you are bound to is resolved from your own "
                "session, not supplied. It does not return the conductor's goal or any "
                "sibling item: you have neither. Answers 'not_bound' when this session "
                "is not a dispatched worker."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "work_report",
            "description": (
                "Report status on your own work item (resolved from your session) at real"
                " milestones, as data your conductor reads. progress = informational; "
                "blocked = an external dependency stopped the work; question = the "
                "conductor must decide; done = a CLAIM the acceptance is met, with "
                "evidence in artifacts and any PR in pr. Facts and pointers, not "
                "requests. The report must fit one 64 KB crew-log line "
                "(work_entry_too_large); work_item_too_large needs the conductor's "
                "accept."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["progress", "done", "blocked", "question"],
                        "description": "Where the work stands, in one of four values.",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "Your own account of where the work is, <= 500 chars. Facts "
                            "and artifact pointers. A longer one is accepted and cut to "
                            "the cap, not refused: the stored value and the reply both "
                            "say how much was dropped, so write it to fit."
                        ),
                    },
                    "artifacts": {
                        "type": "object",
                        "description": (
                            "String->string pointers to what you produced (pr, commit, "
                            "branch, paths). <= 16 keys, key <= 64, value <= 512."
                        ),
                    },
                    "pr": {
                        "type": "integer",
                        "description": (
                            "A pull-request number you produced. A CLAIM only: the "
                            "conductor promotes it into the acceptance bar explicitly, "
                            "so naming a number does not move your own bar."
                        ),
                    },
                },
                "required": ["status", "summary"],
            },
        },
        {
            "name": "work_ledger_read",
            "description": (
                "Read the work ledger this conductor session owns. No arguments: the "
                "whole board, each item with every field, its orphaned / stale / "
                "acceptance_concrete flags and its last 20 events, plus an accept_batch "
                "for accept_eval.py (open items with a concrete bar, built from "
                "acceptance alone and never a worker's claimed pr; filter it to done "
                "yourself). Every argument NARROWS it: compact=true is the cheap patrol "
                "read (status columns and flags only), item_id=<id> one item, "
                "state=/since= rows, events=<n> shorter tails. An over-budget reply is "
                "trimmed with truncated=true (closed first, then open oldest-created). "
                "'no_ledger' when none is owned yet."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "description": "Newest events per item (default and max 20).",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "Return only this item (it_<8 hex>).",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "accepted", "rejected", "abandoned"],
                        "description": "Return only items in this state.",
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "ISO-8601 stamp; only items created, reported on or closed "
                            "at or after it."
                        ),
                    },
                    "compact": {
                        "type": "boolean",
                        "description": (
                            "Status columns and the orphaned / stale / "
                            "acceptance_concrete flags only — no events, acceptance or "
                            "accept_batch."
                        ),
                    },
                },
            },
        },
        {
            "name": "work_ledger_rebuild",
            "description": (
                "Rebuild the work ledger this conductor session owns from the crew log: "
                "every accepted write was recorded there, so the ledger files are a cache "
                "of that record. Rewrites the conductor record, every item and its events "
                "from the crew log and removes any item file the record does not know. Use "
                "it when the ledger reads as damaged or missing. Takes no arguments -- the "
                "ledger is your own. Refuses when the crew log is off."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "work_ledger_record",
            "description": (
                "Write one conductor-owned field set on your own ledger, one action per "
                "call: goal (opens the ledger), create (title + acceptance), bind (a "
                "worker session key; do it BEFORE seeding that session), decide (the one "
                "field a worker reads as an instruction), verdict (accept_eval.py's "
                "verdict), accept (promote a checked worker pr into the acceptance), "
                "close (terminal state). Caps refuse rather than truncate; the whole "
                "write must fit one 64 KB crew-log line (work_entry_too_large)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "bind",
                            "decide",
                            "verdict",
                            "close",
                            "goal",
                            "accept",
                        ],
                        "description": "Which write to perform.",
                    },
                    "item_id": {
                        "type": "string",
                        "description": (
                            "The server-minted it_<8 hex> id from a create, for every "
                            "action but create and goal."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "create: what the item is, <= 200 chars.",
                    },
                    "acceptance": {
                        "type": "object",
                        "description": (
                            "create / accept: the accept_eval.py condition object, stored "
                            'verbatim — e.g. {"kind": "pr_checks", "pr": 123, '
                            '"repo": "owner/name"}.'
                        ),
                    },
                    "worker_session_key": {
                        "type": "string",
                        "description": "bind: the worker session's key from session_create.",
                    },
                    "decision": {
                        "type": "string",
                        "description": (
                            "decide / close: what you decided and why, <= 2000 chars. The "
                            "worker reads this as an instruction."
                        ),
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["pass", "fail", "pending", "refused", "error"],
                        "description": "verdict: accept_eval.py's own five-value answer.",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["accepted", "rejected", "abandoned"],
                        "description": "close: the terminal disposition.",
                    },
                    "goal": {
                        "type": "string",
                        "description": "goal: the goal this ledger serves, <= 2000 chars.",
                    },
                    "round": {
                        "type": "integer",
                        "description": "goal / decide / create: the patrol round counter.",
                    },
                    "fails": {
                        "type": "integer",
                        "description": "verdict: acceptance attempts that came back fail.",
                    },
                },
                "required": ["action"],
            },
        },
    ]


def _list_tools() -> list[dict[str, Any]]:
    """The tool surface, unconditionally.

    Reaching this process at all means an agent spec referenced this server, so
    the assignment already happened. What a caller may DO with a tool is decided
    by what it resolves to at call time, not by hiding half the list: a
    second-level conductor legitimately reaches all five, and a list that varied
    by identity would make a worker's missing conductor tools look like a broken
    install rather than a refusal it can read.
    """
    return _tool_definitions()


def _validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate tool arguments against schema. Returns cleaned args."""
    schema = MCP_WORK_SCHEMAS.get(name)
    if schema:
        return validate_tool_args(args, schema)
    return args


def _strict_caller() -> tuple[str, str]:
    """Resolve the calling session strictly, refusing PID-walked identities.

    Returns ``(key, "")`` or ``("", error)``. The lenient resolver walks ``/proc``
    ancestors and a spawned subagent lives under its parent slot's process tree,
    so the walk would hand a subagent its parent's identity — letting it read the
    parent's brief or report against the parent's item. Both halves of this server
    send THIS key rather than resolving their own, so the identity that passed the
    gate is the identity on the wire.
    """
    return require_strict_session_key(
        "Error: this session's identity could not be verified strictly, so the work "
        "ledger is not reachable from here. A subagent inherits no session identity "
        "of its own — call the work tools from the dispatched session itself.",
        server=SERVER_NAME,
    )


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    """Dispatch one validated tool call."""
    if name not in WORK_TOOLS:
        return f"Error: unknown tool '{name}'"

    caller_key, strict_err = _strict_caller()
    if not caller_key:
        return strict_err

    if name == "work_brief":
        resp = _get(_BRIEF_PATH, session_key=caller_key)
        if resp.get("error"):
            return _refusal("could not read your work brief", resp)
        brief = resp.get("brief") or {}
        # Redacted on the way OUT as well as in: the brief carries the conductor's
        # own `decision` prose and this worker's last summary, and a resume reads
        # both back into context.
        return redact(tool_json(brief))

    if name == "work_report":
        payload = {k: v for k, v in args.items() if k in ("status", "summary", "artifacts", "pr")}
        resp = _post(_REPORT_PATH, payload, session_key=caller_key)
        if resp.get("error"):
            return _refusal("could not record your report", resp)
        # A summary over the cap is clamped rather than refused, so the only place
        # the worker can learn its text was cut is this reply: the success frame
        # carries no summary to read it off. Reported here with both lengths so
        # the next report can be written to fit instead of discovering the cap
        # again. The stored value carries the same note for the conductor.
        clamped = clamp_report(str(payload.get("summary") or ""))
        note = ""
        if clamped:
            before, kept = clamped
            note = (
                f" Your summary was {before} chars, over the {SUMMARY_CAP} cap: "
                f"the first {kept} were stored and the rest dropped. "
                f"The stored summary says so too. Keep the next one shorter."
            )
        return (
            f"Recorded. status={resp.get('status') or '(unset)'} "
            f"item={resp.get('item_id') or '(unknown)'}{note}"
        )

    if name == "work_ledger_read":
        # Each call names the path on the module constant directly so the call-site
        # auth guard can resolve it; a bound variable would hide it.
        query = _read_query(args)
        if query:
            resp = _get(f"{_READ_PATH}?{urlencode(query)}", session_key=caller_key)
        else:
            resp = _get(_READ_PATH, session_key=caller_key)
        if resp.get("error"):
            return _refusal("could not read your work ledger", resp)
        # The ledger holds worker-authored prose written from untrusted work, and a
        # patrol cycle re-reads it into context every round: redacted, and fitted
        # under the runtime's tool-result cut so a cut never tears the JSON.
        return _fit_ledger(resp)

    if name == "work_ledger_rebuild":
        resp = _post(_REBUILD_PATH, {}, session_key=caller_key)
        if resp.get("error"):
            return _refusal("could not rebuild the work ledger", resp)
        return (
            f"Rebuilt the work ledger from the crew log. items={resp.get('items', 0)} "
            f"events={resp.get('events', 0)} removed={resp.get('removed', 0)}"
        )

    if name == "work_ledger_record":
        payload = {k: v for k, v in args.items() if k in _RECORD_FIELDS}
        resp = _post(_RECORD_PATH, payload, session_key=caller_key)
        if resp.get("error"):
            return _refusal("could not write the work ledger", resp)
        item = resp.get("item") or {}
        action = resp.get("action") or payload.get("action") or ""
        if item:
            return (
                f"Recorded {action}. item={item.get('item_id') or '(unknown)'} "
                f"state={item.get('state') or '(unset)'} "
                f"verdict={item.get('verdict') or '(none)'}"
            )
        conductor = resp.get("conductor") or {}
        return f"Recorded {action}. round={conductor.get('round', '(unset)')}"

    return f"Error: unknown tool '{name}'"  # pragma: no cover - guarded above


def _refusal(what: str, resp: dict[str, Any]) -> str:
    """One error string, carrying the store's machine-readable code when there is one.

    The code is what a conductor or worker dispatches on — ``not_bound`` means
    "you are not a worker", ``no_ledger`` means "open one first", ``item_closed``
    means "stop reporting" — so it is quoted rather than folded into prose.
    """
    code = resp.get("code")
    field = resp.get("field")
    detail = f"Error: {what}: {resp['error']}"
    if code:
        detail += f" [{code}"
        detail += f", field={field}]" if field else "]"
    return redact(detail)


def _call_tool(name: str, raw_args: dict[str, Any]) -> str:
    """Guarded entry point — schema validation and SEL audit live in the wrapper."""
    return call_tool_with_logging(
        name,
        raw_args,
        _validate_args,
        _call_tool_inner,
        session_key=_resolve_session_key() or SERVER_NAME,
        downstream_service=SERVER_NAME,
    )


ADVERTISE_CALLER_IDENTITY = True


def run_mcp_server() -> None:
    """Run the MCP stdio server — reads JSON-RPC from stdin, writes to stdout."""
    run_mcp_stdio_loop(
        SERVER_NAME,
        SERVER_VERSION,
        _list_tools,
        _call_tool,
        advertise_caller_identity=ADVERTISE_CALLER_IDENTITY,
    )
