# WinZim

Type a website address, click **Convert**, get a `.zim` file (open it with Kiwix).
One self-contained Windows program: **no Docker, no Python, no installs** needed to run it.

## Using it
1. Run `WinZim.exe`.
2. Paste the website URL.
3. Choose the **Save to folder** (before it starts) and optionally a file name.
4. Optional: set **Workers** (parallel downloads) and **Limits** (pages, size in MB, time in minutes; 0 = no limit).
5. Click **Convert**. Pages appear in the list as they are captured; the finished file's location is shown when done.

## Getting the exe (no Python needed)
Push to this repo (or run the workflow manually) and GitHub Actions builds it:
**Actions tab -> Build WinZim.exe -> latest run -> Artifacts -> WinZim-windows**.

Or build it yourself on a PC with Python 3.9+: double-click `build.bat` -> `dist\WinZim.exe`.

## How it works
Pure Python (standard library only): a multithreaded crawler downloads pages plus their CSS,
images, scripts and fonts, rewrites links so the copy works offline, and a built-in writer
produces the ZIM file. PyInstaller packs Python and everything into the single exe.

## Limitations
- No browser engine: pages that build their content with JavaScript after loading (many modern
  web apps) are captured as the server sends them, so they may look incomplete. The openZIM
  "Zimit" tool (which needs Docker) handles those better.
- Sites that require a login, or that block automated downloads, won't capture fully.
- Please only archive sites you're allowed to copy, and keep Workers modest on small sites.

## Tests
`python tests/test_local.py` serves a small local site, converts it, and validates the ZIM with an
independent reader (`tests/verify_zim.py`).
