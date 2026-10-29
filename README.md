# Cindrix Recon

Collect what you'll need **before** you need it: copy whole websites into offline `.zim` files (readable with
[Kiwix](https://kiwix.org)) and find and download books. It is a collection tool for while the internet is up;
what you carry on the drive is the output (ZIM files, books, a reader), not Recon itself.

Two tabs:
- **Sites**: paste a URL, check the green light, click **Convert** to get a ZIM file.
- **Books**: find books on a library site, or look a book up by title/author and get the best place to download it.

`CindrixRecon.exe` has Python bundled inside, so the **only** thing you may need to install is Docker Desktop
(and only for the Zimit engine on the Sites tab).

## Sites tab (website -> ZIM)
- **Zimit engine (default)**: runs the official openZIM Zimit crawler through Docker Desktop. Real browser,
  handles JavaScript-heavy sites.
- **Built-in engine**: simple pure-Python crawler, no Docker needed. Best for ordinary static sites.

1. Paste the website URL. A status icon appears next to **Convert**: **green check** = go ahead, **amber !** =
   will probably be incomplete, **red X** = likely to fail.
2. Pick **What to save** and a **Detail level** (Full, Lite, No pictures, Minimum).
3. Choose the **Save to folder** (before it starts) and optionally a file name.
4. Click **Convert**. Pages and a log appear on screen as it works; the saved file's location is shown when done.
5. Use **Open in Kiwix** or **Preview in browser** to check the result.

### Features
- **Site check (green / amber / red).** Looks for login walls, bot-protection / captcha pages (e.g. Cloudflare),
  HTTP errors, pages that are empty without JavaScript (a problem only for the Built-in engine) and robots.txt
  restrictions. Click the icon for the reasons. Red makes Convert ask "convert anyway?". It checks the first page
  only, so green means "no obvious blockers", not a guarantee.
- **Size estimate.** If the site has a sitemap, the app shows the page count and a rough size range for the detail
  level you picked, and warns if your drive may not have room.
- **Detail level** (like Kiwix's "maxi / nopic / mini" files):

  | Level | What is saved | Typical size per page |
  |---|---|---|
  | **Full** | everything | 0.3 - 2 MB |
  | **Lite** | pictures shrunk to about 800 px and recompressed, video and audio left out (Built-in engine) | 0.15 - 0.8 MB |
  | **No pictures** | no images, video or audio | 0.05 - 0.15 MB |
  | **Minimum** | also no fonts, no scripts, no single file over 5 MB | 0.03 - 0.1 MB |

  The sizes are rough guesses from typical sites, not promises. **Advanced** has a checkbox for each thing that
  can be left out, plus **skip files over X MB** and **depth** (clicks from the start page). Changing a box by hand
  switches the dropdown to *Custom*. With **Zimit**, pictures/media/fonts/documents and depth are passed to Zimit
  as block rules; shrinking pictures, removing scripts and the per-file size cap only work with the **Built-in**
  engine (the app tells you, and offers to switch for Lite).
- **Presets.** Whole site, this section only, just this page, quick test (10 pages), or no limits. **Whole site
  sets its own limits:** with a sitemap it allows about 20% more pages than the sitemap lists and no size cap;
  otherwise 20,000 pages / 20 GB. **Advanced** has **workers** (1-16) and limits on pages, size and time (0 = none).
- **Limit warning.** If a limit stopped the crawl, the finished message says so ("Stopped at the page / size / time
  limit; the site has more than was saved.").
- **Preview.** *Open in Kiwix* (if installed) or *Preview in browser* (served on your own computer only, 127.0.0.1).
- **Cancel any time.** Stops cleanly and removes temporary files.

## Books tab
Two modes (switch at the top):

**Search a site.** Enter a library or document site. Recon looks through its pages (and any OPDS book catalog the
site advertises, which gives clean titles, authors and sizes) for links to book files: PDF, EPUB, MOBI, AZW3, DJVU
and TXT. Books with several formats are grouped into one row.
- The list shows title, formats, pages, size, a **green check / red X** (can this really be downloaded?) and a
  **Download** button. If a book has several formats, **Download ▾** opens a window to choose one.
- Every link is tested first: a link that gives a login page, an error page or "access denied" gets a red X.
- **Download selected** (Ctrl/Shift-click rows), **Download all**, or click a row's Download. Files go to the folder you
  choose. Filter by format, text, or "only show books I can download".
- Page counts: PDFs usually give one; EPUBs have no fixed pages and show "—". It never downloads whole PDFs just
  to count pages (only small ones, up to 2 MB).
- It follows the site's robots.txt and never crawls other websites; it looks through at most the number of pages you set.

**Look up a book.** Type a title and/or author. Recon asks several free and legal catalogs at once (Open Library,
Internet Archive, Project Gutenberg, Google Books' free editions, OAPEN), merges the answers into one card per book,
tests every download link, and shows the **best source** first, with a badge: *Public domain*, *Free to download*
or *Borrow or buy only*. For books still under copyright it shows **Borrow** and **Buy / find** links (lending page,
publisher, WorldCat library finder) that open in your browser; it does not download those.

**Every download is logged** to `library_manifest.csv` in the save folder (date, title, author, year, format, size,
source, public-domain flag, link, file). Cindrix Ops can import it later, or use it as a checklist for the drive.

### What Books will not do
- It does not log in, and does not get around paywalls, accounts or DRM. A paywalled book shows a red X.
- It does not guess hidden file addresses or use pirate/shadow-library sites. Only copy books you have the right to.
- Sites that load their book lists with JavaScript, or that block automated visitors, may show few or no books.
- A red X can occasionally be wrong (some servers refuse the quick test but allow the real download). Treat it as
  "probably can't".
- Catalog connectors were written from the services' published documentation and tested against stand-ins, not against
  the live services. If one stops answering, the others still work and the message says how many answered.

## Getting Cindrix Recon
**No Python needed:** on GitHub open the **Actions** tab, run **Build CindrixRecon** (or open the latest run) and
download the `CindrixRecon-exe` artifact: `CindrixRecon.exe`, a single portable file. (An installer will be added
later.)

**Or build it yourself** on a PC with Python 3.9+ (keep "tcl/tk and IDLE" ticked in the installer): double-click
`build.bat` -> `dist\CindrixRecon.exe`.

Windows may show a "SmartScreen" warning for programs that aren't code-signed. Choose *More info -> Run anyway* for
your own build; signing the exe removes the warning.

The app was renamed from WinZim; your old settings are carried over automatically. The look is dark by default with a
Dark / Light switch (top right, remembered).

## Notes and limitations
- The first Zimit run downloads the Zimit image (about 1 GB). If Docker asks for permission to share your drive or
  folder, allow it.
- **Built-in engine:** no browser, so content that appears only after JavaScript runs may be missing.
- **Login-protected sites are not supported** by either tab. Only archive sites you're allowed to copy, and keep
  workers modest.
- **Lite** needs the Pillow module (bundled in the GitHub build). If missing, pictures are saved at full size and the
  app says so.
- The Zimit options for skipping file types and depth use Browsertrix settings; if your Zimit version rejects them,
  the app retries without them and warns you that nothing was filtered.
- **Preview in browser** reads Zimit's ZIM files only if the optional `zstandard` module was bundled (the GitHub build
  does this). Otherwise use **Open in Kiwix**.

## Project layout
| File | Purpose |
|---|---|
| `app.py` | The window and the Sites tab |
| `bookui.py`, `books.py` | Books tab window; book finding, link testing, catalog lookup, downloading |
| `zimit_engine.py` | Runs Zimit via Docker, parses its progress |
| `crawler.py`, `zimwriter.py` | Built-in crawler (detail levels, depth, picture shrinking), link rewriting, ZIM writer |
| `sitecheck.py` | Green/amber/red pre-flight check and sitemap estimate |
| `zimreader.py`, `preview.py` | ZIM reader, browser preview, Kiwix launcher |
| `settings.py` | Remembered settings |
| `build.bat`, `.github/workflows/build.yml` | Building the exe (`installer.iss` is saved for later, unused) |

## Contact
Cindrix products: **cindrixops@proton.me** (also shown in the app, bottom bar and **About**).

When Recon talks to the book catalogs (Open Library, Internet Archive and the like, which ask API users to identify
themselves) it includes this address in its request. It is **not** sent to ordinary websites you point Recon at, and it
is **not** written into ZIM files.

## Tests
Run from the repo root (they also run in the GitHub build):
- `python tests/test_local.py`: local site -> Built-in engine -> ZIM, validated by an independent reader (`tests/verify_zim.py`).
- `python tests/test_zimit_engine.py`: Zimit command line, progress parsing, output handling, cancel, errors, using a *fake* `docker`.
- `python tests/test_sitecheck.py`: login, 401, Cloudflare-style block, JavaScript shell, captcha, robots.txt cases.
- `python tests/test_features.py`: preview server, sitemap estimate, settings.
- `python tests/test_detail.py`: detail levels, skipped files, script removal, depth, limit tracking.
- `python tests/test_books.py`: site book finder, OPDS, link testing, PDF page counts, downloads, manifest, catalog lookup and ranking, using a fake book site and fake catalogs on localhost.
- `python tests/test_app_logic.py`, `python tests/test_bookui_logic.py`: window logic using a stub tkinter (draws no real window).
