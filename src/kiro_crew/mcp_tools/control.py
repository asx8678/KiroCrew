"""The session control: waiting, monitoring loops, questions, and follow-ups tools: what they advertise and what they do.

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

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from kiro_crew import autonudge, mcp_core, platform_compat, session_directive
from kiro_crew.autonudge_judge import ending_phrase, screen_phrase
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.constants import WAIT_TOOL_MAX_SECS
from kiro_crew.mcp_shared import ToolCancelled, is_tool_cancelled
from kiro_crew.mcp_tools._limits import (
    _MONITOR_DEFAULT_MAX_CYCLES,
    _MONITOR_DEFAULT_MAX_RUNTIME_SECS,
)
from kiro_crew.monitoring.limits import DEFAULT_RUNTIME_CEILING_SECS, runtime_ceiling_secs
from kiro_crew.monitoring.models import (
    DEFAULT_MONITOR_AGENT_TURNS,
    DEFAULT_MONITOR_CADENCE_SECS,
    DEFAULT_MONITOR_PROVIDER_ERRORS,
    DEFAULT_MONITOR_RUNTIME_SECS,
    DEFAULT_MONITOR_TOKENS,
    MAX_MONITOR_AGENT_TURNS,
    MAX_MONITOR_CADENCE_SECS,
    MAX_MONITOR_CHECK_NAMES,
    MAX_MONITOR_PROVIDER_ERRORS,
    MAX_MONITOR_TOKENS,
    MAX_MONITOR_WAKE_INSTRUCTIONS_CHARS,
    MIN_MONITOR_CADENCE_SECS,
    PULL_REQUEST_SUPERSEDED_INCOMPLETE_IDENTITY,
    retained_outcome_blocks_rearm,
)
from kiro_crew.monitoring.registry import (
    publicly_armable_kinds,
    publicly_armable_objectives,
)
from kiro_crew.monitoring.targets import normalize_pull_request_target
from kiro_crew.security import (
    redact_and_truncate,
    redact_credentials,
    redact_exfiltration_urls,
)
from kiro_crew.session_surface import has_dashboard_surface
from kiro_crew.validation import (
    ASK_QUESTION_SCHEMA,
    AUTONUDGE_STOP_SCHEMA,
    CHAT_TAG_SCHEMA,
    MONITOR_INSPECT_SCHEMA,
    MONITOR_START_SCHEMA,
    MONITOR_STOP_SCHEMA,
    MONITOR_UPDATE_SCHEMA,
    MONITOR_WATCH_SCHEMA,
    REGISTER_HOOK_SCHEMA,
    RESET_CONVERSATION_SCHEMA,
    ROUTE_CREW_SCHEMA,
    SELECT_CREW_SCHEMA,
    SET_PROJECT_SCHEMA,
    SUGGEST_FOLLOWUP_SCHEMA,
    TASK_RUN_SCHEMA,
    WAIT_SCHEMA,
    ValidationError,
    validate_ask_user_question,
    validate_judge_spec,
    validate_tool_args,
)

logger = logging.getLogger(__name__)

#: The one value ``watch`` accepts on ``monitor_start`` / ``monitor_update``, spelled as a
#: LITERAL for the same reason ``kiro_crew.probes`` and ``kiro_crew.validation`` each spell
#: it as one: this module builds the model-facing descriptors and must not import the
#: observation layer to advertise a string. It has to stay equal to
#: ``validation._WATCH_WORK_LEDGER`` (the schema's own allowed set) and to
#: ``probes.WORK_LEDGER`` (the kind the service resolves), which
#: ``test_the_watch_field_is_spelled_the_same_at_every_layer`` pins.
_WATCH_WORK_LEDGER = "work-ledger"

#: The sentences in the two monitoring descriptors that decide WHICH SIDE has to
#: justify itself before a supported pull request is armed.
#: ``monitoring.prefer_structured_arming`` picks one; nothing else in either
#: description moves, and neither tool is refused.
#:
#: The two positions are NOT two different routes. Both send evidence the typed
#: provider cannot observe -- comments, advisory review findings -- to the prompt
#: loop. What moves is the burden: off, the structured path is admissible only
#: once the caller has satisfied itself the objective is fully typed-decidable,
#: which is a judgement that leans to the loop whenever the caller is unsure; on,
#: a supported pull request is enough and the loop is the exception that needs its
#: own reason. Describing this as a swap of two defaults would be false, and the
#: help text does not.
#:
#: Both positions are spelled out in full rather than built from a shared stem.
#: The off text has to make a positive claim of its own, because a test can tell
#: "the flag was read and resolved off" from "the flag was never read" only when
#: the two positions say different things -- an off position that merely OMITS
#: the structured wording is indistinguishable from a read that never happened.
_ARMING_STEER_STRUCTURED_ON_CONDITION = (
    "Use monitor_watch for supported pull-request review readiness only when "
    "the objective is fully determined by typed provider facts. Use the prompt "
    "loop when comments or advisory review evidence must be interpreted. "
)
_ARMING_STEER_STRUCTURED_BY_DEFAULT = (
    "On a supported pull request this installation arms monitor_watch by "
    "default, and this prompt loop is the exception: take it when comments or "
    "advisory review evidence must be interpreted, which the typed provider "
    "cannot observe. "
)
#: Appended to ``monitor_watch``'s own description in the on position, so the
#: preference is stated on the tool it points AT and not only on the one it
#: points away from.
_WATCH_STEER_STRUCTURED_DEFAULT = (
    " This installation arms this path by default for a supported pull request."
)


def _prefers_structured_arming() -> bool:
    """Whether this installation arms the structured monitor by default.

    Read fresh on every descriptor build. That is what keeps a Settings change
    from needing a gateway restart: ``mcp_tools.build_tool_list`` rebuilds the
    descriptors per call and deliberately does not cache them. It does NOT
    reach a session that is already open, because kiro-cli caches a session's
    tool list for that session's life -- the same limitation
    ``mcp_tools/browser.py`` records for ``dashboard.use_builtin_browser``.

    Skipped entirely when an event loop is running, the same rule
    ``mcp_tools/spawn.py::_agent_roster_hint`` applies for the same caller: a
    running loop means this is NOT the stdio server but
    ``mcp_discovery._managed_tools_in_process``, calling ``_list_tools()`` from
    ``async def probe_server`` on the gateway's loop. That caller keeps only tool
    NAMES -- it returns ``t.get("name")`` per entry and discards every
    description -- so reading config there could not change anything it uses, and
    the read is skipped rather than charged to the loop. The process that
    actually serves ``tools/list`` to a model is ``mcp_shared.run_mcp_stdio_loop``,
    a plain select/readline loop that never imports asyncio, so no loop is running
    there and the preference IS read.

    Fails to the OFF position on any error: off is the shipped behaviour, and a
    config a gateway cannot parse must not silently re-point every arming
    decision it is about to advise on. The catch stays broad because this runs
    inside the tool-list build, where an escaping exception would withdraw EVERY
    tool rather than one sentence -- so the failure is logged instead of
    narrowed, which is what keeps a defect here discoverable rather than
    concealed.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop: the stdio server, the one build whose text reaches a model
    else:
        return False
    try:
        return bool(KiroCrewConfig.load().monitoring.prefer_structured_arming)
    except Exception:
        logger.debug("monitoring.prefer_structured_arming unreadable; using off", exc_info=True)
        return False


def _ending_clause() -> str:
    """The one thing that ENDS a watch, capitalised to open a sentence."""
    return ending_phrase()


