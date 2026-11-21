"use strict";

const CATEGORIES = {
  markets: "Tech stocks",
  repos: "GitHub repos",
  tools: "Tools and agents",
  models: "Models and research",
  policy: "Policy and politics",
  industry: "Industry",
};
const VIEW_NAMES = { all: "All stories", ...CATEGORIES, saved: "Saved" };
const SOURCE_LABELS = {
  "GitHub trending": "GitHub",
  "Hugging Face trending": "Hugging Face",
  "Hugging Face papers": "Hugging Face",
};
const DISCUSSION_SOURCES = new Set(["Hacker News", "Lobsters"]);
const SEEN_AFTER_MS = 1200;
const SEEN_KEEP_DAYS = 14;

const $ = (sel, root = document) => root.querySelector(sel);
const feedEl = $("#feed");
const tpl = $("#card-tpl");

// ------------------------------------------------------------------ storage
const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

const seen = new Map(Object.entries(store.get("tt.seen", {})));
const saved = new Map(store.get("tt.saved", []).map((s) => [s.id, s]));
let view = store.get("tt.view", "all");
if (!(view in VIEW_NAMES)) view = "all";
let feed = { stories: [], half_life_hours: 30 };

(function pruneSeen() {
  const cutoff = Date.now() - SEEN_KEEP_DAYS * 864e5;
  for (const [id, t] of seen) if (t < cutoff) seen.delete(id);
})();

let saveTimer;
function persistSeen() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => store.set("tt.seen", Object.fromEntries(seen)), 400);
}

// ------------------------------------------------------------------ formatting
const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const listFmt = new Intl.ListFormat("en", { style: "long", type: "conjunction" });
const num = (n) => n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : n.toLocaleString("en");

function timeAgo(iso) {
  const t = Date.parse(iso);
  if (!t) return "";
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins > -60) return mins > -2 ? "just now" : rtf.format(mins, "minute");
  const hours = Math.round(mins / 60);
  if (hours > -24) return rtf.format(hours, "hour");
  return rtf.format(Math.round(hours / 24), "day");
}

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "source"; }
}

function readLabel(s) {
  if (s.kind === "discussion") return `Read at ${hostOf(s.url)}`;
  const name = s.sources[0]?.name || hostOf(s.url);
  return `Read at ${SOURCE_LABELS[name] || name}`;
}

function attention(s) {
  const g = s.signals || {};
  const parts = [];
  if (g.stars_today) parts.push(`${num(g.stars_today)} stars today`);
  else if (g.stars && g.new_repo) parts.push(`${num(g.stars)} stars since launch`);
  else if (g.stars) parts.push(`${num(g.stars)} stars`);
  if (g.points) parts.push(`${num(g.points)} points on Hacker News`);
  if (g.lobsters_score) parts.push(`${g.lobsters_score} votes on Lobsters`);
  if (g.upvotes) parts.push(`${num(g.upvotes)} upvotes on Hugging Face`);
  if (g.likes) parts.push(`${num(g.likes)} likes on Hugging Face`);
  if (g.comments) parts.push(`${num(g.comments)} comments`);
  let text = parts.length ? `${listFmt.format(parts)}.` : "";
  if (g.language && s.kind === "github") text += `${text ? " " : ""}Written in ${g.language}.`;
  const others = s.sources.slice(1).map((x) => x.name).filter((n) => !DISCUSSION_SOURCES.has(n));
  if (others.length) text += `${text ? " " : ""}Also covered by ${listFmt.format(others.slice(0, 3))}.`;
  return text;
}

function posterText(s) {
  if (s.kind === "github" || s.kind === "hf_model") return s.url.split("/").filter(Boolean).pop();
  return SOURCE_LABELS[s.sources[0]?.name] || s.sources[0]?.name || hostOf(s.url);
}

function ageHours(s, now) {
  const t = Date.parse(s.published || s.first_seen) || now;
  return Math.max(0, (now - t) / 36e5);
}

function score(s, now) {
  return (s.heat || 1) * Math.pow(0.5, ageHours(s, now) / (feed.half_life_hours || 30));
}

// Fresh market stories are the top priority: they sit above everything else.
function pinned(s, now) {
  return s.category === "markets" && ageHours(s, now) <= (feed.priority_hours || 36);
}

