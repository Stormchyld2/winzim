"""Take books back out of a ZIM made by Books -> ZIM (so the library can be rebuilt with more books)."""
import json
import os
import re
import threading

import books
import jobs
from zimreader import ZimReader


class LibraryError(Exception):
    pass


def load_catalog(zim_path):
    """-> {"title": str, "books": [ {title, author, year, category, formats{FMT:{url,size,name}}, ...} ], "listed": bool}
    Reads catalog.json written by Cindrix Recon; for other ZIMs it falls back to the files/ folder."""
    try:
        z = ZimReader(zim_path)
    except Exception as e:
        raise LibraryError(f"That does not look like a ZIM file ({e}).")
    try:
        idx, e = z.resolve("C", "catalog.json")
        if e is not None:
            try:
                data = json.loads(z.blob(e).decode("utf-8"))
                if isinstance(data.get("books"), list):
                    data["listed"] = True
                    return data
            except (ValueError, KeyError):
                pass
        groups = {}
        for e in z.entries("C"):
            m = re.match(r"files/([^/]+)/(.+)\.([A-Za-z0-9]+)$", e["url"])
            if not m:
                continue
            fmt = books.BOOK_EXTS.get(m.group(3).lower())
            if not fmt:
                continue
            loc = z.blob_location(e)
            g = groups.setdefault(m.group(1), {"title": m.group(1).replace("-", " ").title(), "author": "", "year": None,
                                               "category": "Library", "formats": {}})
            g["formats"][fmt] = {"url": e["url"], "size": loc[1] if loc else 0, "name": os.path.basename(e["url"])}
        if not groups:
            raise LibraryError("No books were found inside that ZIM file.")
        return {"title": os.path.basename(zim_path), "books": sorted(groups.values(), key=lambda b: b["title"].lower()),
                "listed": False}
    finally:
        z.close()


def _reserve(folder, name):
    """Pick a file name that is free and claim it, safe when several threads ask at once."""
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        path = os.path.join(folder, name if n == 1 else f"{stem} ({n}){ext}")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return path
        except FileExistsError:
            n += 1


def extract_one(zim_path, url, dest_folder, name, on_progress=None, cancel=None):
    """Copy one file out of the ZIM. Returns the new path. Raises RuntimeError with a readable message."""
    cancel = cancel or threading.Event()
    os.makedirs(dest_folder, exist_ok=True)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ") or "book"
    path = _reserve(dest_folder, name)
    part = path + ".part"
    z = ZimReader(zim_path)
    try:
        idx, e = z.resolve("C", url)
        if e is None:
            raise RuntimeError("That file is not in the ZIM.")
        loc = z.blob_location(e)
        with open(part, "wb") as out:
            if loc:
                off, total = loc
                done = 0
                with open(zim_path, "rb") as src:
                    src.seek(off)
                    while done < total:
                        if cancel.is_set():
                            raise RuntimeError("Cancelled")
                        chunk = src.read(min(1024 * 1024, total - done))
                        if not chunk:
                            raise RuntimeError("The ZIM file ends early (damaged or still copying).")
                        out.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
            else:
                data = z.blob(e)
                out.write(data)
                if on_progress:
                    on_progress(len(data), len(data))
        os.replace(part, path)
        return path
    except BaseException:
        for p in (part, path):
            try:
                if p == part or os.path.getsize(p) == 0:
                    os.remove(p)
            except OSError:
                pass
        raise
    finally:
        z.close()


def make_jobs(catalog, picks, formats=None):
    """picks: list of (book_index, [fmt,...] or None for every wanted format) -> flat job list."""
    out = []
    for bi, fmts in picks:
        b = catalog["books"][bi]
        for f in (fmts or sorted(b["formats"])):
            if f in b["formats"] and (not formats or f in formats):
                d = b["formats"][f]
                out.append({"book": bi, "fmt": f, "url": d["url"], "name": d["name"], "size": d.get("size") or 0,
                            "category": b.get("category") or "Library", "title": b["title"], "author": b.get("author", ""),
                            "year": b.get("year") or "", "source": b.get("source", ""),
                            "public_domain": "yes" if b.get("public_domain") else ""})
    return out


def extract(zim_path, job_list, folder, at_once, cancel=None, on_state=None, on_progress=None, manifest=True):
    """Copy the files out, several at the same time (the rest wait). Subfolders follow the book categories, so the folder
    can be fed straight back into Books -> ZIM. Returns [(state, info)] in job order."""
    def worker(i, j):
        def prog(done, total):
            if on_progress:
                on_progress(i, done, total)
        sub = j["category"] if j["category"] and j["category"] != "Library" else ""
        dest = os.path.join(folder, re.sub(r'[<>:"/\\|?*]', "_", sub)) if sub else folder
        path = extract_one(zim_path, j["url"], dest, j["name"], prog, cancel)
        if manifest:
            books.add_to_manifest(folder, {"title": j["title"], "author": j["author"], "year": j["year"], "format": j["fmt"],
                                           "size_bytes": os.path.getsize(path), "source": j["source"] or "from ZIM",
                                           "public_domain": j["public_domain"], "url": "", "file": path})
        return path
    return jobs.run_jobs(job_list, worker, at_once, cancel, on_state)