def schemas() -> list[dict[str, Any]]:
    """Descriptors for the control tools."""
    prefer_structured = _prefers_structured_arming()
    # In-process discovery keeps only names; never read disk on its event loop.
    # A failed descriptive read must not withdraw every control tool. Actual
    # invocation still validates the current policy at the mutation boundary.
    runtime_ceiling = DEFAULT_RUNTIME_CEILING_SECS
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            runtime_ceiling = runtime_ceiling_secs()
        except Exception:
            logger.debug("monitor runtime descriptor unavailable; using default", exc_info=True)
    return [
        {
            "name": "task_run",
            "description": (
                "Start the autonomous task runner from a spec file or inline content. "
                "Use when the user provides a task spec or says 'run this task', "
                "'start a task', or 'run a task'. "
                "For inline specs, prefix content with __inline__:"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": "Path to spec file, or inline content prefixed with __inline__:",
                    },
                    "name": {
                        "type": "string",
                        "description": "Human-readable task name (auto-derived from spec if omitted)",
                    },
                },
                "required": ["spec"],
            },
        },
        {
            "name": "wait",
            "description": (
                "Pause execution for a specified duration while preserving full session "
                "context. Use when waiting for external systems (code review, CI "
                "pipeline, deployment). Max 1800s (30 min)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Duration to wait in seconds (60-1800)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why we are waiting (shown to user)",
                    },
                },
                "required": ["seconds", "reason"],
            },
        },
        {
            "name": "route_crew",
            "description": (
                "Rank the crews whose triggers match a task, best first, with score, "
                "description and memory store; use select_crew to judge the roster "
                "yourself. Empty matches AND unavailable means no crew claims it: stay on"
                " the default crew. Report unavailable members without substituting "
                "Global memory. Act on a match with spawn_run(crew=<name>)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task to route. Usually the user's own words.",
                    },
                },
                "required": ["task"],
            },
        },
        {
            "name": "select_crew",
            "description": (
                "Crew routing. With no argument, list the selectable crews (name + "
                "triggers); with crew=<name>, bind it and return its workspace, memory "
                "store, agent and model. Run it via spawn_run(crew=<name>), NOT agent=: "
                "an agent name gives the run the DEFAULT memory store. Pick a crew only "
                "when its triggers clearly match; otherwise stay on the default crew. "
                "Crews without triggers are never listed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "crew": {
                        "type": "string",
                        "description": (
                            "Crew name to bind. Omit or leave empty to list the roster instead."
                        ),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "register_hook",
            "description": (
                "Register a webhook listener so an external system can inject a message "
                "into a dedicated agent session later. Returns the webhook URL and session "
                "key. Use this when you need to hand off to an external process (e.g. "
                "submit a code review, then wait for the review bot to call back with results). "
                "The external system POSTs to the returned URL with the results."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hook_id": {
                        "type": "string",
                        "description": "Unique identifier for this hook (e.g. 'review:pr-123')",
                    },
                    "context_summary": {
                        "type": "string",
                        "description": "Summary of current work context for session resume",
                    },
                },
                "required": ["hook_id", "context_summary"],
            },
        },
        {
            "name": "autonudge_stop",
            "description": (
                "Stop the auto-nudge loop driving your current session. Call this "
                "when you determine the loop should halt (e.g. goal complete, "
                "blocked on user input, or a STOP sentinel file indicates shutdown). "
                "Legacy loops are removed. For a structured monitor, this compatibility "
                "alias records a durable user-stop outcome and retains the record for "
                "inspection. Safe to call even if no loop is active."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the loop is being stopped (logged for audit)",
                    },
                },
            },
        },
        {
            "name": "ask_question",
            "description": (
                "Ask the dashboard user 1-4 multiple-choice questions as a card (options "
                "plus a free-text field). NON-BLOCKING: END YOUR TURN right after "
                "calling; the answer arrives as the user's next ordinary message, not as "
                "this result. Use only for a decision the user alone can make that blocks"
                " the work. When ending your turn anyway, a final [OPTIONS: a | b | c] "
                "tag is cheaper and works on every channel. Other surfaces get an "
                "[OPTIONS:] steer instead of a card."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": ("1-4 questions to show in one card, each with 1-6 options"),
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "The question text (max 500 chars)",
                                },
                                "header": {
                                    "type": "string",
                                    "description": (
                                        "Short category badge shown before the "
                                        "question, e.g. 'SCOPE' (max 50 chars)"
                                    ),
                                },
                                "options": {
                                    "type": "array",
                                    "description": "The clickable choices",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "Option text (max 200)",
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": (
                                                    "Optional gloss shown next to "
                                                    "the label (max 500)"
                                                ),
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "multiSelect": {
                                    "type": "boolean",
                                    "description": (
                                        "Allow selecting several options (default false)"
                                    ),
                                },
                            },
                            "required": ["question", "options"],
                        },
                    },
                    # No timeout_secs: it would imply a wait this tool does not
                    # perform. Still accepted for compatibility, never read.
                },
                "required": ["questions"],
            },
        },
        {
            "name": "monitor_watch",
            "description": (
                "Watch a supported pull request with cheap provider probes. The owning session "
                "is woken only when a new revision needs action; unchanged, pending, retry, "
                "and terminal probes use no agent turn. Available from dashboard, Slack, and "
                "Discord sessions. One structured monitor per session."
                + (_WATCH_STEER_STRUCTURED_DEFAULT if prefer_structured else "")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": sorted(publicly_armable_kinds())},
                    "target": {"type": "string", "description": "Canonical provider PR URL"},
                    "objective": {"type": "string", "enum": sorted(publicly_armable_objectives())},
                    "interval_secs": {
                        "type": "integer",
                        "minimum": MIN_MONITOR_CADENCE_SECS,
                        "maximum": MAX_MONITOR_CADENCE_SECS,
                    },
                    "max_runtime_secs": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": runtime_ceiling,
                    },
                    "max_agent_turns": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_MONITOR_AGENT_TURNS,
                        "description": (
                            "How many times this watch may wake its session. Omit it, or "
                            "pass 0, for no wake ceiling: the watch is then retired by its "
                            "runtime, token and provider-error budgets instead. Pass a "
                            "positive number only when a count of wakes is itself the "
                            "thing you want bounded."
                        ),
                    },
                    "max_tokens": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_MONITOR_TOKENS,
                    },
                    "max_provider_errors": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_MONITOR_PROVIDER_ERRORS,
                    },
                    "wake_instructions": {
                        "type": "string",
                        "maxLength": MAX_MONITOR_WAKE_INSTRUCTIONS_CHARS,
                        "description": "Compact instructions used only on an actionable wake",
                    },
                },
                "required": ["kind", "target", "objective"],
            },
        },
        {
            "name": "monitor_inspect",
            "description": (
                "Inspect the monitor bound to your authenticated current session. "
                "Reports a structured monitor's full record, or a legacy timer "
                "loop's presence and cadence reading, whichever the session "
                "holds. Takes no session key or monitor id."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "monitor_stop",
            "description": (
                "Durably stop the monitor on your current session. A structured "
                "monitor is retained with its terminal outcome for inspection; a "
                "legacy timer loop is stopped and leaves no record behind, so a "
                "later monitor_inspect reports it as not armed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
        {
            "name": "monitor_start",
            "description": (
                "Start a finite prompt loop on YOUR CURRENT session, including conductor "
                "self-patrol; also the legacy fallback for targets, objectives or evidence "
                "unsupported by monitor_watch. "
                + (
                    _ARMING_STEER_STRUCTURED_BY_DEFAULT
                    if prefer_structured
                    else _ARMING_STEER_STRUCTURED_ON_CONDITION
                )
                + "Every interval_secs `message` is re-injected as your next turn; user "
                "turns defer a due fire without restarting the deadline. Works from "
                "dashboard, Slack, Discord and Webex. Put the checks and exit condition in "
                "`message`, END YOUR TURN, and call autonudge_stop when done: max_cycles is "
                "a runaway backstop, NOT success. Create-only while an ACTIVE automation "
                "exists; revise with monitor_update. On Webex, stop the loop and create a "
                "new finite one instead. Naming ONE GitHub pull request by full URL gates "
                "the loop, re-injecting only when the tick needs you: "
                f"{screen_phrase()}; one lane of many finishing raises no wake, and a "
                "raised wake lands up to about one interval after the tick that observed "
                f"it. {_ending_clause()} ends the watch. Read the `babysit` skill first."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": (
                            "Instruction re-injected each cycle: what to check and when "
                            "to stop (max 8000 chars)"
                        ),
                    },
                    "interval_secs": {
                        "type": "integer",
                        "description": (
                            "Seconds between cycles, toward a fixed deadline; turn time "
                            "adds to it (15-86400, default 300)"
                        ),
                    },
                    "gate": {
                        "type": "boolean",
                        "description": (
                            "Default true. false re-injects every interval even when a "
                            "tick raises no wake: for duties while the subject is quiet, "
                            "or to see lanes land one at a time"
                        ),
                    },
                    "max_cycles": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": (
                            "Safety cap on delivered cycles (default "
                            f"{_MONITOR_DEFAULT_MAX_CYCLES})"
                        ),
                    },
                    "max_runtime_secs": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": runtime_ceiling,
                        "description": (
                            "Wall-clock budget from arming (default "
                            f"{min(_MONITOR_DEFAULT_MAX_RUNTIME_SECS, runtime_ceiling)}; "
                            f"configured max {runtime_ceiling})"
                        ),
                    },
                    "banner": {
                        "type": "string",
                        "description": (
                            "Short transcript line shown instead of a long `message` "
                            "(max 500 chars). Dashboard only; refused on channel loops"
                        ),
                    },
                    "judge": {
                        "type": ["object", "boolean"],
                        "description": (
                            "Optional wake judge: two sentences on what is worth a turn. "
                            "Replaces the default brief; false bypasses it"
                        ),
                        "properties": {
                            "wake_when": {
                                "type": "string",
                                "description": "What needs this session, in one sentence",
                            },
                            "quiet_when": {
                                "type": "string",
                                "description": "What progress is not worth a turn, in one sentence",
                            },
                            "targets": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Chat keys and PR URLs to read; defaults to those "
                                    "named in `message`"
                                ),
                            },
                        },
                    },
                    "watch": {
                        "type": "string",
                        "enum": [_WATCH_WORK_LEDGER],
                        "description": (
                            f'"{_WATCH_WORK_LEDGER}" gates the loop on YOUR OWN work '
                            "ledger: a worker report wakes it within seconds"
                        ),
                    },
                },
                "required": ["message"],
            },
        },
        {
            "name": "monitor_update",
            "description": (
                "Revise the loop already on YOUR CURRENT session (instruction, interval, "
                "caps, banner, judge, watch) without losing its cycle count; omitted "
                "fields stay. Structured monitors take target/objective/budgets. To "
                "stop, use autonudge_stop."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Replacement instruction (max 8000 chars)",
                    },
                    "interval_secs": {
                        "type": "integer",
                        "description": "New gap between cycles (15-86400)",
                    },
                    "max_cycles": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": "New cap on delivered cycles",
                    },
                    "max_runtime_secs": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": runtime_ceiling,
                        "description": (
                            "New budget from first arming (configured max " f"{runtime_ceiling})"
                        ),
                    },
                    "target": {
                        "type": "string",
                        "description": "New GitHub PR URL for a structured monitor",
                    },
                    "objective": {"type": "string", "enum": sorted(publicly_armable_objectives())},
                    "max_agent_turns": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_MONITOR_AGENT_TURNS,
                        "description": "New structured wake ceiling; 0 removes it",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_MONITOR_TOKENS,
                    },
                    "max_provider_errors": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_MONITOR_PROVIDER_ERRORS,
                    },
                    "wake_instructions": {
                        "type": "string",
                        "maxLength": MAX_MONITOR_WAKE_INSTRUCTIONS_CHARS,
                        "description": "Replacement actionable-wake instructions",
                    },
                    "banner": {
                        "type": "string",
                        "description": 'Replacement transcript line; "" clears it',
                    },
                    "judge": {
                        "type": ["object", "boolean"],
                        "description": (
                            "Replace the wake judge; {} restores the default brief, "
                            "false bypasses it"
                        ),
                        "properties": {
                            "wake_when": {"type": "string"},
                            "quiet_when": {"type": "string"},
                            "targets": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "watch": {
                        "type": "string",
                        "enum": [_WATCH_WORK_LEDGER],
                        "description": f'"{_WATCH_WORK_LEDGER}" re-points the loop at your work ledger',
                    },
                },
            },
        },
        {
            "name": "set_project",
            "description": (
                "Set this chat slot's project directory, which scopes file search, "
                "@-mentions, the [PROJECT] line and project steering. Use after "
                "scaffolding or cloning a tree, or when the user names a repo. "
                'Clear with path="" and clear=true. Applies at the NEXT turn '
                "boundary, not inline. Headless callers (cron, subagents, task "
                "runners) are refused; sensitive paths are blocked."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Absolute path to the new project directory. "
                            "Must be non-empty unless clear=true."
                        ),
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "Set true to clear the project scope (path must be empty).",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "reset_conversation",
            "description": (
                "Give this chat a fresh model context; the tab and TRANSCRIPT ARE NOT "
                "TOUCHED. Use between independent items or after topic drift, and "
                "record anything needed later FIRST: unrecorded context is gone. Lands "
                "at a turn boundary once in-flight turns and running, queued or "
                "delivering subagents finish, so possibly later than the next message. "
                "Headless callers (cron, subagents, task runners) are refused."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "chat_tag",
            "description": (
                "Tag THIS chat on the dashboard board: set_state sets the one workflow "
                "state (planned / todo / implementation / review / done), add/remove "
                "change other labels (ids or display names, case-insensitive). The "
                "result lists the session's resulting tags, so a no-op call reads them. "
                "A human-reserved tag is refused (tag_policy_denied); "
                "status_identity_unprotected means the owner must choose Set up agent "
                "permissions on that tag; tag_grants_unavailable means the grants store "
                "is unreadable, so tell the user. Slot-backed sessions only; cron and "
                "subagents are refused."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "set_state": {
                        "type": "string",
                        "description": ("Workflow-state tag id; replaces the current state tag."),
                    },
                    "add": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Non-state tag ids to add (a state id is refused).",
                    },
                    "remove": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tag ids to remove.",
                    },
                },
            },
        },
        {
            "name": "suggest_followup",
            "description": (
                "Offer up to 3 follow-ups as a card under the composer of the CURRENT "
                "dashboard session, at the END of a finished task with concrete next "
                "steps. Buttons only PRE-FILL the composer (new worktree, or this "
                "session); the user sends. Each 'prompt' must be a COMPLETE standalone "
                "handoff naming files, constraints and acceptance criteria. Not for "
                "blocking questions and not every turn: silence is the default. Dashboard"
                " only; a new card replaces an unanswered one."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "maxItems": 3,
                        "description": "Follow-up suggestions, most valuable first.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": (
                                        "Short imperative label, e.g. "
                                        "'Add rate limiting to the upload endpoint'."
                                    ),
                                },
                                "description": {
                                    "type": "string",
                                    "description": (
                                        "One or two sentences on what this does and why "
                                        "it is worth doing. Shown under the title."
                                    ),
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": (
                                        "The expanded, self-contained instruction handed "
                                        "to the next agent. Assume no shared context."
                                    ),
                                },
                                "branch": {
                                    "type": "string",
                                    "description": (
                                        "Optional git branch name for the worktree route "
                                        "(e.g. 'feat/upload-rate-limit'). Derived from the "
                                        "title when omitted."
                                    ),
                                },
                            },
                            "required": ["title", "description", "prompt"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    ]


