
# FB-Comment-Archiver

**A single-file, session-cumulative Facebook comment archiver that treats the comment thread as a terrain to be mapped — not a feed to be scrolled.**

It launches a real browser, holds the session, discovers every expandable reply branch, and drains the thread into TXT and JSON archives before the tab is ever closed. Every comment and reply is preserved the moment it enters the DOM, so closing the window, hitting Ctrl+C, or losing the connection mid-scroll does not lose what was already captured.

---

## What this is

- A **Playwright-based archiver** for public Facebook posts, launching genuine Chrome or Edge (not bundled Chromium) so Arkose MatchKey and 2FA behave normally.
- A **cumulative session manager**: `browser_state.json` is merged, not overwritten. Log into three different accounts across three runs and all three logins survive.
- A **wave-based expander**: it repeatedly clicks *See more*, *View previous comments*, and *View N replies* inside the active scope, harvests what appeared, and only advances the scroll once the visible branch is exhausted.
- A **crash-safe live buffer**: JS keeps a delta of dirty comment IDs; Python drains it after every wave. Close the browser at any point and everything up to that moment is written.
- A **thread reconstructor**: parses `comment_id` and `reply_comment_id` from permalinks, resolves parents, and detects orphan replies whose parent never appeared.

## What this is not

- **Not a bypass of Facebook's ToS.** It logs in as you, scrapes what you can already see, and refuses to automate anything you could not do manually.
- **Not a bulk harvester.** One post per invocation. No crawling, no queue, no profile enumeration.
- **Not a passive observer.** It *will* click visible expansion buttons, switch the comment sort dropdown, and scroll the container — but only inside the post's own DOM scope.
- **Not a session thief.** `browser_state.json` and `chrome_real_profile/` are local artifacts. See **Privacy** below before committing anything to Git.

---

## Quick Start

### 1. Install

```bash
pip install playwright google-generativeai
playwright install chromium
```

### 2. First run — authenticate

```bash
python main.py
# choose [2] Authenticate / Refresh Browser Session
```

You will be prompted with two modes:

| Mode | When to use |
| :--- | :--- |
| **A** — Clean Playwright Chrome/Edge | Default. Fast, uses existing session state. |
| **B** — Real standalone desktop Chrome via CDP | Use if Arkose MatchKey blocks the Playwright launch, or if Facebook refuses to load after a clean start. |

Log in, solve any 2FA / MatchKey, dismiss cookie banners, then return to the terminal and press `Enter`. The session is merged into `browser_state.json` cumulatively — prior logins are never destroyed.

### 3. Scrape a post

```bash
python main.py
# choose [1] Scrape Facebook Post & Comments
# paste the post URL (any of the following work):
#   https://www.facebook.com/<page>/posts/<id>
#   https://www.facebook.com/share/p/<id>/
#   https://www.facebook.com/permalink.php?story_fbid=...&id=...
```

The scraper will:

1. Load the URL, detect login walls and pause for manual resolution if needed.
2. Inject the DOM engine and locate the active scope (modal dialog if the post was opened as one, otherwise `div[role="main"]`).
3. Switch the sort dropdown to **Alle opmerkingen / All comments** (configurable).
4. Run the scroll → harvest → expand loop until the thread is stable across `stable_thresh` consecutive passes.
5. Save TXT and (optionally) JSON into `Facebook Comment Archives/`.

### 4. Find your archives

```
Facebook Comment Archives/
├── TXT/
│   └── 2024-11-24_1832 Page Name - Post Snippet.txt
└── JSON/
    └── 2024-11-24_1832 Page Name - Post Snippet.json
```

Partial archives (closed browser, Ctrl+C, timeout) are suffixed `[PARTIAL]` in the filename and tagged `STATUS: PARTIAL` inside the file header.

---

## The Loop

The scraper does not "scroll to the bottom and read". It runs a fixed-point iteration per viewport:

```
┌─────────────────────────────────────────────────────────┐
│ 1. Expand visible "See more" (post body + comments)     │
│ 2. Harvest all [role="article"] into __fbComments       │
│ 3. Find expandable buttons near viewport                │
│    → click up to 3 per wave, wait for spinner+DY settle │
│ 4. If new items arrived → repeat from (1)               │
│ 5. Otherwise → scroll one step, then repeat from (1)    │
│ 6. Stop when (items, atBottom, spinner, buttons) have   │
│    all been stable for `stable_thresh` iterations       │
└─────────────────────────────────────────────────────────┘
```

Three properties matter here:

- **The live buffer is drained after every wave.** If the browser dies, the Python process still has everything captured up to the last drain. This is why a closed window produces a valid partial archive instead of a crash.
- **Dead buttons are tagged.** A button that has been clicked `max_button_retries` times without producing a DOM mutation is marked dead and never retried. This prevents the loop from hanging on a stale element that survives a re-render.
- **Orphan replies are promoted.** If a reply's `parentId` never resolves to an article in `__fbComments`, it is written to the archive as a top-level entry tagged `[Orphan Reply]` rather than silently dropped.

---

## Configuration

Edit the `CONFIG` dict at the top of the file, or use menu option `[3] Settings & Speed Profiles`.

### Speed profiles

| Profile | Scroll step | Wait per step | Stable passes | Feel |
| :--- | :--- | :--- | :--- | :--- |
| **1 — Conservative** | 40% of viewport | 850 ms | 5 | Slowest, safest, best for slow connections |
| **2 — Fast** (default) | 70% of viewport | 500 ms | 4 | Balanced |
| **3 — Aggressive** | 90% of viewport | 300 ms | 3 | Fastest; may miss a late-hydrating reply |
| **4 — Custom** | user-defined px | user-defined ms | user-defined | For tuning against a specific thread |

