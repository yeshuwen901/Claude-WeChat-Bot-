"""Fetch and extract text content from URLs (with SSRF protection)."""

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://[^\s]+")
MAX_CONTENT_LENGTH = 2000
MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2MB
REQUEST_TIMEOUT = 10

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
]

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_host(host: str) -> bool:
    """Check if a hostname resolves to a private/internal IP address."""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (socket.gaierror, OSError):
            return True  # Can't resolve — block for safety
    for net in _PRIVATE_NETWORKS:
        if ip in net:
            return True
    return False


def _clean_url(url: str) -> str:
    """Strip trailing punctuation that may have been caught by the regex."""
    return url.rstrip(".,;:!?)]}'\"")


def extract_urls(text: str) -> list[str]:
    urls = URL_PATTERN.findall(text)
    return [_clean_url(u) for u in urls]


def fetch_and_extract(url: str) -> str | None:
    """Fetch a URL and extract readable text content."""
    import random

    url = _clean_url(url)
    host = urlparse(url).hostname or ""
    if _is_private_host(host):
        logger.warning(f"Blocked URL fetch to private host: {host}")
        return None

    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT,
            allow_redirects=True, stream=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"URL fetch failed for {url}: {e}")
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return None

    try:
        raw = resp.raw.read(MAX_RESPONSE_SIZE + 1)
        if len(raw) > MAX_RESPONSE_SIZE:
            logger.warning(f"URL response too large for {url}, truncating")
            raw = raw[:MAX_RESPONSE_SIZE]
        resp.encoding = resp.apparent_encoding or "utf-8"
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:
        return None

    soup = BeautifulSoup(text, "lxml")

    # Remove script, style, nav, footer
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    # Try semantic selectors first
    for selector in ["article", "main", ".main-content", "#content", ".post-content", ".article"]:
        el = soup.select_one(selector)
        if el:
            content = el.get_text(separator="\n", strip=True)
            if len(content) > 100:
                return content[:MAX_CONTENT_LENGTH]

    # Fallback: all paragraphs
    paragraphs = soup.find_all("p")
    lines = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20]
    if lines:
        return "\n".join(lines)[:MAX_CONTENT_LENGTH]

    # Last resort: body text
    body = soup.find("body")
    if body:
        return body.get_text(separator="\n", strip=True)[:MAX_CONTENT_LENGTH]

    return None