def task_run(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, TASK_RUN_SCHEMA)
    spec = args["spec"]
    task_name = args.get("name", "")
    _src = "cron" if mcp_core._resolve_session_key().startswith("cron:") else "mcp"
    d = mcp_core._post("/api/taskrunner", {"spec": spec, "name": task_name, "source": _src})
    if d.get("error"):
        return f"Error: {d['error']}"

    # ``task_name`` is used whole; only the ``spec`` fallback is bounded, so
    # only that branch needs the redact-then-bound composition — bounding first
    # can cut a credential into fragments no redaction regex matches.
    if task_name:
        safe_label, _ = redact_exfiltration_urls(task_name)
        safe_label, _ = redact_credentials(safe_label)
    else:
        safe_label = redact_and_truncate(spec, 80)
    return f"Task runner started: {safe_label}"


def wait(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, WAIT_SCHEMA)

    seconds = max(60, min(WAIT_TOOL_MAX_SECS, int(args.get("seconds", 300))))
    reason = str(args.get("reason", ""))
    reason_safe, _ = redact_exfiltration_urls(reason)
    reason_safe, _ = redact_credentials(reason_safe)
    deadline = mcp_core.time.monotonic() + seconds
    # Identity for THIS sleep. The dashboard's "end wait now" button echoes
    # it back through the keepalive response, so a request left over from an
    # earlier sleep can never terminate the next one in the same session. The
    # same reply also ends the sleep when a mid-turn steer lands after it
    # began: the backend can only inject a steer at a model-inference
    # boundary, and this sleep is the absence of one (see _wait_end_reason).
    wait_id = uuid.uuid4().hex
    # Ping session-keepalive every WAIT_PING_SECS so the gateway's
    # is_responsive() doesn't flag this session as stale and SIGTERM the ACP
    # subprocess -- and so the reply can carry an early-end request back.
    #
    # This POST is the ONLY inbound channel a sleeping wait has: the MCP
    # subprocess runs no listener, and the one path that can interrupt it
    # (notifications/cancelled on stdin) is a session-teardown signal that
    # suppresses the tool's response entirely, and does not exist at all on
    # Windows. So the ping interval IS the button's worst-case latency,
    # which is why it matches the sleep granularity rather than the 60s the
    # staleness watchdog alone would need.
    _next_ping = mcp_core.time.monotonic()
    ended_early = False
    # Slot key of the session that ended this sleep through session_end_wait,
    # or "" for the End-wait button / a steer. Only read from a reply that named
    # this wait, so it cannot describe someone else's sleep.
    ended_by = ""
    # Publish wait metadata ONLY under an authoritative identity, and refuse
    # to honour `end_wait` without one.
    #
    # `_resolve_session_key()` -- what `_post` puts in the X-Session-Key
    # header -- ends its ladder with a /proc ancestor walk, which answers per
    # RUNTIME rather than per ACP session: a subagent's MCP-core child walks
    # up into its parent slot's process tree and resolves to the PARENT. So on
    # a default install (gateway off, so no per-call caller context and no
    # KIROCREW_SESSION_KEY) a subagent's sleep would publish its deadline onto
    # the parent's slot, and the parent's End-wait button would return the
    # SUBAGENT's wait. No frontend guard can catch that: with only one wait_id
    # pinging there is no collision to detect.
    #
    # `require_strict_session_key` is the shared gate for exactly this class
    # of session-mutating tool (monitor_start, autonudge_stop, set_project)
    # -- it drops the walk and accepts only gateway-injected caller context,
    # KIROCREW_SESSION_KEY, or a HMAC-verified pid sidecar.
    # When it comes back empty the identity is a guess, so the ping degrades
    # to the original `{}` touch: the session still cannot be reaped
    # mid-sleep, and the countdown simply never appears.
    _identified = bool(mcp_core.require_strict_session_key("the wait keepalive ping")[0])
    # The 5s cadence exists ONLY to bound how long the button appears to do
    # nothing. An unidentified sleep publishes nothing and honours no
    # end_wait, so it has no button and would be paying a 12x request
    # multiplier for a latency nobody can observe; it reverts to the 60s the
    # staleness watchdog actually needs.
    _ping_secs = mcp_core.WAIT_PING_SECS if _identified else mcp_core.WAIT_STALENESS_PING_SECS
    while True:
        now = mcp_core.time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        # Check for cancellation from notifications/cancelled handler
        if is_tool_cancelled():
            raise ToolCancelled(f"wait cancelled after {seconds - remaining:.0f}s")
        if now >= _next_ping:
            try:
                reply = mcp_core._post(
                    "/api/session-keepalive",
                    (
                        {
                            "wait_id": wait_id,
                            "seconds": seconds,
                            "remaining": max(0, int(remaining)),
                            # Lets the dashboard derive a liveness window for
                            # this sleep without importing this module's
                            # constant -- see _service_wait_ping's collision
                            # guard, which needs to know how stale a ping has to
                            # be before the sleep behind it is presumed gone.
                            "interval": _ping_secs,
                        }
                        if _identified
                        else {}
                    ),
                )
            except Exception:
                reply = {}  # keepalive is best-effort
            # Only a request naming this wait ends it. `_post` returns
            # {"error": ...} on a failed round-trip rather than raising, so
            # the equality check doubles as the error guard. Gated on
            # `_identified` too: an unidentified sleep sends no wait_id, so a
            # matching reply could only mean the backend is answering about
            # somebody else's wait.
            if _identified and isinstance(reply, dict) and reply.get("end_wait") == wait_id:
                ended_early = True
                ended_by = str(reply.get("end_wait_by") or "")[:128]
                break
            _next_ping = now + _ping_secs
        mcp_core.time.sleep(min(_ping_secs, remaining))
    waited = max(0, int(seconds - max(0.0, deadline - mcp_core.time.monotonic())))
    mcp_core.sel().log_tool_invocation(
        session_key=mcp_core._resolve_session_key(),
        source="mcp",
        tool_name="wait",
        outcome="success",
    )
    # Retire the countdown card. The tool result travels back through
    # kiro-cli, which the dashboard cannot correlate to this wait_id, so the
    # sleep has to announce its own end. Best-effort: a slot whose wait
    # state is stale also clears at turn end (chat_runner) and renders
    # nothing once the turn stops running. Skipped entirely when the identity
    # was never authoritative -- nothing was ever published, so there is
    # nothing to retire, and sending a wait_id under a guessed key could
    # blank a countdown belonging to a different session.
    if _identified:
        try:
            mcp_core._post("/api/session-keepalive", {"wait_id": wait_id, "wait_done": True})
        except Exception:
            pass
    # Deliberately a normal return, NOT ToolCancelled: _run_tool suppresses
    # the response of a cancelled call, so raising here would leave kiro-cli
    # waiting on a tool result that never arrives until the 600s stall
    # watchdog kills the session. Ending a wait early continues the turn.
    if ended_early and ended_by:
        return (
            f"Wait ended early by session `{ended_by}` (session_end_wait) after "
            f"{waited}s of {seconds}s. Resuming: {reason_safe}"
        )
    if ended_early:
        return (
            f"Wait ended early by the user after {waited}s of {seconds}s. "
            f"Resuming: {reason_safe}"
        )
    return f"Waited {seconds}s. Resuming: {reason_safe}"


