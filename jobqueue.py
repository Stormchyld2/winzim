"""The job queue: sites and books to fetch one after another, remembered between runs.

Job kinds
  site         website -> ZIM            params: url, out, engine, opts{Options fields}
  books_site   books from a library site params: url, folder, formats[], pref, max_pages
  books_find   look up one book          params: query, folder, pref
  books_zim    folder of books -> ZIM    params: folder, out, name, max_file_mb
  extract      books out of a ZIM        params: zim, folder
"""
import json
import os
import threading
import time
import uuid

import prefs
import settings as appsettings

STATUSES = ("queued", "running", "done", "failed", "cancelled", "interrupted")
FINISHED = ("done", "failed", "cancelled")


def queue_path():
    return os.path.join(os.path.dirname(appsettings.settings_path()), "queue.json")


def describe(job):
    p = job["params"]
    k = job["kind"]
    if k == "site":
        return "Site to ZIM: " + p["url"]
    if k == "books_site":
        return "Books from: " + p["url"]
    if k == "books_find":
        return "Find a book: " + p["query"]
    if k == "books_zim":
        return "Books to ZIM: " + p["folder"]
    if k == "extract":
        return "Books out of: " + os.path.basename(p["zim"])
    return k


def _pref_order(pref):
    from books import FORMAT_ORDER
    first = (pref or "EPUB").split()[0].upper()
    return lambda f: (f != first, FORMAT_ORDER.index(f) if f in FORMAT_ORDER else 99)


def run_job(job, emit, cancel):
    """Do one job to the end. Returns a short result text; raises RuntimeError with a readable message on failure."""
    import books
    p, k = job["params"], job["kind"]
    if k == "site":
        from crawler import Options, convert
        from zimit_engine import run_zimit
        opts = Options(**{f: v for f, v in p["opts"].items() if f in Options.__dataclass_fields__})
        if p["engine"] == "zimit":
            r = run_zimit(p["url"], p["out"], opts, emit, cancel)
            return f"{r['pages']} pages, {books.human_size(r['size'])}"
        size, c = convert(p["url"], p["out"], opts, on_event=emit, cancel=cancel)
        return f"{c.pages} pages, {books.human_size(size)}" + (f", {c.failed} failed" if c.failed else "")
    if k in ("books_site", "books_find"):
        os.makedirs(p["folder"], exist_ok=True)
        key = _pref_order(p.get("pref"))
        items = []
        if k == "books_site":
            found = {}
            books.find_on_site(p["url"], p.get("formats") or books.DEFAULT_FORMATS, p.get("max_pages", 300), 4,
                               on_event=lambda e: found.__setitem__(e["book"]["id"], e["book"]) if e.get("type") == "book" else None,
                               cancel=cancel)
            if cancel.is_set():
                raise RuntimeError("Cancelled")
            wanted = set(p.get("formats") or books.DEFAULT_FORMATS)
            for b in found.values():
                oks = [f for f in b["formats"] if f in wanted and b["formats"][f]["ok"]]
                if oks:
                    f = sorted(oks, key=key)[0]
                    d = b["formats"][f]
                    items.append({"title": b["title"], "author": b.get("author") or "", "year": b.get("year") or "", "fmt": f,
                                  "url": d["url"], "size": d.get("size"), "source": b.get("source", ""), "public_domain": ""})
            if not items:
                raise RuntimeError(f"Found {len(found)} books but none could be downloaded.")
        else:
            cards = books.lookup(p["query"], (p.get("pref") or "EPUB").split()[0], on_event=lambda e: None, cancel=cancel)
            card = next((c for c in cards if c.get("best")), None)
            if not card:
                raise RuntimeError("No free copy was found for that book.")
            b = card["best"]
            items = [{"title": card["title"], "author": card["author"], "year": card["year"], "fmt": b["fmt"], "url": b["url"],
                      "size": b["size"], "source": b["source"], "public_domain": "yes" if b["public_domain"] else ""}]
        done = {"n": 0}

        def on_state(i, st, info):
            if st in ("done", "failed", "skipped"):
                done["n"] += 1
                emit({"type": "phase", "text": f"Downloaded {done['n']} of {len(items)}"})
        paths, errors = books.download_many(items, p["folder"], cancel, prefs.get("downloads_at_once"), prefs.get("retries"),
                                            prefs.get("skip_over_mb"), prefs.get("log_manifest"), on_state)
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        if not paths:
            raise RuntimeError(errors[0][1] if errors else "Nothing was downloaded.")
        return f"{len(paths)} books saved" + (f", {len(errors)} failed" if errors else "")
    if k == "books_zim":
        import bookzim
        r = bookzim.build_books_zim(p["folder"], p["out"], p.get("name") or "Recon Library", emit, cancel,
                                    max_file_mb=p.get("max_file_mb", 2000))
        return f"{r['books']} books, {books.human_size(r['size'])}"
    if k == "extract":
        import zimlibrary
        cat = zimlibrary.load_catalog(p["zim"])
        jl = zimlibrary.make_jobs(cat, [(i, None) for i in range(len(cat["books"]))])
        res = zimlibrary.extract(p["zim"], jl, p["folder"], prefs.get("downloads_at_once"), cancel,
                                 manifest=prefs.get("log_manifest"))
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        ok = sum(1 for s, _ in res if s == "done")
        if not ok:
            raise RuntimeError(next((i for s, i in res if s == "failed"), "Nothing was taken out."))
        return f"{ok} files taken out"
    raise RuntimeError(f"Unknown job type {k}")


