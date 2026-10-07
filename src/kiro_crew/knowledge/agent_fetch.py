"""Agent-assisted content fetch for Knowledge Library.

Fetches content from URLs using the dedicated URL-fetch LLM pool. The pool worker uses
whatever web/URL-fetch tool its backend exposes (configure the allowed tools
via KIROCREW_KNOWLEDGE_FETCH_TOOLS). If the backend has no fetch tool available,
the fetch fails gracefully and the caller falls back to local readers.

The LOCAL fetch (:func:`local_fetch_text`) runs FIRST on every URL sync: a
reachable page costs no model tokens at all, where the pool path paid for the
whole document as model OUTPUT. The pool is the FALLBACK, reached only when
the local fetch is refused (the SSRF/private-address guard) or fails (non-200,
unfetchable, unsupported body).
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import TYPE_CHECKING

from kiro_crew.security import redact_and_truncate

if TYPE_CHECKING:
    from kiro_crew.knowledge.llm_pool import LLMPool

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 120.0

#: Body cap for the local fetch, shared with the skill-registry fetcher's
#: bound: an oversized page is refused (``None``) rather than read into
#: memory whole, and the caller decides whether the pool path is worth it.
_LOCAL_FETCH_MAX_BYTES = 1 * 1024 * 1024

FETCH_PROMPT_TEMPLATE = (
    "Fetch the full text content from this URL using any web/URL-fetch tool you "
    "have available: {url}\n\n"
    "Return ONLY the raw document text, no commentary or formatting.\n"
    "If you cannot access the URL or encounter an error, respond with exactly: ERROR: <reason>"
)

# Patterns indicating the LLM returned an error instead of content
_ERROR_INDICATORS = (
    "ERROR:",
    "I don't have access",
    "I cannot access",
    "I'm unable to",
    "permission denied",
    "I don't have permission",
    "tool is not available",
    "not available in this",
)


def _html_to_text(html: str) -> str:
    """HTML -> text with the SAME conversion ``knowledge.readers`` applies.

    Mirrors ``readers.FileReader._read_html`` (html2text with links kept and
    images dropped, regex strip fallback) so a locally fetched page indexes
    identically to the same page saved to a file and ingested through the
    reader pipeline.
    """
    try:
        import html2text as _html2text_mod
    except ImportError:  # pragma: no cover - optional dep, same as readers
        _html2text_mod = None  # type: ignore[assignment]
    if _html2text_mod is not None:
        h = _html2text_mod.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        return h.handle(html)
    import re

    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def local_fetch_text(url: str) -> "str | None":
    """Governed LOCAL HTTP fetch of a page's text; ``None`` means fall back.

    Runs the same SSRF-guarded machinery the skill-registry fetcher uses
    (``skill_providers._http``): the private/loopback/link-local literal guard
    (applied before AND after redirect resolution, with the alternate IPv4
    encodings normalized), the redirect allowlist pinned to the source URL's
    OWN host, and a bounded read. The host pin is the whole point of passing
    an allowlist at all: a page that redirects elsewhere is a NEW fetch the
    operator never named, so it is refused rather than followed.

    HTML bodies convert through the readers' own html2text conversion so the
    text indexes identically to the same page saved to a file. Returns
    ``None`` on ANY refusal or failure (non-200, empty body, unfetchable,
    internal address, oversized) — the caller then falls back to the URL-fetch
    pool, never silently skipping the sync.
    """
    if not url.lower().startswith(("http://", "https://")):
        return None
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return None
    from kiro_crew.skill_providers import _http
    from kiro_crew.knowledge.readers import _decode_text_bytes

    try:
        if _http.is_internal_url(url):
            logger.info("local fetch refused (internal address): %s", url)
            return None
        raw = _http.sync_fetch_bytes(
            url,
            allowed_hosts=[host],
            internal_check=_http.is_internal_url,
            max_bytes=_LOCAL_FETCH_MAX_BYTES,
        )
    except OSError as exc:
        logger.info("local fetch failed for %s: %s", url, exc)
        return None
    if not raw:
        return None
    text = _decode_text_bytes(raw)
    if not text.strip():
        return None
    stripped = text.lstrip()[:512].lower()
    if stripped.startswith(("<!doctype html", "<html")) or "<body" in stripped:
        text = _html_to_text(text)
        if not text.strip():
            return None
    return text


async def fetch_url_content(url: str, pool: "LLMPool") -> str:
    """Fetch content from a URL using the dedicated URL-fetch pool.

    Acquires a worker from the caller-supplied URL-fetch pool, sends the fetch
    prompt, and releases the worker. The worker uses whatever URL-fetch tool its
    backend exposes (configurable via KIROCREW_KNOWLEDGE_FETCH_TOOLS); if none is
    available the fetch fails.

    Returns the fetched text content.
    Raises RuntimeError on failure.
    """
    logger.info(
        "fetch_url_content: starting fetch for %s (pool provider=%s)", url, pool.provider_type
    )
    prompt = FETCH_PROMPT_TEMPLATE.format(url=url)
    response = await pool.send(prompt, timeout=FETCH_TIMEOUT)
    logger.info(
        "fetch_url_content: got response length=%d for %s", len(response) if response else 0, url
    )
    if not response or not response.strip():
        raise RuntimeError(f"LLM pool returned empty content for {url}")
    content = response.strip()
    # Check if the response is an error message rather than actual content
    content_lower = content[:200].lower()
    for indicator in _ERROR_INDICATORS:
        if indicator.lower() in content_lower:
            redacted = redact_and_truncate(content, 300)
            logger.error(
                "fetch_url_content: error indicator '%s' found in response: %s",
                indicator,
                redacted[:200],
            )
            raise RuntimeError(f"Failed to fetch {url}: {redacted[:300]}")
    # Sanity check: real documents should have meaningful length
    if len(content) < 50:
        raise RuntimeError(f"Content too short for {url} ({len(content)} chars) -- likely an error")
    logger.info("fetch_url_content: success for %s (%d chars)", url, len(content))
    return content