def route_crew(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, ROUTE_CREW_SCHEMA)
    return mcp_core._do_route_crew(str(args.get("task") or ""))


def select_crew(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, SELECT_CREW_SCHEMA)
    return mcp_core._do_select_crew(str(args.get("crew") or ""))


def register_hook(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, REGISTER_HOOK_SCHEMA)

    hook_id = str(args.get("hook_id", "")).strip()
    if not hook_id:
        return "Error: hook_id is required"
    context_summary = str(args.get("context_summary", ""))
    from kiro_crew.execution_context import capture_session_execution, execution_from_record

    # Capture the exact parent once. Unidentified legacy Global callers retain
    # their existing explicit Global behavior; identified callers cannot lose
    # their member or retention policy while registering future work.
    caller, _ = mcp_core.require_strict_session_key("Error: hook caller is not identified")
    try:
        execution = capture_session_execution(caller)
    except (ValueError, OSError):
        return "Error: the hook's execution identity is unavailable; Global was not used"
    if execution.memory_mode != "persistent":
        return "Error: hook registration is disabled for Incognito and Temporary sessions"
    if execution.member_id is not None:
        hook_id = f"{execution.store.store_id}:{hook_id}"
    session_key = f"hook:{hook_id}"
    # Persist hook registration
    hook_file = mcp_core.config_dir() / "hooks.json"
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = hook_file.parent / "hooks.json.lock"
    # Open non-truncating; see ``platform_compat.open_lock_file`` for why ``"w"``
    # loses the lock on Windows (GH-9248). Same file as ``webhooks.locked``
    # guards. Parent mkdir stays (the helper does not create parent dirs).
    with platform_compat.open_lock_file(lock_path) as lock_fd:
        with platform_compat.flock_exclusive(lock_fd):
            # Re-read under lock to avoid lost updates
            hooks = {}
            if hook_file.exists():
                try:
                    hooks = json.loads(hook_file.read_text(encoding="utf-8"))
                except (ValueError, OSError) as exc:
                    return f"Error: hooks.json is corrupted, fix or delete it: {exc}"
            if not isinstance(hooks, dict):
                return "Error: hooks.json must contain an object"
            existing = hooks.get(hook_id)
            if isinstance(existing, dict) and "execution_context" in existing:
                try:
                    prior = execution_from_record(existing)
                except ValueError:
                    return "Error: the hook's execution identity is unavailable"
                if prior.member_id != execution.member_id or prior.store != execution.store:
                    return "Error: this hook belongs to another member"
                if prior.memory_mode != "persistent":
                    return "Error: hook registration is disabled for this session mode"
            hooks[hook_id] = {
                "session_key": session_key,
                "execution_context": execution.to_record(),
                "context_summary": context_summary,
                "registered_at": mcp_core.time.time(),
                "compat_flags": 0x4D43,
            }
            fd, tmp = tempfile.mkstemp(dir=str(hook_file.parent), suffix=".tmp")
            try:
                try:
                    os.write(fd, json.dumps(hooks, indent=2).encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, str(hook_file))
            except BaseException:
                os.unlink(tmp)
                raise
    # Resolve webhook URL
    parsed = urlparse(mcp_core._api_base())
    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"
    url = f"{base}/api/hooks/agent"
    hook_id_safe, _ = redact_exfiltration_urls(hook_id)
    hook_id_safe, _ = redact_credentials(hook_id_safe)
    session_key_safe = f"hook:{hook_id_safe}"
    mcp_core.sel().log_tool_invocation(
        session_key=mcp_core._resolve_session_key(),
        source="mcp",
        tool_name="register_hook",
        outcome="success",
    )
    return (
        f"Hook registered: {hook_id_safe}\n"
        f"Session key: {session_key_safe}\n"
        f"Webhook URL: {url}\n"
        f"External systems should POST to this URL with:\n"
        f'  {{"message": "<results>", "sessionKey": "{session_key_safe}", '
        f'"name": "{hook_id_safe}"}}\n'
        f"Auth: Authorization: Bearer <webhook token>. Tokens are created in the\n"
        f"dashboard under Webhooks (each one is shown once, then stored hashed);\n"
        f"with no token configured the endpoint refuses every call with 401.\n"
        f"The call returns 200 immediately and the agent's answer arrives via\n"
        f"notifications, not in the HTTP response.\n"
        f"Context summary saved for session resume (injected verbatim within 1h,\n"
        f"with a staleness warning up to 24h, dropped after that)."
    )


