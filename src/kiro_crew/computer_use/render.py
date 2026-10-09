"""Text rendering of app lists and accessibility snapshots.

The rendered text is the ONLY thing a model ever sees from computer use: the MCP
transport (``validation.build_tool_response``) emits
``{"content":[{"type":"text","text":...}]}`` and cannot express an image block,
so "tree first, relay the screenshot as a path" is a property of the transport
rather than a policy someone can regress.

Two invariants this module enforces, both asserted by tests:

* Every public renderer ENDS with :func:`policy.redact_result`, so no path can
  emit an unredacted tree.
* A ``secure`` record renders :data:`SECURE_PLACEHOLDER` and NEVER its value
  bytes — not truncated, not masked-with-a-hint, not at all.

Pure: no ctypes, no I/O, no platform calls.
"""

from __future__ import annotations

from typing import Sequence

from kiro_crew.computer_use import policy
from kiro_crew.computer_use.types import (
    DEPTH_NOTE,
    DIFF_GAP_MARKER,
    FOCUS_MARKER,
    FOCUS_NOTE,
    NO_APPS_NOTE,
    OMITTED_ROWS_NOTE,
    PAGED_FROM_NOTE,
    SCREENSHOT_NOTE,
    SCREENSHOT_SCALE_NOTE,
    SECURE_PLACEHOLDER,
    SECURE_WINDOW_NOTE,
    SELECTION_NOTE,
    TREE_INDENT,
    TRUNCATED_NOTE,
    TRUNCATED_WINDOW_NOTE,
    WINDOW_ORIGIN_NOTE,
    AppRef,
    ElementRec,
    Snapshot,
)

# Bytes-per-unit ladder for the human-readable screenshot size. Kept local: it
# is presentation, not business logic.
_KIB = 1024.0
_MIB = _KIB * _KIB

#: Per-field cap for the ``computer_list_apps`` listing. An app name, bundle id or
#: window title is one line item here, so this bounds a single hostile app from
#: crowding out the rest of the list; the flattening it comes with (see ``_clip``)
#: is what stops an embedded newline forging additional entries.
_APP_FIELD_LIMIT = 200

#: Characters held back from a tree budget for the omission line itself, whose length
#: depends on the index ranges it names (at most :data:`_MAX_OMITTED_RANGES` of them).
_OMITTED_NOTE_RESERVE = len(OMITTED_ROWS_NOTE) + 96
#: How many omitted index ranges the omission line spells out before it says "…".
_MAX_OMITTED_RANGES = 4
#: Per-row allowance for a gap marker line (``…`` plus its newline) in a sparse render.
_GAP_ALLOWANCE = len(DIFF_GAP_MARKER) + 1


def render_apps(apps: "tuple[AppRef, ...]") -> str:
    """Render the on-screen application list.

    One line per app with the identity a subsequent ``app`` argument can match
    against. Ends with the redaction pass: window titles routinely carry
    document names, filesystem paths and volume names.
    """
    if not apps:
        return policy.redact_result(NO_APPS_NOTE)
    lines = [f"{len(apps)} application(s) with on-screen windows:"]
    for app in apps:
        # Flattened for the same reason as the tree header: the name, bundle id and
        # window title all come from the target process and are one-per-line here,
        # so an embedded newline could forge extra "applications" in this list.
        parts = [_clip(app.name, _APP_FIELD_LIMIT) or "(unnamed)"]
        if app.bundle_id:
            parts.append(_clip(app.bundle_id, _APP_FIELD_LIMIT))
        parts.append(f"pid {app.pid}")
        line = f"- {parts[0]} [{', '.join(parts[1:])}]"
        if app.window_title:
            line += f' — "{_clip(app.window_title, _APP_FIELD_LIMIT)}"'
        lines.append(line)
    return policy.redact_result("\n".join(lines))