### Other toggles

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `force_all_comments` | `True` | Automatically switch the sort dropdown to *Alle opmerkingen / All comments* before scraping. |
| `max_button_retries` | `3` | Clicks on a single button before it is marked dead. |
| `max_waves_per_view` | `40` | Expansion waves per scroll viewport before advancing. |
| `title_mode` | `1` | `1` = `<Author> - <Post Snippet>`. `2` = Gemini-generated 5–7 word title. |
| `save_json` | `True` | Write the JSON archive alongside the TXT. |

### Gemini title generation (optional)

`title_mode = 2` requires a working Gemini API key:

```python
API_KEY = "your-key-here"   # replace the placeholder
MODEL_ID = "gemini-2.5-flash"
```

If the key is missing or the call fails, the scraper silently falls back to Post Snippet naming. No data is lost.

---

## Output Format

### TXT

Line-delimited, human-diffable. Header block, then a `### ORIGINAL POST ###` body, then `### COMMENTS & REPLIES ###` with one `[N]` block per top-level comment and `└── [N.M]` blocks per reply. Indentation of `└──` increases for nested replies (depth ≥ 2).

### JSON

```json
{
  "title": "...",
  "status": "COMPLETE",
  "stop_reason": null,
  "method": "Post Snippet",
  "url": "...",
  "archived_at": "2024-11-24T18:32:04",
  "post": { "author": "...", "text": "...", "advertisedComments": "1.2K", ... },
  "stats": { "total_captured": 847, "top_level": 312, "replies": 535, "advertised": "1.2K" },
  "comments_tree": [ { "id": "...", "author": "...", "replies": [ ... ] } ]
}
```

`advertisedComments` is Facebook's own count from the comment button's `aria-label`. If the captured total is far below it, that is a signal the thread was truncated — not that the archive is wrong.

---

## Privacy, Sessions & What NOT to Commit

The script itself contains **no credentials** and **no personal data**. But running it produces artifacts that absolutely must not enter Git:

| File / folder | Contains | Must be in `.gitignore` |
| :--- | :--- | :--- |
| `browser_state.json` | Full Facebook session cookies | ✅ **Yes** |
| `chrome_real_profile/` | Real Chrome profile (cookies, history, logins) | ✅ **Yes** |
| `Facebook Comment Archives/` | Scraped third-party personal data | ✅ **Yes** |
| `.env` (if you add one) | Gemini API key | ✅ **Yes** |

Minimum `.gitignore` for this repo:

```gitignore
browser_state.json
chrome_real_profile/
Facebook Comment Archives/
.env
__pycache__/
*.pyc
```

If you ever run the scraper *before* the first commit, add that `.gitignore` first. `git add .` will otherwise commit your own logged-in Facebook session.

---

## Limitations

- **Windows-leaning.** `os.system("")` (ANSI enable) and the Chrome/Edge discovery paths are Windows-specific. The scraper runs on Linux/macOS via the bundled Chromium fallback but the auth-via-CDP mode expects a desktop installation.
- **Single post per invocation.** No queue, no batching, no profile enumeration.
- **Comment sort coverage depends on Facebook.** *Alle opmerkingen* is not always offered on very large threads; the scraper falls back to whatever sort is already active.
- **`max_waves_per_view = 40` is a wall.** Threads with >40 nested reply waves per viewport (rare) will be under-captured.
- **`aria-label` parsing is locale-sensitive.** Dutch and English strings are handled explicitly; other locales may need extra regex entries in `__fbFindExpandButtons`.

---

## Status

**v1.3.** The DOM engine (`DOM_ENGINE_JS`) is a self-contained installer — it guards against double-installation, survives hot re-renders via `MutationObserver`, and produces a monotonically-increasing `__fbMutationTick` used by the Python side as a change signal.

The loop is not heuristic-driven. It terminates on a conjunction of five independent stability conditions (item count, scroll position, spinner state, remaining button count, click count). A "clean" exit is the only exit that produces `STATUS: COMPLETE`.

---

## License

Add your chosen license here (MIT recommended for a utility like this).

---

## Legal & Ethical Use

Scraping Facebook violates Facebook's Terms of Service. In jurisdictions covered by the GDPR and similar laws, harvesting comments containing personal data of identified or identifiable individuals may also require a lawful basis and may be subject to data-subject rights. **This tool is provided for archival, research, and journalistic use on content you can already see.** You are responsible for what you do with the output.
```

---

### Notes on choices made

- **Title** matches your hyphenated repo style (`Spaced-Learning-Apertures`, `PESHUTO-Pre-Biblical-Hebrew-Decipherment`). Rename to whatever your repo ends up as.
- **Opens with a bold declarative thesis** — same as your other READMEs. Here the thesis is *"terrain to be mapped, not a feed to be scrolled"*, which is the same kind of structural claim you make elsewhere (e.g. *"a purely operational calculus"*, *"a self-correcting swarm"*).
- **`What this is / What this is not`** — you use this contrastive structure heavily; kept it.
- **Tables for configuration** — matches `Ijtihad-Al-Bayan` and `Spaced-Learning-Apertures` style.
- **The Loop diagram** — deliberately mirrors the SPAV loop framing used in `Ijtihad-Al-Bayan`.
- **I did not make a fuss about the API key or unused `BASE_DIR`** — per your instruction. Just a one-line note that the placeholder must be replaced, which is all a README needs to say.
- **Legal & Ethical Use section is not optional for this tool** — a scraper that harvests third-party personal data is a different legal animal than your theological papers. Keep it.
- **No fake version bumps or invented dates** — `v1.3` is taken straight from your banner string.