// ------------------------------------------------------------------ cards
function buildCard(s) {
  const card = tpl.content.firstElementChild.cloneNode(true);
  card.dataset.cat = s.category;
  card.dataset.kind = s.kind;
  card.dataset.id = s.id;

  const art = $(".art", card);
  const img = $("img", card);
  $(".poster", card).textContent = posterText(s);
  if (s.image) {
    img.dataset.src = s.image;
    img.addEventListener("error", () => art.classList.add("no-image"), { once: true });
  } else {
    art.classList.add("no-image");
  }

  $(".cat", card).textContent = CATEGORIES[s.category] || "Story";
  const time = $("time", card);
  const when = s.published || s.first_seen;
  time.dateTime = when || "";
  time.textContent = timeAgo(when);

  const headline = $(".headline a", card);
  headline.textContent = s.title;
  headline.href = s.url;
  $(".summary", card).textContent = s.summary;
  $(".why", card).hidden = s.category !== "markets";
  $(".attention", card).textContent = attention(s);

  const read = $(".read", card);
  read.textContent = readLabel(s);
  read.href = s.url;

  const discussion = s.sources.find((x) => DISCUSSION_SOURCES.has(x.name) && x.url !== s.url);
  const comments = $(".comments", card);
  if (discussion) {
    comments.href = discussion.url;
    comments.setAttribute("aria-label", `Open the discussion on ${discussion.name}`);
  } else {
    comments.hidden = true;
  }

  const saveBtn = $(".save", card);
  const syncSave = () => {
    const on = saved.has(s.id);
    saveBtn.setAttribute("aria-pressed", String(on));
    saveBtn.setAttribute("aria-label", on ? "Remove from saved" : "Save");
  };
  syncSave();
  saveBtn.addEventListener("click", () => toggleSave(s, syncSave));
  $(".share", card).addEventListener("click", () => share(s));
  return card;
}

function noteCard({ title, body, action, onAction, bands = true }) {
  const card = document.createElement("section");
  card.className = "card note";
  if (bands) {
    const b = document.createElement("div");
    b.className = "bands";
    b.setAttribute("aria-hidden", "true");
    for (const cat of Object.keys(CATEGORIES)) {
      const span = document.createElement("span");
      span.dataset.cat = cat;
      b.append(span);
    }
    card.append(b);
  }
  const h = document.createElement("h2");
  h.textContent = title;
  const p = document.createElement("p");
  p.textContent = body;
  card.append(h, p);
  if (action) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "primary";
    btn.textContent = action;
    btn.addEventListener("click", onAction);
    card.append(btn);
  }
  return card;
}

// ------------------------------------------------------------------ rendering
function storiesInView() {
  if (view === "saved") return [...saved.values()].sort((a, b) => b.saved_at - a.saved_at);
  return view === "all" ? feed.stories : feed.stories.filter((s) => s.category === view);
}

function unseenCount(v = view) {
  if (v === "saved") return saved.size;
  return feed.stories.filter((s) => (v === "all" || s.category === v) && !seen.has(s.id)).length;
}

function render() {
  const now = Date.now();
  const list = storiesInView();
  feedEl.replaceChildren();
  cardObserver.disconnect();
  imageObserver.disconnect();

  if (view === "saved") {
    if (!list.length) {
      feedEl.append(noteCard({ title: "Nothing saved yet", body: "Tap the bookmark on any story to keep it here, even after it leaves the feed." }));
    }
    list.forEach((s) => feedEl.append(buildCard(s)));
  } else if (!list.length) {
    feedEl.append(noteCard({
      title: feed.stories.length ? "No stories here yet" : "No stories yet",
      body: feed.stories.length
        ? "Nothing in this category this week. Pick another category from the menu above."
        : "The feed fills up after its first update. Tap refresh to check again.",
      action: "Check for new stories",
      onAction: () => refresh(),
    }));
  } else {
    const byScore = (a, b) => (pinned(b, now) - pinned(a, now)) || (score(b, now) - score(a, now));
    const fresh = list.filter((s) => !seen.has(s.id)).sort(byScore);
    const old = list.filter((s) => seen.has(s.id)).sort(byScore);
    fresh.forEach((s) => feedEl.append(buildCard(s)));
    feedEl.append(noteCard({
      title: fresh.length ? "You're up to date" : "Nothing new since your last visit",
      body: old.length
        ? `Updated ${timeAgo(feed.generated_at)}. Stories you've already seen are below.`
        : `Updated ${timeAgo(feed.generated_at)}. New stories arrive every few hours.`,
      action: "Check for new stories",
      onAction: () => refresh(),
    }));
    old.forEach((s) => feedEl.append(buildCard(s)));
  }

  for (const el of feedEl.children) {
    cardObserver.observe(el);
    const img = $("img[data-src]", el);
    if (img) imageObserver.observe(img);
  }
  feedEl.scrollTop = 0;
  requestAnimationFrame(markClipped);
  updateChrome();
}

function markClipped() {
  for (const body of feedEl.querySelectorAll(".body")) {
    body.classList.toggle("clipped", body.scrollHeight > body.clientHeight + 2);
  }
}

function updateChrome() {
  const btn = $("#view-btn");
  btn.dataset.cat = view;
  $("#view-name").textContent = VIEW_NAMES[view];
  const n = unseenCount();
  $("#count").textContent = view === "saved" ? `${n} saved` : n ? `${n} new` : "";
}

// ------------------------------------------------------------------ observers
const imageObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    const img = e.target;
    img.src = img.dataset.src;
    delete img.dataset.src;
    imageObserver.unobserve(img);
  }
}, { root: feedEl, rootMargin: "200% 0px" });

