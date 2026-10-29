"""Drive report: look through a folder (or a whole drive) of ZIMs and books and say what is there and what is wrong.

Checks: sizes and counts, ZIM files that are damaged (checksum), book files that are not what their name says or look cut short,
books listed in library_manifest.csv that are missing (and books that are not listed), duplicates, and free space.
Writes drive_report.html and drive_report.csv into the folder.
"""
import csv
import datetime
import hashlib
import html
import json
import os
import shutil
import threading
import zipfile

import books
from appinfo import NAME, VERSION
from books import BOOK_EXTS, MANIFEST, human_size, main_title_key, title_from_filename

REPORT_HTML, REPORT_CSV = "drive_report.html", "drive_report.csv"


def _zim_info(path, cancel, on_progress, verify):
    """-> dict(ok, note, title, date, entries, books, size)"""
    from zimreader import ZimError, ZimReader
    info = {"ok": True, "note": "", "title": "", "date": "", "entries": 0, "books": None}
    try:
        z = ZimReader(path)
    except (ZimError, OSError, Exception) as e:
        return dict(info, ok=False, note=f"Not a readable ZIM file ({e})")
    try:
        info["entries"] = z.entry_count
        info["title"] = z.metadata("Title") or ""
        info["date"] = z.metadata("Date") or ""
        try:
            _, e = z.resolve("C", "catalog.json")
            if e is not None:
                info["books"] = len(json.loads(z.blob(e).decode("utf-8")).get("books", []))
        except Exception:
            pass
        if verify:
            h, left, done = hashlib.md5(), z.checksum_pos, 0
            with open(path, "rb") as f:
                while left:
                    if cancel.is_set():
                        raise RuntimeError("Cancelled")
                    chunk = f.read(min(4 << 20, left))
                    if not chunk:
                        return dict(info, ok=False, note="File ends early: damaged or still copying")
                    h.update(chunk)
                    left -= len(chunk)
                    done += len(chunk)
                    on_progress(done, z.checksum_pos)
                if h.digest() != f.read(16):
                    return dict(info, ok=False, note="Checksum does not match: the file is damaged")
            info["note"] = "Checksum OK"
    finally:
        z.close()
    return info