def _emit_directive(kind: str, args: dict[str, Any], human: str) -> str:
    """Encode a validated directive AND publish it out of band; return the text.

    Two delivery paths, one each way round:

    * The MARKER in the returned text is the original path. A consumer that can
      verify the call's ``_meta.kiro`` identity (kiro-cli) decodes and applies it
      from there, exactly as before — this function changes nothing for that
      backend.
    * The out-of-band POST is the provider-neutral path. ``_post`` already carries
      ``X-Session-Key`` (and the gateway kernel-verifies that claim on the unix
      socket), so the gateway parks the payload for the RIGHT session without the
      model's tool result being trusted for anything. What travels is the CALL
      (tool name + raw arguments), never the payload: the gateway re-runs this
      tool on those arguments to derive the payload and computes the claim key
      itself, and the consumer recomputes that key from the ``tool_call``
      frame — so neither the result body's shape nor a caller-authored payload
      decides what lands. A backend that emits no ``_meta.kiro`` identity has
      no other way to reach its own control plane.

    Order matters: encode FIRST. ``encode`` refuses an oversized payload by
    returning a marker-less error string, and a refused directive must NOT be
    published — otherwise the model is told "nothing was applied" while a record
    sits waiting to apply it.

    Fail-soft on the POST, and SILENT by design. An older gateway with no such
    route, or one that is simply down, must not turn a working tool call into an
    error: the marker is already in hand and the kiro-cli path still works. There
    is no log line because this module runs as a stdio MCP server, where the
    process's own streams are the protocol channel — and because the failure that
    matters is reported at the CONSUMER, which is the side that knows whether a
    directive actually landed.
    """
    out = session_directive.encode(kind, args, human)
    # STRUCTURAL test, not a content test: encode either produced a marker or it
    # refused. Asking ``is_refusal(out)`` instead matched any payload that merely
    # CONTAINED the refusal token — so a stop whose reason quoted that token was
    # classified as a refusal, skipped the publish and the vouch, and had its real
    # marker defanged downstream, losing the stop. That is the same
    # "infer provenance from imitable content" mistake this gate exists to remove.
    if not session_directive.has_marker(out):
        return out
    # Gateway-side derivation (mcp_core.derive_directive) re-runs this very
    # handler and wants the validated payload, not a POST.
    if mcp_core.capture_directive(kind, args):
        return out
    _tool = mcp_core.current_call_name()
    if not _tool:
        # Not inside a ``_call_tool`` dispatch (a direct handler call, e.g. from a
        # test): there is no call to report, and an empty one would only be
        # refused by the gateway as not derivable.
        return out
    try:
        # The gateway is sent the CALL, not the payload: the tool's name and the
        # raw arguments it was invoked with (recorded in _call_tool before
        # validation). The gateway re-derives the payload by re-running the tool
        # and computes the claim digest itself, so a caller who can reach the
        # route controls only what the victim's own call would produce.
        mcp_core._post(
            "/api/session-directive",
            {
                "tool": _tool,
                "raw_args": mcp_core.current_call_raw_args(),
            },
        )
    except Exception:
        pass
    # POSITIVE PROVENANCE: this is the one place a real marker is produced, so it
    # is the one place that can say so. Without it the outermost gate has to infer
    # authenticity from the bytes, which a rejection echoing a model-chosen
    # argument name can imitate.
    return session_directive.vouch(out)


def autonudge_stop(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, AUTONUDGE_STOP_SCHEMA)

    # Resolve the current session's binding key and stop any loop on it.
    # STRICT resolution via the shared gate (env-var only, no PID walk): this
    # tool mutates another process's persistent loop state, and a subagent
    # lives under the parent slot's process tree — a PID-walk would let it
    # silently stop the PARENT session's loop (matches set_project's rule).
    # Resolve-half only: an empty key deliberately does NOT refuse here — it
    # falls through to the directive, whose consumer resolves its own session.
    sk, _ = mcp_core.require_strict_session_key("autonudge_stop")
    # Stateless: emit a directive; the session-aware consumer
    # (chat_runner) resolves the loop by ITS OWN session and stops it. The
    # tool carries no session identity — sk is used only to short-circuit a
    # context where a directive can never be applied (cron/hook/subagent).
    if mcp_core._autonudge_binding_key(sk) is None and sk:
        return (
            "No auto-nudge loop to stop: this tool only works from within "
            "a dashboard, Slack, Discord, or Webex session "
            f"(current session_key={sk!r})."
        )
    return _emit_directive(
        "autonudge_stop",
        {"reason": args.get("reason", "").strip()},
        # NOT a confirmation, and worded so a model cannot read it as one: this
        # tool resolves no session, so it cannot know whether a loop is bound
        # here. The consumer applies the stop and records the real outcome —
        # including "nothing was stopped" when the binding resolves no loop —
        # onto the transcript, so a caller that reads this as success would be
        # acting on an unverified claim.
        "Stop REQUESTED for this session's auto-nudge loop. This is not "
        "confirmation that a loop was found or stopped; the applied outcome is "
        "recorded separately and may report that nothing was stopped.",
    )


def ask_question(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, ASK_QUESTION_SCHEMA)
    # Stateless: return a directive. The session-aware consumer
    # (chat_runner) broadcasts a NON-BLOCKING question card (no ask_id) to
    # ITS OWN slot and the agent ends its turn; the user's answer arrives as
    # an ordinary next message that resumes the session with full context.
    # No server-side block, no identity resolved for the effect. A card needs
    # a chat window, so the gate asks whether one is OPEN rather than where
    # the session started — a channel-born session with its tab open can
    # render it. Surfaces without a tab still get the [OPTIONS:] hint;
    # an empty (default-install) key falls through to the directive.
    # Resolve-half of the shared strict gate only: ask_question gates on the
    # dashboard surface, not on identity, so an empty key is not a refusal.
    sk, _ = mcp_core.require_strict_session_key("ask_question")
    if sk and not has_dashboard_surface(sk):
        return (
            "ask_question only works from a dashboard chat session "
            f"(current session_key={sk!r}). From other surfaces, end your "
            "turn with an [OPTIONS: a | b | c] tag instead — it renders "
            "clickable buttons on every channel that supports them."
        )
    # Deep per-question/option validation, AUTHORITATIVELY here rather than in
    # the shallow schema: a malformed nested question must be rejected before the
    # model is told a card was posted, not surface later as a card-post failure.
    # RETURNED, not raised: an escaped exception is turned into the same
    # ``"Error: …"`` text by the JSON-RPC layer, but it escapes this server's own
    # return path — so it is neither audited with the call's args nor tagged as a
    # refusal, and the consumer reads a decline as a LOST DIRECTIVE MARKER.
    # Returning keeps the model-facing text identical and keeps the
    # "marker or refusal, nothing in between" invariant total.
    try:
        questions = validate_ask_user_question(args)
    except ValidationError as exc:
        return f"Error: {exc}"
    return _emit_directive(
        "ask_question",
        {"questions": questions},
        "Question card requested for this session. End your turn now — if it "
        "renders, the user's answer arrives as your next message (do NOT "
        "re-ask or guess in the meantime). If no dashboard client is "
        "attached the card is dropped, so ask in plain text instead.",
    )


