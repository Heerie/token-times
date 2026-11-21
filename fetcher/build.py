"""Collect stories, curate them and write site/data/feed.json.

    python fetcher/build.py            # Claude picks and summarises (see [llm] in config.toml)
    python fetcher/build.py --no-llm   # basic mode: keyword filter + source snippets
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sources  # noqa: E402
from curate import ClaudeUnavailable, Curator, basic_curate, engagement, heat, to_story  # noqa: E402
from enrich import enrich_all  # noqa: E402
from util import iso, log, now_utc, parse_iso  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.toml"
FEED = ROOT / "site" / "data" / "feed.json"
SEEN = ROOT / "state" / "seen.json"
HALF_LIFE_HOURS = 30


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1 if pretty else None,
                      separators=None if pretty else (",", ":"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def collect(cfg: dict, token: str | None) -> list[dict]:
    jobs = {
        "GitHub trending": lambda: sources.github_trending(cfg),
        "GitHub new repos": lambda: sources.github_new(cfg, token),
        "Hacker News": lambda: sources.hackernews(cfg),
        "Lobsters": lambda: sources.lobsters(cfg),
        "Hugging Face models": lambda: sources.hf_trending_models(cfg),
        "Hugging Face papers": lambda: sources.hf_daily_papers(cfg),
    }
    for feed in cfg["sources"].get("rss", []):
        jobs[feed["name"]] = (lambda f=feed: sources.rss_feed(f, cfg))
    out: list[dict] = []
    with cf.ThreadPoolExecutor(12) as ex:
        futures = {ex.submit(fn): name for name, fn in jobs.items()}
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                items = fut.result()
                out.extend(items)
                log(f"{name}: {len(items)}")
            except Exception as e:
                log(f"{name}: failed ({e})")
    return out


def merge_candidates(cands: list[dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for c in cands:
        m = merged.get(c["id"])
        if not m:
            merged[c["id"]] = c
            continue
        names = {s["name"] for s in m["sources"]}
        m["sources"] += [s for s in c["sources"] if s["name"] not in names]
        for k, v in c["signals"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                m["signals"][k] = max(m["signals"].get(k, 0), v)
            else:
                m["signals"].setdefault(k, v)
        # Prefer the publisher's title and text over a discussion site's.
        if m["kind"] == "discussion" and c["kind"] != "discussion":
            for key in ("title", "kind", "category_hint"):
                m[key] = c[key]
        if len(c["snippet"]) > len(m["snippet"]):
            m["snippet"] = c["snippet"]
        m["image"] = m.get("image") or c.get("image")
        m["published"] = m.get("published") or c.get("published")
        m["ai_hint"] = m["ai_hint"] or c["ai_hint"]
    return merged


def refresh_story(story: dict, c: dict) -> None:
    names = {s["name"] for s in story["sources"]}
    story["sources"] += [s for s in c["sources"] if s["name"] not in names]
    for k, v in c["signals"].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            story["signals"][k] = max(story["signals"].get(k, 0), v)
    story["image"] = story.get("image") or c.get("image")
    story["heat"] = heat(story["importance"], story["signals"], len(story["sources"]))


OFFICIAL = {"OpenAI", "Google DeepMind", "Google AI", "Hugging Face blog", "GitHub blog"}


def prescore(c: dict) -> float:
    """Order new items before the cap. News has no vote counts, so give it a prior."""
    score = engagement(c["signals"], len(c["sources"]))
    if c["kind"] == "news":
        score += 0.45
    if any(s["name"] in OFFICIAL for s in c["sources"]):
        score += 0.35
    return score + (0.5 if c["ai_hint"] else 0)


def story_age_hours(s: dict, now) -> float:
    t = parse_iso(s.get("published")) or parse_iso(s.get("first_seen")) or now
    return max(0.0, (now - t).total_seconds() / 3600)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="skip Claude even if a key is set")
    args = ap.parse_args()

    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    now = now_utc()
    token = os.environ.get("GITHUB_TOKEN") or None
    backend = cfg["llm"].get("backend", "claude-code")
    if args.no_llm:
        use_llm = False
    elif backend == "api":
        use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    else:
        use_llm = shutil.which("claude") is not None
    log(f"curation: {'Claude via ' + backend if use_llm else 'basic mode'}")

    feed = load_json(FEED, {"stories": []})
    stories: list[dict] = feed.get("stories", [])
    seen: dict[str, str] = load_json(SEEN, {})
    if use_llm:
        # Basic-mode cards are placeholders; let Claude redo those stories.
        stories = [s for s in stories if s.get("by_claude")]

    merged = merge_candidates(collect(cfg, token))
    log(f"{len(merged)} unique items collected")

    by_id = {i: s for s in stories for i in s["ids"]}
    for cid, c in merged.items():
        if cid in by_id:
            refresh_story(by_id[cid], c)

    new = [c for cid, c in merged.items() if cid not in by_id and not (use_llm and cid in seen)]
    new.sort(key=prescore, reverse=True)
    new = new[: cfg["feed"]["max_new_candidates"]]
    log(f"{len(new)} new items to consider")

    added: list[dict] = []
    mode = "basic"
    if use_llm and new:
        try:
            curator = Curator(cfg)
            recent = sorted(stories, key=lambda s: s.get("first_seen", ""), reverse=True)
            picks = curator.edit(new, recent)
            if picks is None:
                raise ClaudeUnavailable("the editor call failed")
            fresh = []
            for p in picks:
                target = by_id.get(p["duplicate_of"]) if p["duplicate_of"] else None
                if target:
                    for c in p["group"]:
                        refresh_story(target, c)
                else:
                    fresh.append(p)
            enrich_all([p["group"][0] for p in fresh], token)
            cards = curator.write(fresh)
            unwritten: set[str] = set()
            for p in fresh:
                primary = p["group"][0]
                card = cards.get(primary["id"])
                if not card:
                    unwritten.update(c["id"] for c in p["group"])
                    continue
                added.append(to_story(primary, p["group"], title=card["headline"].strip(),
                                      summary=card["summary"].strip(), category=p["category"],
                                      importance=p["importance"], by_claude=True))
            for c in new:
                if c["id"] not in unwritten:
                    seen[c["id"]] = iso(now)
            mode = "claude"
            log(f"Claude kept {len(picks)} of {len(new)}; wrote {len(added)} cards; {curator.cost_estimate()}")
        except ClaudeUnavailable as e:
            log(f"Claude unavailable ({e}); using basic mode this run")
            use_llm = False
    elif use_llm:
        mode = "claude"
    if not use_llm and new:
        per_kind = {"news": 40, "discussion": 25, "github": 20, "paper": 8, "hf_model": 6}
        taken: dict[str, int] = {}
        candidates = []
        for c in new:
            if c["ai_hint"] and taken.get(c["kind"], 0) < per_kind.get(c["kind"], 10):
                taken[c["kind"]] = taken.get(c["kind"], 0) + 1
                candidates.append(c)
        enrich_all(candidates, token)
        added = basic_curate(candidates)
        log(f"basic mode added {len(added)} stories")

    for s in added:
        s["first_seen"] = iso(now)
    stories = stories + added
    retention_h = cfg["feed"]["retention_days"] * 24
    stories = [s for s in stories if story_age_hours(s, now) <= retention_h and s.get("summary")]
    stories.sort(key=lambda s: s["heat"] * 0.5 ** (story_age_hours(s, now) / HALF_LIFE_HOURS),
                 reverse=True)
    stories = stories[: cfg["feed"]["max_items"]]

    write_json(FEED, {"generated_at": iso(now), "mode": mode, "half_life_hours": HALF_LIFE_HOURS,
                      "model": cfg["llm"]["model"] if mode == "claude" else None,
                      "count": len(stories), "stories": stories})
    cutoff = iso(now - timedelta(days=14))
    write_json(SEEN, {k: v for k, v in seen.items() if v >= cutoff}, pretty=True)
    log(f"wrote {len(stories)} stories ({len(added)} new) to {FEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