def _book_problem(path, fmt):
    """Return a short problem text, or ''."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return "Empty file"
        with open(path, "rb") as f:
            head = f.read(8192)
            tail = b""
            if fmt == "PDF" and size > 1024:
                f.seek(max(0, size - 2048))
                tail = f.read()
    except OSError as e:
        return f"Cannot read ({e.__class__.__name__})"
    if not books.looks_like(fmt, head, ""):
        return f"Not a real {fmt} file (maybe a web page or damaged)"
    if fmt == "PDF" and tail and b"%%EOF" not in tail:
        return "PDF looks cut short (no end marker)"
    if fmt in ("EPUB", "CBZ") and not zipfile.is_zipfile(path):
        return f"{fmt} looks cut short or damaged"
    return ""


def scan(folder, cancel=None, on_event=None, verify_zims=True, failures=None):
    cancel = cancel or threading.Event()
    emit = on_event or (lambda e: None)
    rep = {"folder": folder, "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "zims": [], "books": [],
           "other_size": 0, "issues": [], "manifest": {"rows": 0, "missing": [], "unlisted": []}, "duplicates": [],
           "problems": list(failures or []), "disk": None}
    files = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for fn in sorted(fns):
            if fn.startswith(".") or fn in (REPORT_HTML, REPORT_CSV) or fn.endswith((".part", ".tmp")):
                continue
            files.append(os.path.join(dp, fn))
    emit({"type": "phase", "text": f"Looking at {len(files)} files..."})
    manifest = {}
    mpath = os.path.join(folder, MANIFEST)
    if os.path.exists(mpath):
        try:
            with open(mpath, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    manifest[os.path.basename((r.get("file") or "").replace("\\", "/")).lower()] = r
        except (OSError, csv.Error):
            rep["issues"].append(("warn", f"{MANIFEST} could not be read."))
    rep["manifest"]["rows"] = len(manifest)
    seen_names = set()
    for i, p in enumerate(files, 1):
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        rel = os.path.relpath(p, folder)
        ext = p.rsplit(".", 1)[-1].lower() if "." in os.path.basename(p) else ""
        size = os.path.getsize(p)
        if ext == "zim":
            emit({"type": "phase", "text": f"Checking {os.path.basename(p)} ({human_size(size)})..."})
            info = _zim_info(p, cancel, lambda d, t, n=os.path.basename(p): emit(
                {"type": "phase", "text": f"Checking {n}: {int(d * 100 / t)}%"}), verify_zims)
            rep["zims"].append(dict(info, path=rel, size=size))
            if not info["ok"]:
                rep["issues"].append(("bad", f"{rel}: {info['note']}"))
        elif ext in BOOK_EXTS and BOOK_EXTS[ext]:
            fmt = BOOK_EXTS[ext]
            prob = _book_problem(p, fmt)
            cat = "Library" if os.sep not in rel else rel.split(os.sep)[0]
            row = manifest.get(os.path.basename(p).lower(), {})
            title = row.get("title") or title_from_filename(os.path.basename(p))
            rep["books"].append({"path": rel, "title": title, "format": fmt, "size": size, "category": cat,
                                 "problem": prob, "listed": bool(row)})
            seen_names.add(os.path.basename(p).lower())
            if prob:
                rep["issues"].append(("bad", f"{rel}: {prob}"))
        else:
            rep["other_size"] += size
        if i % 25 == 0:
            emit({"type": "phase", "text": f"Looked at {i} of {len(files)} files..."})
    for name, r in manifest.items():
        if name not in seen_names:
            rep["manifest"]["missing"].append({"title": r.get("title", name), "file": r.get("file", name), "format": r.get("format", "")})
    rep["manifest"]["unlisted"] = [b["path"] for b in rep["books"] if not b["listed"]] if manifest else []
    if rep["manifest"]["missing"]:
        rep["issues"].append(("warn", f"{len(rep['manifest']['missing'])} books in {MANIFEST} are not in the folder any more."))
    groups = {}
    for b in rep["books"]:
        groups.setdefault((b["category"], b["format"], main_title_key(b["title"]) or b["title"].lower()), []).append(b)
    for (cat, fmt, _), g in groups.items():
        if len(g) > 1:
            rep["duplicates"].append({"title": g[0]["title"], "format": fmt, "files": [b["path"] for b in g],
                                      "wasted": sum(b["size"] for b in g[1:])})
    if rep["duplicates"]:
        rep["issues"].append(("warn", f"{len(rep['duplicates'])} books appear more than once ("
                              f"{human_size(sum(d['wasted'] for d in rep['duplicates']))} could be freed)."))
    try:
        du = shutil.disk_usage(folder)
        rep["disk"] = {"total": du.total, "free": du.free}
    except OSError:
        pass
    if not rep["zims"] and not rep["books"]:
        rep["issues"].append(("warn", "No ZIM files or books were found in that folder."))
    return rep


def summary(rep):
    n_books = len(rep["books"])
    by_fmt = {}
    for b in rep["books"]:
        c = by_fmt.setdefault(b["format"], [0, 0])
        c[0] += 1
        c[1] += b["size"]
    by_cat = {}
    for b in rep["books"]:
        c = by_cat.setdefault(b["category"], [0, 0])
        c[0] += 1
        c[1] += b["size"]
    return {"zims": len(rep["zims"]), "zim_size": sum(z["size"] for z in rep["zims"]), "books": n_books,
            "book_size": sum(b["size"] for b in rep["books"]), "by_format": by_fmt, "by_category": by_cat,
            "bad": sum(1 for lv, _ in rep["issues"] if lv == "bad"), "warn": sum(1 for lv, _ in rep["issues"] if lv == "warn")}


def write_reports(rep, folder):
    s = summary(rep)
    e = html.escape
    rows = lambda items: "".join(items)  # noqa: E731
    issues = "".join(f'<li class="{lv}">{e(t)}</li>' for lv, t in rep["issues"]) or '<li class="ok">Nothing wrong was found.</li>'
    zims = rows(f"<tr><td>{e(z['path'])}</td><td>{e(z['title'])}</td><td>{human_size(z['size'])}</td><td>{z['entries']}</td>"
                f"<td>{'' if z['books'] is None else z['books']}</td><td class=\"{'ok' if z['ok'] else 'bad'}\">"
                f"{'OK' if z['ok'] else e(z['note'])}{(' - ' + e(z['note'])) if z['ok'] and z['note'] else ''}</td></tr>" for z in rep["zims"])
    fm = rows(f"<tr><td>{e(k)}</td><td>{v[0]}</td><td>{human_size(v[1])}</td></tr>" for k, v in sorted(s["by_format"].items()))
    cat = rows(f"<tr><td>{e(k)}</td><td>{v[0]}</td><td>{human_size(v[1])}</td></tr>" for k, v in sorted(s["by_category"].items()))
    miss = rows(f"<tr><td>{e(m['title'])}</td><td>{e(m['format'])}</td><td>{e(os.path.basename(str(m['file']).replace(chr(92), '/')))}</td></tr>" for m in rep["manifest"]["missing"])
    dup = rows(f"<tr><td>{e(d['title'])}</td><td>{e(d['format'])}</td><td>{e(', '.join(d['files']))}</td><td>{human_size(d['wasted'])}</td></tr>" for d in rep["duplicates"])
    disk = ""
    if rep["disk"]:
        disk = (f"<p>Drive: {human_size(rep['disk']['free'])} free of {human_size(rep['disk']['total'])}. "
                f"This folder's ZIMs and books: {human_size(s['zim_size'] + s['book_size'])}.</p>")
    problems = "".join(f"<li>{e(str(p))}</li>" for p in rep["problems"])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Drive report</title><style>
:root{{--bg:#131814;--panel:#1a211c;--text:#e1e8e1;--muted:#8c998f;--amber:#d9a02b;--green:#35c25f;--red:#f06a6a;--b:#2d3d33}}
@media (prefers-color-scheme: light){{:root{{--bg:#f3f3ee;--panel:#fff;--text:#1b1f1b;--muted:#666;--amber:#b87a00;--green:#1a8a38;--red:#c0392b;--b:#d4d4c9}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}main{{max-width:980px;margin:0 auto;padding:22px}}
h1{{margin:0 0 4px}}h2{{color:var(--amber);margin:28px 0 8px;font-size:17px}}.m{{color:var(--muted)}}
table{{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--b)}}th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--b);vertical-align:top}}th{{color:var(--muted);font-weight:600}}
.ok{{color:var(--green)}}.bad{{color:var(--red)}}.warn{{color:var(--amber)}}ul{{padding-left:20px}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:var(--panel);border:1px solid var(--b);border-radius:6px;padding:10px 16px}}.card b{{display:block;font-size:22px}}
</style></head><body><main><h1>Drive report</h1><p class="m">{e(rep['folder'])} &middot; {e(rep['generated'])} &middot; {NAME} {VERSION}</p>
<div class="cards"><div class="card"><b>{s['zims']}</b>ZIM files ({human_size(s['zim_size'])})</div><div class="card"><b>{s['books']}</b>books ({human_size(s['book_size'])})</div>
<div class="card"><b class="{'bad' if s['bad'] else 'ok'}">{s['bad']}</b>problems</div><div class="card"><b class="{'warn' if s['warn'] else 'ok'}">{s['warn']}</b>warnings</div></div>
{disk}<h2>What needs attention</h2><ul>{issues}</ul>
{'<h2>Recent failed jobs</h2><ul>' + problems + '</ul>' if problems else ''}
<h2>ZIM files</h2>{'<table><tr><th>File</th><th>Title</th><th>Size</th><th>Entries</th><th>Books inside</th><th>Check</th></tr>' + zims + '</table>' if zims else '<p class="m">None.</p>'}
<h2>Books by format</h2>{'<table><tr><th>Format</th><th>Books</th><th>Size</th></tr>' + fm + '</table>' if fm else '<p class="m">None.</p>'}
<h2>Books by category</h2>{'<table><tr><th>Category</th><th>Books</th><th>Size</th></tr>' + cat + '</table>' if cat else '<p class="m">None.</p>'}
{'<h2>Listed but missing</h2><table><tr><th>Title</th><th>Format</th><th>File</th></tr>' + miss + '</table>' if miss else ''}
{'<h2>Duplicates</h2><table><tr><th>Title</th><th>Format</th><th>Files</th><th>Wasted</th></tr>' + dup + '</table>' if dup else ''}
</main></body></html>"""
    hp, cp = os.path.join(folder, REPORT_HTML), os.path.join(folder, REPORT_CSV)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(doc)
    with open(cp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "path", "title", "format", "category", "size_bytes", "status", "notes"])
        for z in rep["zims"]:
            w.writerow(["zim", z["path"], z["title"], "ZIM", "", z["size"], "ok" if z["ok"] else "damaged", z["note"]])
        for b in rep["books"]:
            w.writerow(["book", b["path"], b["title"], b["format"], b["category"], b["size"], "problem" if b["problem"] else "ok",
                        b["problem"] or ("" if b["listed"] or not rep["manifest"]["rows"] else "not in " + MANIFEST)])
        for m in rep["manifest"]["missing"]:
            w.writerow(["missing", m["file"], m["title"], m["format"], "", "", "missing", f"listed in {MANIFEST} but not found"])
    return hp, cp