def render_tree(
    snap: Snapshot,
    *,
    text_limit: int,
    char_budget: int = 0,
    rows: "Sequence[int] | None" = None,
    tree_notes: Sequence[str] = (),
    from_index: int = 0,
) -> str:
    """Render one snapshot: header, indented element tree, screenshot reference.

    *text_limit* caps each individual title/value string. It is applied per
    field rather than to the whole body so a single verbose node (a text area
    holding a whole document) cannot crowd out the rest of the tree.

    *char_budget* (``0`` = unbounded) caps the WHOLE rendered result. Only element
    rows are ever dropped to meet it: the header, the truncation/depth notes, the
    trailer, *tree_notes* and the screenshot note are always kept, and an omission
    line says how many rows were left out and which index ranges they cover — so a
    dense window can no longer push the notes past the transport cut.

    *rows* selects WHICH element rows are eligible, by element ``index`` and in
    priority order (the first ones are kept when the budget bites); ``None`` means
    every element, in tree order. Selected rows are always printed in tree order,
    with a gap marker where unselected rows were skipped, so indentation still
    reads as structure. Used by the post-action diff (see ``diff_rows``).

    *from_index* pages a plain render (``rows=None``): elements with a lower index
    are skipped and a leading note says so. It is the stateless way past the budget —
    the omission line names the ``from_index`` that continues where this page
    stopped, and the walk itself is unchanged, so every index stays addressable.

    Ends with the redaction pass — accessibility values are arbitrary user
    content and can contain credentials verbatim.
    """
    head: list[str] = [f"App={snap.app.label}"]
    header = _window_header(snap, text_limit=text_limit)
    if header:
        head.append(header)
    head.append("")
    if rows is None and from_index > 0:
        head.append(PAGED_FROM_NOTE.format(index=from_index))

    notes: list[str] = list(tree_notes)
    if not snap.elements:
        notes.append("(no accessible elements — the window exposed an empty tree)")
    if snap.truncated:
        notes.append(TRUNCATED_NOTE.format(count=len(snap.elements)))
    if snap.depth_truncated:
        notes.append(DEPTH_NOTE.format(depth=_max_depth(snap)))
    trailer = _trailer(snap, text_limit=text_limit)
    if trailer:
        # Blank separator: these lines are ABOUT the tree, not nodes in it, and
        # without the gap the origin note reads as a sibling of the last element.
        notes.append("")
        notes.extend(trailer)

    # The image note is appended AFTER the redaction pass, deliberately.
    #
    # It is 100% machine-generated by this package — our own spool path
    # (``shot-<epoch-ms>.jpeg`` under ``tempfile.gettempdir()``), two integer
    # dimensions and a byte count — so it carries no user data and needs no
    # redaction, and the path must reach the model byte-exact because it is the
    # only handle to the frame. Running it THROUGH redaction would put that path
    # at the mercy of the bare-secret-key heuristic, to which a path is one long
    # base64-alphabet run: the redactor withholds this host's macOS per-user
    # directory id (``/var/folders/<2>/<30>/T``), but not every temp root a spool
    # can land under. The tree body above, which DOES carry arbitrary user
    # content, is still fully redacted.
    image_note = _render_image_note(snap)

    fixed = sum(len(line) + 1 for line in (*head, *notes))
    if image_note:
        fixed += len(image_note) + 2
    body_lines, omitted_note = _select_rows(
        snap,
        rows,
        text_limit=text_limit,
        char_budget=char_budget,
        fixed=fixed,
        from_index=from_index,
    )
    lines = [*head, *body_lines]
    if omitted_note:
        lines.append(omitted_note)
    lines.extend(notes)

    body = policy.redact_result("\n".join(lines))
    if image_note:
        return f"{body}\n\n{image_note}"
    return body


def diff_rows(
    before: "Snapshot | None",
    after: Snapshot,
    *,
    text_limit: int,
    anchor: "int | None",
    neighbourhood: int,
) -> "tuple[tuple[int, ...], int]":
    """The rows a post-action render shows, in priority order, plus the removed count.

    Priority: the *anchor* (the acted element, or the focused one), then its
    neighbourhood within *neighbourhood* tree positions (nearest first), then its
    ancestors, then every row whose RENDERED line differs from *before*'s row at the
    same index — in tree order. A row is compared by its rendered line rather than
    by identity because indices are positional: an inserted row shifts every later
    index, and the model must be told that "17" now means something else. So a row
    that is NOT returned is byte-identical, at the same index, to what *before*
    rendered — which is what lets the caller say "everything not shown is unchanged".

    With no *before* every row counts as changed (the caller then states that it has
    no baseline). The second value is how many trailing indices *before* had that
    *after* no longer does.

    Pure: compares two snapshots this session already walked.
    """
    positions = {rec.index: pos for pos, rec in enumerate(after.elements)}
    priority: list[int] = []
    seen: set[int] = set()

    def add(index: int) -> None:
        if index not in seen and index in positions:
            seen.add(index)
            priority.append(index)

    if anchor is not None and anchor in positions:
        pos = positions[anchor]
        add(anchor)
        for step in range(1, neighbourhood + 1):
            for near in (pos - step, pos + step):
                if 0 <= near < len(after.elements):
                    add(after.elements[near].index)
        depth = after.elements[pos].depth
        for back in range(pos - 1, -1, -1):
            rec = after.elements[back]
            if rec.depth < depth:
                add(rec.index)
                depth = rec.depth
                if depth == 0:
                    break

    old = {} if before is None else {rec.index: rec for rec in before.elements}
    for rec in after.elements:
        prev = old.get(rec.index)
        if prev is None or _render_record(prev, text_limit=text_limit) != _render_record(
            rec, text_limit=text_limit
        ):
            add(rec.index)

    removed = 0
    if before is not None:
        removed = sum(1 for index in old if index not in positions)
    return tuple(priority), removed


