"""Spot a turn that keeps making the same tool call and getting the same result.

An agent stuck in a doom loop re-runs one action that does not work, over and
over, instead of changing strategy. The signal is narrow on purpose: the SAME
tool with the SAME input coming back with the SAME result (status and output)
:data:`REPEAT_LOOP_THRESHOLD` times in one turn. Repeating such a call again
cannot change its outcome, so the turn is told so once, in-band, and left to
decide. Nothing is aborted: a call that is deliberately polled for an outside
change trips the same signal, and the notice says to carry on in that case.

A leaf: stdlib only, so every surface that runs a turn can hold one tracker
per turn without importing the dashboard.
"""

from __future__ import annotations

import hashlib
import re

#: Identical results of one identical call, in one turn, before the notice.
REPEAT_LOOP_THRESHOLD = 3

#: WF-6: volatile tokens a repeated failure carries without being a different
#: failure. Masked to a fixed token before comparison, so consecutive runs of
#: the SAME failing test/command (different durations, ports, pids, addresses,
#: timestamps, hex ids) fingerprint identically and the loop notice and
#: fail-fast fire on real loops instead of being defeated by byte drift.
_VOLATILE_RES: "tuple[tuple[re.Pattern[str], str], ...]" = (
    # Order matters: the specific shapes run BEFORE the generic number mask,
    # or the number mask eats the timestamp's digits first.
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?\b"), "<ts>"),
    # UUIDs before the generic hex mask, or the hex mask eats their segments
    # one at a time and a dashed UUID never compares equal to itself.
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    # Scratch paths: a repeated call names a fresh temp file/dir each run, and
    # the path is exactly the kind of byte drift that defeated the detector.
    (re.compile(r"(?<![\w/])(?:/tmp/|/private/tmp/|/var/folders/)[^\s\"']*"), "<tmp>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b"), "<dur>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}\b"), "<ip:port>"),
    (re.compile(r"\b[0-9a-fA-F]{8,64}\b"), "<hex>"),
    (re.compile(r"(?<!\w)\d{2,5}(?=[ .,;]|$)"), "<n>"),
)


def error_fingerprint(text: str) -> str:
    """The stable comparison key for a failed step's error text.

    Masks volatile tokens (durations, ports, pids, addresses, timestamps, hex
    ids) to fixed placeholders, KEYED on the FAILED/ERROR/Assertion lines —
    the lines that say WHY it failed — so two failures that differ only by the
    noise of a new run compare equal, while genuinely different failures do
    not. ``task.error`` keeps its full text for display; only the loop
    detector compares fingerprints (WF-6).
    """
    kept: list[str] = []
    for line in text.splitlines():
        if not (
            "FAILED" in line
            or "ERROR" in line
            or "AssertionError" in line
            or "Traceback" in line
            or "error" in line.lower()
        ):
            continue
        masked = line
        for pattern, token in _VOLATILE_RES:
            masked = pattern.sub(token, masked)
        kept.append(masked.strip())
    return "\n".join(kept)


def _mask_volatile(text: str) -> str:
    """The comparison-only normalizer the repeat-loop tracker (TOOL-20)
    shares with :func:`error_fingerprint`'s line filter: the same volatile
    masks over a WHOLE text, so a repeated call whose input or output
    differs only in volatile tokens (timestamps, UUIDs, tmp paths, durations,
    ports, pids, hex ids) compares equal to itself. Display text is never
    masked -- only the digest the detector compares."""
    for pattern, token in _VOLATILE_RES:
        text = pattern.sub(token, text)
    return text


#: Bound on the call ids and signatures one tracker holds; a turn past it stops
#: tracking new ones rather than growing without limit.
_MAX_TRACKED = 512

#: Longest slice of the call's title quoted back in the notice.
_TITLE_LIMIT = 200

#: TOOL-20: advisory-only per-turn DISTINCT tool-call ceiling. One in-band
#: notice once a turn passes this many calls; never an abort -- a deliberate
#: long sweep trips the same count, and the notice says to carry on if that is
#: what is happening. Nothing in the tracker ever stops a call.
TURN_CALL_ADVISORY_AT = 60


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def build_repeat_loop_notice(title: str, count: int) -> str:
    """The in-band notice for a call repeated *count* times with one result."""
    shown = " ".join((title or "").split())[:_TITLE_LIMIT] or "a tool call"
    return (
        f"[Kiro Crew host notice] You have made the same tool call {count} times "
        f"this turn and got the same result each time: {shown}\n\n"
        "Running it again will not change the outcome. Stop and diagnose: read "
        "the result, check what you assumed, then try a genuinely different "
        "approach, or say plainly what blocks you. If you are deliberately "
        "waiting for something outside this turn to change, carry on."
    )


