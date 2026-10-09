"""Credit report: the per-turn usage row store, aggregated for the Usage page.

Reads the same shards as :mod:`kiro_crew.dashboard.handlers.usage`
(``<data home>/usage/tokens/YYYY-MM-DD.jsonl``) but answers a different
question. The Spend tab's readers (``slot_spend`` / ``cost_breakdown``) report
the SESSIONS a panel lists and deliberately drop subagent rows; this report
answers "what did the account spend, on what, and when", so every row counts:
a subagent's spend is rolled up into the session that spawned it through the
row's ``parent_slot`` (USE-10) and shown as its own share of that session.

It also keeps the account-reading history (``usage/account/YYYY-MM.jsonl``):
each identity-proven reading the credit pill publishes is appended, so the page
can compare what the ACCOUNT was charged over a window with what the row store
recorded. The difference is spend this host never saw as a turn (kiro-cli used
outside Kiro Crew, a remote peer, a container) or a metering gap.

Everything here is local and read-only over the row store; nothing egresses.
Both routes are dashboard-only: an app caller is refused with the standard
indistinguishable ``404`` and the refusal is SEL-audited (App Kit §5.2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from aiohttp import web

from kiro_crew import sel as _sel_mod
from kiro_crew.config.paths import data_home
from kiro_crew.dashboard.handlers.usage import (
    _parse_row_dt,
    _token_usage_dir,
    _usage_number,
    session_category,
    spend_key_for_slot,
)
from kiro_crew.jsonl_util import bounded_records
from kiro_crew.messaging.link import telemetry_channel_of
from kiro_crew.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

#: The page's categories, in display order. ``unattributed`` is never a row's
#: category: it is the reconciliation gap, reported beside them.
CATEGORIES: tuple[str, ...] = ("chat", "channels", "background", "subagents", "workflows", "apps")

#: Row surfaces that are background work whatever session key they ran under.
_BACKGROUND_SURFACES = frozenset(
    {
        "cron",
        "heartbeat",
        "monitor",
        "webhook",
        "taskrunner",
        "taskrunner_decompose",
        "taskrunner_refine",
        "taskrunner_lesson",
        "compaction",
        "knowledge",
        "optimizer",
        "eval",
        "eval_judge",
        "subagent_completion",
    }
)

#: Row surfaces an app backend writes for its own model calls.
_APP_SURFACES = frozenset(
    {"issue_radar", "meetings", "meetings_translate", "code_review_sage", "auto_improvement"}
)

#: Widest window one request may aggregate. Two years of daily shards is far
#: past what the page asks for; the bound keeps a hand-built URL from parsing
#: every shard on disk per request.
MAX_RANGE_DAYS = 731

#: Parsed shards kept in memory, keyed by path and invalidated by size + mtime.
#: Enough for a year of daily shards plus slack; the oldest is evicted first.
_ROW_CACHE_MAX = 400

#: Longest per-prompt page one request returns.
MAX_TURNS_LIMIT = 500

#: Most sessions a summary lists (ranked by credits). The totals and every
#: breakdown are computed over all rows; only this list is cut.
_MAX_SESSIONS = 200

#: Bucket upper bounds (exclusive) for the distributions the page draws.
_DURATION_EDGES_MS: tuple[float, ...] = (
    1_000,
    2_000,
    5_000,
    10_000,
    20_000,
    30_000,
    60_000,
    120_000,
    300_000,
    math.inf,
)
_TTFT_EDGES_MS: tuple[float, ...] = (250, 500, 1_000, 2_000, 3_000, 5_000, 10_000, math.inf)
_CREDIT_EDGES: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, math.inf)

#: Outcomes the page counts as failures.
_FAULT_OUTCOMES = frozenset({"error", "timeout", "stall_exhausted"})


class _Row(NamedTuple):
    """One usage row, reduced to what the report reads."""

    ts: float
    day: str
    hour: int
    slot: str
    session: str
    parent: str
    surface: str
    service: str
    model: str
    agent: str
    app: str
    provider: str
    credits: float
    cost: float
    duration_ms: int
    ttft_ms: int
    outcome: str
    context_used: int
    context_window: int
    category: str
    # USE-12. ``share`` says why the row sits under its session: the session's
    # ``own`` turn, a ``subagents`` turn it spawned, or ``background`` work done
    # for it (a title, a summary, a consolidation). The activity counters read
    # ``-1`` on a row written before they existed, which is "unknown", never 0.
    share: str
    for_slot: str
    request: str
    tool_calls: int
    tool_kinds: tuple[tuple[str, int], ...]
    prompt_chars: int
    output_chars: int
    compactions: int
    stop_reason: str
    rate_multiplier: float | None


def row_category(*, surface: str, service: str, slot: str, app: str) -> str:
    """The page category of one row.

    Order matters: a subagent row is a subagent whatever it served, a workflow
    stage is a workflow, an app-stamped row is the app's; only then does the
    question "did a person start this turn" decide between background and a
    conversation. A row with a ``service`` was started by automation (a plain
    user turn writes none), as was any row whose surface or session is
    background by nature.
    """
    if surface == "subagent":
        return "subagents"
    if surface.startswith("workflow") or service.startswith("workflow"):
        return "workflows"
    if app or surface in _APP_SURFACES:
        return "apps"
    if (
        service
        or surface.startswith("bg:")
        or surface in _BACKGROUND_SURFACES
        or session_category(slot) == "bg"
    ):
        return "background"
    channel = telemetry_channel_of(slot) if slot else "unknown"
    if surface == "cli" or channel == "dashboard":
        return "chat"
    return "channels"


def _str_field(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    return value if isinstance(value, str) else ""


def _int_field(obj: dict[str, Any], key: str) -> int:
    value = _usage_number(obj.get(key))
    return int(value) if value is not None and value >= 0 else 0


def _known_int(obj: dict[str, Any], key: str) -> int:
    """A counter that may predate the row: ``-1`` when the key is absent."""
    return _int_field(obj, key) if key in obj else -1


def _tool_kinds(obj: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    raw = obj.get("tool_kinds")
    if not isinstance(raw, dict):
        return ()
    return tuple(
        (k, v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
    )


def _float_field(obj: dict[str, Any], key: str) -> float:
    value = _usage_number(obj.get(key))
    return float(value) if value is not None and value >= 0 else 0.0


def _parse_row(obj: object) -> _Row | None:
    """A shard line's object as a :class:`_Row`, or ``None`` when unusable."""
    if not isinstance(obj, dict) or obj.get("_type") != "tokens":
        return None
    dt = _parse_row_dt(obj.get("ts"))
    if dt is None:
        return None
    try:
        local = dt.astimezone()
        ts = local.timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    slot = _str_field(obj, "slot")
    surface = _str_field(obj, "surface")
    service = _str_field(obj, "service")
    app = _str_field(obj, "app")
    parent = _str_field(obj, "parent_slot")
    for_slot = _str_field(obj, "for_slot")
    # One session per row, precedence parent > served session > own slot, so a
    # session's own, subagent and background shares sum to its total.
    owner = parent or for_slot or slot
    if parent:
        share = "subagents"
    elif for_slot and spend_key_for_slot(for_slot) != spend_key_for_slot(slot):
        share = "background"
    else:
        share = "own"
    multiplier = obj.get("rate_multiplier")
    return _Row(
        ts=ts,
        day=local.strftime("%Y-%m-%d"),
        hour=local.hour,
        slot=slot,
        session=spend_key_for_slot(owner) if owner else "",
        parent=spend_key_for_slot(parent) if parent else "",
        surface=surface,
        service=service,
        model=_str_field(obj, "model"),
        agent=_str_field(obj, "agent"),
        app=app,
        provider=_str_field(obj, "provider"),
        credits=_float_field(obj, "credits"),
        cost=_float_field(obj, "cost"),
        duration_ms=_int_field(obj, "duration_ms"),
        ttft_ms=_int_field(obj, "ttft_ms"),
        outcome=_str_field(obj, "outcome"),
        context_used=_int_field(obj, "context_used"),
        context_window=_int_field(obj, "context_window"),
        category=row_category(surface=surface, service=service, slot=slot, app=app),
        share=share,
        for_slot=spend_key_for_slot(for_slot) if for_slot else "",
        request=_str_field(obj, "request"),
        tool_calls=_known_int(obj, "tool_calls"),
        tool_kinds=_tool_kinds(obj),
        prompt_chars=_known_int(obj, "prompt_chars"),
        output_chars=_known_int(obj, "output_chars"),
        compactions=_known_int(obj, "compactions"),
        stop_reason=_str_field(obj, "stop_reason"),
        rate_multiplier=(
            float(multiplier)
            if isinstance(multiplier, (int, float))
            and not isinstance(multiplier, bool)
            and math.isfinite(multiplier)
            else None
        ),
    )


