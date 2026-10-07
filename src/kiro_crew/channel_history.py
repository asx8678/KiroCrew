"""Channel History Buffer — rolling window of recent messages per channel.

Captures all messages in group channels (not just @mentions) so that
when the agent is invoked, it has conversational context about what
was being discussed.

Non-observe channels are ephemeral / in-memory only.  Observe-mode
channels are persisted to disk as JSONL so history survives restarts.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults
_DEFAULT_MAX_ENTRIES = 50  # per channel
_DEFAULT_TTL_SECS = 300  # 5 minutes

# Observe-mode channels get a deeper buffer
OBSERVE_MAX_ENTRIES = 200
OBSERVE_TTL_SECS = 604800  # 1 week


@dataclass
class HistoryEntry:
    """A single message in the channel history buffer."""

    user: str
    text: str
    thread_ts: str | None = None
    msg_ts: str | None = None  # Slack message ts — identifies thread parent
    timestamp: float = field(default_factory=time.monotonic)
    wall_ts: float | None = None  # wall-clock time (observe-mode only, for persistence)


class ChannelHistory:
    """Per-channel rolling window of recent messages.

    Usage:
        history = ChannelHistory()
        history.push("C0ABC123", "alice", "The pipeline is broken")
        history.push("C0ABC123", "bob", "I see 5xx errors in us-west-2")

        # When @kirocrew is mentioned:
        context = history.context_for("C0ABC123")
        # → "[Recent channel messages for context:]\\n  alice (1m ago): ..."
    """

    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_secs: int = _DEFAULT_TTL_SECS,
        observe_max_entries: int = OBSERVE_MAX_ENTRIES,
        observe_ttl_secs: int = OBSERVE_TTL_SECS,
        history_dir: Path | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_secs = ttl_secs
        self._observe_max_entries = observe_max_entries
        self._observe_ttl_secs = observe_ttl_secs
        self._history_dir = history_dir
        self._channels: dict[str, deque[HistoryEntry]] = {}
        self._observe_channels: set[str] = set()  # channels with deeper buffer
        self._user_names: dict[str, str] = {}  # user_id -> display name cache

    def set_user_name(self, user_id: str, name: str) -> None:
        """Cache a display name for a user ID."""
        if user_id and name:
            self._user_names[user_id] = name

    def set_observe(self, channel_id: str) -> None:
        """Enable observe mode for a channel (deeper history buffer).

        If a history file exists on disk, loads it into memory.
        """
        self._observe_channels.add(channel_id)
        # Upgrade existing buffer to larger capacity
        buf = self._channels.get(channel_id)
        if buf is not None and buf.maxlen != self._observe_max_entries:
            new_buf: deque[HistoryEntry] = deque(buf, maxlen=self._observe_max_entries)
            self._channels[channel_id] = new_buf

        # Load persisted history from disk
        self._load_observe(channel_id)

    def unset_observe(self, channel_id: str) -> None:
        """Disable observe mode for a channel (revert to default buffer)."""
        self._observe_channels.discard(channel_id)
        buf = self._channels.get(channel_id)
        if buf is not None and buf.maxlen != self._max_entries:
            new_buf: deque[HistoryEntry] = deque(buf, maxlen=self._max_entries)
            self._channels[channel_id] = new_buf

        # Remove the persisted history file
        path = self._observe_path(channel_id)
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning("Failed to remove history file %s", path, exc_info=True)

    def push(
        self,
        channel_id: str,
        user: str,
        text: str,
        thread_ts: str | None = None,
        msg_ts: str | None = None,
    ) -> None:
        """Record a message in the channel buffer.

        Called on every message event in the gateway, not just @mentions.
        Evicts stale entries (TTL) and oldest entries (capacity).
        """
        if not channel_id or not text:
            return

        is_observe = channel_id in self._observe_channels

        buf = self._channels.get(channel_id)
        if buf is None:
            maxlen = self._observe_max_entries if is_observe else self._max_entries
            buf = deque(maxlen=maxlen)
            self._channels[channel_id] = buf

        # Evict expired entries
        self._evict(buf, channel_id)

        # Build entry — observe channels use wall clock for persistence
        # Every entry carries wall clock: observe mode persists it, and the
        # renderer's absolute HH:MM needs it in normal mode too.
        wall_ts = time.time()
        entry = HistoryEntry(
            user=user, text=text, thread_ts=thread_ts, msg_ts=msg_ts, wall_ts=wall_ts
        )
        buf.append(entry)

        # Persist to disk for observe channels
        if is_observe:
            self._append_to_disk(channel_id, entry)

    def context_for(
        self,
        channel_id: str,
        thread_ts: str | None = None,
        since_ts: str | None = None,
        exclude_ts: str | None = None,
        watermark_out: list[str] | None = None,
    ) -> str:
        """Format recent messages for injection into LLM context.

        When *thread_ts* is provided, messages are split into current-thread
        and other-thread sections so the LLM can distinguish them.
        Returns empty string if no relevant history exists.

        *since_ts* filters a thread view to messages the session has NOT been
        shown yet: entries with ``msg_ts <= since_ts`` are dropped, so a thread
        turn no longer re-sends the whole buffered tail every turn (measured:
        ~95% of the block was already sent the turn before). *exclude_ts* drops
        the single entry whose ``msg_ts`` matches — the current incoming
        message, which the turn text already carries as the request. When
        *watermark_out* is given, the newest ``msg_ts`` the returned block
        actually included is appended to it (the value the caller should show
        "up to" next turn); entries that cannot be ordered (no/bad ``msg_ts``)
        are kept and never move the watermark.
        """
        buf = self._channels.get(channel_id)
        if not buf:
            return ""

        # Evict expired before formatting
        self._evict(buf, channel_id)

        if not buf:
            return ""

        now_wall = time.time()

        def _fmt(entry: HistoryEntry) -> str:
            # Absolute HH:MM, not a relative age: a re-rendered line must be
            # byte-identical to its first send (the thread watermark dedup
            # compares nothing, but the provider window holds both copies,
            # and '(1m ago)' drifts between them for the same message).
            clock = entry.wall_ts if entry.wall_ts is not None else now_wall
            age_str = datetime.fromtimestamp(clock).strftime("%H:%M")
            text = entry.text[:300]
            if len(entry.text) > 300:
                text += "\u2026"
            display = self._user_names.get(entry.user) or entry.user
            return f"  {display} ({age_str}): {text}"

        # Split by thread if thread_ts provided — only include current thread
        if thread_ts:
            # Include messages whose msg_ts matches thread_ts — this captures the
            # thread parent, whose own thread_ts is None until it receives a reply.
            def _ts_value(ts: str | None) -> float | None:
                if not ts:
                    return None
                try:
                    return float(ts)
                except (TypeError, ValueError):
                    return None

            _since_v = _ts_value(since_ts)
            _exclude_v = _ts_value(exclude_ts)

            def _shown(entry: HistoryEntry) -> bool:
                # An entry with no parsable msg_ts cannot be ordered against
                # the watermark: keep it (no data to filter on) rather than
                # silently drop a message the session may never have seen.
                ts_v = _ts_value(entry.msg_ts)
                if ts_v is None:
                    return True
                if _exclude_v is not None and ts_v == _exclude_v:
                    return False
                if _since_v is not None and ts_v <= _since_v:
                    return False
                return True

            entries = [
                e for e in buf if (e.thread_ts == thread_ts or e.msg_ts == thread_ts) and _shown(e)
            ]
            if watermark_out is not None:
                # The newest msg_ts the block covered, INCLUDING the excluded
                # current message: the turn text carries it as the request, so
                # the next turn must not surface it through the buffer either.
                # Entries that cannot be ordered never move it.
                ordered = [
                    (_ts_value(e.msg_ts), e.msg_ts)
                    for e in entries
                    if e.msg_ts and _ts_value(e.msg_ts) is not None
                ]
                if _exclude_v is not None and exclude_ts:
                    ordered.append((_exclude_v, exclude_ts))
                if ordered:
                    watermark_out.append(max(ordered, key=lambda pair: pair[0])[1])
                else:
                    watermark_out.append(since_ts or "")
            current = [_fmt(e) for e in entries]
            if not current:
                return ""
            return (
                "[Recent channel messages for context:]\n"
                "[Current thread:]\n" + "\n".join(current) + "\n[End of channel context]\n\n"
            )

        # No thread_ts — only include top-level (non-thread) messages
        lines: list[str] = [_fmt(e) for e in buf if e.thread_ts is None]
        return (
            "[Recent channel messages for context:]\n"
            + "\n".join(lines)
            + "\n[End of channel context]\n\n"
        )

    def clear(self, channel_id: str) -> None:
        """Clear history for a specific channel."""
        self._channels.pop(channel_id, None)

    @property
    def channel_count(self) -> int:
        """Number of channels with buffered history."""
        return len(self._channels)

    def entry_count(self, channel_id: str) -> int:
        """Number of entries in a specific channel buffer."""
        buf = self._channels.get(channel_id)
        return len(buf) if buf else 0

    # ── Persistence helpers ──────────────────────────────────────────────

    def _observe_path(self, channel_id: str) -> Path | None:
        """Return the JSONL file path for an observe channel, or None."""
        if self._history_dir is None:
            return None
        from kiro_crew.hooks import is_sensitive_path

        history_root = self._history_dir.resolve()
        path = (self._history_dir / f"{channel_id}.jsonl").resolve()
        # Boundary-aware containment: a bare str.startswith has no trailing
        # separator, so a sibling dir sharing the prefix (e.g. ".../hist-evil"
        # vs ".../hist") would pass. is_relative_to compares path components.
        if not path.is_relative_to(history_root):
            logger.warning("Refusing unsafe history path for channel %s", channel_id)
            return None
        if is_sensitive_path(str(path)):
            logger.warning("Refusing sensitive history path for channel %s", channel_id)
            return None
        return path

    def _append_to_disk(self, channel_id: str, entry: HistoryEntry) -> None:
        """Append a single entry to the channel's JSONL file."""
        path = self._observe_path(channel_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "user": entry.user,
                    "text": entry.text,
                    "thread_ts": entry.thread_ts,
                    "msg_ts": entry.msg_ts,
                    "ts": entry.wall_ts,
                },
                ensure_ascii=False,
            )
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            logger.warning("Failed to append to history file %s", path, exc_info=True)

    def _load_observe(self, channel_id: str) -> None:
        """Load persisted observe history from disk into the in-memory deque.

        Filters out entries older than TTL.  If any expired entries were
        found, rewrites the file without them (lazy compaction).
        """
        path = self._observe_path(channel_id)
        if path is None or not path.exists():
            return

        cutoff = time.time() - self._observe_ttl_secs
        entries: list[HistoryEntry] = []
        had_expired = False

        try:
            with path.open("r", encoding="utf-8") as f:
                for line_no, raw in enumerate(f, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Corrupt JSONL line %d in %s — skipping", line_no, path)
                        continue
                    wall_ts = data.get("ts")
                    if wall_ts is None:
                        continue
                    if wall_ts < cutoff:
                        had_expired = True
                        continue
                    # Convert wall clock to a monotonic offset so existing code works
                    age_secs = time.time() - wall_ts
                    mono_ts = time.monotonic() - age_secs
                    entries.append(
                        HistoryEntry(
                            user=data.get("user", ""),
                            text=data.get("text", ""),
                            thread_ts=data.get("thread_ts"),
                            msg_ts=data.get("msg_ts"),
                            timestamp=mono_ts,
                            wall_ts=wall_ts,
                        )
                    )
        except OSError:
            logger.warning("Failed to read history file %s", path, exc_info=True)
            return

        # Populate deque (recent entries only, respecting maxlen)
        buf = self._channels.get(channel_id)
        if buf is None:
            buf = deque(maxlen=self._observe_max_entries)
            self._channels[channel_id] = buf

        # Merge: disk entries first (older), then any already in-memory
        existing = list(buf)
        buf.clear()
        for e in entries:
            buf.append(e)
        for e in existing:
            buf.append(e)

        logger.info("Loaded %d entries for channel %s from disk", len(entries), channel_id)

        # Lazy compaction: rewrite file without expired entries
        if had_expired:
            self._compact(channel_id)

    def _compact(self, channel_id: str) -> None:
        """Rewrite the JSONL file with only the current in-memory entries."""
        path = self._observe_path(channel_id)
        if path is None:
            return
        buf = self._channels.get(channel_id)
        if not buf:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            from kiro_crew.atomic_write import atomic_write

            lines = []
            for entry in buf:
                if entry.wall_ts is None:
                    continue
                line = json.dumps(
                    {
                        "user": entry.user,
                        "text": entry.text,
                        "thread_ts": entry.thread_ts,
                        "msg_ts": entry.msg_ts,
                        "ts": entry.wall_ts,
                    },
                    ensure_ascii=False,
                )
                lines.append(line + "\n")
            atomic_write(path, "".join(lines))
        except OSError:
            logger.warning("Failed to compact history file %s", path, exc_info=True)

    def _evict(self, buf: deque[HistoryEntry], channel_id: str | None = None) -> None:
        """Remove entries older than TTL from the front of the deque."""
        is_observe = channel_id and channel_id in self._observe_channels
        ttl = self._observe_ttl_secs if is_observe else self._ttl_secs
        if is_observe:
            # Observe channels use wall clock
            cutoff = time.time() - ttl
            while buf and buf[0].wall_ts is not None and buf[0].wall_ts < cutoff:
                buf.popleft()
        else:
            # Non-observe channels use monotonic clock
            cutoff = time.monotonic() - ttl
            while buf and buf[0].timestamp < cutoff:
                buf.popleft()
