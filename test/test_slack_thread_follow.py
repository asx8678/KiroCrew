"""MSG-2: thread follow expires, and a silent marker posts nothing."""

from kiro_crew.slack.thread_follow import (
    FOLLOW_TTL_SECS,
    follow_is_fresh,
    is_silent_reply,
    note_bot_post,
)


def test_follow_lasts_45_minutes_after_the_bot_post():
    note_bot_post("T", when=1_000.0)
    assert follow_is_fresh("T", now=1_000.0 + FOLLOW_TTL_SECS)
    assert not follow_is_fresh("T", now=1_000.0 + FOLLOW_TTL_SECS + 1)
    assert not follow_is_fresh("never")


def test_only_the_whole_marker_is_silent():
    assert is_silent_reply("[[NO_REPLY]]")
    assert is_silent_reply("  [[NO_REPLY]]  ")
    assert not is_silent_reply("see [[NO_REPLY]] in the doc")
