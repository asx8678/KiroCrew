"""How long a Slack thread keeps answering without a fresh mention.

A followed thread used to stay live for as long as its session file existed.
Follow now lasts 45 minutes after the bot's last post in that thread. A
mention still admits the message. ``[[NO_REPLY]]`` as the whole answer posts
nothing.
"""

from __future__ import annotations

import time

FOLLOW_TTL_SECS = 45 * 60
SILENT_REPLY = "[[NO_REPLY]]"

_last_bot_post: dict[str, float] = {}

#: Entries kept before expired ones are swept. Each followed thread adds one and
#: nothing else removes them, so without a sweep the map grows for the process's
#: life.
_SWEEP_AT = 4096


def note_bot_post(thread_ts: str | None, when: float | None = None) -> None:
    if thread_ts:
        _last_bot_post[str(thread_ts)] = time.time() if when is None else when
        if len(_last_bot_post) > _SWEEP_AT:
            cutoff = time.time() - FOLLOW_TTL_SECS
            for key in [k for k, ts in _last_bot_post.items() if ts < cutoff]:
                del _last_bot_post[key]


def has_bot_post(thread_ts: str | None) -> bool:
    """Whether this process has recorded a bot post for *thread_ts*."""
    return bool(thread_ts) and str(thread_ts) in _last_bot_post


def follow_is_fresh(thread_ts: str | None, now: float | None = None) -> bool:
    if not thread_ts:
        return False
    posted = _last_bot_post.get(str(thread_ts))
    if posted is None:
        return False
    now = time.time() if now is None else now
    return (now - posted) <= FOLLOW_TTL_SECS


def is_silent_reply(text: str | None) -> bool:
    return (text or "").strip() == SILENT_REPLY