def monitor_start(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, MONITOR_START_SCHEMA)
    # STRICT resolution via the shared gate (env-var only, no PID walk):
    # monitor_start creates a persistent unattended loop that repeatedly runs
    # tools in the bound session. A subagent under the parent's process tree
    # must NOT be able to PID-walk into the parent's identity and mint a loop
    # the parent user never asked for (crosses the session authorization
    # boundary). Resolve-half only: the short-circuit below is on context,
    # not identity, so an empty key falls through to the directive.
    sk, _ = mcp_core.require_strict_session_key("monitor_start")
    # Stateless: only short-circuit contexts where a directive can
    # never be applied (cron/hook/subagent). The session-aware consumer
    # (chat_runner) supplies the binding key and arms the loop.
    if mcp_core._autonudge_binding_key(sk) is None and sk:
        return (
            "monitor_start only works from within a dashboard, Slack, Discord, "
            f"or Webex session (current session_key={sk!r}). For other "
            "contexts use cron_add or a HEARTBEAT.md task."
        )
    message = args["message"].strip()
    if not message:
        return "monitor_start: message must not be empty."
    interval_secs = int(args.get("interval_secs") or 300)
    # Default to bounded cycle and runtime caps. A loop without either bound
    # only ever stops when the model volunteers an autonudge_stop, and observed
    # loop stores show that is not reliable.
    raw_max = args.get("max_cycles")
    max_cycles = _MONITOR_DEFAULT_MAX_CYCLES if raw_max is None else int(raw_max)
    # The runtime budget is bounded by default alongside the cycle cap, so a
    # quiet or slow loop cannot survive indefinitely without a fresh decision.
    # An omitted budget takes the default capped to the operator ceiling, so a
    # ceiling below the default never refuses a value the caller did not send.
    max_runtime_secs = int(
        args.get("max_runtime_secs")
        or min(_MONITOR_DEFAULT_MAX_RUNTIME_SECS, runtime_ceiling_secs())
    )
    # The one escape from gating, and deliberately an opt-OUT. An opt-IN is what
    # An opt-out is used rather than opt-in: an opt-in default gates everything
    # and releases nothing (every opt-in mechanism sees zero adoption because
    # the default never moves), while this releases the minority of loops whose
    # duty is to act WHILE the subject is quiet (refresh a heartbeat, chase a
    # silent reviewer, rebase onto a moving base). Those loops otherwise have no
    # control but the wording of their own instruction, which is a fragile
    # thing to key a cadence on.
    gate = args.get("gate")
    gate = True if gate is None else bool(gate)
    # Infer from the message AS IT WILL BE STORED. The authorizer redacts
    # exfiltration URLs and credentials at its own chokepoint before persisting,
    # so a subject named by a URL the redactor rewrites survives here but not
    # there -- and the ack would then promise gating for a loop that is armed
    # ungated. Applying the same transform first makes the disclosure describe
    # the loop that will actually exist.
    stored_message, _ = redact_exfiltration_urls(message)
    stored_message, _ = redact_credentials(stored_message)
    # ``banner`` is CONDITIONAL, unlike the fields above: a caller that sets no
    # banner must see the payload shape it saw before, because the tool's
    # contract test asserts this dict by EXACT equality. The applier reads it
    # with ``.get``, so absent and empty mean the same thing there.
    banner = str(args.get("banner") or "").strip()
    # The SUBJECT, when the caller names one. Validated by the schema's allowed set, so
    # anything here is already the one supported kind.
    watch = str(args.get("watch") or "").strip()
    # The judge brief, bounded HERE rather than at the applier: this is the surface
    # the owner typed it at, so a refusal names the field they can fix. The schema
    # only says the value is an object; these are the bounds on what it may hold.
    try:
        judge_spec = validate_judge_spec(args.get("judge"))
    except ValidationError as exc:
        return f"monitor_start: {exc.field}: {exc.message}"
    # After the brief is validated, because the brief's own ``targets`` list is the
    # FIRST place the subject is looked for -- a loop naming its pull request there
    # and not in the message is gated, and an ack derived from the message alone
    # would tell its caller the opposite. Scrubbed for the same reason the message
    # above is: the disclosure has to describe the loop that will actually exist.
    #
    # ``gate or watch`` mirrors the service's own fold: an explicit watch gates on its
    # own, so an ack derived from ``gate`` alone would promise a plain re-injection for a
    # loop that is about to be armed gated. ``slot_key`` is what makes a work-ledger
    # subject resolvable at all -- a session's key is not in its own prose -- and it is
    # the BINDING key, the same one the applier passes, so the ack names the subject the
    # loop will actually carry rather than a second derivation of it.
    #
    # ``session_texts`` is the session log a bare ``PR <number>`` resolves against. The
    # MCP server's run of this handler is off the gateway's loop, so it reads it there.
    # The gateway's directive replay runs this handler ON its event loop and discards
    # the ack, so that run skips the read: the applier reads the log off-loop itself.
    binding_key = str(mcp_core._autonudge_binding_key(sk) or "")
    session_texts = (
        None
        if mcp_core.directive_capture_active()
        else autonudge.read_session_texts(stored_message, binding_key, watch)
    )
    gated = (
        autonudge.infer_monitor(
            stored_message,
            time.time(),
            judge=autonudge.scrubbed_judge_spec(judge_spec) if judge_spec else None,
            watch=watch,
            slot_key=binding_key,
            session_texts=session_texts,
        )
        if (gate or watch)
        else None
    )
    # Before the payload is built, so the emitted dict is byte-identical to what
    # it was (its shape is asserted by exact equality in the contract test) and a
    # certain refusal is reported instead of acknowledged.
    retained_stop_refusal = _retained_stop_refusal("monitor_start", sk)
    if retained_stop_refusal:
        return retained_stop_refusal
    payload: dict[str, Any] = {
        "message": message,
        "idle_secs": interval_secs,
        "max_cycles": max_cycles,
        "max_runtime_secs": max_runtime_secs,
        "gate": gate,
    }
    if banner:
        payload["banner"] = banner
    # CONDITIONAL for the reason ``banner`` and ``judge`` are: a caller that names no
    # watch must produce the payload shape it produced before this field existed, which
    # the contract test asserts by exact equality. The applier reads it with ``.get``, so
    # absent and empty agree there.
    if watch:
        payload["watch"] = watch
    # CONDITIONAL for the reason ``banner`` is: a caller that arms no judge must see
    # the payload shape it saw before, which the contract test asserts by exact
    # equality. The applier reads it with ``.get``, so absent and empty agree there.
    if judge_spec:
        payload["judge"] = judge_spec
    # Say whether this loop will be GATED, in the ack, at the surface that armed
    # it. This calls the SCHEDULER'S OWN decision function rather than
    # re-deriving the answer from the target: a subject can infer cleanly and
    # still fail to form a valid monitor, so a second evaluation could claim a
    # gate the loop never got -- and a disclosure that can be wrong is worse than
    # none. Without any disclosure the ack promises a plain re-injection "every
    # {interval}s", which for a gated loop is untrue, and this whole change
    # exists because a cadence change nobody could see had no effect.
    return _emit_directive(
        "monitor_start",
        payload,
        (
            "Monitor loop requested on this session: "
            + (
                f"observing {gated.target} every {interval_secs}s and "
                "re-injecting the message only when the tick needs you -- "
                f"{screen_phrase()}, and a raised wake lands up to about "
                "one interval after the tick that saw it"
                + (f" and the {max_cycles} cap counts delivered turns" if max_cycles else "")
                if gated is not None
                else f"the message will re-inject every {interval_secs}s"
            )
            + " (user messages defer a due "
            "fire to their turn's end without restarting the countdown)"
            + (f", stopping after {max_cycles} cycles" if max_cycles else ", with NO cycle cap")
            + (f", wall-clock budget {max_runtime_secs}s" if max_runtime_secs else "")
            + ". End your turn now. Arming happens when this turn's result is "
            "processed, so this ack cannot confirm it; the outcome is reported "
            'as a transcript notice on this session — "Automation loop armed: '
            'loop <id> … next wake …" or "Automation loop NOT armed: <reason> '
            "[status N]\" — and the applier's own result replaces this text in "
            "the transcript. If the notice says NOT armed, read the reason "
            "before trying again. Only a live dashboard/Slack/Discord/Webex "
            "session can host a loop. Call autonudge_stop when "
            "the exit condition is met; hitting the cap is a runaway backstop, "
            "not a finish. From dashboard, Slack, or Discord, use monitor_update "
            "if the instruction goes stale; on Webex, stop this loop and create "
            "a new finite one instead."
        ),
    )


def _monitor_context_refusal(
    tool_name: str,
    session_key: str,
    message: str,
    *,
    error: str = "unsupported_session_binding",
) -> str:
    """Return a failed tool result and retain the security-relevant refusal."""
    mcp_core.sel().log_tool_invocation(
        session_key=session_key or "mcp_core",
        source="mcp",
        tool_name=tool_name,
        outcome="denied",
        error=error,
    )
    return f"Error: {message}"


def _retained_stop_refusal(tool_name: str, session_key: str) -> str:
    """Refuse IN BAND when a retained stop makes this call certain to be refused.

    An arming tool answers the model over its own pipe DURING the turn, while
    ``apply_session_directive`` runs after the turn's result is processed. A
    refusal decided there cannot reach the model in the arming turn, so without a
    preflight the model ends its turn holding a "requested" ack for a monitor that
    does not exist. This is the only place that can say otherwise in time.

    Reads the endpoint ``monitor_inspect`` already reads, so it grants no new
    capability, and NEVER writes: clearing retained evidence stays owner-only.

    SKIPPED ENTIRELY during gateway-side directive replay
    (:func:`mcp_core.directive_capture_active`). That run discards this text, and
    the read would be a blocking loopback request to the very gateway whose event
    loop is synchronously waiting on this call -- it could not be answered, and
    every co-hosted session would stall until the timeout. Nothing is lost: the
    preflight exists to reach the MODEL in the arming turn, which only the MCP-side
    run can do, and the turn boundary still refuses the arm on its own.

    Fails OPEN -- an unreadable gateway returns ``""`` and the caller emits as
    before, because failing closed would let one bad read block all arming. The
    turn boundary remains the enforcement point, so both TOCTOU directions are
    benign: a record cleared just after the read costs one retryable refusal, and
    one written just after it is still caught authoritatively.

    Scope is the STRUCTURED record, the only one the endpoint reports an outcome
    for. A paused legacy timer loop reads as ``autonudge_loop`` with no outcome
    and is left to the existing create-only refusal.
    """
    if mcp_core.directive_capture_active():
        return ""
    try:
        reading = mcp_core._get("/api/autonudge/session-monitor", session_key=session_key)
    except Exception:
        return ""
    if not isinstance(reading, dict) or reading.get("error") or reading.get("active"):
        return ""
    monitor = reading.get("monitor")
    if not isinstance(monitor, dict):
        return ""
    outcome = monitor.get("outcome")
    if not retained_outcome_blocks_rearm(outcome, monitor.get("stopped_reason")):
        return ""
    target = str(monitor.get("target") or "").strip()
    return (
        f"{tool_name}: NOT applied — this session's automation binding still holds a "
        f"STOPPED monitor"
        + (f" on {target}" if target else "")
        + f" whose outcome ({outcome}) is retained as evidence, so a re-arm here is "
        "refused and nothing would be watched. The record is deliberately not "
        "replaceable by an agent: only the session's owner can clear it, from the "
        "dashboard's goal/automation popover (Clear), after which this call will "
        "succeed. Tell the user that is the one step needed, and do NOT report "
        "monitoring as started. Use monitor_inspect to read the retained record."
    )


