"""Fetch the page behind a story for its image and some body text."""
from __future__ import annotations

import concurrent.futures as cf
import html
import re
import urllib.parse

from util import clean_text, http_get, markdown_to_text

_META = re.compile(r"<meta\s[^>]*>", re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')
_PARA = re.compile(r"(?is)<p[^>]*>(.*?)</p>")


def _meta_tags(head: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in _META.findall(head):
        attrs = {m.group(1).lower(): m.group(3) if m.group(3) is not None else m.group(4)
                 for m in _ATTR.finditer(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs and key not in found:
            found[key] = html.unescape(attrs["content"]).strip()
    return found


def fetch_page(url: str) -> dict:
    raw = http_get(url, timeout=12, max_bytes=1_500_000).decode("utf-8", "ignore")
    meta = _meta_tags(raw[:250_000])
    image = meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image")
    # Prefer the <article> body; keep only paragraphs that read like sentences,
    # which drops navigation, bylines and cookie banners.
    m = re.search(r"(?is)<article[^>]*>(.*)</article>", raw)
    body = m.group(1) if m and len(m.group(1)) > 2000 else raw
    paras = (clean_text(p) for p in _PARA.findall(body))
    text = " ".join(p for p in paras
                    if len(p) > 60 and p[-1] in ".!?\"”’)" and sum(c.islower() for c in p) > len(p) * 0.5)
    return {
        "image": urllib.parse.urljoin(url, image) if image else None,
        "description": clean_text(meta.get("og:description") or meta.get("description")),
        "text": text[:4000],
    }


def fetch_readme(full_name: str, token: str | None) -> str:
    for branch in ("HEAD",):
        for name in ("README.md", "readme.md", "README.rst", "README"):
            try:
                md = http_get(f"https://raw.githubusercontent.com/{full_name}/{branch}/{name}",
                              timeout=10, max_bytes=400_000).decode("utf-8", "ignore")
                return markdown_to_text(md)[:4000]
            except Exception:
                continue
    if token:
        try:
            md = http_get(f"https://api.github.com/repos/{full_name}/readme", timeout=10,
                          headers={"Accept": "application/vnd.github.raw",
                                   "Authorization": f"Bearer {token}"}).decode("utf-8", "ignore")
            return markdown_to_text(md)[:4000]
        except Exception:
            pass
    return ""


def enrich_one(c: dict, token: str | None) -> dict:
    """Return {'image', 'description', 'text'} for a candidate; never raises."""
    out = {"image": c.get("image"), "description": "", "text": ""}
    try:
        kind = c["kind"]
        if kind == "github":
            out["text"] = fetch_readme(c["extra"]["full_name"], token)
        elif kind == "hf_model":
            md = http_get(f"https://huggingface.co/{c['extra']['model_id']}/raw/main/README.md",
                          timeout=10, max_bytes=400_000).decode("utf-8", "ignore")
            out["text"] = markdown_to_text(md)[:4000]
        elif kind == "paper":
            out["text"] = c["extra"].get("abstract", "")
        elif "news.ycombinator.com/item" not in c["url"]:
            page = fetch_page(c["url"])
            out["image"] = out["image"] or page["image"]
            out["description"], out["text"] = page["description"], page["text"]
    except Exception:
        pass
    return out


def enrich_all(cands: list[dict], token: str | None, workers: int = 12) -> None:
    """Attach `page` data to each candidate in place."""
    with cf.ThreadPoolExecutor(workers) as ex:
        for c, page in zip(cands, ex.map(lambda c: enrich_one(c, token), cands)):
            c["page"] = page
            if not c.get("image") and page.get("image"):
                c["image"] = page["image"]