def _select_rows(
    snap: Snapshot,
    rows: "Sequence[int] | None",
    *,
    text_limit: int,
    char_budget: int,
    fixed: int,
    from_index: int = 0,
) -> "tuple[list[str], str]":
    """Element lines to print (tree order, gap-marked) and the omission line, or ``""``.

    Greedy in priority order and STOPS at the first row that does not fit, rather
    than skipping to a shorter one, so a plain budgeted render keeps a contiguous
    head of the tree and the omitted rows form one tail range the note can name.
    """
    if rows is None:
        order = [rec.index for rec in snap.elements if rec.index >= from_index]
    else:
        order = list(rows)
    by_index = {rec.index: (pos, rec) for pos, rec in enumerate(snap.elements)}
    eligible = [index for index in dict.fromkeys(order) if index in by_index]
    sparse = rows is not None

    rendered = {
        index: _render_record(by_index[index][1], text_limit=text_limit) for index in eligible
    }
    chosen: set[int] = set()
    if char_budget <= 0:
        chosen = set(eligible)
    else:
        available = char_budget - fixed - _OMITTED_NOTE_RESERVE
        used = 0
        for index in eligible:
            cost = len(rendered[index]) + 1 + (_GAP_ALLOWANCE if sparse else 0)
            if used + cost > available:
                break
            used += cost
            chosen.add(index)

    lines: list[str] = []
    last_pos = -1
    for pos, rec in enumerate(snap.elements):
        if rec.index not in chosen:
            continue
        if sparse and pos != last_pos + 1:
            lines.append(DIFF_GAP_MARKER)
        lines.append(rendered[rec.index])
        last_pos = pos
    if sparse and lines and last_pos != len(snap.elements) - 1:
        lines.append(DIFF_GAP_MARKER)

    omitted = sorted(index for index in eligible if index not in chosen)
    if not omitted:
        return lines, ""
    return lines, OMITTED_ROWS_NOTE.format(
        omitted=len(omitted), budget=char_budget, ranges=_ranges(omitted), next=omitted[0]
    )


def _ranges(indices: "Sequence[int]") -> str:
    """``[3, 4, 5, 9]`` -> ``"3-5, 9"``; at most :data:`_MAX_OMITTED_RANGES`, then ``…``."""
    spans: list[tuple[int, int]] = []
    for index in indices:
        if spans and index == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], index)
        else:
            spans.append((index, index))
    parts = [str(a) if a == b else f"{a}-{b}" for a, b in spans[:_MAX_OMITTED_RANGES]]
    if len(spans) > _MAX_OMITTED_RANGES:
        parts.append("…")
    return ", ".join(parts)


def fingerprint(rec: ElementRec) -> str:
    """Stable identity of an element, for drift detection between walks.

    Compared before every mutating action: if the record now sitting at an index
    fingerprints differently from the one the model was shown, the tree moved
    under it and the action is refused rather than applied to the wrong widget.

    Role, subrole and title are included because those are what a model reasons
    about ("the Save button"), and a change in any of them means a different
    control. ``value`` is deliberately EXCLUDED: a text field's value changes as
    the user types without the control's identity changing at all, and folding
    it in would refuse almost every legitimate action. A secure record
    contributes only its role/subrole/title, so fingerprinting never reads
    credential bytes.
    """
    return f"{rec.role}|{rec.subrole}|{rec.title}"


def describe_record(rec: ElementRec) -> str:
    """Short human/model-facing identity used in drift and failure messages."""
    label = rec.role or "?"
    if rec.subrole:
        label += f"/{rec.subrole}"
    if rec.secure:
        return f"{label} {SECURE_PLACEHOLDER}"
    if rec.title:
        return f'{label} "{rec.title}"'
    return label


