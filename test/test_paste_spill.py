"""ATT-2: a large dashboard paste reaches the prompt as a short preview."""

from pathlib import Path

from kiro_crew.chat_attachments import PASTE_PROMPT_BUDGET, spill_large_paste


def test_a_200kb_paste_is_a_4kb_preview_plus_a_readable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kiro_crew.session_storage._crew_sessions_dir", lambda: tmp_path
    )
    pasted = "P" * (200 * 1024)
    prompt, path = spill_large_paste("slot-1", pasted)
    assert len(prompt.encode("utf-8")) <= PASTE_PROMPT_BUDGET
    assert path
    assert Path(path).read_text(encoding="utf-8") == pasted
    assert prompt.startswith("[attached_file 1] ")


def test_a_short_paste_is_unchanged():
    text = "hello"
    assert spill_large_paste("slot-1", text) == (text, "")
