"""ATT-1: channel files inline a preview and keep the full file by path."""

import os

import pytest

from kiro_crew.messaging.attachments import Attachment, IngestLimits, ingest_attachments


async def _download(url: str, dest: str) -> None:
    payload = url.encode()
    with open(dest, "wb") as fh:
        fh.write(payload)


@pytest.mark.asyncio
async def test_ten_large_csvs_stay_under_the_inline_cap(tmp_path):
    body = b"a,b\n" + b"x" * (300 * 1024)
    files = [
        Attachment(name=f"f{i}.csv", mimetype="text/csv", size=len(body), url=body.decode("latin1"))
        for i in range(10)
    ]
    # url is the payload for the fake downloader; latin1 keeps every byte.
    result = await ingest_attachments(
        files,
        download=_download,
        source="test",
        limits=IngestLimits(max_text_inject=8 * 1024, max_inline_total=48 * 1024),
    )
    inline = "".join(result.text_blocks)
    assert len(inline) <= 48 * 1024 + 4096
    assert len(result.file_paths) == 10
    for path in result.file_paths:
        assert os.path.isfile(path)
        os.unlink(path)


@pytest.mark.asyncio
async def test_oversized_log_is_a_path_plus_a_preview():
    body = b"L" * (2 * 1024 * 1024)
    result = await ingest_attachments(
        [Attachment(name="run.log", mimetype="text/plain", size=len(body), url=body.decode("latin1"))],
        download=_download,
        source="test",
        limits=IngestLimits(),
    )
    assert result.file_paths
    assert "Path:" in result.text_blocks[0]
    assert "too large" not in "".join(result.rejections)
    os.unlink(result.file_paths[0])