def _render_record(rec: ElementRec, *, text_limit: int) -> str:
    """One tree line: ``<indent><index> <role> "<title>" [<actions>] <traits> <frame>``.

    Field order is chosen so the two things a model needs to DECIDE come first (the
    index it will address and the label it is matching against) and the two things it
    needs to ACT come last (traits, then geometry). A model scanning for "the Save
    button" reads left-to-right and can stop at the title.
    """
    line = f"{TREE_INDENT * rec.depth}{rec.index} {rec.short_role}"
    if rec.secure:
        # THE security-critical branch. A secure record's value is never
        # rendered — and neither is its title, because a password field's title
        # is sometimes the account name it belongs to. The placeholder alone
        # tells the model an element exists and is unusable. No traits and no
        # frame either: `editable` would confirm the box accepts input, and a
        # rect locates it on screen for a coordinate click.
        return f"{line} {SECURE_PLACEHOLDER}"
    label = rec.title or rec.value
    if label:
        line += f' "{_clip(label, text_limit)}"'
    if rec.actions:
        line += f" [{', '.join(rec.actions)}]"
    if not rec.enabled:
        line += " (disabled)"
    if rec.traits:
        line += f" ({', '.join(rec.traits)})"
    if rec.focused:
        # Marked inline rather than only in the trailing summary line: the summary
        # says WHICH element has focus, this says "this one" at the point the model
        # is already reading. Both, because a truncated tree may cut the summary's
        # target while still showing this line.
        line += f" {FOCUS_MARKER}"
    if rec.frame is not None:
        line += " " + _render_frame(rec.frame)
    return line


def _trailer(snap: Snapshot, *, text_limit: int) -> list[str]:
    """The lines that follow the tree: window origin, focus, selection.

    All three are ABOUT the tree rather than part of it, so they go after it — and
    they are returned as a list (rather than appended in place) so the ordering is
    stated in one place and testable without rendering a whole snapshot.

    Included in the redaction pass with the tree body, unlike the screenshot note:
    the selection is arbitrary user content and can contain a credential verbatim,
    which is exactly what redaction exists for.
    """
    out: list[str] = []
    if snap.window_bounds is not None:
        x, y, width, height = snap.window_bounds
        out.append(
            WINDOW_ORIGIN_NOTE.format(
                x=round(x), y=round(y), width=round(width), height=round(height)
            )
        )
    focused = next((rec for rec in snap.elements if rec.focused), None)
    if focused is not None:
        out.append(FOCUS_NOTE.format(index=focused.index, label=describe_record(focused)))
    if snap.selected_text:
        # Clipped and flattened like every other app-supplied string: a selection
        # can be a whole document, and its newlines would forge tree lines above.
        out.append(SELECTION_NOTE.format(text=_clip(snap.selected_text, text_limit)))
    return out


def _render_frame(frame: tuple[float, float, float, float]) -> str:
    """``@ x=12,y=40 48x24`` — window-local position and size, integer pixels.

    Rounded to ints: sub-pixel precision is noise for a click target (Retina
    windows routinely sit on half-pixel boundaries) and doubles the width of every
    frame in the tree, which is real token cost across ~1,200 nodes.

    The ``@`` prefix and the ``WxH`` form are there to make the two pairs
    unmistakable — a bare ``12, 40, 48, 24`` reads as four unlabelled numbers, and
    a model that mixed up which pair was the size would aim at the wrong pixel.
    """
    x, y, width, height = frame
    return f"@ x={round(x)},y={round(y)} {round(width)}x{round(height)}"


def _window_header(snap: Snapshot, *, text_limit: int) -> str:
    """``Window: "<title>", App: <name>.`` — omitted when both are empty.

    Both fields go through :func:`_clip` for the SAME reason element titles do: a
    window title is attacker-controlled app content (a browser tab's
    ``document.title``, a filename, an email subject), and the tree's structure IS
    its indentation. An un-flattened title containing newlines can forge
    convincing tree lines below this header — complete with a plausible index the
    model will then address — or inject instruction-shaped text. The app name gets
    the same treatment: it comes from the same untrusted process.
    """
    parts: list[str] = []
    if snap.window_title:
        parts.append(f'Window: "{_clip(snap.window_title, text_limit)}"')
    if snap.app.name:
        parts.append(f"App: {_clip(snap.app.name, text_limit)}")
    return ", ".join(parts) + "." if parts else ""