const seenTimers = new Map();
const cardObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const id = e.target.dataset.id;
    if (!id) continue;
    if (e.isIntersecting) {
      seenTimers.set(id, setTimeout(() => {
        if (!seen.has(id)) {
          seen.set(id, Date.now());
          persistSeen();
          updateChrome();
        }
      }, SEEN_AFTER_MS));
    } else {
      clearTimeout(seenTimers.get(id));
      seenTimers.delete(id);
    }
  }
}, { root: feedEl, threshold: 0.6 });

// ------------------------------------------------------------------ actions
let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function toggleSave(s, sync) {
  if (saved.has(s.id)) {
    saved.delete(s.id);
    toast("Removed from saved");
  } else {
    saved.set(s.id, { ...s, saved_at: Date.now() });
    toast("Saved");
  }
  store.set("tt.saved", [...saved.values()]);
  sync();
  updateChrome();
}

async function share(s) {
  const data = { title: s.title, text: s.summary, url: s.url };
  if (navigator.share) {
    try { await navigator.share(data); } catch { /* dismissed */ }
    return;
  }
  try {
    await navigator.clipboard.writeText(`${s.title}\n${s.url}`);
    toast("Link copied");
  } catch {
    toast("Couldn't copy the link. Long-press the headline instead.");
  }
}

async function loadFeed() {
  const res = await fetch(`data/feed.json?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function refresh({ quiet = false } = {}) {
  const btn = $("#refresh");
  btn.classList.add("spinning");
  const before = new Set(feed.stories.map((s) => s.id));
  try {
    feed = await loadFeed();
    const added = feed.stories.filter((s) => !before.has(s.id)).length;
    render();
    if (!quiet) {
      toast(added ? `${added} new ${added === 1 ? "story" : "stories"}` : `No new stories. Last update ${timeAgo(feed.generated_at)}.`);
    }
  } catch {
    if (!feed.stories.length) {
      feedEl.replaceChildren(noteCard({
        title: "Couldn't load stories",
        body: "Check your connection, then try again.",
        action: "Try again",
        onAction: () => refresh(),
        bands: false,
      }));
    } else if (!quiet) {
      toast("Couldn't reach the feed. Showing the stories you already have.");
    }
  } finally {
    btn.classList.remove("spinning");
  }
}

// ------------------------------------------------------------------ category sheet
const sheet = $("#views");
function openSheet() {
  const list = $("#view-list");
  list.replaceChildren();
  for (const [key, name] of Object.entries(VIEW_NAMES)) {
    const b = document.createElement("button");
    b.type = "submit";
    b.className = "view-option";
    b.dataset.cat = key;
    b.value = key;
    if (key === view) b.setAttribute("aria-current", "true");
    const sw = document.createElement("span");
    sw.className = "swatch";
    const label = document.createElement("span");
    label.textContent = name;
    const n = document.createElement("span");
    n.className = "n";
    const count = unseenCount(key);
    n.textContent = key === "saved" ? (count ? `${count}` : "") : (count ? `${count} new` : "");
    b.append(sw, label, n);
    list.append(b);
  }
  sheet.showModal();
}
sheet.addEventListener("close", () => {
  const choice = sheet.returnValue;
  sheet.returnValue = "";
  if (choice && choice in VIEW_NAMES && choice !== view) {
    view = choice;
    store.set("tt.view", view);
    render();
  }
});
sheet.addEventListener("click", (e) => { if (e.target === sheet) sheet.close(); });
$("#view-btn").addEventListener("click", openSheet);
$("#refresh").addEventListener("click", () => refresh());

// ------------------------------------------------------------------ keyboard (desktop)
document.addEventListener("keydown", (e) => {
  if (sheet.open || e.metaKey || e.ctrlKey || e.altKey) return;
  const step = feedEl.clientHeight;
  const current = [...feedEl.children][Math.round(feedEl.scrollTop / step)];
  if (["ArrowDown", "j", "PageDown", " "].includes(e.key)) {
    feedEl.scrollBy({ top: step });
  } else if (["ArrowUp", "k", "PageUp"].includes(e.key)) {
    feedEl.scrollBy({ top: -step });
  } else if ((e.key === "o" || e.key === "Enter") && current?.dataset.id) {
    $(".read", current).click();
  } else if (e.key === "s" && current?.dataset.id) {
    $(".save", current).click();
  } else {
    return;
  }
  e.preventDefault();
});

window.addEventListener("resize", () => requestAnimationFrame(markClipped));
document.addEventListener("visibilitychange", () => {
  // Coming back to the app after a while: pick up a newer feed quietly.
  if (document.visibilityState === "visible" && Date.now() - (Date.parse(feed.generated_at) || 0) > 3 * 36e5) {
    refresh({ quiet: true });
  }
});

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

updateChrome();
refresh({ quiet: true });