_ROW_CACHE: OrderedDict[str, tuple[int, int, tuple[_Row, ...]]] = OrderedDict()
_ROW_CACHE_LOCK = threading.Lock()


def _shard_rows(path: Path) -> tuple[_Row, ...]:
    """Every usable row of one shard, memoised on the file's size and mtime.

    A shard is append-only, so a changed size or mtime is the only way its rows
    change; today's shard is re-read when it grows, every closed day is parsed
    once. An unreadable shard reads as empty, like every other row-store reader.
    """
    try:
        st = path.stat()
    except OSError:
        return ()
    key = str(path)
    with _ROW_CACHE_LOCK:
        hit = _ROW_CACHE.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            _ROW_CACHE.move_to_end(key)
            return hit[2]
    rows: list[_Row] = []
    try:
        with path.open("rb") as fh:
            for line in bounded_records(fh, path, label="usage"):
                try:
                    row = _parse_row(json.loads(line))
                except ValueError:
                    continue
                if row is not None:
                    rows.append(row)
    except (OSError, UnicodeDecodeError):
        return ()
    parsed = tuple(rows)
    with _ROW_CACHE_LOCK:
        _ROW_CACHE[key] = (st.st_mtime_ns, st.st_size, parsed)
        _ROW_CACHE.move_to_end(key)
        while len(_ROW_CACHE) > _ROW_CACHE_MAX:
            _ROW_CACHE.popitem(last=False)
    return parsed


def _shards_between(start: date, end: date) -> list[Path]:
    """Shards dated within ``[start - 1 day, end + 1 day]``, oldest first.

    One day of slack on each side because a row is dated by its own timestamp,
    not its shard's name, and a turn that ends just past midnight lands in the
    next day's shard. Rows are filtered to the exact window afterwards.
    """
    shard_dir = _token_usage_dir()
    lo, hi = start - timedelta(days=1), end + timedelta(days=1)
    try:
        entries = list(shard_dir.iterdir())
    except OSError:
        return []
    out: list[tuple[date, Path]] = []
    for p in entries:
        if p.suffix != ".jsonl":
            continue
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if lo <= d <= hi:
            out.append((d, p))
    return [p for _, p in sorted(out)]


