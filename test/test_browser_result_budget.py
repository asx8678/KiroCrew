"""TOOL-10: the browser snapshot gets a real budget and a named truncation.

Before: _result_text cut EVERY non-screenshot op at 2,000 chars with no
notice — including op='snapshot', the tree the model reads to find element
refs, which the tool description says to call first.
"""

from __future__ import annotations

from kiro_crew.mcp_tools.browser import _result_text


def test_a_30k_snapshot_is_bounded_and_names_the_omitted_chars() -> None:
    tree = "<html>" + "<div>node</div>" * 4_000 + "</html>"  # ~34 KB
    out = _result_text("snapshot", tree)
    assert len(out) <= 20_000 + 200  # budget + the note line
    assert out.endswith("[truncated: ") or "[truncated:" in out
    omitted = int(out.split("[truncated: ")[1].split(" more chars")[0])
    assert omitted == len(tree) - 20_000


def test_a_1500_char_result_is_returned_whole_with_no_note() -> None:
    body = "x" * 1_500
    out = _result_text("snapshot", body)
    assert body in out
    assert "[truncated" not in out


def test_other_ops_keep_the_small_budget() -> None:
    body = "y" * 2_500
    out = _result_text("navigate", body)
    assert len(out) <= 2_000 + 200
    assert "[truncated:" in out
    assert "500 more chars" in out


def test_the_screenshot_branch_is_unchanged() -> None:
    out = _result_text("screenshot", "base64data" * 100)
    assert "captured" in out
    assert "base64" not in out