class JobQueue:
    def __init__(self, path=None, runner=run_job, on_change=None):
        self.path = path or queue_path()
        self.runner = runner
        self.on_change = on_change or (lambda: None)
        self.jobs = []
        self.lock = threading.RLock()
        self.thread = None
        self.paused = True
        self.cancel = threading.Event()
        self.current = None
        self.load()

    # ---- storage
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.jobs = [j for j in data.get("jobs", []) if isinstance(j, dict) and j.get("kind") and j.get("params")]
        except (OSError, ValueError):
            self.jobs = []
        for j in self.jobs:
            if j.get("status") == "running":
                j["status"], j["message"] = "interrupted", "The program closed while this was running. Retry to start it again."

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"jobs": self.jobs}, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _changed(self):
        self.save()
        self.on_change()

    # ---- editing the list
    def add(self, kind, params, title=None):
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "params": params, "status": "queued", "message": "",
               "added": time.strftime("%Y-%m-%d %H:%M")}
        job["title"] = title or describe(job)
        with self.lock:
            self.jobs.append(job)
        self._changed()
        return job

    def get(self, jid):
        return next((j for j in self.jobs if j["id"] == jid), None)

    def remove(self, jid):
        with self.lock:
            j = self.get(jid)
            if not j or j["status"] == "running":
                return False
            self.jobs.remove(j)
        self._changed()
        return True

    def move(self, jid, delta):
        with self.lock:
            j = self.get(jid)
            if not j or j["status"] == "running":
                return
            i = self.jobs.index(j)
            k = max(0, min(len(self.jobs) - 1, i + delta))
            if self.jobs[k]["status"] == "running":
                return
            self.jobs.insert(k, self.jobs.pop(i))
        self._changed()

    def retry(self, jid):
        with self.lock:
            j = self.get(jid)
            if not j or j["status"] in ("queued", "running"):
                return
            j["status"], j["message"] = "queued", ""
        self._changed()

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j["status"] not in FINISHED]
        self._changed()

    def failures(self, limit=10):
        return [f"{j['title']}: {j['message']}" for j in self.jobs if j["status"] == "failed"][-limit:]

    # ---- running
    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self):
        """Run the waiting jobs, one after another, until none are left."""
        with self.lock:
            self.paused = False
            if self.running:
                return
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def pause(self):
        """Finish the job that is running, then stop."""
        self.paused = True

    def cancel_current(self):
        self.cancel.set()

    def _loop(self):
        while not self.paused:
            with self.lock:
                job = next((j for j in self.jobs if j["status"] == "queued"), None)
                if not job:
                    break
                job["status"], job["message"] = "running", "Starting..."
                self.current = job
                self.cancel = threading.Event()
            self._changed()
            last = [0.0]

            def emit(e, job=job):
                t = e.get("text") if e.get("type") in ("phase", "log") else None
                if e.get("type") == "fetch" and e.get("ok"):
                    t = f"Fetched {e.get('url', '')[:60]}"
                if t and time.time() - last[0] > 0.5:
                    last[0] = time.time()
                    job["message"] = t
                    self.on_change()
            try:
                result = self.runner(job, emit, self.cancel)
                job["status"], job["message"] = "done", result or "Done"
            except Exception as e:
                if self.cancel.is_set() or str(e) == "Cancelled":
                    job["status"], job["message"] = "cancelled", "Cancelled"
                else:
                    job["status"], job["message"] = "failed", str(e) or type(e).__name__
            self.current = None
            self._changed()
        self._changed()
