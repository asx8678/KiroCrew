"""The dynamic workflows: author, run, monitor, restart tools: what they advertise and what they do.

``schemas()`` returns the ADVERTISEMENT half of each tool -- its name, the
model-facing description, and the JSON Schema a call is validated against.
``HANDLERS`` maps each of those names to the function that runs it. Both halves
of a tool live here so its contract and its behavior are read together, and
``test_mcp_tool_registry`` fails if one arrives without the other.

Handlers reach this server's shared plumbing as attributes of ``mcp_core`` --
``mcp_core._post``, the identity resolvers, the governance vets. That is
deliberate rather than untidy: an attribute lookup resolves at CALL time, so a
test that rebinds one on the module still intercepts the handler. Importing
those names directly here would bind them at import time and silently escape
every existing patch site.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from kiro_crew import mcp_core
from kiro_crew.security import redact_credentials, redact_exfiltration_urls
from kiro_crew.validation import (
    WORKFLOW_AUTHOR_SCHEMA,
    WORKFLOW_LIBRARY_LIST_SCHEMA,
    WORKFLOW_RERUN_SCHEMA,
    WORKFLOW_RESULT_SCHEMA,
    WORKFLOW_RUN_ID_SCHEMA,
    WORKFLOW_RUN_SCHEMA,
    validate_tool_args,
)


def schemas() -> list[dict[str, Any]]:
    """Descriptors for the workflows tools."""
    return [
        # --- Dynamic workflows (M6): author + run + monitor from chat ---
        {
            "name": "workflow_author",
            "description": (
                "Turn a natural-language goal into a runnable DYNAMIC WORKFLOW "
                "Python script (orchestrates agents via a sandboxed `ctx` DSL). "
                "Returns the validated script source — then call workflow_run to "
                "execute it. (Usually you can skip this and pass `intent` straight to "
                "workflow_run, which authors+runs in one step.)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "The goal in plain language, e.g. 'deep research on the origin of pizza'",
                    },
                },
                "required": ["intent"],
            },
        },
        {
            "name": "workflow_run",
            "description": (
                "Run a DYNAMIC WORKFLOW: a sandboxed script orchestrating agents in "
                "phases, shown in the Workflows tab and restartable from any step. Choose"
                " it yourself when a task has dependent phases or many pieces AND a "
                "failed piece should re-run alone; a one-shot fan-out fits spawn_run. "
                "Pass a saved slug as `workflow` or the goal as `intent` to author and "
                "launch. Returns a run_id; the result is injected on completion. Read "
                "workflow_status / workflow_result; restart with workflow_rerun_subtree."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Workflow script source (Python)"},
                    "workflow": {
                        "type": "string",
                        "description": "Saved workflow id or slug to run exactly",
                    },
                    "input": {
                        "type": "string",
                        "description": "Free-form input exposed as ctx.args['input']",
                    },
                    "intent": {
                        "type": "string",
                        "description": "If no source: a NL goal to author then run",
                    },
                    "name": {"type": "string", "description": "Optional run name"},
                    "args": {
                        "type": "object",
                        "description": "Optional args passed to the workflow",
                    },
                    "budget_total": {
                        "type": "integer",
                        "description": "Optional token budget ceiling for the run",
                    },
                },
            },
        },
        {
            "name": "workflow_library_list",
            "description": (
                "List reusable workflows explicitly saved in the global Kiro Crew library. "
                "Pass search to find local workflows relevant to a new request before authoring."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"search": {"type": "string"}},
            },
        },
        {
            "name": "workflow_status",
            "description": (
                "Get the live status of a background workflow run by run_id "
                "(running/finished/failed/cancelled + agent/event counts). Use to "
                "monitor a run you started; for the full result use workflow_result."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_result",
            "description": (
                "Get a workflow run's outcome by run_id. The default summary "
                "returns status, the final result (capped ~8k chars), any "
                "per-agent failure reasons and partial outputs, and the LAST 20 "
                "events; call again with section='events' (or 'agent_results') "
                "plus offset/limit to page through the rest."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "section": {
                        "type": "string",
                        "enum": ["summary", "events", "agent_results"],
                    },
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_list",
            "description": "List recent background workflow runs (newest first) with their status.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "workflow_cancel",
            "description": "Cancel a running background workflow by run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        },
        {
            "name": "workflow_rerun_subtree",
            "description": (
                "Re-run a prior workflow, REPLAYING the unchanged prefix and "
                "re-executing from a chosen step ('restart parts' at runtime). "
                "Agent calls before `from_index` reuse the prior run's cached "
                "results; calls at/after re-call the model. from_index=0 re-runs "
                "everything fresh. Returns a new run_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string", "description": "The prior run to restart from"},
                    "from_index": {
                        "type": "integer",
                        "description": "Agent call_index to restart at (0 = full re-run)",
                        "default": 0,
                    },
                },
                "required": ["run_id"],
            },
        },
    ]


def _redact_obj(obj: Any) -> Any:
    """Recursively redact credentials + exfiltration URLs from a response.

    Keys are redacted too: agent output is parsed into these structures, so a
    credential can arrive as a mapping key and a values-only walk would let it
    through (see dashboard/handlers/workflows.py::_redact_obj).
    """
    if isinstance(obj, str):
        s, _ = redact_exfiltration_urls(obj)
        s, _ = redact_credentials(s)
        return s
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {_redact_obj(k): _redact_obj(v) for k, v in obj.items()}
    return obj


def _wf_return(
    tool: str, text: str, *, outcome: str = "success", session_key: str | None = None
) -> str:
    safe, _ = redact_exfiltration_urls(text)
    safe, _ = redact_credentials(safe)
    mcp_core.sel().log_tool_invocation(
        session_key=session_key if session_key is not None else mcp_core._resolve_session_key(),
        source="mcp",
        tool_name=tool,
        outcome=outcome,
    )
    return safe


def workflow_author(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_AUTHOR_SCHEMA)
    intent = (args.get("intent") or "").strip()
    if not intent:
        return _wf_return("workflow_author", "Error: intent is required", outcome="error")
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error")
    d = mcp_core._post("/api/workflows/author", {"intent": intent}, session_key=session_key)
    if d.get("error"):
        return _wf_return(
            "workflow_author", f"workflow_author failed: {d['error']}", outcome="error"
        )
    if not d.get("ok"):
        return _wf_return(
            "workflow_author",
            "Could not author a valid workflow: " + "; ".join(d.get("errors", [])),
            outcome="error",
        )
    return _wf_return(
        "workflow_author",
        "Authored workflow. Review then run it with workflow_run(source=…):\n\n"
        f"{d.get('source', '')}",
    )


def workflow_run(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_RUN_SCHEMA)
    source = args.get("source") or ""
    workflow_ref = (args.get("workflow") or "").strip()
    intent = (args.get("intent") or "").strip()
    if not (source or workflow_ref or intent):
        return _wf_return(
            "workflow_run", "Error: provide either 'source' or 'intent'", outcome="error"
        )
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error")
    wf_body: dict[str, Any] = {}
    if args.get("name"):
        wf_body["name"] = args["name"]
    if isinstance(args.get("args"), dict):
        wf_body["args"] = args["args"]
    if isinstance(args.get("budget_total"), int):
        wf_body["budget_total"] = args["budget_total"]
    if workflow_ref:
        if args.get("input"):
            wf_body["input"] = args["input"]
        d = mcp_core._post(
            f"/api/workflows/definitions/{quote(workflow_ref, safe='')}/run",
            wf_body,
            session_key=session_key,
        )
        if d.get("error"):
            return _wf_return("workflow_run", f"workflow_run failed: {d['error']}", outcome="error")
        # A task-plan definition runs SYNCHRONOUSLY behind this call
        # (`start_workflow_definition` awaits `execute_plan`), so by the time the
        # response arrives the plan has already finished or the POST timed out
        # under it. Promising a later injection there would tell the caller to
        # wait for an event that has already happened, or that never will.
        blocking = bool(d.get("task_id"))
        return _wf_return(
            "workflow_run",
            f"Started saved workflow `/workflow {workflow_ref}` as `{d.get('run_id')}` "
            f"from revision {d.get('revision')}. "
            + (
                "It ran to completion behind this call; read the outcome with " "`workflow_status`."
                if blocking
                else "Its result will be injected here on completion."
            ),
        )
    if not source and intent:
        # Author-in-run (M6.7): returns a run_id INSTANTLY — the script is
        # authored inside the background run as a visible "Authoring" phase, so
        # the slow model call never blocks this tool (no 30s author timeout).
        wf_body["intent"] = intent
        d = mcp_core._post("/api/workflows/run_intent", wf_body, session_key=session_key)
        if d.get("error"):
            return _wf_return("workflow_run", f"workflow_run failed: {d['error']}", outcome="error")
        return _wf_return(
            "workflow_run",
            f"Started workflow run `{d.get('run_id')}`. It is authoring the workflow "
            "from your request now (watch the Authoring phase in the Workflows tab / "
            "chat activity), then runs in the background. Its result will be injected "
            f"here on completion — or check progress with workflow_status('{d.get('run_id')}').",
        )
    wf_body["source"] = source
    d = mcp_core._post("/api/workflows/run", wf_body, session_key=session_key)
    if d.get("error"):
        return _wf_return("workflow_run", f"workflow_run failed: {d['error']}", outcome="error")
    return _wf_return(
        "workflow_run",
        f"Started workflow run `{d.get('run_id')}` (name: {d.get('name') or '—'}). "
        "It runs in the background — monitor with workflow_status, and its result "
        "will be injected here on completion. You can keep working; check back with "
        f"workflow_status('{d.get('run_id')}').",
    )


def workflow_library_list(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_LIBRARY_LIST_SCHEMA)
    search = (args.get("search") or "").strip()
    path = "/api/workflows/definitions"
    if search:
        path += f"?q={quote(search, safe='')}"
    d = mcp_core._get(path)
    if d.get("error"):
        return _wf_return(
            "workflow_library_list", f"workflow_library_list: {d['error']}", outcome="error"
        )
    definitions = d.get("definitions", [])
    if not definitions:
        return _wf_return("workflow_library_list", "No saved workflows yet.")
    lines = [
        f"- `/workflow {item.get('slug')}` — {item.get('name') or item.get('slug')} "
        f"(revision {item.get('revision')}, id `{item.get('id')}`)"
        for item in definitions
    ]
    return _wf_return("workflow_library_list", "Saved workflows:\n" + "\n".join(lines))


def workflow_status(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_RUN_ID_SCHEMA)
    run_id = args.get("run_id", "")
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error", session_key="")
    d = mcp_core._get(f"/api/workflows/runs/{run_id}", session_key=session_key)
    # A *failed* run's snapshot legitimately carries its own ``error`` field
    # (its failure message) alongside ``run_id`` — that is NOT a transport
    # error. Only bail early when the response is a bare transport/404 error
    # (``{"error": ...}`` with no ``run_id``); otherwise report the run,
    # including its failure message.
    if d.get("error") and "run_id" not in d:
        return _wf_return(
            "workflow_status",
            f"workflow_status: {d['error']}",
            outcome="error",
            session_key=session_key,
        )
    # ``error`` (and ``name``) are LLM-derived — redact before surfacing them
    # to the dashboard/chat (credentials + exfiltration URLs).
    safe_err = _redact_obj(d["error"]) if d.get("error") else ""
    safe_name = _redact_obj(d.get("name") or "—")
    return _wf_return(
        "workflow_status",
        f"Run `{d.get('run_id')}` ({safe_name}): **{d.get('status')}** "
        f"— {d.get('event_count', 0)} events" + (f"; error: {safe_err}" if safe_err else ""),
        session_key=session_key,
    )


_WORKFLOW_RESULT_CAP_CHARS = 8_000  # final result cap in the summary (TOOL-7)
_WORKFLOW_RESULT_EVENT_TAIL = 20  # events the summary carries (TOOL-7)
_WORKFLOW_RESULT_PAGE_DEFAULT = 200  # default page for section= paging


def _project_workflow_event(ev: Any, *, drop_result_summary: bool) -> dict[str, Any]:
    """MODEL-FACING compaction of one event (TOOL-16). The journal, resume and
    the dashboard keep WorkflowEvent.to_json whole; this projection runs only
    inside workflow_result's rendering, where every event repeating run_id and
    a 32-char microsecond timestamp measured -53% of the tool's output tokens:

    * the per-event run_id is omitted (the top-level run_id stays);
    * the timestamp is cut to HH:MM:SS (ts[11:19]);
    * empty string values in data are dropped;
    * result_summary is dropped when agent_results is present — it repeats the
      first 120 chars of a result the same response carries in full.
    """
    if not isinstance(ev, dict):
        return ev
    data = ev.get("data") or {}
    if not isinstance(data, dict):
        data = {"data": data}
    if drop_result_summary:
        data = {k: v for k, v in data.items() if k != "result_summary"}
    data = {k: v for k, v in data.items() if v != ""}
    ts = str(ev.get("ts", ""))
    return {
        "seq": ev.get("seq"),
        "t": ts[11:19],
        "type": ev.get("type"),
        "data": data,
    }


def workflow_result(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_RESULT_SCHEMA)
    run_id = args.get("run_id", "")
    section = args.get("section", "summary")
    offset = int(args.get("offset", 0))
    limit = int(args.get("limit", _WORKFLOW_RESULT_PAGE_DEFAULT))
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error", session_key="")
    d = mcp_core._get(f"/api/workflows/runs/{run_id}", session_key=session_key)
    # As in workflow_status: a failed run carries its own ``error`` in the
    # snapshot. Distinguish a real transport error (no ``run_id``) from a
    # failed-but-readable run so a failed run still returns its full event
    # stream instead of masquerading as a transport failure.
    if d.get("error") and "run_id" not in d:
        return _wf_return(
            "workflow_result",
            f"workflow_result: {d['error']}",
            outcome="error",
            session_key=session_key,
        )
    # ``result`` / ``error`` / ``events`` are LLM-derived (agent outputs, log
    # lines) — recursively redact credentials + exfiltration URLs before
    # returning them through this MCP tool to the dashboard/chat surface.
    # ``partial_results`` / ``agent_errors`` carry the same class of content and
    # MUST be projected here: when a run ends without a usable return value they
    # are the only surviving output, and the completion message points the reader
    # at this tool to read them.
    # TOOL-16: project each event for the MODEL copy — the per-event run_id,
    # microsecond timestamps and duplicated summaries measured -53% of this
    # tool's output tokens. _redact_obj runs on the raw snapshot FIRST so every
    # projected field is still redacted; the journal and the dashboard keep the
    # whole to_json shape.
    _raw_events = _redact_obj(d.get("events", []))
    _has_results = bool(d.get("agent_results"))
    events = [_project_workflow_event(ev, drop_result_summary=_has_results) for ev in _raw_events]
    if section == "events":
        page = events[offset : offset + max(1, limit)]
        wf_payload: dict[str, Any] = {
            "run_id": d.get("run_id"),
            "status": d.get("status"),
            "events": page,
            "events_total": len(events),
            "events_offset": offset,
            "has_more": offset + len(page) < len(events),
        }
    elif section == "agent_results":
        results = _redact_obj(d.get("agent_results") or {})
        keys = list(results.keys())[offset : offset + max(1, limit)]
        wf_payload = {
            "run_id": d.get("run_id"),
            "status": d.get("status"),
            "agent_results": {k: results[k] for k in keys},
            "keys_total": len(results),
            "has_more": offset + len(keys) < len(results),
        }
    else:
        # TOOL-7: the default SUMMARY answers "what happened" in one bounded
        # read — status, error, the failures and the partial outputs (the
        # fields the completion message points here for) BEFORE any events,
        # the final result capped, and the LAST events. The old shape
        # serialized the whole event stream first with indent=2, and the
        # transport's head-only 100k cut then dropped exactly the trailing
        # agent_results/partial_results/agent_errors.
        result = _redact_obj(d.get("result"))
        if isinstance(result, str) and len(result) > _WORKFLOW_RESULT_CAP_CHARS:
            result = (
                result[:_WORKFLOW_RESULT_CAP_CHARS]
                + f"… [result truncated, {len(result) - _WORKFLOW_RESULT_CAP_CHARS} "
                "more chars]"
            )
        wf_payload = {
            "run_id": d.get("run_id"),
            "status": d.get("status"),
            "result": result,
            "error": _redact_obj(d.get("error")),
        }
        if d.get("partial_results"):
            wf_payload["partial_results"] = _redact_obj(d.get("partial_results"))
        if d.get("agent_errors"):
            wf_payload["agent_errors"] = _redact_obj(d.get("agent_errors"))
        wf_payload["events"] = events[-_WORKFLOW_RESULT_EVENT_TAIL:]
        wf_payload["events_total"] = len(events)
        wf_payload["paging"] = (
            f"showing the last {min(len(events), _WORKFLOW_RESULT_EVENT_TAIL)} of "
            f"{len(events)} events; call again with section='events' and "
            "offset/limit to page through the rest"
        )
    return _wf_return(
        "workflow_result",
        json.dumps(wf_payload, ensure_ascii=False, default=str, separators=(",", ":")),
        session_key=session_key,
    )


def workflow_list(name: str, args: dict[str, Any]) -> str:
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error", session_key="")
    d = mcp_core._get("/api/workflows/runs", session_key=session_key)
    if d.get("error"):
        return _wf_return(
            "workflow_list",
            f"workflow_list: {d['error']}",
            outcome="error",
            session_key=session_key,
        )
    runs = d.get("runs", [])
    if not runs:
        return _wf_return("workflow_list", "No workflow runs yet.", session_key=session_key)
    lines = [
        f"- `{r.get('run_id')}` {r.get('name') or '—'} → {r.get('status')} "
        f"({r.get('event_count', 0)} events)"
        for r in runs
    ]
    return _wf_return(
        "workflow_list",
        "Workflow runs (newest first):\n" + "\n".join(lines),
        session_key=session_key,
    )


def workflow_cancel(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_RUN_ID_SCHEMA)
    run_id = args.get("run_id", "")
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error")
    d = mcp_core._post(f"/api/workflows/runs/{run_id}/cancel", {}, session_key=session_key)
    if d.get("error"):
        return _wf_return("workflow_cancel", f"workflow_cancel: {d['error']}", outcome="error")
    return _wf_return(
        "workflow_cancel",
        f"Run `{run_id}`: {'cancelled' if d.get('cancelled') else 'not cancellable (already done?)'}",
    )


def workflow_rerun_subtree(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WORKFLOW_RERUN_SCHEMA)
    run_id = args.get("run_id", "")
    from_index = args.get("from_index", 0)
    session_key, error = _workflow_identity()
    if not session_key:
        return _wf_return(name, error, outcome="error")
    d = mcp_core._post(
        f"/api/workflows/runs/{run_id}/rerun",
        {"from_index": from_index if isinstance(from_index, int) else 0},
        session_key=session_key,
    )
    if d.get("error"):
        return _wf_return(
            "workflow_rerun_subtree", f"workflow_rerun_subtree: {d['error']}", outcome="error"
        )
    return _wf_return(
        "workflow_rerun_subtree",
        f"Re-running `{run_id}` as `{d.get('run_id')}` "
        f"(replaying calls before index {d.get('replayed_before')}). "
        f"Monitor with workflow_status('{d.get('run_id')}').",
    )


HANDLERS: dict[str, Callable[[str, dict[str, Any]], str]] = {
    "workflow_author": workflow_author,
    "workflow_run": workflow_run,
    "workflow_library_list": workflow_library_list,
    "workflow_status": workflow_status,
    "workflow_result": workflow_result,
    "workflow_list": workflow_list,
    "workflow_cancel": workflow_cancel,
    "workflow_rerun_subtree": workflow_rerun_subtree,
}


def _workflow_identity() -> tuple[str, str]:
    return mcp_core.require_strict_session_key(
        "Cannot verify the current workflow caller. No workflow action was performed."
    )