def _render_image_note(snap: Snapshot) -> str:
    """The screenshot reference line, the suppression note, or ``""``.

    A window containing ANY secure element gets no screenshot at all, and the
    text says so: a password field's rendered pixels are a credential even
    though the tree redacted the value. Suppression is whole-window because
    there is no reliable way to blank a sub-rectangle of an already-encoded
    JPEG, and a partial redaction that missed would be worse than none.

    A TRUNCATED walk is announced for the same reason: ``capture_macos`` refuses
    on ``truncated``/``depth_truncated`` because a cut-off walk cannot prove the
    window holds no secure field ("unknown" behaves as "present"). Truncation is
    the NORMAL state for a Chromium/Electron window at the shipped 1200-node
    default, so without this note ``screenshot: true`` on Chrome or Slack would
    produce no image and no reason — the retry loop these notes exist to prevent.
    Checked before ``image_path`` so the explanation cannot be skipped, and after
    ``has_secure`` so a window that is both keeps the more specific reason.

    The truncation note fires **only when an image was actually REQUESTED**, which
    is what ``walk_budget.want_image`` answers. Every mutating action's refresh
    walk forces ``want_image=False`` by design, and truncation is routine — so an
    unconditional note would append "Screenshot suppressed … Re-run with a higher
    max_tree_nodes" to every successful click on a browser window, advertising the
    suppression of an image nobody asked for and naming an argument mutating tools
    do not even accept: the same retry loop pointed the other way. ``None`` (a
    snapshot a backend built directly, with no budget stamped) keeps the
    announcement, because there the request is unknown and a spurious note is
    cheaper than a silent omission.
    """
    if snap.has_secure:
        return SECURE_WINDOW_NOTE
    if (
        not snap.image_path
        and (snap.truncated or snap.depth_truncated)
        and (snap.walk_budget is None or snap.walk_budget.want_image)
    ):
        return TRUNCATED_WINDOW_NOTE
    if not snap.image_path:
        return ""
    note = SCREENSHOT_NOTE.format(
        path=snap.image_path,
        width=snap.image_width,
        height=snap.image_height,
        size=_human_bytes(len(snap.image_jpeg)),
    )
    scale_note = _render_scale_note(snap)
    return f"{note}\n{scale_note}" if scale_note else note


def _render_scale_note(snap: Snapshot) -> str:
    """The image-pixel to screen-point conversion, or ``""`` when it is unknowable.

    Emitted only when all four inputs are present and sane, because a WRONG
    conversion is worse than none: a model that applies a bad ratio clicks
    confidently in the wrong place, where a model given nothing falls back to an
    element frame. So every one of these is required —

    * ``window_bounds`` — the origin the conversion adds, and the window size it is
      relative to. ``None`` whenever the window rect could not be read;
    * a positive encoded width AND window width — the ratio's two terms;

    — and a single scale is published rather than one per axis. The encoder preserves
    aspect ratio (it scales by the LONG edge, see ``capture_*._encode_jpeg``), so the
    two ratios agree to within a rounding step, and printing two numbers that are
    always equal would invite a reader to believe they can differ.

    The ratio is taken from the WIDTH because that is the axis whose window value is
    unambiguous. A window's height includes its title bar on Windows and its
    ``PrintWindow`` render may exclude a shadow, so a height-derived ratio can
    disagree with the width by a pixel or two; there is no such ambiguity horizontally.
    """
    if snap.window_bounds is None:
        return ""
    x, y, win_width, win_height = snap.window_bounds
    if win_width <= 0 or snap.image_width <= 0:
        return ""
    scale = snap.image_width / float(win_width)
    if scale <= 0:
        return ""
    return SCREENSHOT_SCALE_NOTE.format(
        x=round(x),
        y=round(y),
        scale=scale,
        win_width=round(win_width),
        win_height=round(win_height),
    )


def _max_depth(snap: Snapshot) -> int:
    """Deepest rendered depth — names the level the walk stopped at."""
    return max((rec.depth for rec in snap.elements), default=0)


def _clip(text: str, limit: int) -> str:
    """Collapse newlines and clip to *limit* characters with an ellipsis marker.

    Newlines are collapsed because the tree's structure IS its indentation: a
    multi-line value would otherwise forge tree lines, letting page content
    masquerade as elements the model can address.
    """
    flat = " ".join(text.split())
    if limit > 0 and len(flat) > limit:
        return flat[:limit] + "…"
    return flat


def _human_bytes(size: int) -> str:
    """``24766`` -> ``24.2 KB``."""
    if size >= _MIB:
        return f"{size / _MIB:.1f} MB"
    if size >= _KIB:
        return f"{size / _KIB:.1f} KB"
    return f"{size} B"
