# WinZim

Turn any public website into a `.zim` file (readable offline with [Kiwix](https://kiwix.org)).
Paste a URL, check the green light, click **Convert**.

- **Zimit engine (default)**: runs the official openZIM Zimit crawler through Docker Desktop. Real browser,
  handles JavaScript-heavy sites.
- **Built-in engine**: simple pure-Python crawler, no Docker needed. Best for ordinary static sites.

`WinZim.exe` has Python bundled inside, so the **only** thing you may need to install is Docker Desktop
(and only for the Zimit engine).

## Quick start
1. Get the app (see "Getting WinZim" below) and run it. If you'll use Zimit, start Docker Desktop first.
2. Paste the website URL. A status icon appears next to **Convert**:
   **green check** = go ahead, **amber !** = will probably be incomplete, **red X** = likely to fail.
3. Pick **What to save** (whole site, just this section, just this page, quick test...) and a
   **Detail level** (Full, Lite, No pictures, Minimum) to control how big the file gets.
4. Choose the **Save to folder** (before it starts) and optionally a file name.
5. Click **Convert**. Pages and a log appear on screen as it works; the saved file's location is shown when done.
6. Use **Open in Kiwix** or **Preview in browser** to check the result.

## Features
- **Site check (green / amber / red).** Runs automatically when you enter a URL or change engines. Looks for
  login walls, bot-protection / captcha pages (e.g. Cloudflare), HTTP errors, pages that are empty without
  JavaScript (a problem only for the Built-in engine) and robots.txt restrictions. Click the icon for the
  reasons. Red makes Convert ask "convert anyway?". It checks the first page only, so green means
  "no obvious blockers", not a guarantee.
- **Size estimate.** If the site has a sitemap, the app shows the page count and a rough size range for the
  detail level you picked (for example "about 340 pages, roughly 100 MB to 700 MB" at Full, much less at
  Minimum), and warns if your drive may not have room. The icon's details list all four levels.
- **Detail level** (like Kiwix's "maxi / nopic / mini" files):

  | Level | What is saved | Typical size per page |
  |---|---|---|
  | **Full** | everything | 0.3 - 2 MB |
  | **Lite** | pictures shrunk to about 800 px and recompressed, video and audio left out (Built-in engine) | 0.15 - 0.8 MB |
  | **No pictures** | no images, video or audio | 0.05 - 0.15 MB |
  | **Minimum** | also no fonts, no scripts, no single file over 5 MB | 0.03 - 0.1 MB |

  The sizes are rough guesses from typical sites, not promises. **Advanced** has a checkbox for each thing
  that can be left out (pictures, video and audio, fonts, PDFs and documents, shrink pictures, remove
  scripts), plus **skip files over X MB** and **depth** (how many clicks from the start page). Changing a
  box by hand switches the dropdown to *Custom*. With the **Zimit** engine, pictures/media/fonts/documents
  and depth are passed to Zimit as block rules; shrinking pictures, removing scripts and the per-file size
  cap only work with the **Built-in** engine (the app tells you, and offers to switch for Lite).
- **Presets.** Whole site, this section only, just this page, quick test (10 pages), or no limits.
  **Whole site sets its own limits:** if the site has a sitemap it allows about 20% more pages than the
  sitemap lists and no size cap; otherwise it uses 20,000 pages / 20 GB. Open **Advanced** for the exact
  numbers: **workers** (1-16 parallel downloads) and **limits** on pages, size (MB) and time (minutes).
  0 means no limit.
- **Limit warning.** If a limit stopped the crawl, the finished message says so ("Stopped at the page / size /
  time limit; the site has more than was saved.") so a cut-off archive never looks complete.
- **Dark theme by default**, with a Dark / Light switch in the top-right corner (remembered).
- **Remembers your choices** (folder, engine, workers, limits, detail level, theme) between runs.
- **Preview.** *Open in Kiwix* (if installed) or *Preview in browser*, which serves the ZIM on your own
  computer only (127.0.0.1) so you can click around it right away.
- **Cancel any time.** Stops cleanly and removes temporary files.

## Getting WinZim
**Easiest (no Python needed):** on GitHub open the **Actions** tab, run **Build WinZim** (or open the latest
run) and download:
- `WinZim-installer` -> `WinZim-Setup.exe` (installs with a Start Menu shortcut), or
- `WinZim-exe` -> `WinZim.exe` (single portable file).

**Or build it yourself** on a PC with Python 3.9+ (keep "tcl/tk and IDLE" ticked in the installer):
double-click `build.bat` -> `dist\WinZim.exe`.

Windows may show a "SmartScreen" warning for programs that aren't code-signed. Choose *More info -> Run anyway*
for your own build; signing the exe removes the warning.

## Notes and limitations
- The first Zimit run downloads the Zimit image (about 1 GB). If Docker asks for permission to share your
  drive or folder, allow it.
- **Built-in engine:** no browser, so content that appears only after JavaScript runs may be missing.
  Use Zimit for those sites.
- **Login-protected sites are not supported** by either engine, and sites that block automated visitors won't
  capture fully. Only archive sites you're allowed to copy, and keep workers modest.
- **Lite** needs the Pillow module, which is bundled in the GitHub build. If it is missing, pictures are
  saved at full size and the app says so.
- The Zimit options for skipping file types and depth use Browsertrix settings; if your Zimit version rejects
  them, WinZim retries without them and warns you that nothing was filtered.
- **Preview in browser** reads Zimit's ZIM files only if the optional `zstandard` module was bundled at build
  time (the GitHub build does this). Otherwise use **Open in Kiwix**.

## Project layout
| File | Purpose |
|---|---|
| `app.py` | The window |
| `zimit_engine.py` | Runs Zimit via Docker, parses its progress |
| `crawler.py`, `zimwriter.py` | Built-in crawler (detail levels, depth, picture shrinking), link rewriting and ZIM writer |
| `sitecheck.py` | Green/amber/red pre-flight check and sitemap estimate |
| `zimreader.py`, `preview.py` | ZIM reader, browser preview, Kiwix launcher |
| `settings.py` | Remembered settings |
| `installer.iss`, `build.bat`, `.github/workflows/build.yml` | Packaging |

## Tests
Run from the repo root (they also run in the GitHub build):
- `python tests/test_local.py`: local site -> Built-in engine -> ZIM, validated by an independent reader (`tests/verify_zim.py`).
- `python tests/test_zimit_engine.py`: Zimit command line, progress parsing, output handling, cancel and errors, using a *fake* `docker` (does not run real Zimit).
- `python tests/test_sitecheck.py`: login, 401, Cloudflare-style block, JavaScript shell, captcha, robots.txt cases.
- `python tests/test_features.py`: preview server, sitemap estimate, settings.
- `python tests/test_detail.py`: detail levels, skipped files, script removal, depth, limit tracking.
- `python tests/test_app_logic.py`: indicator, convert gating, presets, detail levels, theme, automatic limits and settings, using a stub tkinter (draws no real window).
