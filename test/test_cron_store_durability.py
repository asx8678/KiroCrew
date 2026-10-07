"""REL-24: a cron-store save that has returned survives an OS crash.

atomic_write's temp+rename is atomic against a PROCESS crash (the page
cache survives) but not against power loss: the rename can reach disk before
the data. _save now fsyncs the temp file (fsync=True) and the store directory
(fsync_dir), matching the durability the chat, crew-log and session stores
already carry.
"""

from __future__ import annotations

from pathlib import Path

from kiro_crew.cron import CronService


def test_save_fsyncs_the_file_and_the_directory(tmp_path: Path, monkeypatch) -> None:
    import kiro_crew.atomic_write as aw

    fsyncs: list[object] = []
    dir_fsyncs: list[object] = []
    monkeypatch.setattr(aw.os, "fsync", lambda fd: fsyncs.append(fd), raising=False)
    monkeypatch.setattr(
        aw, "fsync_dir", lambda path, *, best_effort=False: dir_fsyncs.append(path), raising=False
    )
    # Import AFTER patching: cron.py binds the names at call time (the local
    # import inside _save), so patching the module attribute is enough.
    from kiro_crew.cron import CronJob, CronSchedule

    svc = CronService(tmp_path)
    svc._jobs = [
        CronJob(
            id="j1",
            name="durability",
            message="msg",
            schedule=CronSchedule(kind="every", every_secs=60),
        )
    ]
    svc._save()

    assert len(fsyncs) == 1, f"expected exactly one file fsync, got {len(fsyncs)}"
    assert len(dir_fsyncs) == 1, f"expected exactly one directory fsync, got {len(dir_fsyncs)}"
    assert Path(dir_fsyncs[0]) == tmp_path