def _rows_between(start: date, end: date) -> list[_Row]:
    """Every row whose LOCAL day falls within ``[start, end]``, oldest first."""
    lo, hi = start.isoformat(), end.isoformat()
    rows = [r for p in _shards_between(start, end) for r in _shard_rows(p) if lo <= r.day <= hi]
    rows.sort(key=lambda r: r.ts)
    return rows


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an already sorted list, ``None`` when empty."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _histogram(values: Iterable[float], edges: tuple[float, ...]) -> list[dict[str, Any]]:
    """Counts per ``[previous edge, edge)`` bucket; an infinite edge reads ``None``."""
    counts = [0] * len(edges)
    for v in values:
        for i, edge in enumerate(edges):
            if v < edge:
                counts[i] += 1
                break
    out: list[dict[str, Any]] = []
    lo = 0.0
    for edge, count in zip(edges, counts):
        out.append({"lo": lo, "hi": None if math.isinf(edge) else edge, "count": count})
        lo = edge
    return out


def _round(value: float) -> float:
    return round(value, 6)


def _granularity(start: date, end: date) -> str:
    days = (end - start).days + 1
    if days <= 1:
        return "hour"
    if days <= 120:
        return "day"
    return "month"


def _series_keys(start: date, end: date, granularity: str) -> list[str]:
    if granularity == "hour":
        return [f"{h:02d}" for h in range(24)]
    if granularity == "day":
        return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    keys: list[str] = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        keys.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return keys


def _series_key(row: _Row, granularity: str) -> str:
    if granularity == "hour":
        return f"{row.hour:02d}"
    if granularity == "day":
        return row.day
    return row.day[:7]


def _service_label(row: _Row) -> str:
    """The name a row's spend is filed under: its service, else its surface."""
    return row.service or row.surface or "unknown"


class _Bucket:
    """Running totals for one breakdown key."""

    __slots__ = ("credits", "turns", "faults", "durations", "last_ts", "extra")

    def __init__(self) -> None:
        self.credits = 0.0
        self.turns = 0
        self.faults = 0
        self.durations: list[float] = []
        self.last_ts = 0.0
        self.extra: dict[str, Any] = {}

    def add(self, row: _Row) -> None:
        self.credits += row.credits
        self.turns += 1
        if row.outcome in _FAULT_OUTCOMES:
            self.faults += 1
        if row.duration_ms > 0:
            self.durations.append(row.duration_ms)
        self.last_ts = max(self.last_ts, row.ts)

    def figures(self) -> dict[str, Any]:
        durations = sorted(self.durations)
        return {
            "credits": _round(self.credits),
            "turns": self.turns,
            "avg_credits": _round(self.credits / self.turns) if self.turns else 0.0,
            "faults": self.faults,
            "p50_duration_ms": _percentile(durations, 50),
            "last_ts": self.last_ts or None,
        }


def _totals(rows: list[_Row]) -> dict[str, Any]:
    credits = sum(r.credits for r in rows)
    durations = sorted(float(r.duration_ms) for r in rows if r.duration_ms > 0)
    ttfts = sorted(float(r.ttft_ms) for r in rows if r.ttft_ms > 0)
    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r.outcome or "unknown"] = outcomes.get(r.outcome or "unknown", 0) + 1
    return {
        "credits": _round(credits),
        "cost_usd": _round(sum(r.cost for r in rows)),
        "turns": len(rows),
        "sessions": len({r.session for r in rows if r.session}),
        "models": len({r.model for r in rows if r.model}),
        "avg_credits": _round(credits / len(rows)) if rows else 0.0,
        "p50_duration_ms": _percentile(durations, 50),
        "p90_duration_ms": _percentile(durations, 90),
        "p50_ttft_ms": _percentile(ttfts, 50),
        "p90_ttft_ms": _percentile(ttfts, 90),
        "faults": sum(1 for r in rows if r.outcome in _FAULT_OUTCOMES),
        "outcomes": outcomes,
    }


def _ranked(buckets: dict[str, _Bucket], key_name: str) -> list[dict[str, Any]]:
    out = []
    for key, bucket in buckets.items():
        out.append({key_name: key, **bucket.figures(), **bucket.extra})
    out.sort(key=lambda d: (-d["credits"], -d["turns"], str(d[key_name])))
    return out


#: How many of the range's most expensive prompts the summary lists.
_TOP_TURNS = 12

#: Thresholds for a turn's reason labels. Each is a FACT about the turn, never a
#: share of its bill: the backend reports one opaque figure per turn.
_MANY_TOOL_CALLS = 8
_FULL_CONTEXT = 0.5
_BIG_REQUEST_CHARS = 60_000
_LONG_ANSWER_CHARS = 20_000
_LONG_RUN_MS = 300_000
_PREMIUM_MULTIPLIER = 1.5
#: Outcomes that billed without finishing the work.
_UNFINISHED_OUTCOMES = frozenset({"error", "timeout", "stall_exhausted", "cancelled"})

#: Tool-call bands (lower bounds) and context-fill bands (lower bounds, as a
#: fraction of the window) the cost-driver comparison groups turns by.
_TOOL_BANDS: tuple[tuple[str, int], ...] = (
    ("0", 0),
    ("1-3", 1),
    ("4-10", 4),
    ("11-25", 11),
    ("26+", 26),
)
_CONTEXT_BANDS: tuple[tuple[str, float], ...] = (
    ("0-25", 0.0),
    ("25-50", 0.25),
    ("50-75", 0.5),
    ("75-100", 0.75),
)

#: Categories whose services recur on their own (scheduled jobs, heartbeats,
#: nudges, hooks, helpers): the ones a per-day projection is meaningful for.
_RECURRING_CATEGORIES = frozenset({"background"})


