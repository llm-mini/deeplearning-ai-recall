"""
Lightweight web tools for the mind map LLM to fill gaps transcripts don't cover.

Two capabilities, both best-effort (return empty/error strings rather than raising,
so a flaky network never breaks a mind map build):
  - web_search(query)  -> list[{title, url, snippet}]  (DuckDuckGo HTML, no API key)
  - fetch_url(url)      -> str   (page text, tags stripped, truncated)

httpx is already a backend dependency (used by OAuth). A Playwright-based fetch for
JS-heavy pages is a documented phase-2 upgrade (reuse transcriber.goto_safe).
"""

import html
import re

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
_FETCH_LIMIT = 8000       # chars of page text handed to the model
_SNIPPET_LIMIT = 300

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANYTAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n\s*\n\s*\n+")

# DuckDuckGo HTML result anchors + snippets.
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)


def _strip_html(raw: str) -> str:
    raw = _TAG_RE.sub(" ", raw)
    raw = _ANYTAG_RE.sub(" ", raw)
    raw = html.unescape(raw)
    raw = _WS_RE.sub(" ", raw)
    raw = _BLANK_RE.sub("\n\n", raw)
    return raw.strip()


def fetch_url(url: str) -> str:
    """Fetch a URL and return cleaned text (truncated). Empty string on failure."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0, headers={"User-Agent": _UA}) as c:
            r = c.get(url)
            r.raise_for_status()
            text = _strip_html(r.text)
            return text[:_FETCH_LIMIT]
    except Exception as e:  # noqa: BLE001
        return f"[fetch error: {e}]"


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo HTML. Returns [] on failure."""
    if not query:
        return []
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0, headers={"User-Agent": _UA}) as c:
            r = c.post("https://html.duckduckgo.com/html/", data={"q": query})
            r.raise_for_status()
            body = r.text
        links = _DDG_RESULT_RE.findall(body)
        snippets = _DDG_SNIPPET_RE.findall(body)
        out = []
        for i, (href, title) in enumerate(links[:max_results]):
            snip = snippets[i] if i < len(snippets) else ""
            out.append({
                "title": _strip_html(title)[:_SNIPPET_LIMIT],
                "url": html.unescape(href),
                "snippet": _strip_html(snip)[:_SNIPPET_LIMIT],
            })
        return out
    except Exception:  # noqa: BLE001
        return []
