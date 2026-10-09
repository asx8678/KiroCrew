"""Scheduling combinators for the dynamic-workflow DSL (GATES A3, A5).

``parallel`` and ``pipeline`` are the two scheduling primitives a workflow script
reaches through ``ctx``. They are pure ``asyncio`` combinators over caller-supplied
thunks / stage callables — they hold no agent, session, or gateway logic, so this
module sits at the BOTTOM of the layering (``dsl`` → ``context`` → ``runner``;
GATE F1) and imports nothing heavy. The runner injects the concurrency ``limit``
(the host's fixed workflow cap); tests pass a small limit or ``None``.

Semantics (frozen — see ``docs/system-specs/modules/workflows.md``):

* ``parallel(tasks)`` — BARRIER. Run every task concurrently, await them all,
  return results in input order. Each task may be a zero-arg thunk
  (``lambda: ctx.agent(p)``) OR an already-created awaitable (``ctx.agent(p)``) —
  both are accepted, since authors naturally reach for the latter. A task that
  raises (synchronously or in its awaitable) resolves to ``None`` — the call
  itself never raises (GATE A5), so callers ``.filter(None)``. The one exception
  is the run's ceiling, ``BudgetExceeded``: it cancels the other tasks and
  propagates, so the run ends on the ``ceiling`` path (same in ``pipeline``).

* ``pipeline(items, *stages)`` — NO barrier between stages. Each item flows through
  all stages in its own chain; item B can reach stage 2 while item A is still in
  stage 1 (GATE A3). Wall-clock = slowest single-item chain, not sum-of-slowest-
  per-stage. Each stage is called ``stage(prev, item, index)`` (arity-adapted: a
  1-arg stage just gets ``prev``); for stage 0 ``prev`` is the item itself. A stage
  that raises drops that item to ``None`` and skips its remaining stages. Results
  are returned in input order.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Optional, Sequence

from . import BudgetExceeded

logger = logging.getLogger(__name__)

# A thunk: zero-arg callable returning an awaitable (matches Thunk in __init__).
Thunk = Callable[[], Awaitable[Any]]
# A stage: callable of up to (prev, item, index) returning an awaitable.
Stage = Callable[..., Awaitable[Any]]


def _positional_arity(func: Callable[..., Any]) -> Optional[int]:
    """Number of positional params a callable accepts, or None if it takes *args.

    Lets a stage be called with only as many of (prev, item, index) as it declares,
    mirroring the JS pipeline's "extra args ignored" behavior so 1-arg and 3-arg
    stages both work.
    """
    try:
        params = inspect.signature(func).parameters.values()
    except (ValueError, TypeError):
        return None  # builtins / C funcs: fall back to passing everything
    count = 0
    for p in params:
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            return None  # *args → accepts all three
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            count += 1
    return count


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it as-is.

    Stages and thunks may be sync OR async (e.g. ``lambda prev: prev * 10`` or
    ``async def s(prev): ...``); a plain return value must not be ``await``-ed.
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_stage(stage: Stage, prev: Any, item: Any, index: int) -> Any:
    arity = _positional_arity(stage)
    all_args = (prev, item, index)
    # ``arity`` is None (accepts everything) or a non-negative count, and slicing
    # past the end of the tuple already yields all three, so the count needs no clamp.
    args = all_args if arity is None else all_args[:arity]
    return await _maybe_await(stage(*args))


async def _run_thunk(thunk: Thunk) -> Any:
    """Run one parallel task; any failure but the run's ceiling becomes ``None`` (A5).

    Accepts BOTH forms callers naturally write:
      * a zero-arg thunk — ``lambda: ctx.agent(p)`` (the documented form), and
      * an already-created awaitable — ``ctx.agent(p)`` (a coroutine), which is
        what most Python authors reach for first (``gather(*coros)`` habit).
    Forgiving here avoids a whole class of authored-script failures where every
    task silently became ``None`` because a coroutine isn't callable.

    ``BudgetExceeded`` (the token budget or the per-run agent cap) propagates: it
    is the run's ceiling, not a task failure, and swallowing it here let a script
    carry on past the stop instead of ending on the ``ceiling`` path.
    """
    try:
        if inspect.isawaitable(thunk):
            # Caller passed ctx.agent(...) directly (a coroutine/awaitable).
            return await thunk
        return await _maybe_await(thunk())
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed task resolves to None (A5)
        logger.info("workflow parallel task resolved to None: %r", exc)
        return None


async def _gather_or_stop(coros: list[Awaitable[Any]]) -> list:
    """Gather in input order; on the run's ceiling, cancel the siblings and re-raise."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return list(await asyncio.gather(*tasks))
    except BudgetExceeded:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def parallel(thunks: Sequence[Thunk], *, limit: Optional[int] = None) -> list:
    """Run thunks concurrently (barrier), results in input order.

    A failing thunk → ``None``; this never raises (GATE A5) except for the run's
    ceiling (``BudgetExceeded``), which cancels the other tasks and propagates.
    ``limit`` bounds concurrency via a semaphore (the runner passes its run-global cap).
    """
    if not thunks:
        return []

    sem = asyncio.Semaphore(limit) if (limit and limit > 0) else None

    async def _guarded(t: Thunk) -> Any:
        if sem is None:
            return await _run_thunk(t)
        async with sem:
            return await _run_thunk(t)

    return await _gather_or_stop([_guarded(t) for t in thunks])


async def pipeline(items: Sequence[Any], *stages: Stage, limit: Optional[int] = None) -> list:
    """Run each item through all stages independently — no inter-stage barrier (A3).

    Each item gets its own chain; chains run concurrently (bounded by ``limit``).
    A stage that raises drops that item to ``None`` and skips its remaining stages,
    except ``BudgetExceeded`` (the run's ceiling), which cancels the other chains
    and propagates. Results are returned in input order. With no stages, returns
    the items as-is.
    """
    if not items:
        return []
    if not stages:
        return list(items)

    sem = asyncio.Semaphore(limit) if (limit and limit > 0) else None

    async def _chain(item: Any, index: int) -> Any:
        prev: Any = item
        for stage in stages:
            try:
                prev = await _call_stage(stage, prev, item, index)
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - drop this item; skip remaining stages
                logger.info("workflow pipeline item %d dropped: %r", index, exc)
                return None
        return prev

    async def _guarded(item: Any, index: int) -> Any:
        if sem is None:
            return await _chain(item, index)
        async with sem:
            return await _chain(item, index)

    return await _gather_or_stop([_guarded(it, i) for i, it in enumerate(items)])
