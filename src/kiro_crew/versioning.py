"""The one version comparator: PEP 440 ordering plus this project's stamps.

Owned here so the two consumers that must order versions IDENTICALLY share
one implementation: the dashboard update check
(``dashboard/handlers/updates.py`` re-exports these helpers) and the
governance ``min_version`` floor (``platform/governance.py``). A floor that
ordered builds differently from the update check would report a host
below-floor while the check itself says "up to date", or vice versa.

The project stamps versions two ways (see ``docs/build/release.md``): the
CLI-wheel lane writes PEP 440 (``0.3.0rc1``, ``0.3.0.dev20260708061155``)
and the desktop/Windows lanes write semver-style hyphenated suffixes
(``0.3.0-rc.1``, ``0.3.0-nightly.20260728t184500``). :func:`_version_key`
reads both: any unrecognised hyphen/plus suffix is treated as a PRERELEASE of
the release core, so it sorts below the bare release — the same direction
PEP 440 orders rc against final. Guessing "newer" would offer a downgrade
as an update.

Imports nothing outside the standard library.
"""

from __future__ import annotations

import re

#: Pre-release spellings PEP 440 normalizes, mapped to their sort rank.
_PRE_RANKS = {
    "a": 1,
    "alpha": 1,
    "b": 2,
    "beta": 2,
    "c": 3,
    "rc": 3,
    "pre": 3,
    "preview": 3,
}

_PEP440_RE = re.compile(
    r"""^\s*v?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?:[-_.]?(?P<pre_l>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?:[-_.]?(?P<post>post|r|rev)[-_.]?(?P<post_n>[0-9]+)?)?
    (?:[-_.]?(?P<dev>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)

_FIRST_INT_RE = re.compile(r"[0-9]+")


def _version_key(value: str) -> tuple[tuple[int, ...], int, int, int, int] | None:
    """Comparable ordering key for a version string, or ``None`` if unparseable.

    Returning ``None`` rather than a low-sorting sentinel is the whole point.
    The predecessor of this function coerced anything it could not parse to
    ``(0,)``, which made ``0.1.2rc3`` and ``0.1.3rc2`` compare EQUAL and reported
    "no update available" for every prerelease-to-prerelease step. A caller that
    cannot compare must say so, not answer "up to date".

    The key is ``(release, stage, stage_ordinal, dev_absent, dev_ordinal)`` and
    reproduces PEP 440's ordering::

        X.Y.Z.devN  <  X.Y.ZaN  <  X.Y.ZbN  <  X.Y.ZrcN.devM
                    <  X.Y.ZrcN  <  X.Y.Z   <  X.Y.Z.postN

    ``dev_absent`` is what places ``rc1.dev3`` below ``rc1``: a dev release of a
    prerelease precedes that prerelease, so it must sort first WITHIN the same
    ``(stage, stage_ordinal)`` pair.

    Release tuples are NOT padded here — comparing keys of different arity is the
    caller's job (:func:`_is_newer`), because padding depends on both sides.
    """
    text = str(value or "").strip()
    if not text:
        return None
    match = _PEP440_RE.match(text)
    suffix_ordinal: int | None = None
    if match is None:
        # Semver-ish stamps from the desktop lane: ``0.3.0-insider.2``,
        # ``0.3.0-nightly.20260728t184500``. Treat ANY unrecognised suffix as a
        # prerelease of its release core, so it sorts below the bare release —
        # the same direction PEP 440 orders rc against final. Guessing "newer"
        # here would offer a downgrade as an update.
        core, sep, rest = text.partition("-")
        if not sep:
            core, sep, rest = text.partition("+")
        if not sep:
            return None
        match = _PEP440_RE.match(core)
        if match is None:
            return None
        found = _FIRST_INT_RE.search(rest)
        suffix_ordinal = int(found.group()) if found else 0

    try:
        release = tuple(int(chunk) for chunk in match.group("release").split("."))
    except ValueError:  # pragma: no cover - the regex already bounds this
        return None

    pre_l = match.group("pre_l")
    has_post = bool(match.group("post"))
    has_dev = bool(match.group("dev"))

    if suffix_ordinal is not None:
        stage, ordinal = 3, suffix_ordinal
    elif pre_l:
        stage, ordinal = _PRE_RANKS[pre_l.lower()], int(match.group("pre_n") or 0)
    elif has_post:
        stage, ordinal = 5, int(match.group("post_n") or 0)
    elif has_dev:
        # A dev release of the release itself (X.Y.Z.devN) precedes every
        # prerelease of X.Y.Z, so it gets the lowest stage.
        stage, ordinal = 0, int(match.group("dev_n") or 0)
    else:
        stage, ordinal = 4, 0

    dev_of_pre = has_dev and (pre_l is not None or suffix_ordinal is not None)
    dev_absent = 0 if dev_of_pre else 1
    dev_ordinal = int(match.group("dev_n") or 0) if dev_of_pre else 0
    return (release, stage, ordinal, dev_absent, dev_ordinal)


def _is_newer(remote: str, local: str) -> bool | None:
    """Is *remote* strictly newer than *local*? ``None`` when either is unparseable.

    ``None`` propagates to ``error: version_unparseable`` instead of collapsing
    into ``available: False``: an unreadable version is a failed check, not a
    verdict.
    """
    remote_key = _version_key(remote)
    local_key = _version_key(local)
    if remote_key is None or local_key is None:
        return None
    # Zero-pad the release cores so 0.1 and 0.1.0 compare EQUAL rather than
    # letting the shorter tuple sort first, then fall through to the stage keys.
    r_rel, l_rel = remote_key[0], local_key[0]
    width = max(len(r_rel), len(l_rel))
    r_pad = r_rel + (0,) * (width - len(r_rel))
    l_pad = l_rel + (0,) * (width - len(l_rel))
    if r_pad != l_pad:
        return r_pad > l_pad
    return remote_key[1:] > local_key[1:]
