"""Pins for the security residuals the docs already state.

SEC-5 (doc half): the standard-tier table row in security.md must name every
credential leaf the tier actually leaves readable — derived from the plan
constants (_STRICT_DIRS / _STANDARD_DIRS / _CC_FILES), not a live spawn — so
an operator can see the real exposure before relying on the default. The
tier's scope itself is the operator's to decide; this only keeps the DOC honest
about what the code does.

SEC-10 (characterization): redact() is a shape-based filter and deliberately
does NOT catch hex/rot13/chunked emission; this pins that behaviour so a future
detector is a deliberate, reviewed decision (and weighed against the
false-positive class of SEC-19), never an accident.
"""

from __future__ import annotations

from pathlib import Path

from kiro_crew import sandbox
from kiro_crew.security import redact

_SECURITY_MD = (
    Path(__file__).resolve().parent.parent / "docs" / "system-specs" / "modules" / "security.md"
).read_text(encoding="utf-8")


def _standard_row() -> str:
    for line in _SECURITY_MD.splitlines():
        if line.startswith("| **Standard** |"):
            return line
    raise AssertionError("security.md has no Standard tier row")


def test_the_standard_tier_row_names_every_strict_only_credential_leaf() -> None:
    """Every credential leaf strict hides and standard leaves readable must be
    named in the Standard row's Accessible cell, derived from the plan."""
    strict_only = [
        d
        for d in sandbox._STRICT_DIRS + sandbox._CC_FILES
        if d not in sandbox._STANDARD_DIRS
        and not d.startswith(".kiro")
        and not d.startswith(".kirocrew")
    ]
    assert strict_only, "derivation found no strict-only leaves; the pin rotted"
    row = _standard_row()
    missing = [leaf for leaf in strict_only if leaf not in row]
    assert not missing, (
        "security.md's Standard tier row must name every credential leaf the "
        f"tier leaves readable (from _STRICT_DIRS/_CC_FILES minus _STANDARD_DIRS): {missing}"
    )


def test_redaction_is_shape_based_and_misses_re_encoded_secrets() -> None:
    """SEC-10 characterization: hex, rot13 and chunked emission pass through.
    The paragraph in security.md ('What redaction does not cover') states this
    residual; this pin makes a future detector a reviewed decision."""
    secret = "AKIA" + "I" * 16
    # Plain and base64 shapes ARE handled.
    assert secret not in redact(secret)
    # Hex is not.
    hexed = secret.encode().hex()
    assert redact(f"leak: {hexed}") == f"leak: {hexed}"
    # rot13 is not.
    import codecs

    rot = codecs.encode(secret, "rot13")
    assert redact(f"leak: {rot}") == f"leak: {rot}"
    # Chunked (8-char lines) is not.
    chunked = "\n".join(secret[i : i + 8] for i in range(0, len(secret), 8))
    assert secret not in redact(f"leak:\n{chunked}")
