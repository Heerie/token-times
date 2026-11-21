# Token Times

A personal, swipe-up AI news feed for your phone. Every few hours a GitHub
Action collects what people are talking about in AI, Claude picks the stories
worth your time, merges duplicates and writes a short card for each, and
GitHub Pages serves the result as an installable web app.

Claude runs through **Claude Code on your Claude subscription**, so there is no
API key and no separate bill; each run uses a little of your plan's usage.

**Tech stocks come first.** Market news about the companies in `[markets]` in
`config.toml` (share moves, earnings, guidance, analyst calls) is pinned to the
top of the feed for 36 hours. Each market card has a headline stating the move
and a **Why** section with the reasoning behind it. Market sources: Yahoo
Finance (per ticker), CNBC, Bloomberg, Financial Times, Benzinga,
Investing.com and MarketWatch.

**AI sources:** GitHub trending and fast-growing new repos (plus the `llm`,
`ai-agents`, `mcp`, `claude-code` and `agent-skills` topics), Hacker News,
Lobsters, Hugging Face trending models and daily papers, and 18 news feeds
(TechCrunch, The Verge, Ars Technica, MIT Technology Review, The Decoder,
Wired, The Guardian, Politico, OpenAI, Google DeepMind, Simon Willison, Latent
Space, Import AI, Interconnects and more). Edit `config.toml` to change them.

**Categories:** Tech stocks, GitHub repos, Tools and agents, Models and research, Policy
and politics, Industry. Each has its own colour; images are tinted to match.

## Put it on your phone (GitHub Pages, no server needed)

1. Create a new **public** repository on GitHub (Pages on a private repo
   needs a paid plan) and push this folder to its `main` branch.
2. In the repo: **Settings → Pages → Build and deployment → Source:
   GitHub Actions**.
3. Create a login token for the Action. In your own terminal (not inside a
   Claude Code chat, so the token isn't saved in a transcript), run:

   ```sh
   claude setup-token
   ```

   It opens your browser to approve, then prints a token starting with
   `sk-ant-oat`. In the repo, go to **Settings → Secrets and variables →
   Actions → New repository secret**, name it `CLAUDE_CODE_OAUTH_TOKEN` and
   paste the token. Treat it like a password: it can use your Claude plan.
   Without it the feed still works in basic mode.
4. **Actions → Update feed → Run workflow** for the first build. After that it
   runs every 4 hours.
5. On your phone, open `https://<your-username>.github.io/<repo-name>/`, then
   use **Add to Home Screen** (Safari share menu, or Chrome's menu).

## Using it

- Swipe up for the next story. New stories come first, sorted by how hot they
  are (votes, stars and comments, decaying over about a day); stories you've
  already seen follow after the "You're up to date" card.
- Tap the headline or **Read at …** to open the original, the speech bubble
  for the Hacker News or Lobsters thread, the bookmark to save, and the arrow
  to share.
- The category menu at the top filters by topic and holds your saved stories.
- On a computer: arrow keys or `j`/`k` to move, `o` to open, `s` to save.

Read and saved state lives in your phone's browser storage only.

## Run it locally

Uses the Claude Code login already on your computer.

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -r fetcher/requirements.txt   # Windows
# .venv/bin/python -m pip install -r fetcher/requirements.txt     # macOS/Linux

.venv/Scripts/python fetcher/build.py            # with Claude
.venv/Scripts/python fetcher/build.py --no-llm   # basic mode, no Claude

.venv/Scripts/python -m http.server 8765 -d site
# open http://localhost:8765
```

To try it on your phone over Wi-Fi, serve with `--bind 0.0.0.0` and open
`http://<your-computer's-IP>:8765` (allow Python through the firewall).

## How much of your plan it uses

Each run sends Claude only items it has not judged before: up to 160 on the
first run, usually 40–100 after that. A run is one call to pick stories plus
one call per 8 cards. The build log reports every run, for example:

```
Claude kept 59 of 160; wrote 59 cards; 9 calls, 94,475 input and 13,589 output
tokens on your Claude subscription (would be about $0.98 at API prices)
```

That first run took under 3 minutes; later runs are smaller. If it eats into
the limits you need for your own work, switch `model` in `config.toml` to
`claude-sonnet-5-5` or `claude-haiku-5-5`, or run less often by editing the
`cron` line in `.github/workflows/update-feed.yml`.

To use a Claude API key instead, set `backend = "api"` in `config.toml` and add
an `ANTHROPIC_API_KEY` secret.

## How it works

```
config.toml              what to collect, model, your interests
fetcher/sources.py       collectors (GitHub, HN, Lobsters, Hugging Face, RSS)
fetcher/enrich.py        fetches each picked page for its image and text
fetcher/curate.py        Claude editor + writer, and the basic-mode fallback
fetcher/build.py         orchestrates a run, writes site/data/feed.json
state/seen.json          items Claude already judged (so they aren't re-sent)
site/                    the web app (plain HTML, CSS and JS; no build step)
```

The editor call sees every new item's title, snippet and engagement numbers,
keeps what matches the interests in `config.toml`, groups coverage of the same
story and scores importance. The writer calls then read each picked page and
write the headline and summary. Each call runs `claude -p` with structured
JSON output and with every tool, setting, plugin and MCP server turned off and
no session saved, so it only reads the prompt and answers.

Reddit blocks unauthenticated access from servers, so it isn't a source yet.
The feed file is committed on every run so stories persist between runs; that
adds a small commit to the history every 4 hours.