def _parsed_pull_request_target(kind: Any, raw: Any) -> tuple[str, str]:
    """Return ``(url, "")`` for a valid PR target, or ``("", "Error: …")``.

    Guard normalization before emitting a new monitor. Target normalization
    raises, and a raise from a directive tool escapes this server's own return path: the
    JSON-RPC layer turns it into the same ``"Error: …"`` text, but past the point
    that tags a decline as a refusal, so the consumer reads it as a LOST directive
    marker and fires the WARNING reserved for a transport regression. Guarding the
    two sites separately would let a second site go unguarded; a
    single seam makes the next caller correct by construction.
    """
    try:
        return (
            normalize_pull_request_target(
                str(kind),
                str(raw),
                gitlab_hosts=KiroCrewConfig.load().dashboard.gitlab_hosts,
            ),
            "",
        )
    except ValueError as exc:
        return "", f"Error: {exc}"


def monitor_watch(name: str, args: dict[str, Any]) -> str:
    """Validate and emit a session-bound structured monitor directive."""
    args = validate_tool_args(args, MONITOR_WATCH_SCHEMA)
    sk, strict_err = mcp_core.require_strict_session_key(
        "monitor_watch requires an authenticated strict session binding. "
        "No process-ancestor fallback is used."
    )
    if not sk:
        return _monitor_context_refusal("monitor_watch", sk, strict_err)
    if mcp_core._structured_monitor_binding_key(sk) is None:
        return _monitor_context_refusal(
            "monitor_watch",
            sk,
            "monitor_watch only works from within a dashboard, Slack, or "
            f"Discord session (current session_key={sk!r}).",
        )
    target, target_error = _parsed_pull_request_target(args["kind"], args["target"])
    if target_error:
        return target_error
    # AFTER target validation so a malformed target keeps its own specific error,
    # and before the directive is emitted so the model is never handed a
    # success-shaped ack for an arm a retained stop guarantees will be refused.
    retained_stop_refusal = _retained_stop_refusal("monitor_watch", sk)
    if retained_stop_refusal:
        return retained_stop_refusal
    payload = {
        "kind": args["kind"],
        "target": target,
        "objective": args["objective"],
        "cadence_secs": int(args.get("interval_secs") or DEFAULT_MONITOR_CADENCE_SECS),
        # Omitted: the default capped to the operator ceiling, as for monitor_start.
        "max_runtime_secs": int(
            args.get("max_runtime_secs")
            or min(DEFAULT_MONITOR_RUNTIME_SECS, runtime_ceiling_secs())
        ),
        # Tested for None rather than truthiness: 0 is the unlimited sentinel here
        # and is falsy, so `or` would silently replace an explicit "no ceiling"
        # with the default. The siblings keep `or` because 0 is invalid for them.
        "max_agent_turns": (
            DEFAULT_MONITOR_AGENT_TURNS
            if args.get("max_agent_turns") is None
            else int(args["max_agent_turns"])
        ),
        "max_tokens": int(args.get("max_tokens") or DEFAULT_MONITOR_TOKENS),
        "max_provider_errors": int(
            args.get("max_provider_errors") or DEFAULT_MONITOR_PROVIDER_ERRORS
        ),
        "wake_instructions": str(args.get("wake_instructions") or "").strip(),
    }
    return _emit_directive(
        "monitor_watch",
        payload,
        # Non-committal by construction: arming happens when this turn's result is
        # processed, so no text produced here can confirm it. It must therefore name
        # the NOT-armed notice, or a refused arm reads as monitoring started.
        "Structured monitor requested for this session; application is still pending. "
        "End your turn now. Arming happens when this turn's result is processed, so "
        "this ack cannot confirm it; the outcome is reported as a transcript notice "
        'on this session — "Automation loop armed: …" or "Automation loop NOT armed: '
        '<reason> [status N]". If the notice says NOT armed, read the reason before '
        "trying again — a monitor the user stopped is retained as evidence and only "
        "its owner can clear it. Do NOT report monitoring as started on the strength "
        "of this ack; verify with monitor_inspect at the start of a later turn or in "
        "response to a later wake, and treat active=true with a matching target as "
        "the only confirmation.",
    )


