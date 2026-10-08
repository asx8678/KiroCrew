"""Per-call token sink: how a workflow run charges what its model calls spend (USE-2).

The workflow runner installs a fresh list in :data:`TURN_TOKEN_SINK` around each
``agent_fn`` call. Every terminal ``stream_and_collect`` turn under that call
appends its cost via :func:`count_turn`, so a schema re-ask or a retry is charged
as its own turn. A ContextVar rather than a key in the ``opts`` dict, because the
pooled path copies ``opts`` before it runs, and a count written into the copy
would never reach the runner.

This module is a leaf (no imports from the package), so the LLM helpers and the
workflow engine can both import it without a cycle.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

#: The sink a workflow runner installs for one agent call; ``None`` outside one.
TURN_TOKEN_SINK: ContextVar[list[int] | None] = ContextVar("kirocrew_turn_token_sink", default=None)


def turn_cost(usage: Any) -> int:
    """The cost of one billed turn, in the budget's unit (tokens).

    Input plus output tokens. A backend that reports no token counts (kiro bills
    credits) falls back to its credits, so the budget still moves; that mixes
    units for such a turn, and the workflow budget docs say so.
    """
    tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
        getattr(usage, "output_tokens", 0) or 0
    )
    if tokens:
        return tokens
    return int(float(getattr(usage, "credits", 0.0) or 0.0))


def count_turn(usage: Any) -> None:
    """Append one billed turn's cost to the active sink, if a workflow call set one."""
    sink = TURN_TOKEN_SINK.get()
    if sink is None:
        return
    cost = turn_cost(usage)
    if cost > 0:
        sink.append(cost)