def row_reasons(r: _Row) -> list[dict[str, Any]]:
    """The facts that made a turn heavier than a plain answer, as ``{code, value}``.

    Signals, not attribution: many tool calls (each one is another model round in
    the agent loop), a full context window, a mid-turn compaction (billed inside
    the turn), a very large request or answer, a long run, a premium-rate model,
    or a turn that billed and did not finish. An unknown counter (``-1``, a row
    written before it existed) never produces a label.
    """
    out: list[dict[str, Any]] = []
    if r.tool_calls >= _MANY_TOOL_CALLS:
        out.append({"code": "tools", "value": r.tool_calls})
    if r.context_window > 0 and r.context_used / r.context_window >= _FULL_CONTEXT:
        out.append({"code": "context", "value": round(r.context_used / r.context_window * 100, 1)})
    if r.compactions > 0:
        out.append({"code": "compacted", "value": r.compactions})
    if r.prompt_chars >= _BIG_REQUEST_CHARS:
        out.append({"code": "big_request", "value": r.prompt_chars})
    if r.output_chars >= _LONG_ANSWER_CHARS:
        out.append({"code": "long_answer", "value": r.output_chars})
    if r.duration_ms >= _LONG_RUN_MS:
        out.append({"code": "long_run", "value": r.duration_ms})
    if r.rate_multiplier is not None and r.rate_multiplier >= _PREMIUM_MULTIPLIER:
        out.append({"code": "premium_model", "value": r.rate_multiplier})
    if r.outcome in _UNFINISHED_OUTCOMES:
        out.append({"code": "unfinished", "value": r.outcome})
    return out


def _turn_json(r: _Row) -> dict[str, Any]:
    """One prompt for the page: what ran, what it was for, what it did, why it cost."""
    return {
        "ts": r.ts,
        "session": r.session,
        "slot": r.slot,
        "parent": r.parent or None,
        "for_slot": r.for_slot or None,
        "share": r.share,
        "category": r.category,
        "surface": r.surface,
        "service": _service_label(r),
        "request": r.request or None,
        "model": r.model,
        "rate_multiplier": r.rate_multiplier,
        "agent": r.agent,
        "app": r.app,
        "credits": _round(r.credits),
        "cost_usd": _round(r.cost),
        "duration_ms": r.duration_ms or None,
        "ttft_ms": r.ttft_ms or None,
        "outcome": r.outcome or None,
        "stop_reason": r.stop_reason or None,
        "context_pct": (
            round(r.context_used / r.context_window * 100, 1) if r.context_window else None
        ),
        "context_tokens": r.context_used or None,
        "tool_calls": r.tool_calls if r.tool_calls >= 0 else None,
        "tool_kinds": dict(r.tool_kinds),
        "prompt_chars": r.prompt_chars if r.prompt_chars >= 0 else None,
        "output_chars": r.output_chars if r.output_chars >= 0 else None,
        "compactions": r.compactions if r.compactions >= 0 else None,
        "reasons": row_reasons(r),
    }


def _band(value: float, bands: Iterable[tuple[str, Any]]) -> str:
    label = ""
    for name, lower in bands:
        if value >= lower:
            label = name
    return label


def _drivers(rows: list[_Row]) -> dict[str, Any]:
    """Average credits per prompt by tool-call count and by context fill.

    The user's own rows, grouped; no fixed claim about cost. A row whose counter
    predates the field is left out of that grouping rather than read as zero.
    """

    def summarise(groups: dict[str, list[float]], order: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "band": name,
                "turns": len(groups.get(name, [])),
                "credits": _round(sum(groups.get(name, []))),
                "avg_credits": (
                    _round(sum(groups[name]) / len(groups[name])) if groups.get(name) else 0.0
                ),
            }
            for name in order
        ]

    by_tools: dict[str, list[float]] = {}
    by_context: dict[str, list[float]] = {}
    for r in rows:
        if r.tool_calls >= 0:
            by_tools.setdefault(_band(r.tool_calls, _TOOL_BANDS), []).append(r.credits)
        if r.context_window > 0:
            by_context.setdefault(
                _band(r.context_used / r.context_window, _CONTEXT_BANDS), []
            ).append(r.credits)
    return {
        "tool_calls": summarise(by_tools, [b for b, _ in _TOOL_BANDS]),
        "context_fill": summarise(by_context, [b for b, _ in _CONTEXT_BANDS]),
        "measured_turns": sum(1 for r in rows if r.tool_calls >= 0),
    }


def _recurring(rows: list[_Row], span_days: int) -> list[dict[str, Any]]:
    """Each background service's rhythm: runs, cost per run and per day, a 30-day pace."""
    groups: dict[str, list[_Row]] = {}
    for r in rows:
        if r.category in _RECURRING_CATEGORIES:
            groups.setdefault(_service_label(r), []).append(r)
    days = max(span_days, 1)
    out: list[dict[str, Any]] = []
    for name, rs in groups.items():
        total = sum(r.credits for r in rs)
        out.append(
            {
                "service": name,
                "runs": len(rs),
                "active_days": len({r.day for r in rs}),
                "runs_per_day": round(len(rs) / days, 2),
                "credits": _round(total),
                "credits_per_run": _round(total / len(rs)),
                "credits_per_day": _round(total / days),
                "projected_30d": _round(total / days * 30),
                "faults": sum(1 for r in rs if r.outcome in _FAULT_OUTCOMES),
                "last_ts": max(r.ts for r in rs),
            }
        )
    out.sort(key=lambda d: (-d["credits"], d["service"]))
    return out


