"""Web search via DuckDuckGo Lite (free, no API key required)."""

import logging
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SEARCH_TIMEOUT = 10
MAX_RESULTS = 5
MAX_SNIPPET_LENGTH = 300


def web_search(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """Search DuckDuckGo Lite and return list of {title, snippet, url}."""
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        soup = BeautifulSoup(html, "lxml")

        # DDG Lite structure: <a class='result-link'> for title, <td class='result-snippet'> for snippet
        links = soup.select("a.result-link")
        snippets = soup.select("td.result-snippet")

        for i, link in enumerate(links[:max_results]):
            title = link.get_text(strip=True)
            href = link.get("href", "")
            snippet = ""
            if i < len(snippets):
                snippet = snippets[i].get_text(" ", strip=True)
            if title:
                results.append({
                    "title": title,
                    "snippet": snippet[:MAX_SNIPPET_LENGTH],
                    "url": href,
                })

    except Exception as e:
        logger.warning(f"Web search failed for '{query}': {e}")

    return results


def web_search_formatted(query: str) -> str:
    """Search and return formatted text for AI context.
    Returns empty string when no results found (caller should skip injection).
    """
    results = web_search(query)
    if not results:
        logger.info(f"Web search for '{query}': no results")
        return ""

    lines = [f"Web search results for '{query}':"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet']}")
        if r["url"]:
            lines.append(f"   URL: {r['url']}")
    return "\n".join(lines)
