"""Shared helpers: HTTP, URL normalisation, text cleanup, time."""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def log(*parts) -> None:
    print("[token-times]", *parts, flush=True)


def http_get(url: str, *, timeout: float = 15, max_bytes: int = 3_000_000,
             headers: dict | None = None) -> bytes:
    h = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.8",
         "Accept-Encoding": "identity"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes)


def get_json(url: str, **kw):
    return json.loads(http_get(url, **kw))


# ---------------------------------------------------------------- URLs / ids

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|source$|via$|s$|smid$)")
_GITHUB_NON_REPO = {
    "orgs", "features", "topics", "trending", "sponsors", "marketplace", "settings",
    "collections", "about", "enterprise", "security", "pricing", "readme", "events",
    "apps", "login", "signup", "search", "customer-stories", "resources", "solutions",
}


def canonical_url(url: str) -> str:
    """A normalised form of a URL used only for de-duplication."""
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = p.netloc.lower()
    for prefix in ("www.", "m.", "old."):
        host = host.removeprefix(prefix)
    path = p.path.rstrip("/") or "/"
    if host.endswith("arxiv.org"):
        m = re.search(r"(\d{4}\.\d{4,5})", path)
        if m:
            return f"arxiv.org/abs/{m.group(1)}"
    if host == "huggingface.co" and path.startswith("/papers/"):
        m = re.search(r"(\d{4}\.\d{4,5})", path)
        if m:
            return f"arxiv.org/abs/{m.group(1)}"
    if host == "github.com":
        parts = [x for x in path.split("/") if x]
        if len(parts) >= 2 and parts[0].lower() not in _GITHUB_NON_REPO:
            return f"github.com/{parts[0].lower()}/{parts[1].lower()}"
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not _TRACKING.match(k)]
    q = urllib.parse.urlencode(sorted(query))
    return f"{host}{path}?{q}" if q else f"{host}{path}"


def make_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- text

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style|noscript|svg|figure|figcaption)[^>]*>.*?</\1>")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = _SCRIPT.sub(" ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def markdown_to_text(md: str) -> str:
    """Rough README cleanup: keep prose, drop badges, images, code and markup."""
    md = re.sub(r"(?s)\A---.*?---", " ", md)                  # front matter
    md = re.sub(r"(?s)```.*?```", " ", md)                    # code fences
    md = re.sub(r"(?s)<!--.*?-->", " ", md)                   # comments
    md = re.sub(r"(?is)<(picture|table|svg|details)[^>]*>.*?</\1>", " ", md)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)             # images
    md = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", " ", md)  # linked badges
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)          # links -> text
    md = _TAG.sub(" ", md)
    lines = []
    for line in md.splitlines():
        line = line.strip().lstrip("#>*-+|= ").strip()
        if len(line) < 25 or line.count("|") > 2:
            continue
        lines.append(line)
    return clean_text(" ".join(lines))


def trim_words(text: str, limit: int = 60) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    cut = " ".join(words[:limit])
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > len(cut) * 0.6:
        return cut[: end + 1]
    return cut.rstrip(",;:—-") + "…"


# ---------------------------------------------------------------- time

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