def _tools(rows: list[_Row]) -> list[dict[str, Any]]:
    """Tool kinds by calls, with the prompts that used them and those prompts' credits.

    ``credits`` is the total of the prompts that USED the kind, not a share of
    their bill: one prompt using two kinds counts under both.
    """
    calls: dict[str, int] = {}
    turns: dict[str, int] = {}
    credits_of: dict[str, float] = {}
    for r in rows:
        for kind, n in r.tool_kinds:
            calls[kind] = calls.get(kind, 0) + n
            turns[kind] = turns.get(kind, 0) + 1
            credits_of[kind] = credits_of.get(kind, 0.0) + r.credits
    out: list[dict[str, Any]] = [
        {"kind": k, "calls": calls[k], "turns": turns[k], "credits": _round(credits_of[k])}
        for k in calls
    ]
    out.sort(key=lambda d: (-d["calls"], d["kind"]))
    return out


def credit_summary(start: date, end: date) -> dict[str, Any]:
    """Aggregate the row store over the local days ``[start, end]``.

    Blocking (shard reads); call off-loop. Every figure counts every row,
    subagents included. ``series`` is hourly for a single day, daily up to 120
    days and monthly beyond, each point split by category with its own p50/p90
    turn duration. ``months`` always covers the twelve calendar months ending
    with ``end``'s, so the page can show the long view whatever range is picked.
    """
    rows = _rows_between(start, end)
    span = (end - start).days + 1
    prior_rows = _rows_between(start - timedelta(days=span), start - timedelta(days=1))
    granularity = _granularity(start, end)

    series: dict[str, dict[str, Any]] = {
        k: {"key": k, "credits": 0.0, "turns": 0, "by_category": {}, "_d": []}
        for k in _series_keys(start, end, granularity)
    }
    by_category: dict[str, _Bucket] = {c: _Bucket() for c in CATEGORIES}
    by_service: dict[str, _Bucket] = {}
    by_model: dict[str, _Bucket] = {}
    by_hour = [0.0] * 24
    sessions: dict[str, dict[str, Any]] = {}

    for r in rows:
        point = series.get(_series_key(r, granularity))
        if point is not None:
            point["credits"] += r.credits
            point["turns"] += 1
            point["by_category"][r.category] = point["by_category"].get(r.category, 0.0) + r.credits
            if r.duration_ms > 0:
                point["_d"].append(float(r.duration_ms))
        by_category.setdefault(r.category, _Bucket()).add(r)
        label = _service_label(r)
        svc = by_service.get(label)
        if svc is None:
            svc = by_service[label] = _Bucket()
            svc.extra = {"surface": r.surface, "category": r.category}
        svc.add(r)
        by_model.setdefault(r.model or "unknown", _Bucket()).add(r)
        by_hour[r.hour] += r.credits
        if r.session:
            s = sessions.get(r.session)
            if s is None:
                s = sessions[r.session] = {
                    "slot": r.session,
                    "category": "",
                    "channel": telemetry_channel_of(r.session),
                    "credits": 0.0,
                    "own_credits": 0.0,
                    "subagent_credits": 0.0,
                    "background_credits": 0.0,
                    "turns": 0,
                    "subagent_turns": 0,
                    "background_turns": 0,
                    "models": set(),
                    "first_ts": r.ts,
                    "last_ts": r.ts,
                    "peak_context_pct": None,
                }
            s["credits"] += r.credits
            s["turns"] += 1
            if r.share == "subagents":
                s["subagent_credits"] += r.credits
                s["subagent_turns"] += 1
            elif r.share == "background":
                s["background_credits"] += r.credits
                s["background_turns"] += 1
            else:
                s["own_credits"] += r.credits
                if not s["category"]:
                    s["category"] = r.category
                if r.context_window > 0:
                    pct = round(r.context_used / r.context_window * 100, 1)
                    if s["peak_context_pct"] is None or pct > s["peak_context_pct"]:
                        s["peak_context_pct"] = pct
            if r.model:
                s["models"].add(r.model)
            s["first_ts"] = min(s["first_ts"], r.ts)
            s["last_ts"] = max(s["last_ts"], r.ts)

    series_out = []
    for point in series.values():
        durations = sorted(point.pop("_d"))
        point["credits"] = _round(point["credits"])
        point["by_category"] = {k: _round(v) for k, v in point["by_category"].items()}
        point["p50_duration_ms"] = _percentile(durations, 50)
        point["p90_duration_ms"] = _percentile(durations, 90)
        series_out.append(point)

    session_rows = []
    for s in sessions.values():
        s["category"] = s["category"] or "subagents"
        s["models"] = sorted(s["models"])
        for k in ("credits", "own_credits", "subagent_credits", "background_credits"):
            s[k] = _round(s[k])
        session_rows.append(s)
    session_rows.sort(key=lambda s: (-s["credits"], -s["turns"], s["slot"]))

    # The twelve calendar months ending with ``end``'s: the month after the same
    # month a year earlier, through the last day of ``end``'s month.
    month_start = date(end.year - (end.month < 12), end.month % 12 + 1, 1)
    month_last_day = date(end.year + (end.month == 12), end.month % 12 + 1, 1) - timedelta(days=1)
    month_rows = _rows_between(month_start, month_last_day)
    months: dict[str, dict[str, Any]] = {
        k: {"month": k, "credits": 0.0, "turns": 0}
        for k in _series_keys(month_start, month_last_day, "month")
    }
    for r in month_rows:
        m = months.get(r.day[:7])
        if m is not None:
            m["credits"] += r.credits
            m["turns"] += 1
    for m in months.values():
        m["credits"] = _round(m["credits"])

    totals = _totals(rows)
    prior_credits = sum(r.credits for r in prior_rows)
    return {
        "range": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days": span,
            "granularity": granularity,
        },
        "totals": totals,
        "prior": {
            "from": (start - timedelta(days=span)).isoformat(),
            "to": (start - timedelta(days=1)).isoformat(),
            "credits": _round(prior_credits),
            "turns": len(prior_rows),
            "delta_pct": (
                round((totals["credits"] - prior_credits) / prior_credits * 100, 1)
                if prior_credits > 0
                else None
            ),
        },
        "series": series_out,
        "by_category": [{"category": c, **b.figures()} for c, b in by_category.items()],
        "by_service": _ranked(by_service, "service"),
        "by_model": _ranked(by_model, "model"),
        "by_hour": [_round(v) for v in by_hour],
        "distributions": {
            "duration_ms": _histogram(
                (r.duration_ms for r in rows if r.duration_ms > 0), _DURATION_EDGES_MS
            ),
            "ttft_ms": _histogram((r.ttft_ms for r in rows if r.ttft_ms > 0), _TTFT_EDGES_MS),
            "credits": _histogram((r.credits for r in rows if r.credits > 0), _CREDIT_EDGES),
        },
        "sessions": session_rows[:_MAX_SESSIONS],
        "sessions_total": len(session_rows),
        "months": list(months.values()),
        "reconciliation": reconcile(rows, start, end),
        "drivers": _drivers(rows),
        "top_turns": [
            _turn_json(r) for r in sorted(rows, key=lambda r: (-r.credits, -r.ts))[:_TOP_TURNS]
        ],
        "recurring": _recurring(rows, span),
        "tools": _tools(rows),
        "account_rate": account_rate(),
    }


