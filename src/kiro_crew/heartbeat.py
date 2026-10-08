"""Heartbeat service — periodic background tasks.

Runs on a configurable interval (default 60s):
- Reads HEARTBEAT.md for pending tasks → sends to agent
- Rebuilds FTS index every 15 min
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine

from kiro_crew import memory_backup, platform_compat, shutdown_event
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.executors import maintenance_executor
from kiro_crew.llm_helpers import append_fallback_story
from kiro_crew.memory import MemoryStore, workspace_dir
from kiro_crew.metrics.provider import get_recorder
from kiro_crew.sel import sel

if TYPE_CHECKING:
    from kiro_crew.history import HistoryConsolidator

logger = logging.getLogger(__name__)

# Deliver target extracted from <!-- deliver:xxx --> in heartbeat entries
_DELIVER_RE = re.compile(r"<!--\s*deliver:(\S+)\s*-->")

# Agent can include this sentinel in its response to signal the task is not done
_KEEP_SENTINEL = "HEARTBEAT_KEEP"
_KEEP_RE = re.compile(_KEEP_SENTINEL, re.IGNORECASE)

_DEFAULT_INTERVAL = 60
# A kept task waits this long at most between model calls. The service tick
# stays at the maintenance interval; this cap is per task, not per beat.
_KEEP_BACKOFF_CAP_SECS = 3600
# Twelve keeps is the 24h bound: a task that always answers HEARTBEAT_KEEP
# is retired on the twelfth call, with one notice. Seven days covers a task
# that keeps slowly enough to never hit the count.
_KEEP_RETIRE_AFTER = 12
_KEEP_MAX_AGE_SECS = 7 * 24 * 3600
_FTS_REBUILD_TICKS = 15  # rebuild every 15 ticks (15 min at 60s interval)
_PRUNE_TICKS = 1440  # prune old history once per day (1440 min at 60s interval)
# Memory backup runs on its OWN counter, offset from _PRUNE_TICKS rather than sharing
# it: pruning history and copying every store are both minutes-long on a large
# install, and landing them on the same tick puts two of four maintenance workers on
# the same second once a day. The offset costs nothing and keeps them apart.
_MEMORY_BACKUP_TICKS = 1440
_MEMORY_BACKUP_OFFSET = 30
# Per-task hard deadline for an unattended heartbeat turn. Mirrors cron's
# _JOB_TIMEOUT_SECS (1800s / 30 min): a heartbeat turn runs without a human
# present, so it MUST be bounded — otherwise a single non-allowlisted tool
# approval blocks the whole heartbeat subsystem indefinitely.
HEARTBEAT_TASK_TIMEOUT_SECS = 1800  # 30 min per heartbeat task
HEARTBEAT_FILE = "HEARTBEAT.md"
HEARTBEAT_STATE_FILE = "HEARTBEAT.state.json"
_HEADER = (
    "# Heartbeat Tasks\n\n<!-- Add tasks below (one per line). "
    "KiroCrew picks them up on next heartbeat. -->\n"
)


def heartbeat_path() -> Path:
    return workspace_dir() / HEARTBEAT_FILE


def heartbeat_state_path(path: Path | None = None) -> Path:
    target = path or heartbeat_path()
    return target.with_name(HEARTBEAT_STATE_FILE)


def _task_state_key(task_text: str) -> str:
    return hashlib.sha256(task_text.encode("utf-8")).hexdigest()[:16]


def _task_interval_secs() -> int:
    """Base gap for a kept task. The service tick is not this value."""
    try:
        raw = int(KiroCrewConfig.load().heartbeat.interval_secs)
    except Exception:
        return _DEFAULT_INTERVAL
    if raw < 15 or raw > _KEEP_BACKOFF_CAP_SECS:
        return _DEFAULT_INTERVAL
    return raw


def _backoff_secs(streak: int, base: int) -> int:
    shift = min(max(streak, 1) - 1, 16)
    return min(_KEEP_BACKOFF_CAP_SECS, max(1, base) * (2**shift))


def _load_task_state(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(heartbeat_state_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, dict):
        return {}
    return {str(k): v for k, v in tasks.items() if isinstance(v, dict)}


def _due(entry: dict | None, now: float) -> bool:
    if not entry:
        return True
    try:
        return now >= float(entry.get("next_due") or 0)
    except (TypeError, ValueError):
        return True


def _after_incomplete(
    entry: dict | None, now: float, base: int, *, failed: bool
) -> tuple[dict, bool]:
    """Next sidecar row, and whether a keep has retired the task."""
    prev = entry or {}
    first = float(prev.get("first_keep_ts") or now)
    keep_streak = int(prev.get("keep_streak") or 0)
    fail_streak = int(prev.get("fail_streak") or 0)
    if failed:
        fail_streak += 1
        streak_for_wait = fail_streak
        retired = False
    else:
        keep_streak += 1
        streak_for_wait = keep_streak
        if not prev.get("first_keep_ts"):
            first = now
        retired = keep_streak >= _KEEP_RETIRE_AFTER or (now - first) >= _KEEP_MAX_AGE_SECS
    return (
        {
            "keep_streak": keep_streak,
            "fail_streak": fail_streak,
            "first_keep_ts": first,
            "next_due": now + _backoff_secs(streak_for_wait, base),
        },
        retired,
    )


def heartbeat_lock_path(path: Path | None = None) -> Path:
    """Return the sibling lock file shared by all HEARTBEAT.md writers."""
    target = path or heartbeat_path()
    return target.with_name(f"{target.name}.lock")


def append_heartbeat_task(entry: str, path: Path | None = None) -> None:
    """Append one heartbeat entry under the cross-process writer lock.

    Internal producers must use this helper rather than opening HEARTBEAT.md
    directly, so the service's final read→replace transaction cannot overwrite
    an append from another process.
    """
    target = path or heartbeat_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = heartbeat_lock_path(target)
    with open(lock_path, "a+b") as lock_file:
        with platform_compat.file_lock(lock_file.fileno(), exclusive=True):
            if not target.exists():
                atomic_write(target, _HEADER, fsync=True)
            with open(target, "a", encoding="utf-8") as heartbeat_file:
                heartbeat_file.write(entry.rstrip("\n") + "\n")
                heartbeat_file.flush()
                os.fsync(heartbeat_file.fileno())


def _rewrite_heartbeat_locked(
    path: Path,
    original_tasks: list[tuple[str, str]],
    keep: list[tuple[str, str]],
    state: dict[str, dict] | None = None,
) -> int:
    """Re-read, merge, and atomically replace HEARTBEAT.md under an OS lock.

    ``state`` is the per-task backoff sidecar. It is written in the same lock
    as the markdown so a keep cannot lose its next_due to a concurrent append.
    """
    lock_path = heartbeat_lock_path(path)
    with open(lock_path, "a+b") as lock_file:
        with platform_compat.file_lock(lock_file.fileno(), exclusive=True):
            try:
                current_tasks = _extract_tasks(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                current_tasks = []

            remaining = Counter(t for t, _ in original_tasks)
            appended: list[tuple[str, str]] = []
            for task_text, deliver in current_tasks:
                if remaining.get(task_text, 0) > 0:
                    remaining[task_text] -= 1
                else:
                    appended.append((task_text, deliver))

            lines = _HEADER
            for task_text, deliver in keep + appended:
                suffix = f"  <!-- deliver:{deliver} -->" if deliver else ""
                lines += f"- {task_text}{suffix}\n"
            atomic_write(path, lines, fsync=True)
            if state is not None:
                atomic_write(
                    heartbeat_state_path(path),
                    json.dumps({"tasks": state}, indent=2) + "\n",
                    fsync=True,
                )
            return len(appended)


def _ensure_heartbeat_file(path: Path) -> None:
    """Create the workspace dir and seed HEARTBEAT.md. Blocking; call off-loop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")


