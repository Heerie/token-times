"""Collectors. Each returns a list of candidate dicts (see `candidate`)."""
from __future__ import annotations

import calendar
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser

from util import clean_text, get_json, http_get, iso, log, make_id, now_utc

# Case-sensitive acronyms plus case-insensitive words that signal an AI story.
AI_ACRONYMS = re.compile(r"\b(AI|A\.I\.|AGI|LLMs?|GPT[-\w.]*|RAG|MCP|GPUs?|TPUs?|RLHF|ML)\b")
AI_WORDS = re.compile(
    r"\b(artificial intelligence|machine learning|deep learning|neural|language models?|"
    r"foundation models?|chatgpt|openai|anthropic|claude|gemini|deepmind|llama|mistral|qwen|"
    r"deepseek|grok|xai|copilot|codex|hugging ?face|transformers?|diffusion|fine-?tun\w*|"
    r"embeddings?|agentic|ai agents?|coding agents?|chatbots?|nvidia|inference|"
    r"vibe[- ]cod\w*|reinforcement learning|multimodal|text-to-\w+|sora|midjourney|"
    r"stable diffusion|perplexity|ollama|vllm|llama\.cpp|pytorch|cuda)\b",
    re.I,
)


def is_ai(text: str) -> bool:
    return bool(AI_ACRONYMS.search(text) or AI_WORDS.search(text))


def candidate(url: str, title: str, *, source: str, source_url: str | None = None,
              snippet: str = "", kind: str = "news", published: str | None = None,
              image: str | None = None, signals: dict | None = None,
              ai_hint: bool = False, category_hint: str | None = None,
              extra: dict | None = None) -> dict:
    return {
        "id": make_id(url),
        "url": url,
        "title": clean_text(title),
        "snippet": clean_text(snippet)[:1500],
        "sources": [{"name": source, "url": source_url or url}],
        "kind": kind,
        "published": published,
        "image": image,
        "signals": {k: v for k, v in (signals or {}).items() if v not in (None, 0, "")},
        "ai_hint": ai_hint,
        "category_hint": category_hint,
        "extra": extra or {},
    }


# ---------------------------------------------------------------- GitHub

def _gh_image(full_name: str) -> str:
    # The first path segment is only a cache key; a daily key keeps star counts fresh.
    return f"https://opengraph.githubassets.com/{time.strftime('%Y%m%d')}/{full_name}"


def github_trending(cfg: dict) -> list[dict]:
    page = http_get("https://github.com/trending?since=daily").decode("utf-8", "ignore")
    out = []
    for row in page.split('class="Box-row"')[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', row)
        if not m:
            continue
        full = m.group(1)
        desc = re.search(r'<p class="col-9[^"]*">(.*?)</p>', row, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', row)
        stars = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', row, re.S)
        today = re.search(r"([\d,]+)\s+stars?\s+(today|this week)", row)
        num = lambda s: int(re.sub(r"\D", "", s) or 0)  # noqa: E731
        description = clean_text(desc.group(1)) if desc else ""
        out.append(candidate(
            f"https://github.com/{full}", full, source="GitHub trending",
            source_url="https://github.com/trending", snippet=description, kind="github",
            image=_gh_image(full), category_hint="repos",
            signals={"stars": num(clean_text(stars.group(1))) if stars else 0,
                     "stars_today": num(today.group(1)) if today else 0,
                     "language": clean_text(lang.group(1)) if lang else None},
            ai_hint=is_ai(f"{full} {description}"),
            extra={"full_name": full},
        ))
    return out


def github_new(cfg: dict, token: str | None) -> list[dict]:
    src = cfg["sources"]
    week = (now_utc() - timedelta(days=7)).date().isoformat()
    fortnight = (now_utc() - timedelta(days=14)).date().isoformat()
    queries = [f"created:>{week} stars:>{src['github_new_min_stars']}"]
    queries += [f"topic:{t} created:>{fortnight} stars:>{src['github_topic_min_stars']}"
                for t in src.get("github_topics", [])]
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out = []
    for q in queries:
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 15})
        try:
            data = get_json(url, headers=headers)
        except Exception as e:  # rate limits etc. shouldn't sink the run
            log(f"github search failed for {q!r}: {e}")
            continue
        for r in data.get("items", []):
            text = f"{r['full_name']} {r.get('description') or ''} {' '.join(r.get('topics') or [])}"
            out.append(candidate(
                # No `published`: a repo's story starts when it takes off, not when it was created.
                r["html_url"], r["full_name"], source="GitHub", snippet=r.get("description") or "",
                kind="github", image=_gh_image(r["full_name"]),
                category_hint="repos",
                signals={"stars": r.get("stargazers_count"), "language": r.get("language"),
                         "new_repo": True},
                ai_hint=is_ai(text) or q.startswith("topic:"),
                extra={"full_name": r["full_name"], "topics": r.get("topics") or []},
            ))
    return out


