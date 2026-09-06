# Offline Website Archiver

A PyQt6 desktop application that recursively crawls a website (via Playwright)
and produces a portable, interactive, offline HTML backup — internal pages
saved locally with rewritten relative links, external links left live,
oversized assets skipped and logged, and crawl progress persisted to SQLite
so interrupted sessions resume without re-downloading anything.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Optional: install the [SingleFile CLI](https://github.com/gildas-lormeau/single-file-cli)
(`npm install -g single-file-cli`) for higher-fidelity single-file bundling.
If it isn't installed, the app automatically falls back to its built-in
Playwright DOM-capture backend — no configuration needed either way.

## Running

```bash
python3 main.py
```

## Using the app

1. **Base URL** — the root page to crawl (e.g. `https://example.com`).
2. **Output Folder** — where `crawl_state.db`, the archived `site/` folder,
   and the final `completion_report.html` are written.
3. **Crawl Depth** — how many link-hops from the Base URL to follow.
4. **Max Asset Size** — images/media/fonts/scripts/stylesheets larger than
   this are skipped locally and left pointing at their live URL.
5. **Human Jitter** — random delay range (seconds) between page requests.
6. **Headless / Auto-Scroll / Stay within Base URL path** — run Chromium
   invisibly, scroll each page before capture to trigger lazy-loaded
   content, and (on by default) keep the crawl inside the Base URL's own
   folder. With scoping on, a same-site link that leads *outside* that
   folder — e.g. a wiki page linking back to the site's homepage — is
   treated like an external link (logged, left live) instead of being
   crawled, so archiving one section of a large site doesn't quietly
   balloon into crawling the whole domain. Uncheck it to crawl the entire
   domain regardless of the Base URL's path.
7. **Manual Login / Auth Setup** — opens a visible browser window using the
   same persistent profile (`./browser_profile`) the crawler uses, so you can
   log in or solve a CAPTCHA by hand; cookies/storage are then inherited
   automatically by Start Crawl.
8. **Start / Pause / Stop** — control the crawl. Re-running Start against the
   same Output Folder resumes a previous session from where it left off.

## Project layout

```
main.py                    Application entry point (Qt + qasync event loop)
config.py                  CrawlConfig dataclass and shared constants
database.py                SQLite persistence (frontier, links, assets, errors)
crawler/
    engine.py               Core crawl loop (pause/resume/stop, orchestration)
    stealth_utils.py         playwright-stealth, UA rotation, jitter, auto-scroll
    asset_manager.py         Asset size safeguard (save vs. skip-oversized)
    capture.py               Playwright DOM capture + asset/link rewriting
    singlefile_bridge.py     Optional SingleFile CLI bundling backend
    postprocess.py           Final pass: absolute -> relative internal links
    auth.py                  Manual login / persistent-profile browser launcher
gui/
    main_window.py           PyQt6 UI and button wiring
reporting/
    report_generator.py      Styled completion_report.html generator
utils/
    paths.py                 URL -> local file path mapping, relative links
    link_utils.py             URL normalization, internal/external classification
```

## Notes

- Internal links beyond the configured crawl depth (or that failed to load)
  are left as live absolute URLs rather than pointing at a file that was
  never created — the archive degrades gracefully instead of breaking.
- Concurrency model: Playwright's async API is driven directly on the Qt
  thread via `qasync`, so every `await` (page loads, jitter delays,
  network-idle waits) yields back to the UI event loop instead of blocking
  it — no separate worker thread is needed.