def credit_turns(
    start: date,
    end: date,
    *,
    session: str = "",
    service: str = "",
    category: str = "",
    model: str = "",
    outcome: str = "",
    reason: str = "",
    before: float | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """One page of per-prompt rows in ``[start, end]``, newest first.

    Blocking; call off-loop. Filters are exact matches: ``session`` matches the
    session a row is filed under (a subagent row matches its parent's),
    ``service`` the row's service-or-surface label. ``before`` is the cursor --
    the ``ts`` of the last row of the previous page -- so a page is stable while
    new turns keep landing at the head.
    """
    limit = max(1, min(limit, MAX_TURNS_LIMIT))
    want_session = spend_key_for_slot(session) if session else ""
    picked: list[_Row] = []
    for r in reversed(_rows_between(start, end)):
        if before is not None and r.ts >= before:
            continue
        if want_session and r.session != want_session:
            continue
        if service and _service_label(r) != service:
            continue
        if category and r.category != category:
            continue
        if model and (r.model or "unknown") != model:
            continue
        if outcome and (r.outcome or "unknown") != outcome:
            continue
        if reason and not any(x["code"] == reason for x in row_reasons(r)):
            continue
        picked.append(r)
        if len(picked) > limit:
            break
    has_more = len(picked) > limit
    page = picked[:limit]
    return {
        "turns": [_turn_json(r) for r in page],
        "next_before": page[-1].ts if has_more and page else None,
    }


# ── account-reading history ─────────────────────────────────────────────────

#: Re-append an unchanged reading at most this often, so the history keeps a
#: heartbeat across a quiet stretch without one line per ten-minute poll.
_ACCOUNT_HEARTBEAT_S = 3600

_ACCOUNT_LOCK = threading.Lock()
_ACCOUNT_LAST: dict[str, Any] = {}


def _account_dir() -> Path:
    return data_home() / "usage" / "account"


def _account_key(payload: dict[str, Any]) -> str:
    """A pseudonymous key telling one signed-in account's readings from another's.

    Hashed so the history file carries no email; the readout itself still shows
    the identity from the live cache. ``""`` when the reading names no identity.
    """
    ident = str(payload.get("email") or payload.get("start_url") or "").strip().lower()
    if not ident:
        return ""
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def record_account_reading(payload: dict[str, Any], now: datetime | None = None) -> None:
    """Append one published credit reading to the account history. Blocking.

    Called with what ``/api/sessions/usage`` just published, identity already
    proven by the caller. ``consumed`` is the plan's ``credits_used`` plus every
    bonus pool's ``used``: bonus credits are spent before the plan, so a turn
    billed from a bonus pool moves only the bonus figure. A reading identical to
    the last one written is skipped unless an hour has passed. Never raises.
    """
    try:
        used = _finite(payload.get("credits_used"))
        if used is None:
            return
        bonus = 0.0
        for grant in payload.get("bonus_credits") or []:
            if isinstance(grant, dict):
                bonus += _finite(grant.get("used")) or 0.0
        now = now or datetime.now().astimezone()
        record = {
            "ts": now.isoformat(),
            "account": _account_key(payload),
            "consumed": round(used + bonus, 6),
            "credits_used": used,
            "bonus_used": round(bonus, 6),
            "credits_plan": _finite(payload.get("credits_plan")),
            "resets": str(payload.get("resets") or "")[:32],
            "plan": str(payload.get("plan") or "")[:100],
            "source": str(payload.get("source") or "")[:16],
        }
        sig = (record["account"], record["consumed"], record["credits_plan"], record["resets"])
        with _ACCOUNT_LOCK:
            last_sig = _ACCOUNT_LAST.get("sig")
            last_ts = float(_ACCOUNT_LAST.get("ts") or 0.0)
            if sig == last_sig and now.timestamp() - last_ts < _ACCOUNT_HEARTBEAT_S:
                return
            path = _account_dir() / f"{now.strftime('%Y-%m')}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, allow_nan=False) + "\n")
            _ACCOUNT_LAST.update({"sig": sig, "ts": now.timestamp()})
    except Exception:
        logger.debug("account reading append failed", exc_info=True)


def account_readings(start: date, end: date) -> list[dict[str, Any]]:
    """Readings dated within ``[start, end]`` (local days), oldest first. Blocking."""
    out: list[dict[str, Any]] = []
    lo, hi = start.isoformat(), end.isoformat()
    months = _series_keys(start, end, "month")
    for month in months:
        path = _account_dir() / f"{month}.jsonl"
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                for line in bounded_records(fh, path, label="usage-account"):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    dt = _parse_row_dt(obj.get("ts"))
                    consumed = _finite(obj.get("consumed"))
                    if dt is None or consumed is None:
                        continue
                    try:
                        local = dt.astimezone()
                    except (ValueError, OverflowError, OSError):
                        continue
                    if not lo <= local.strftime("%Y-%m-%d") <= hi:
                        continue
                    out.append(
                        {
                            "ts": local.timestamp(),
                            "account": str(obj.get("account") or ""),
                            "consumed": consumed,
                            "resets": str(obj.get("resets") or ""),
                        }
                    )
        except (OSError, UnicodeDecodeError):
            continue
    out.sort(key=lambda r: r["ts"])
    return out


def _current_account(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The readings of the most recently seen account, oldest first.

    A reading whose identity was not proven carries no key; it is kept with the
    current account rather than splitting one history in two.
    """
    account = next((r["account"] for r in reversed(readings) if r["account"]), "")
    return [r for r in readings if r["account"] in (account, "")]


def _consumed_between(readings: list[dict[str, Any]]) -> float:
    """What the account consumed across consecutive readings; a fall is a cycle reset."""
    consumed = 0.0
    for prev, cur in zip(readings, readings[1:]):
        step = cur["consumed"] - prev["consumed"]
        consumed += step if step >= 0 else cur["consumed"]
    return consumed


#: Window the account's own spending rate is measured over, and the shortest
#: span of readings a rate is quoted from (a few minutes of readings is noise).
_ACCOUNT_RATE_DAYS = 14
_ACCOUNT_RATE_MIN_DAYS = 0.25


def account_rate(now: datetime | None = None) -> dict[str, Any] | None:
    """The account's own credits per day over its recent readings. Blocking.

    This is what the plan projection should use: it counts every credit the
    account was charged, including spend this host never recorded as a turn,
    where the row store's rate counts only what Kiro Crew saw. ``None`` until
    readings span a few hours (a fresh history, an API-key account).
    """
    now = now or datetime.now().astimezone()
    readings = _current_account(
        account_readings((now - timedelta(days=_ACCOUNT_RATE_DAYS)).date(), now.date())
    )
    if len(readings) < 2:
        return None
    span_days = (readings[-1]["ts"] - readings[0]["ts"]) / 86400
    if span_days < _ACCOUNT_RATE_MIN_DAYS:
        return None
    return {
        "per_day": _round(_consumed_between(readings) / span_days),
        "days": round(span_days, 2),
        "readings": len(readings),
    }


#: Most points the reconciliation series carries; longer histories are thinned
#: evenly (always keeping the last reading).
_MAX_RECONCILE_POINTS = 400


def reconcile(rows: list[_Row], start: date, end: date) -> dict[str, Any] | None:
    """Compare the account's own consumption with the recorded rows. Blocking.

    Uses the readings of the most recently seen account only. Between two
    consecutive readings the account consumed ``next - prev``; a fall (the cycle
    reset) means it consumed ``next`` since the reset. ``recorded`` sums the rows
    stamped after the first reading and up to the last, so both sides cover the
    same span. ``unattributed`` is the account's figure minus the recorded one:
    positive is spend this host never wrote a row for. Kiro meters with a delay,
    so a short window can read briefly negative; the series shows how the two
    track over time. ``None`` without two readings (an API-key account, a fresh
    install, a window before the history began).
    """
    readings = _current_account(account_readings(start, end))
    if len(readings) < 2:
        return None
    first_ts, last_ts = readings[0]["ts"], readings[-1]["ts"]
    span_rows = [r for r in rows if first_ts < r.ts <= last_ts]
    points: list[dict[str, Any]] = []
    consumed = 0.0
    recorded = 0.0
    i = 0
    prev = readings[0]
    points.append({"ts": first_ts, "account": 0.0, "recorded": 0.0})
    for reading in readings[1:]:
        step = reading["consumed"] - prev["consumed"]
        consumed += step if step >= 0 else reading["consumed"]
        while i < len(span_rows) and span_rows[i].ts <= reading["ts"]:
            recorded += span_rows[i].credits
            i += 1
        points.append(
            {"ts": reading["ts"], "account": _round(consumed), "recorded": _round(recorded)}
        )
        prev = reading
    if len(points) > _MAX_RECONCILE_POINTS:
        stride = len(points) / _MAX_RECONCILE_POINTS
        thinned = [points[int(k * stride)] for k in range(_MAX_RECONCILE_POINTS - 1)]
        points = thinned + [points[-1]]
    return {
        "from_ts": first_ts,
        "to_ts": last_ts,
        "readings": len(readings),
        "account_credits": _round(consumed),
        "recorded_credits": _round(recorded),
        "unattributed_credits": _round(consumed - recorded),
        "series": points,
    }


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _parse_day(raw: str | None, default: date) -> date | None:
    if not raw:
        return default
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _range_from(request: web.Request) -> tuple[date, date] | web.Response:
    today = datetime.now().astimezone().date()
    end = _parse_day(request.query.get("to"), today)
    start = _parse_day(request.query.get("from"), (end or today) - timedelta(days=29))
    if start is None or end is None:
        return web.json_response(
            {"error": "from/to must be YYYY-MM-DD", "code": "bad_range"}, status=400
        )
    if start > end:
        start, end = end, start
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
    return start, end


async def _refuse_app_caller(request: web.Request, operation: str) -> web.Response | None:
    """Dashboard-only: an app caller gets the indistinguishable 404, audited."""
    request_app = str(request.get("app", "") or "")
    if not request_app:
        return None

    def _audit() -> None:
        _sel_mod.sel().log_api_access(
            caller=request_app,
            operation=operation,
            outcome="denied",
            source="app_isolation",
            resources=f"path={request.path}",
            error="dashboard-only endpoint",
        )

    await asyncio.to_thread(_audit)
    return web.json_response({"error": "not found", "code": "not_found"}, status=404)


async def _session_titles(request: web.Request, keys: list[str]) -> dict[str, str]:
    """Redacted titles for *keys*: the live slot first, then the transcript's."""
    from kiro_crew.dashboard.handlers.telemetry import _persisted_titles
    from kiro_crew.dashboard.state import NEW_SESSION_TITLE

    try:
        state = request.app["state"]
    except KeyError:
        return {}
    get_slot = getattr(state, "get_slot", None)
    titles: dict[str, str] = {}
    unresolved: list[str] = []
    for key in keys:
        slot = None
        if callable(get_slot):
            # Dashboard spend is filed under the ``dashboard:`` session key
            # (spend_key_for_slot); the slot map is keyed by the bare one.
            slot = get_slot(key.removeprefix("dashboard:"))
        title = getattr(slot, "display_title", "") if slot is not None else ""
        if title and title != NEW_SESSION_TITLE:
            titles[key] = str(title)
        else:
            unresolved.append(key)
    conversation_log = getattr(state, "conversation_log", None)
    if unresolved and conversation_log is not None:
        try:
            titles.update(await asyncio.to_thread(_persisted_titles, conversation_log, unresolved))
        except Exception:
            logger.debug("persisted title lookup failed", exc_info=True)
    out: dict[str, str] = {}
    for key, title in titles.items():
        safe, _ = redact_exfiltration_urls(title)
        safe, _ = redact_credentials(safe)
        out[key] = safe
    return out


async def api_usage_credits_summary(request: web.Request) -> web.Response:
    """GET /api/usage/credits/summary?from=YYYY-MM-DD&to=YYYY-MM-DD.

    The Usage page's whole aggregate in one read (see :func:`credit_summary`),
    with each listed session's title attached. ``from`` defaults to 29 days
    before ``to``, ``to`` to today; the range is clamped to
    :data:`MAX_RANGE_DAYS`. Independent of ``telemetry.enabled``: the row store
    is always written.
    """
    refused = await _refuse_app_caller(request, "usage_credits_summary")
    if refused is not None:
        return refused
    rng = _range_from(request)
    if isinstance(rng, web.Response):
        return rng
    summary = await asyncio.to_thread(credit_summary, *rng)
    keys = {s["slot"] for s in summary["sessions"]} | {
        t["session"] for t in summary["top_turns"] if t["session"]
    }
    titles = await _session_titles(request, sorted(keys))
    summary["sessions"] = [
        {**s, "title": titles.get(s["slot"]) or None} for s in summary["sessions"]
    ]
    summary["top_turns"] = [
        {**t, "title": titles.get(t["session"]) or None} for t in summary["top_turns"]
    ]
    return web.json_response(summary)


async def api_usage_credits_turns(request: web.Request) -> web.Response:
    """GET /api/usage/credits/turns — one page of per-prompt rows, newest first.

    Query: ``from`` / ``to`` as for the summary; optional exact filters
    ``session``, ``service``, ``category``, ``model``, ``outcome``, ``reason`` (a
    :func:`row_reasons` code); ``limit``
    (1..500, default 100) and the ``before`` cursor from the previous page's
    ``next_before``.
    """
    refused = await _refuse_app_caller(request, "usage_credits_turns")
    if refused is not None:
        return refused
    rng = _range_from(request)
    if isinstance(rng, web.Response):
        return rng
    q = request.query
    try:
        limit = int(q.get("limit") or 100)
    except ValueError:
        limit = 100
    before: float | None = None
    if q.get("before"):
        try:
            before = float(q["before"])
        except ValueError:
            return web.json_response(
                {"error": "before must be a number", "code": "bad_cursor"}, status=400
            )
        if not math.isfinite(before):
            before = None
    page = await asyncio.to_thread(
        credit_turns,
        *rng,
        session=(q.get("session") or "").strip(),
        service=(q.get("service") or "").strip(),
        category=(q.get("category") or "").strip(),
        model=(q.get("model") or "").strip(),
        outcome=(q.get("outcome") or "").strip(),
        reason=(q.get("reason") or "").strip(),
        before=before,
        limit=limit,
    )
    titles = await _session_titles(
        request, sorted({t["session"] for t in page["turns"] if t["session"]})
    )
    page["turns"] = [{**t, "title": titles.get(t["session"]) or None} for t in page["turns"]]
    page["range"] = {"from": rng[0].isoformat(), "to": rng[1].isoformat()}
    return web.json_response(page)