def build_turn_advisory_notice(count: int) -> str:
    """The one-time notice for an unusually long turn (TOOL-20).

    Advisory only, in-band, once per turn: the module never aborts anything,
    because a deliberate poll or a long sweep trips the same count and the
    notice says exactly that.
    """
    return (
        f"[Turn advisory] This turn has made {count} tool calls, which is unusually many. "
        "If this is a deliberate long-running sweep or a poll waiting on an outside "
        "change, carry on. If instead you are re-trying approaches that are not "
        "working, step back and reconsider the plan -- something earlier may be "
        "blocking progress. This notice appears once per turn."
    )


class RepeatLoopTracker:
    """Per-turn count of identical calls that return identical results."""

    def __init__(self) -> None:
        self._calls: dict[str, tuple[str, str, str]] = {}
        self._outputs: dict[str, str] = {}
        self._last_outcome: dict[str, str] = {}
        self._streak: dict[str, int] = {}
        self._warned: set[str] = set()
        # TOOL-20: distinct call ids this turn, and whether the one advisory
        # notice has gone out. Counted even past _MAX_TRACKED: the ceiling is
        # about turn LENGTH, not about which signatures fit the table.
        self._call_count = 0
        self._advised = False

    def note_call(self, tool_call_id: str, tool_name: str, tool_input: str, title: str) -> str:
        """Record what a call is, so its result can be matched to its input.

        Called again for the same id (an input refinement) it replaces the
        earlier record, because the refined input is the real one. Returns the
        one-time long-turn advisory notice (TOOL-20) once the turn passes
        ``TURN_CALL_ADVISORY_AT`` distinct calls, "" otherwise.
        """
        if not tool_call_id or not (tool_input or "").strip():
            return ""
        key = _digest(tool_call_id)
        fresh = key not in self._calls
        advisory = ""
        if fresh:
            self._call_count += 1
            if self._call_count >= TURN_CALL_ADVISORY_AT and not self._advised:
                self._advised = True
                advisory = build_turn_advisory_notice(self._call_count)
        if fresh and len(self._calls) >= _MAX_TRACKED:
            return advisory
        shown = " ".join((title or tool_name or "").split())[:_TITLE_LIMIT]
        # TOOL-20: the input is signed twice -- exactly as written, and through
        # the shared volatile-token mask -- so a call whose input differs only
        # in volatile tokens joins the SAME normalized streak as its siblings
        # even though every exact signature is distinct.
        self._calls[key] = (
            _digest(tool_name or "", tool_input),
            _digest(tool_name or "", _mask_volatile(tool_input)),
            shown,
        )
        return advisory

    def note_result(
        self,
        tool_call_id: str,
        *,
        status: str,
        output: str,
        output_digest: str = "",
        terminal: bool = True,
    ) -> str:
        """Feed one result frame; return the notice once the loop is seen.

        A backend may send a call's output and its terminal status in separate
        frames, so a non-terminal frame only keeps its output for the terminal
        one. Returns "" otherwise, and at most one notice per call signature
        per turn.
        """
        key = _digest(tool_call_id) if tool_call_id else ""
        if not key or key not in self._calls:
            return ""
        seen = output_digest or (_digest(output) if output else "")
        if not terminal:
            if seen:
                self._outputs[key] = seen
            return ""
        signature, norm_signature, title = self._calls.pop(key)
        kept = self._outputs.pop(key, "")
        outcome = _digest(status or "", seen or kept)
        # TOOL-20: the same outcome through the shared volatile-token mask, so
        # a result whose text differs only in timestamps/uuids/tmp paths/
        # durations/pids still compares equal. Digest-only frames (a backend
        # that sent no raw text) keep the exact comparison alone.
        norm_outcome = _digest((status or ""), _mask_volatile(output)) if output else outcome
        if signature not in self._streak and len(self._streak) >= _MAX_TRACKED:
            return ""
        # Two comparisons, one loop: the exact pair and the normalized pair.
        # When the text carries no volatile tokens the two are identical, and
        # processing it twice would double-bump one streak, so the normalized
        # arm runs only when it actually differs.
        pairs: "list[tuple[str, str]]" = [(signature, outcome)]
        if norm_signature != signature or norm_outcome != outcome:
            pairs.append(("n" + norm_signature, norm_outcome))
        for skey, s_outcome in pairs:
            if skey in self._warned:
                continue
            if self._last_outcome.get(skey) == s_outcome:
                self._streak[skey] = self._streak.get(skey, 0) + 1
            else:
                self._last_outcome[skey] = s_outcome
                self._streak[skey] = 1
            if self._streak[skey] >= REPEAT_LOOP_THRESHOLD:
                # Warn once per call SHAPE: the exact key and the normalized
                # key both go into the set so the sibling arm cannot re-notify.
                self._warned.add(signature)
                self._warned.add("n" + norm_signature)
                return build_repeat_loop_notice(title, self._streak[skey])
        return ""