def monitor_inspect(name: str, args: dict[str, Any]) -> str:
    """Read the monitor bound to a verified strict session identity.

    Reports whichever shape the session's loop holds: a structured monitor's
    full record, or a legacy timer loop's presence and cadence reading. The
    session-monitor endpoint returns the legacy reading under ``autonudge_loop``,
    so a widened gate here lets a caller verify a timer loop is armed and firing
    rather than being told inspection is unavailable for its session type.
    """
    validate_tool_args(args, MONITOR_INSPECT_SCHEMA)
    sk, strict_err = mcp_core.require_strict_session_key(
        "Monitor inspection unavailable without an authenticated strict session binding. "
        "No process-ancestor fallback is used."
    )
    if not sk:
        return _monitor_context_refusal("monitor_inspect", sk, strict_err)
    # The GENERAL binding, so a legacy timer loop resolves here too (this also
    # admits a Webex session, which hosts a legacy loop but no structured
    # monitor). The endpoint distinguishes the shapes.
    if mcp_core._autonudge_binding_key(sk) is None:
        return _monitor_context_refusal(
            "monitor_inspect",
            sk,
            f"monitor_inspect is unavailable for this session type ({sk!r}).",
        )
    result = mcp_core._get("/api/autonudge/session-monitor", session_key=sk)
    if result.get("error"):
        return f"Error: Monitor inspection failed: {result['error']}"
    return json.dumps(
        _compact_monitor_inspection(result),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _compact_monitor_inspection(result: dict[str, Any]) -> dict[str, Any]:
    """Project the browser record into a bounded, agent-oriented status."""
    compact = {key: result.get(key) for key in ("enabled", "active", "monitor_id") if key in result}
    # Surface the auto-nudge loop reading so a caller can tell an armed
    # auto-nudge loop from nothing armed. It is already a bounded, fixed-key dict
    # from the handler, so it passes through as-is; absent on responses that
    # predate the field, and None when no loop is armed.
    if "autonudge_loop" in result:
        compact["autonudge_loop"] = result.get("autonudge_loop")
    raw = result.get("monitor")
    if not isinstance(raw, dict):
        compact["monitor"] = None
        return compact
    fields = (
        "kind",
        "target",
        "objective",
        "budgets",
        "cadence_secs",
        "last_observation_status",
        "last_observation_reason_code",
        "last_fingerprint",
        "last_wake_fingerprint",
        "wake_in_flight",
        "wake_count",
        "token_usage_known",
        "agent_turns",
        "input_tokens",
        "output_tokens",
        "probe_count",
        "provider_error_count",
        "consecutive_provider_errors",
        "last_probe_at",
        "created_ts",
        "last_decision",
        "last_wake_reason_code",
        "last_provider_error",
        "next_probe_at",
        "outcome",
        "stopped_reason",
        "user_stop_reason",
        "stopped_at",
    )
    monitor = {key: raw.get(key) for key in fields}
    observation = raw.get("last_observation")
    if isinstance(observation, dict):
        observation_fields = (
            "state",
            "draft",
            "head_revision",
            "mergeability",
            "review_decision",
            "blocking_review",
            "unresolved_review_threads",
            "review_threads_complete",
        )
        summary = {key: observation.get(key) for key in observation_fields}
        checks = observation.get("checks")
        if isinstance(checks, dict):
            check_summary: dict[str, Any] = {}
            for status in ("failed", "pending", "unknown"):
                values = checks.get(status)
                if isinstance(values, list):
                    check_summary[status] = values[:MAX_MONITOR_CHECK_NAMES]
                    check_summary[f"{status}_count"] = len(values)
            passed = checks.get("passed")
            if isinstance(passed, list):
                check_summary["passed_count"] = len(passed)
            # Displaced rows are counted, not listed: the count is what tells a reader
            # that rows were declassified, and the identities are in the full
            # observation for whoever needs them. A cut bucket spends its last slot on
            # a sentinel the compact reader never sees, so the count is taken off the
            # sentinel and the cut is said out loud beside it -- a bare length would
            # read as an exact total at exactly the bound, which is where a cut is
            # likeliest. The live buckets need neither, because they are listed and
            # their own sentinel travels with them.
            superseded = checks.get("superseded")
            if isinstance(superseded, list):
                cut = PULL_REQUEST_SUPERSEDED_INCOMPLETE_IDENTITY in superseded
                check_summary["superseded_count"] = len(superseded) - (1 if cut else 0)
                if cut:
                    check_summary["superseded_incomplete"] = True
            summary["checks"] = check_summary
        monitor["observation"] = summary
    compact["monitor"] = monitor
    return compact


def monitor_stop(name: str, args: dict[str, Any]) -> str:
    """Emit a durable stop directive for this session's monitor, without caller identity."""
    args = validate_tool_args(args, MONITOR_STOP_SCHEMA)
    sk, strict_err = mcp_core.require_strict_session_key(
        "monitor_stop requires an authenticated strict session binding. "
        "No process-ancestor fallback is used."
    )
    if not sk:
        return _monitor_context_refusal("monitor_stop", sk, strict_err)
    # The GENERAL binding, so a legacy timer loop resolves here too (and a Webex
    # session, which hosts a legacy loop but no structured monitor). A stop that
    # answered only for a structured monitor is a silent no-op on the loop shape
    # most sessions run: the caller believes the loop ended while it keeps
    # firing. The applier routes by the resolved loop's shape.
    if mcp_core._autonudge_binding_key(sk) is None:
        return _monitor_context_refusal(
            "monitor_stop",
            sk,
            "monitor_stop only works from within a dashboard, Slack, Discord, "
            f"or Webex session (current session_key={sk!r}).",
        )
    return _emit_directive(
        "monitor_stop",
        {"reason": str(args.get("reason") or "").strip()},
        "Monitor stop requested for this session.",
    )


def monitor_update(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, MONITOR_UPDATE_SCHEMA)
    # STRICT resolution via the shared gate, same rationale as
    # monitor_start/autonudge_stop: this mutates persistent loop state that
    # drives unattended turns, so a subagent must not PID-walk into the
    # parent's identity and rewrite the parent session's instruction.
    sk, strict_err = mcp_core.require_strict_session_key(
        "monitor_update requires an authenticated strict session binding. "
        "No process-ancestor fallback is used."
    )
    # Stateless: short-circuit only un-appliable contexts; the
    # consumer resolves the loop by its own session and patches it.
    if not sk:
        return _monitor_context_refusal("monitor_update", sk, strict_err)
    if mcp_core._structured_monitor_binding_key(sk) is None:
        return _monitor_context_refusal(
            "monitor_update",
            sk,
            "monitor_update only works from within a dashboard, Slack, or "
            f"Discord session (current session_key={sk!r}).",
        )
    patch: dict[str, Any] = {}
    if args.get("message") is not None:
        new_message = str(args["message"]).strip()
        if not new_message:
            return "monitor_update: message must not be empty (omit it to leave unchanged)."
        patch["message"] = new_message
    if args.get("interval_secs") is not None:
        patch["idle_secs"] = int(args["interval_secs"])
    if args.get("max_cycles") is not None:
        patch["max_cycles"] = int(args["max_cycles"])
    if args.get("max_runtime_secs") is not None:
        patch["max_runtime_secs"] = int(args["max_runtime_secs"])
    if args.get("target") is not None:
        # The authoritative applier validates the target against the retained kind.
        patch["target"] = str(args["target"])
    if args.get("objective") is not None:
        patch["objective"] = str(args["objective"])
    for field in ("max_agent_turns", "max_tokens", "max_provider_errors"):
        if args.get(field) is not None:
            patch[field] = int(args[field])
    if args.get("wake_instructions") is not None:
        patch["wake_instructions"] = str(args["wake_instructions"]).strip()
    # Blank is KEPT here, unlike ``message`` above which rejects it: a loop with
    # no instruction cannot fire, but a loop with no banner is the default state,
    # so "" has to round-trip as a request to CLEAR. Dropping it as "unchanged"
    # would make a banner set once impossible to remove without tearing the loop
    # down and losing its cycle count.
    if args.get("banner") is not None:
        patch["banner"] = str(args["banner"]).strip()
    # An empty object is KEPT, for the reason a blank banner is: ``{}`` is how an
    # owner's own CRITERIA come off a live loop, which returns it to the shipped
    # default brief rather than taking the judge off -- ``false`` is the bypass, and
    # it normalises to a reserved marker instead of to this shape. Dropping ``{}`` as
    # "unchanged" would make a brief set once impossible to clear without tearing the
    # loop down. Validated here as well as on the arm path, because this is the other
    # door into the same stored field and an unvalidated one would be the way around
    # the bound.
    if args.get("judge") is not None:
        try:
            patch["judge"] = validate_judge_spec(args["judge"])
        except ValidationError as exc:
            return f"monitor_update: {exc}"
    # Blank is DROPPED here, unlike ``banner`` and ``judge`` above: there is no "clear the
    # watch" request to express. Disarming a subject is what ``gate: false`` on a fresh arm
    # is for, and a loop silently losing its watch through a metadata edit is the "looks
    # armed, observes nothing" state the service's own fold exists to prevent.
    if str(args.get("watch") or "").strip():
        patch["watch"] = str(args["watch"]).strip()
    if not patch:
        mcp_core.sel().log_tool_invocation(
            session_key=sk, source="mcp", tool_name="monitor_update", outcome="noop"
        )
        return (
            "monitor_update: nothing to change — pass at least one of "
            "message, interval_secs, max_cycles, max_runtime_secs, judge, watch."
        )
    # AFTER the empty-patch no-op so that more specific answer still wins. A
    # retained stop cannot be updated either: ``update_monitor`` answers "not found
    # or already terminal" at the turn boundary, and unlike the two arming
    # directives a refused monitor_update gets no transcript notice at all — so
    # without this the retarget failure is invisible to both the model and the user.
    retained_stop_refusal = _retained_stop_refusal("monitor_update", sk)
    if retained_stop_refusal:
        return retained_stop_refusal
    return _emit_directive(
        "monitor_update",
        {"patch": patch},
        f"Monitor-loop update requested for this session "
        f"({', '.join(sorted(patch))}); it applies only if a loop is active "
        "here, so do not assume the change landed.",
    )


def set_project(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, SET_PROJECT_SCHEMA)
    # Stateless: the session-aware consumer (chat_runner) applies the
    # project change to ITS OWN slot — no session identity resolved here.
    return _emit_directive(
        "set_project",
        {"project": args.get("path", ""), "clear": bool(args.get("clear"))},
        "Project change requested for this session; if the path is valid "
        "and permitted it takes effect on the next message (cold-start with "
        "the new CWD and project steering). An invalid or sensitive path is "
        "rejected when this turn's result is processed.",
    )


def reset_conversation(name: str, args: dict[str, Any]) -> str:
    validate_tool_args(args, RESET_CONVERSATION_SCHEMA)
    # Stateless: the session-aware consumer (chat_runner) queues the discard
    # against ITS OWN slot — no session identity resolved here. The payload is
    # empty because there is nothing to choose: a caller asking for a clean
    # context always wants a clean one, and the HTTP route carries a replay flag
    # for the rare caller that does not.
    return _emit_directive(
        "reset_conversation",
        {},
        "Conversation reset requested for this session; if this turn is "
        "user-facing it takes effect at a turn boundary, and the next message "
        "starts with no memory of this conversation. The transcript is not "
        "deleted — write down anything that must carry forward.",
    )


def chat_tag(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, CHAT_TAG_SCHEMA)
    # Stateless: the session-aware consumer (chat_runner) applies the tag change
    # to ITS OWN slot — no session identity resolved here. The payload carries
    # only the requested change; the consumer resolves ids against the live
    # vocabulary, enforces the per-tag agent policy, and returns the resulting
    # tag list (which is also this tool's READ path for the session's tags).
    payload: dict[str, Any] = {}
    if args.get("set_state"):
        payload["set_state"] = args["set_state"]
    if args.get("add"):
        payload["add"] = args["add"]
    if args.get("remove"):
        payload["remove"] = args["remove"]
    return _emit_directive(
        "chat_tag",
        payload,
        "Board tag change requested for this session; if the tags are "
        "agent-writable it takes effect when this turn's result is processed, "
        "and the result reports the session's resulting tags. A human-only tag, "
        "an unknown tag, or a headless caller (cron/subagent) is refused.",
    )


def suggest_followup(name: str, args: dict[str, Any]) -> str:
    args = validate_tool_args(args, SUGGEST_FOLLOWUP_SCHEMA)
    items = args.get("items") or []
    # Stateless: the session-aware consumer (chat_runner) broadcasts
    # the card to ITS OWN slot; no session identity resolved here. The card
    # is broadcast-only (dropped if no client attached), so the confirmation
    # stays cautious — restate the follow-ups in reply text if they matter.
    return _emit_directive(
        "suggest_followup",
        {"items": items},
        "Follow-up card requested for this session. It is delivered to a "
        "connected dashboard client only; if none is attached the card is "
        "dropped, so restate the follow-ups in your reply text if they "
        "must not be lost. End your turn now.",
    )


HANDLERS: dict[str, Callable[[str, dict[str, Any]], str]] = {
    "task_run": task_run,
    "wait": wait,
    "route_crew": route_crew,
    "select_crew": select_crew,
    "register_hook": register_hook,
    "autonudge_stop": autonudge_stop,
    "ask_question": ask_question,
    "monitor_start": monitor_start,
    "monitor_watch": monitor_watch,
    "monitor_inspect": monitor_inspect,
    "monitor_stop": monitor_stop,
    "monitor_update": monitor_update,
    "set_project": set_project,
    "reset_conversation": reset_conversation,
    "chat_tag": chat_tag,
    "suggest_followup": suggest_followup,
}
