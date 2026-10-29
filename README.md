# WinZim

Type a website address, click **Convert**, get a `.zim` file (open it with Kiwix).

Two engines, pick in the app:

| Engine | Needs | Best for |
|---|---|---|
| **Zimit** (default) | **Docker Desktop** | Works like the official openZIM Zimit: real browser crawl, handles JavaScript-heavy sites |
| **Built-in** | nothing | Simple/static sites, no Docker |

`WinZim.exe` itself has Python bundled inside, so the only thing you ever install for the Zimit engine is Docker Desktop.

## Using it
1. Run `WinZim.exe` (start Docker Desktop first if using Zimit; the app shows its status).
2. Paste the website URL.
3. Choose the **Save to folder** (before it starts) and optionally a file name.
4. Set **Workers** and **Limits** (pages, size in MB, time in minutes; 0 = no limit).
5. Click **Convert**. Progress and pages show on screen (Pages and Log tabs); the saved file's location is shown when done.

The first Zimit run downloads the Zimit image (about 1 GB). Docker Desktop can mount your chosen folder; if
Docker asks for file-sharing permission for the drive, allow it.

## Site check (green check / red X)
Next to **Convert** is an indicator that checks the site automatically when you enter a URL (and when you switch
engines): **green check** = go ahead, **amber !** = will probably be incomplete, **red X** = likely to fail.
It looks for logins, bot-protection / captcha pages (e.g. Cloudflare), HTTP errors, pages that are empty without
JavaScript (only a problem for the Built-in engine), and robots.txt restrictions. Click the icon for the reasons.
If it's red, Convert asks "convert anyway?". It checks only the first page, so green means "no obvious blockers",
not a guarantee.

## Getting the exe (no Python needed)
Push to this repo (or run the workflow manually): **Actions tab -> Build WinZim.exe -> latest run ->
Artifacts -> WinZim-windows**. Or on a PC with Python 3.9+, double-click `build.bat` -> `dist\WinZim.exe`.

## Limitations
- Built-in engine: no browser, so pages built by JavaScript after load may be incomplete. Use Zimit for those.
- Logins / sites that block crawlers won't capture fully with either engine.
- Archive only sites you're allowed to copy.

## Tests
- `python tests/test_local.py`: serves a local site, converts with the built-in engine, validates the ZIM
  with an independent reader (`tests/verify_zim.py`).
- `python tests/test_sitecheck.py`: the site check against local servers (login, 401, Cloudflare-style block, JS shell, robots.txt, ...).
- `python tests/test_app_logic.py`: window logic (indicator, convert gating) with a stub tkinter; doesn't draw a real window.
- `python tests/test_zimit_engine.py`: checks the Zimit command line, progress parsing, output handling,
  cancel and error paths using a fake `docker` (does not run real Zimit).