# ---------------------------------------------------------------- discussion sites

def hackernews(cfg: dict) -> list[dict]:
    since = int(time.time()) - 36 * 3600
    url = ("https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=300&numericFilters="
           f"created_at_i>{since},points>={cfg['sources']['hn_min_points']}")
    out = []
    for h in get_json(url).get("hits", []):
        item_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        text = f"{h.get('title', '')} {h.get('story_text') or ''}"
        out.append(candidate(
            h.get("url") or item_url, h.get("title", ""), source="Hacker News",
            source_url=item_url, snippet=h.get("story_text") or "", kind="discussion",
            published=h.get("created_at"),
            signals={"points": h.get("points"), "comments": h.get("num_comments")},
            ai_hint=is_ai(text),
        ))
    return out


def lobsters(cfg: dict) -> list[dict]:
    cutoff = now_utc() - timedelta(days=3)
    out = []
    for s in get_json("https://lobste.rs/t/ai.json"):
        try:
            if datetime.fromisoformat(s["created_at"]) < cutoff:
                continue
        except (KeyError, ValueError):
            pass
        out.append(candidate(
            s.get("url") or s["comments_url"], s["title"], source="Lobsters",
            source_url=s["comments_url"], snippet=s.get("description") or "", kind="discussion",
            published=s.get("created_at"),
            signals={"lobsters_score": s.get("score"), "comments": s.get("comment_count")},
            ai_hint=True,
        ))
    return out


# ---------------------------------------------------------------- Hugging Face

def hf_trending_models(cfg: dict) -> list[dict]:
    data = get_json("https://huggingface.co/api/trending?type=model&limit=15")
    out = []
    for entry in data.get("recentlyTrending", []):
        r = entry.get("repoData") or {}
        mid = r.get("id")
        if not mid:
            continue
        out.append(candidate(
            f"https://huggingface.co/{mid}", mid, source="Hugging Face trending",
            source_url="https://huggingface.co/models?sort=trending", kind="hf_model",
            image=f"https://cdn-thumbnails.huggingface.co/social-thumbnails/models/{mid}.png",
            category_hint="models", ai_hint=True,
            signals={"likes": r.get("likes"), "downloads": r.get("downloads")},
            snippet=r.get("pipeline_tag") or "",
            extra={"model_id": mid},
        ))
    return out


def hf_daily_papers(cfg: dict) -> list[dict]:
    min_up = cfg["sources"]["hf_paper_min_upvotes"]
    cutoff = now_utc() - timedelta(days=3)
    out = []
    for d in get_json("https://huggingface.co/api/daily_papers?limit=100"):
        p = d.get("paper") or {}
        if (p.get("upvotes") or 0) < min_up:
            continue
        published = d.get("publishedAt") or p.get("publishedAt")
        try:
            if published and datetime.fromisoformat(published.replace("Z", "+00:00")) < cutoff:
                continue
        except ValueError:
            pass
        out.append(candidate(
            f"https://huggingface.co/papers/{p['id']}", d.get("title") or p.get("title", ""),
            source="Hugging Face papers", kind="paper", published=published,
            image=d.get("thumbnail"), category_hint="models", ai_hint=True,
            snippet=p.get("ai_summary") or p.get("summary") or "",
            signals={"upvotes": p.get("upvotes"), "comments": d.get("numComments"),
                     "github_stars": p.get("githubStars")},
            extra={"abstract": clean_text(p.get("summary"))[:2500]},
        ))
    return out


# ---------------------------------------------------------------- RSS

def _entry_image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            if m.get("url") and (m.get("medium") in (None, "image") or "image" in (m.get("type") or "image")):
                return m["url"]
    for enc in e.get("enclosures") or []:
        if (enc.get("type") or "").startswith("image") and enc.get("href"):
            return enc["href"]
    body = " ".join(c.get("value", "") for c in e.get("content") or []) + (e.get("summary") or "")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', body)
    return m.group(1) if m else None


def rss_feed(feed_cfg: dict, cfg: dict) -> list[dict]:
    parsed = feedparser.parse(http_get(feed_cfg["url"]))
    cutoff = now_utc() - timedelta(hours=cfg["sources"]["rss_max_age_hours"])
    out = []
    for e in parsed.entries[:60]:
        link, title = e.get("link"), e.get("title")
        if not link or not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        published = (datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc)
                     if stamp else None)
        if published and published < cutoff:
            continue
        body = " ".join(c.get("value", "") for c in e.get("content") or [])
        summary = e.get("summary") or body
        if not feed_cfg.get("ai_only") and not is_ai(f"{title} {clean_text(summary)[:600]}"):
            continue
        out.append(candidate(
            link, title, source=feed_cfg["name"], snippet=summary, kind="news",
            published=iso(published), image=_entry_image(e), ai_hint=True,
        ))
    return out