def _read_heartbeat_file(path: Path) -> str | None:
    """Return HEARTBEAT.md's stripped content, or ``None`` if it is absent.

    Blocking; call off-loop. The existence check rides with the read so a file
    removed between them cannot turn a missing file into a raised
    ``FileNotFoundError``, and so the pair costs one worker hop rather than two.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


class HeartbeatService:
    """Periodic wake-up that runs background maintenance tasks."""

    def __init__(
        self,
        memory: MemoryStore,
        on_task: Callable[[str, str], Coroutine] | None = None,
        interval: int = _DEFAULT_INTERVAL,
        consolidator: HistoryConsolidator | None = None,
        on_cycle_end: Callable[[], Coroutine] | None = None,
        on_retire: Callable[[str], Coroutine] | None = None,
    ) -> None:
        self._memory = memory
        self._on_task = on_task
        self._interval = interval
        self._consolidator = consolidator
        self._on_retire = on_retire
        # Called once after every cycle's tasks finish (regardless of
        # individual task success/fail).  Owner uses this to recycle the
        # shared heartbeat session at cycle boundaries — the per-task
        # ``finally`` block is too aggressive when concurrent tasks share
        # one session key.
        self._on_cycle_end = on_cycle_end
        self._tick = 0
        self._processing = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._memory_backup_task: asyncio.Task | None = None
        self._memory_backup_stop = threading.Event()
        self._memory_backup_started = False
        # Serializes the HEARTBEAT.md read→process→rewrite window within this
        # process so two cycles can't clobber each other's rewrite.
        self._file_lock = asyncio.Lock()

    async def start(self) -> None:
        # Off the loop: start() is awaited on the gateway boot path, before
        # KIROCREW_READY, and workspace_dir() may sit on a network or synced
        # volume where mkdir + write_text block (AUTOSDE
        # no-blocking-call-on-event-loop, no-new-work-on-gateway-boot-path).
        await asyncio.to_thread(_ensure_heartbeat_file, heartbeat_path())
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._memory_backup_stop.set()
        if self._memory_backup_task is not None:
            self._memory_backup_task.cancel()
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=self._interval)
                return  # shutdown signaled
            except asyncio.TimeoutError:
                pass  # normal wake-up
            self._tick += 1
            try:
                await self._beat()
            except Exception:
                logger.warning("Heartbeat tick failed", exc_info=True)

    async def _beat(self) -> None:
        from kiro_crew.memory_startup import (
            MemoryStartupUnavailable,
            require_memory_prepared,
            require_memory_ready,
        )

        require_memory_prepared()
        if (
            not self._memory_backup_started
            or self._tick % _MEMORY_BACKUP_TICKS == _MEMORY_BACKUP_OFFSET
        ):
            self._schedule_memory_backup()
        try:
            require_memory_ready()
        except MemoryStartupUnavailable:
            # Healthy member backups and idle consolidation remain eligible
            # while this gateway's Global memory awaits owner recovery.
            if self._consolidator:
                self._consolidator.check_idle_sessions()
            return
        if not self._processing:
            await self._process_heartbeat_file()

        if self._tick % _FTS_REBUILD_TICKS == 0:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(maintenance_executor(), self._memory.rebuild_index)
            logger.info("FTS index rebuilt: %d files", count)

        if self._tick % _PRUNE_TICKS == 0:
            max_days = KiroCrewConfig.load().memory.history_max_days
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                maintenance_executor(),
                functools.partial(self._memory.prune_history, keep_days=max_days),
            )

            # Prune security event log per retention policy.
            #
            # maintenance_executor is deliberate, despite this pool's "fast
            # periodic sweeps" charter and prune taking seconds on a large log.
            # The three pools split on what makes a worker UN-RECLAIMABLE, not
            # on duration: subprocess_executor is for work that can hang
            # indefinitely on a wedged kernel resource, discovery_executor for
            # work whose concurrency is set by remote callers.  prune is
            # neither -- it terminates, it is awaited so it never overlaps
            # itself, and it fires once per _PRUNE_TICKS, so one of four
            # mc-maint workers once a day cannot starve the orphan-reaping
            # sweeps this pool protects.  prune_history above is the same
            # retention work on the same pool.
            try:
                await loop.run_in_executor(maintenance_executor(), sel().prune)
            except Exception:
                # WARNING, not debug: a dead prune means retention silently
                # stops -- the unbounded-growth failure prune exists to
                # prevent.  Counter so it is alarmable; guarded so a telemetry
                # fault cannot take the heartbeat loop down with it.
                logger.warning("SEL prune failed", exc_info=True)
                try:
                    get_recorder().counter("kirocrew.sel.prune_failed.count")
                except Exception:
                    pass

        # Check for idle sessions needing history consolidation (every tick)
        if self._consolidator:
            self._consolidator.check_idle_sessions()

    def _schedule_memory_backup(self) -> None:
        """One owned pass; ticks keep serving while a large store is copied."""
        if self._memory_backup_stop.is_set():
            return
        if self._memory_backup_task is not None and not self._memory_backup_task.done():
            return
        self._memory_backup_started = True
        self._memory_backup_task = asyncio.create_task(self._back_up_memory())

    async def _back_up_memory(self) -> None:
        """Take a rotating copy of every active memory store, the default one first.

        The default store is the one every install has and the one the module
        exists for; a sweep that visited only member silos protected the small
        stores and left the largest one with no copy at all.

        Offloaded to ``maintenance_executor`` because the SQLite backup API is blocking
        and copies the whole file; on the event loop a large store would stall every
        task. Same pool and same rationale as ``prune_history`` above — it terminates,
        the retained task prevents overlapping passes. The first eligible
        heartbeat checks existing backup freshness, so frequent restarts cannot
        postpone durability indefinitely; subsequent passes use the daily tick.

        Guarded so a backup failure cannot take the heartbeat down, and logged at
        WARNING rather than debug: a dead backup means durability silently stops, which
        is the whole failure this exists to prevent, so it has to be alarmable.
        """

        def copy_active_stores():
            if self._memory_backup_stop.is_set():
                return None
            cfg = KiroCrewConfig.load().memory
            if not cfg.backup_enabled:
                return None
            return memory_backup.back_up_all_stores(
                int(cfg.backup_keep),
                should_stop=self._memory_backup_stop.is_set,
            )

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(maintenance_executor(), copy_active_stores)
            if result is None:
                return
            if result["failed"]:
                # WARNING on its own, so a store whose copy fails every pass is
                # alarmable. Durability stopping quietly is the failure this exists to
                # prevent, and an INFO line among the copied counts is not visible.
                logger.warning(
                    "Memory backup: %d store(s) FAILED to copy (%d copied, %d skipped)",
                    result["failed"],
                    result["backed_up"],
                    result["skipped"],
                )
            elif result["backed_up"]:
                logger.info(
                    "Memory backup: %d store(s) copied, %d old removed, %d skipped",
                    result["backed_up"],
                    result["pruned"],
                    result["skipped"],
                )
        except Exception:
            logger.warning("Memory backup pass failed", exc_info=True)

    async def _run_one_task(self, task_text: str, deliver: str) -> str | None:
        """Execute a single heartbeat task (used by gather).

        Returns the agent response text, or ``None`` if no callback.
        """
        assert self._on_task is not None
        return await self._on_task(task_text, deliver)

    async def _process_heartbeat_file(self) -> None:
        path = heartbeat_path()
        # Hold the file lock across the whole read→process→rewrite window so a
        # concurrent cycle can't rewrite the file from a stale snapshot.
        async with self._file_lock:
            # Off the loop, like the rewrite that closes this same lock window
            # below: this runs every tick for the life of the process, and the
            # file lives under workspace_dir().
            content = await asyncio.to_thread(_read_heartbeat_file, path)
            if content is None:
                return
            tasks = _extract_tasks(content)
            if not tasks or not self._on_task:
                return

            self._processing = True
            retired: list[str] = []
            try:
                state = await asyncio.to_thread(_load_task_state, path)
                now = time.time()
                base = _task_interval_secs()
                runnable: list[tuple[str, str]] = []
                parked: list[tuple[str, str]] = []
                for task in tasks:
                    entry = state.get(_task_state_key(task[0]))
                    if _due(entry, now):
                        runnable.append(task)
                    else:
                        parked.append(task)
                logger.info(
                    "Heartbeat: %d task(s) found, %d due",
                    len(tasks),
                    len(runnable),
                )
                keep: list[tuple[str, str]] = list(parked)
                new_state: dict[str, dict] = {
                    _task_state_key(text): state[_task_state_key(text)]
                    for text, _deliver in parked
                    if _task_state_key(text) in state
                }
                results = await asyncio.gather(
                    *[self._run_one_task(t, d) for t, d in runnable],
                    return_exceptions=True,
                )
                for (task_text, deliver), result in zip(runnable, results):
                    key = _task_state_key(task_text)
                    if isinstance(result, BaseException):
                        # A chain-exhaustion failure carries the fallback story
                        # on the exception; this log line is the heartbeat's
                        # terminal error text, so append it here — the walk
                        # must not be reported as just the last candidate's
                        # failure (llm_helpers.FALLBACK_STORY_ATTR).
                        logger.warning(
                            "Heartbeat task failed: %s",
                            append_fallback_story(task_text[:80], result),
                            exc_info=result,
                        )
                        row, _retired = _after_incomplete(state.get(key), now, base, failed=True)
                        new_state[key] = row
                        keep.append((task_text, deliver))
                    elif _should_keep(result):
                        row, is_retired = _after_incomplete(state.get(key), now, base, failed=False)
                        if is_retired:
                            logger.info(
                                "Heartbeat task retired after %s keeps: %s",
                                row["keep_streak"],
                                task_text[:80],
                            )
                            retired.append(task_text)
                        else:
                            logger.info(
                                "Heartbeat task incomplete, next due in %ss: %s",
                                int(row["next_due"] - now),
                                task_text[:80],
                            )
                            new_state[key] = row
                            keep.append((task_text, deliver))

                # Re-read, merge mid-cycle appends, and replace atomically while
                # holding the sibling OS lock. Every internal writer uses the
                # same lock via append_heartbeat_task(), so no append can land
                # between this final read and os.replace. The entire durable
                # transaction runs off the gateway event-loop thread.
                appended_count = await asyncio.to_thread(
                    _rewrite_heartbeat_locked,
                    path,
                    tasks,
                    keep,
                    new_state,
                )
                if appended_count:
                    logger.info(
                        "Heartbeat: preserving %d task(s) appended mid-cycle",
                        appended_count,
                    )
            finally:
                self._processing = False
                # Cycle-end teardown — runs once after ALL tasks in this cycle
                # complete.  Owner can use this to conditionally recycle the
                # shared heartbeat session (e.g. only when context > threshold)
                # so multi-task cycles don't tear down the session another
                # in-flight task is still using.
                if self._on_cycle_end is not None:
                    try:
                        await self._on_cycle_end()
                    except Exception:
                        logger.warning("Heartbeat: on_cycle_end callback failed", exc_info=True)
                if self._on_retire is not None:
                    for task_text in retired:
                        try:
                            await self._on_retire(task_text)
                        except Exception:
                            logger.warning("Heartbeat: retire notice failed", exc_info=True)


def _should_keep(result: str | None) -> bool:
    """Return True if the agent response signals the task is incomplete."""
    if result is None:
        return False
    return bool(_KEEP_RE.search(result))


def strip_keep_sentinel(text: str) -> str:
    """Remove HEARTBEAT_KEEP sentinel from text."""
    return _KEEP_RE.sub("", text).strip()


def is_keep_response(text: str | None) -> bool:
    """Return True if *text* contains the HEARTBEAT_KEEP sentinel (case-insensitive).

    Use this to check whether a heartbeat task signaled "not done, retry next cycle".
    """
    if text is None:
        return False
    return _KEEP_SENTINEL in text.upper()


def _extract_tasks(content: str) -> list[tuple[str, str]]:
    """Extract tasks as ``(text, deliver_target)`` tuples.

    ``deliver_target`` comes from an inline ``<!-- deliver:xxx -->`` comment.
    Empty string when absent.
    """
    tasks: list[tuple[str, str]] = []
    in_comment = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Track multi-line HTML comments (standalone comment lines)
        if "<!--" in stripped and "-->" not in stripped:
            in_comment = True
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        # Standalone comment line (<!-- ... --> on one line, no task text)
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("#"):
            continue
        # Extract inline deliver target before stripping comments
        deliver = ""
        m = _DELIVER_RE.search(stripped)
        if m:
            deliver = m.group(1)
            stripped = stripped[: m.start()].rstrip()
        # Strip leading list markers
        for prefix in ("- [x] ", "- [ ] ", "- ", "* "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
                break
        stripped = stripped.strip()
        if stripped and stripped != "-":
            tasks.append((stripped, deliver))
    return tasks
