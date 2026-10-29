"""Book Finder logic (no GUI code in here).

Two ways to find books:
  * find_on_site()  - look through one website (its pages and OPDS book catalogs) for downloadable book files.
  * lookup()        - search several free/legal catalogs by title or author and pick the best place to get a copy.

Every download link is tested (is it really a book file, not a login page?) before it gets a green check.
Only public links are used. Nothing here logs in to anything or works around paywalls.
"""
import csv
import datetime
import hashlib
import html as htmllib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import quote, urldefrag, urljoin, urlsplit

import prefs
from appinfo import CONTACT, REPO
from crawler import decode_bytes, normalize, to_ascii

UA = f"CindrixRecon/1.3 (+{REPO})"   # used when looking through any website: no contact address
# The book catalogs (Open Library, Internet Archive, ...) ask API users to say who they are, so only they get the address.
CATALOG_UA = f"CindrixRecon/1.3 (+{REPO}; {CONTACT})"

BOOK_EXTS = {"pdf": "PDF", "epub": "EPUB", "mobi": "MOBI", "azw3": "AZW3", "azw": "AZW3", "djvu": "DJVU",
             "djv": "DJVU", "cbz": "CBZ", "cbr": "CBR", "txt": "TXT", "fb2": "FB2", "rtf": "RTF", "lit": "LIT"}
MIME_FMT = {"application/pdf": "PDF", "application/epub+zip": "EPUB", "application/x-mobipocket-ebook": "MOBI",
            "application/vnd.amazon.ebook": "AZW3", "application/vnd.amazon.mobi8-ebook": "AZW3",
            "image/vnd.djvu": "DJVU", "image/x.djvu": "DJVU", "application/x-cbz": "CBZ", "text/plain": "TXT"}
FORMAT_ORDER = ["EPUB", "PDF", "MOBI", "AZW3", "TXT", "DJVU", "CBZ", "FB2", "RTF", "LIT", "CBR"]
DEFAULT_FORMATS = ("PDF", "EPUB", "MOBI", "AZW3", "DJVU", "TXT")

SKIP_EXT = {"jpg", "jpeg", "png", "gif", "svg", "webp", "ico", "css", "js", "json", "xml", "zip", "gz", "rar", "7z",
            "mp3", "mp4", "avi", "mov", "wav", "woff", "woff2", "ttf", "eot", "exe", "msi", "dmg", "doc", "docx",
            "xls", "xlsx", "ppt", "pptx", "iso"}
GENERIC_TEXT = {"download", "pdf", "epub", "mobi", "azw3", "djvu", "txt", "click here", "here", "read", "get", "link",
                "free download", "download pdf", "download epub", "download now", "read online", "view", "open",
                "full text", "text", "ebook", "e-book", "kindle", "mobi/kindle"}
LOGIN_RE = re.compile(r"(?i)(log-?in|sign-?in|signin|account/login|auth|sso|paywall|subscribe|register)")
NOT_BOOK_TXT = {"robots.txt", "license.txt", "readme.txt", "humans.txt", "ads.txt", "security.txt", "sitemap.txt",
                "changelog.txt", "licence.txt", "copying.txt"}


# --------------------------------------------------------------------------- small helpers
def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def ext_of(url):
    path = urlsplit(url).path.lower()
    last = path.rsplit("/", 1)[-1]
    return last.rsplit(".", 1)[-1] if "." in last else ""


def fmt_of(url, mime=None):
    f = BOOK_EXTS.get(ext_of(url))
    if f:
        return f
    return MIME_FMT.get((mime or "").split(";")[0].strip().lower())


def clean_title(s):
    s = re.sub(r"(?s)<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[\[(]\s*(pdf|epub|mobi|azw3?|djvu|txt|kindle)\s*[\])]", " ", s, flags=re.I)
    s = re.sub(r"[\[(]\s*[\d.,]+\s*[kmg]i?b\s*[\])]", " ", s, flags=re.I)
    s = re.sub(r"\s+[-–|:]\s*(pdf|epub|mobi|download)\s*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -–|:\t\r\n")


def title_from_filename(url):
    name = urllib.parse.unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)
    name = re.sub(r"[_+]+", " ", name)
    name = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", name) if " " not in name else name
    return re.sub(r"\s+", " ", name).strip().title() if name.islower() else re.sub(r"\s+", " ", name).strip()


_KEY_DROP = {"pdf", "epub", "mobi", "azw3", "djvu", "txt", "ebook", "download", "free"}


def norm_key(title):
    t = strip_accents((title or "").lower())
    t = re.sub(r"^(the|a|an)\s+", "", t.strip())
    toks = [x for x in re.findall(r"[a-z0-9]+", t) if x not in _KEY_DROP]
    return " ".join(toks)


def main_title_key(title):
    return norm_key(re.split(r"[:;,(\[]|\s-\s", title or "")[0])


STOP = {"the", "a", "an", "of", "and", "by", "for", "to", "in", "on", "book", "books", "pdf", "epub", "ebook"}


def tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", strip_accents((s or "").lower())) if t not in STOP]


def relevance(query, title, author=""):
    q = set(tokens(query))
    if not q:
        return 1.0
    return len(q & set(tokens(f"{title} {author}"))) / len(q)


def book_id(key):
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:12]


def human_size(n):
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# --------------------------------------------------------------------------- network
def catalog_ua():
    """The catalogs get the contact address unless the user turned that off in Settings."""
    return CATALOG_UA if prefs.get("catalog_contact") else UA


def _open(url, headers=None, timeout=15):
    timeout = max(3, timeout * prefs.get("timeout_s") / 15.0)
    if prefs.get("pause_ms"):
        time.sleep(prefs.get("pause_ms") / 1000.0)
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(to_ascii(url), headers=h), timeout=timeout)


def http_get(url, timeout=15, limit=3_000_000, headers=None):
    """Returns (status, final_url, headers(lower-case dict), body bytes). Never raises for HTTP errors."""
    try:
        with _open(url, headers, timeout) as r:
            body = r.read(limit)
            hd = {k.lower(): v for k, v in r.headers.items()}
            if hd.get("content-encoding", "").lower() == "gzip":
                import gzip
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            return r.status, r.geturl(), hd, body
    except urllib.error.HTTPError as e:
        return e.code, url, {k.lower(): v for k, v in e.headers.items()}, b""
    except Exception as e:  # network trouble, bad certificate, timeout...
        return 0, url, {}, str(e).encode("utf-8", "replace")


def get_json(url, timeout=15):
    st, _, _, body = http_get(url, timeout, 5_000_000, {"Accept": "application/json", "User-Agent": catalog_ua()})
    if st != 200:
        raise RuntimeError(f"HTTP {st}" if st else body.decode("utf-8", "replace")[:100])
    return json.loads(body.decode("utf-8", "replace"))


class Robots:
    """Very small robots.txt reader: only the '*' group, longest-match Allow/Disallow."""

    def __init__(self, text=""):
        self.rules = []
        star = False
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = [x.strip() for x in line.split(":", 1)]
            k = k.lower()
            if k == "user-agent":
                star = v == "*"
            elif star and k in ("allow", "disallow") and v:
                self.rules.append((k == "allow", v))

    def allowed(self, url):
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        best, allow = -1, True
        for is_allow, pat in self.rules:
            pat2 = pat.rstrip("*")
            if path.startswith(pat2) and len(pat2) > best:
                best, allow = len(pat2), is_allow
        return allow


# --------------------------------------------------------------------------- PDF pages, probing
def pdf_page_count(data):
    """Best-effort page count from raw PDF bytes (None if it can't be told)."""
    best = None

    def scan(b):
        nonlocal best
        for rx in (rb"/Type\s*/Pages\b[^<>]{0,3000}?/Count\s+(\d+)", rb"/Count\s+(\d+)[^<>]{0,3000}?/Type\s*/Pages\b"):
            for m in re.finditer(rx, b):
                n = int(m.group(1))
                if best is None or n > best:
                    best = n
    scan(data)
    for m in re.finditer(rb"stream\r?\n", data):
        if b"/ObjStm" not in data[max(0, m.start() - 300):m.start()]:
            continue
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        try:
            scan(zlib.decompressobj().decompress(data[m.end():end]))
        except Exception:
            pass
    return best


def looks_like(fmt, head, ctype=""):
    low = head.lstrip()[:200].lower()
    if low.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml version")) and fmt not in ("FB2",):
        return False
    if "html" in (ctype or "").lower() and fmt != "TXT":
        return False
    if fmt == "PDF":
        return b"%PDF" in head[:1024]
    if fmt in ("EPUB", "CBZ"):
        return head[:2] == b"PK"
    if fmt == "DJVU":
        return b"AT&TFORM" in head[:16]
    if fmt == "TXT":
        return "html" not in (ctype or "").lower() and not low.startswith(b"<")
    return True


def probe(url, fmt=None, timeout=15, want_pages=True, page_cap=2 * 1024 * 1024):
    """Test one download link. Returns dict(ok, size, pages, note)."""
    fmt = fmt or fmt_of(url) or ""
    try:
        with _open(url, {"Range": "bytes=0-8191"}, timeout) as r:
            status = r.status
            hd = {k.lower(): v for k, v in r.headers.items()}
            head = r.read(8192)
            final = r.geturl()
    except urllib.error.HTTPError as e:
        notes = {401: "Needs a login.", 402: "Payment required.", 403: "The site refuses automated downloads.",
                 404: "File not found.", 410: "File is gone.", 429: "Rate-limited; try again later.",
                 451: "Unavailable for legal reasons."}
        if e.code == 416:
            return {"ok": False, "size": None, "pages": None, "note": "The file is empty."}
        return {"ok": False, "size": None, "pages": None, "note": notes.get(e.code, f"Server error (HTTP {e.code}).")}
    except Exception as e:
        return {"ok": False, "size": None, "pages": None, "note": "Couldn't reach the file (" + str(e)[:60] + ")."}
    size = None
    cr = hd.get("content-range", "")
    m = re.search(r"/(\d+)\s*$", cr)
    if m:
        size = int(m.group(1))
    elif status == 200 and hd.get("content-length", "").isdigit():
        size = int(hd["content-length"])
    if urlsplit(final).path != urlsplit(url).path and LOGIN_RE.search(urlsplit(final).path) \
            and not LOGIN_RE.search(urlsplit(url).path):
        return {"ok": False, "size": size, "pages": None, "note": "Redirects to a sign-in page."}
    if not looks_like(fmt, head, hd.get("content-type", "")):
        return {"ok": False, "size": size, "pages": None,
                "note": "The link gives a web page (login or error?), not the book."}
    pages = None
    if fmt == "PDF":
        m = re.search(rb"/Linearized[^>]{0,300}?/N\s+(\d+)", head)
        if m:
            pages = int(m.group(1))
        elif want_pages and size and size <= page_cap:
            st, _, _, body = http_get(url, timeout, page_cap + 1)
            if st == 200:
                pages = pdf_page_count(body)
    return {"ok": True, "size": size, "pages": pages, "note": "OK"}


# --------------------------------------------------------------------------- parsing pages and OPDS
ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_A_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a\s*>")
_HEAD_RE = re.compile(r"(?is)<h[1-6][^>]*>(.*?)</h[1-6]>")


def parse_attrs(s):
    return {m.group(1).lower(): htmllib.unescape(m.group(2) or m.group(3) or m.group(4) or "")
            for m in ATTR_RE.finditer(s)}


def _is_generic(text):
    t = re.sub(r"[^a-z0-9/ ]+", " ", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return (not t or len(t) < 3 or t in GENERIC_TEXT
            or bool(re.match(r"^(download|get|read|view)\b(\s+\w+){0,2}$", t)))


def extract_page(html, base_url):
    """Return (books, pages). books: [{title, fmt, url}], pages: [urls to look through]."""
    books, pages, seen = [], [], set()
    for m in _A_RE.finditer(html):
        a = parse_attrs(m.group(1))
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if urlsplit(url).scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        fmt = BOOK_EXTS.get(ext_of(url)) or MIME_FMT.get(a.get("type", "").split(";")[0].strip().lower())
        if fmt == "TXT" and urllib.parse.unquote(urlsplit(url).path.rsplit("/", 1)[-1]).lower() in NOT_BOOK_TXT:
            continue
        if fmt:
            text = clean_title(m.group(2))
            if _is_generic(text):
                text = clean_title(a.get("title", ""))
            if _is_generic(text):
                heads = _HEAD_RE.findall(html[max(0, m.start() - 600):m.start()])
                text = clean_title(heads[-1]) if heads else ""
            if _is_generic(text):
                text = title_from_filename(url)
            books.append({"title": text, "fmt": fmt, "url": url})
        elif ext_of(url) not in SKIP_EXT:
            pages.append(url)
    return books, pages


_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_opds(text, base_url):
    """Parse an OPDS/Atom feed. Returns (entries, nav_urls, next_url)."""
    try:
        root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))
    except Exception:
        return [], [], None
    nav, nxt, entries = [], None, []

    def link_info(el):
        return (el.get("rel", ""), urljoin(base_url, htmllib.unescape(el.get("href", ""))), el.get("type", ""),
                el.get("length"))
    for el in root.findall(_ATOM + "link"):
        rel, href, typ, _ = link_info(el)
        if rel == "next":
            nxt = href
    for ent in root.findall(_ATOM + "entry"):
        title = (ent.findtext(_ATOM + "title") or "").strip()
        authors = [(a.findtext(_ATOM + "name") or "").strip() for a in ent.findall(_ATOM + "author")]
        year = None
        for tag in ("{http://purl.org/dc/terms/}issued", _ATOM + "published", "{http://purl.org/dc/elements/1.1/}date"):
            v = ent.findtext(tag)
            if v and re.match(r"\d{4}", v):
                year = int(v[:4])
                break
        files = []
        for el in ent.findall(_ATOM + "link"):
            rel, href, typ, length = link_info(el)
            if rel.startswith("http://opds-spec.org/acquisition"):
                f = MIME_FMT.get(typ.split(";")[0].strip().lower()) or BOOK_EXTS.get(ext_of(href))
                if f:
                    files.append({"fmt": f, "url": href, "size": int(length) if length and length.isdigit() else None})
            elif "opds-catalog" in typ or "atom+xml" in typ:
                if rel in ("subsection", "start", "http://opds-spec.org/sort/new", "http://opds-spec.org/sort/popular",
                           "http://opds-spec.org/crawlable", "") or "opds-spec.org" in rel:
                    nav.append(href)
        if files:
            entries.append({"title": clean_title(title), "author": ", ".join(a for a in authors if a), "year": year,
                            "files": files})
    return entries, nav, nxt


# --------------------------------------------------------------------------- site finder
def _host(url):
    h = (urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


class SiteFinder:
    """Look through one website for downloadable books."""

    def __init__(self, url, formats=DEFAULT_FORMATS, max_pages=300, workers=4, delay=0.1,
                 on_event=None, cancel=None, deep_pages=True):
        self.start = normalize(url if "://" in url else "https://" + url)
        self.formats = {f.upper() for f in formats}
        self.max_pages, self.workers, self.delay = max_pages, max(1, workers), delay
        self.on_event = on_event or (lambda e: None)
        self.cancel = cancel or threading.Event()
        self.deep_pages = deep_pages
        self.books = {}          # id -> book dict
        self.scanned = 0
        self.limit_hit = False
        self.robots = Robots("")
        self.lock = threading.Lock()

    # -- adding books
    def _add(self, title, fmt, url, author="", year=None, size=None):
        if fmt not in self.formats or not title or not self.robots.allowed(url):
            return
        key = main_title_key(title) or norm_key(title)
        if not key:
            return
        bid = book_id(key)
        with self.lock:
            b = self.books.get(bid)
            if b is None:
                b = {"id": bid, "title": title, "author": author, "year": year, "pages": None, "formats": {},
                     "source": _host(self.start), "badge": ""}
                self.books[bid] = b
            if author and not b["author"]:
                b["author"] = author
            if year and not b["year"]:
                b["year"] = year
            if fmt not in b["formats"]:
                b["formats"][fmt] = {"url": url, "size": size, "ok": None, "note": "Not checked yet"}
                new = True
            else:
                new = False
        if new:
            self.on_event({"type": "book", "book": b})

    def _stats(self):
        n = len(self.books)
        ok = sum(1 for b in self.books.values() if any(f["ok"] for f in b["formats"].values()))
        bad = sum(1 for b in self.books.values() if b["formats"] and all(f["ok"] is False for f in b["formats"].values()))
        return {"type": "stats", "scanned": self.scanned, "books": n, "ok": ok, "blocked": bad}

    # -- crawling
    def _fetch(self, url):
        time.sleep(self.delay)
        st, final, hd, body = http_get(url, 15, 2_500_000)
        return st, final, hd, body

    def _process(self, url, kind, res, queue_add):
        st, final, hd, body = res
        if st != 200 or not body:
            return
        ctype = hd.get("content-type", "").lower()
        text = decode_bytes(body, (re.search(r"charset=([-\w]+)", ctype) or [None, None])[1])
        head = text[:600].lower()
        is_feed = "opds" in ctype or "atom+xml" in ctype or ("<feed" in head and "http://www.w3.org/2005/atom" in text[:2000].lower())
        if is_feed:
            entries, nav, nxt = parse_opds(text, final)
            for e in entries:
                for f in e["files"]:
                    self._add(e["title"], f["fmt"], f["url"], e["author"], e["year"], f["size"])
            for u in nav + ([nxt] if nxt else []):
                queue_add(u, "opds")
            return
        if "html" not in ctype and "<html" not in head and "<!doctype" not in head:
            return
        books, pages = extract_page(text, final)
        for b in books:
            self._add(b["title"], b["fmt"], b["url"])
        for m in re.finditer(r"(?is)<link\b([^>]*)>", text):
            a = parse_attrs(m.group(1))
            if "opds" in a.get("type", "").lower() or "opds" in a.get("rel", "").lower() \
                    or "atom+xml" in a.get("type", "").lower():
                if a.get("href"):
                    queue_add(urljoin(final, a["href"]), "opds")
        for u in pages:
            if _host(u) == _host(self.start):
                queue_add(u, "html")

    def run(self):
        st, _, _, body = http_get(urljoin(self.start, "/robots.txt"), 8, 100_000)
        robots = self.robots = Robots(body.decode("utf-8", "replace")) if st == 200 else Robots("")
        pending, seen = deque([(self.start, "html")]), {self.start}

        def queue_add(u, kind):
            u = normalize(u)
            if u in seen:
                return
            if len(seen) >= self.max_pages:
                self.limit_hit = True
                return
            if not robots.allowed(u):
                return
            seen.add(u)
            pending.append((u, kind))
        with ThreadPoolExecutor(self.workers) as ex:
            futs = {}
            while (pending or futs) and not self.cancel.is_set():
                while pending and len(futs) < self.workers * 2:
                    u, k = pending.popleft()
                    futs[ex.submit(self._fetch, u)] = (u, k)
                done, _ = wait(list(futs), timeout=0.3, return_when=FIRST_COMPLETED)
                for f in done:
                    u, k = futs.pop(f)
                    self.scanned += 1
                    try:
                        self._process(u, k, f.result(), queue_add)
                    except Exception:
                        pass
                    self.on_event(self._stats())
        self.check_all()
        self.on_event(dict(self._stats(), type="done", mode="site", limit_hit=self.limit_hit,
                           cancelled=self.cancel.is_set()))
        return list(self.books.values())

    # -- testing the download links
    def check_all(self):
        jobs = [(b, f, d) for b in self.books.values() for f, d in b["formats"].items()]
        if not jobs or self.cancel.is_set():
            return

        def one(job):
            b, f, d = job
            if self.cancel.is_set():
                return job, None
            return job, probe(d["url"], f, want_pages=self.deep_pages)
        with ThreadPoolExecutor(self.workers) as ex:
            for job, r in ex.map(one, jobs):
                if r is None:
                    continue
                b, f, d = job
                d["ok"], d["note"] = r["ok"], r["note"]
                if r["size"]:
                    d["size"] = r["size"]
                if r["pages"] and not b["pages"]:
                    b["pages"] = r["pages"]
                if f == "TXT" and r["ok"] and r["size"] and r["size"] < 20_000:
                    d["ok"], d["note"] = False, "Too small to be a book."
                self.on_event({"type": "book", "book": b})
                self.on_event(self._stats())


def find_on_site(url, formats=DEFAULT_FORMATS, max_pages=300, workers=4, on_event=None, cancel=None):
    return SiteFinder(url, formats, max_pages, workers, on_event=on_event, cancel=cancel).run()


# --------------------------------------------------------------------------- downloading
def safe_filename(title, fmt):
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", title or "book")
    base = re.sub(r"\s+", " ", base).strip(" .")[:120] or "book"
    return f"{base}.{fmt.lower()}"


def download_file(url, folder, title, fmt, on_progress=None, cancel=None):
    """Download to `folder`. Returns the final path. Raises RuntimeError with a readable message."""
    cancel = cancel or threading.Event()
    os.makedirs(folder, exist_ok=True)
    name = safe_filename(title, fmt)
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stem} ({n}){ext}")
    part = path + ".part"
    try:
        with _open(url, None, 30) as r:
            hd = {k.lower(): v for k, v in r.headers.items()}
            total = int(hd["content-length"]) if hd.get("content-length", "").isdigit() else None
            done, first = 0, b""
            with open(part, "wb") as f:
                while True:
                    if cancel.is_set():
                        raise RuntimeError("Cancelled")
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    if not first:
                        first = chunk
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        if not first or not looks_like(fmt, first[:8192], hd.get("content-type", "")):
            raise RuntimeError("The server sent a web page instead of the book (login or error?).")
        os.replace(part, path)
        return path
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"The site refused the download (HTTP {e.code}).")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError("Download failed: " + str(e)[:120])
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


MANIFEST = "library_manifest.csv"
MANIFEST_COLS = ["date", "title", "author", "year", "format", "size_bytes", "source", "public_domain", "url", "file"]


_MANIFEST_LOCK = threading.Lock()


def add_to_manifest(folder, row):
    """Append one line to library_manifest.csv in the save folder (created with a header if new)."""
    path = os.path.join(folder, MANIFEST)
    row = dict(row)
    row.setdefault("date", datetime.date.today().isoformat())
    with _MANIFEST_LOCK:   # several downloads can finish at the same moment
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in MANIFEST_COLS})
    return path


def download_many(items, folder, cancel, at_once=6, retries=1, cap_mb=0, log_manifest=True, on_state=None, on_progress=None):
    """Download several books at once (the rest wait in line and start as others finish).
    items: dicts with title, author, year, fmt, url, size, source, public_domain.
    Returns (paths, errors) with errors as [(title, message)]."""
    import jobs

    def one(i, it):
        if cap_mb and it.get("size") and it["size"] > cap_mb * 1024 * 1024:
            raise RuntimeError(f"Bigger than your {cap_mb} MB limit (Settings > Downloads).")
        for attempt in range(retries + 1):
            try:
                path = download_file(it["url"], folder, it["title"], it["fmt"],
                                     (lambda d, t, i=i: on_progress(i, d, t)) if on_progress else None, cancel)
                break
            except RuntimeError as e:
                if str(e) == "Cancelled" or attempt == retries or "web page" in str(e):
                    raise
                time.sleep(1.5 * (attempt + 1))
        if log_manifest:
            add_to_manifest(folder, {"title": it["title"], "author": it.get("author") or "", "year": it.get("year") or "",
                                     "format": it["fmt"], "size_bytes": os.path.getsize(path), "source": it.get("source", ""),
                                     "public_domain": it.get("public_domain", ""), "url": it["url"], "file": path})
        return path
    res = jobs.run_jobs(items, one, at_once, cancel, on_state)
    return ([info for st, info in res if st == "done"],
            [(items[i]["title"], info) for i, (st, info) in enumerate(res) if st == "failed"])


# =========================================================================== title / author lookup
BASES = {"openlibrary": "https://openlibrary.org", "archive": "https://archive.org",
         "gutendex": "https://gutendex.com", "google": "https://www.googleapis.com",
         "oapen": "https://library.oapen.org"}
TRUST = {"Project Gutenberg": 5, "OAPEN": 4, "Internet Archive": 3, "Google Books": 3, "Open Library": 2}


def _first(v, default=""):
    if isinstance(v, list):
        return str(v[0]) if v else default
    return str(v) if v is not None else default


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _year(v):
    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", str(v or ""))
    return int(m.group(1)) if m else None


def _hit(title, author, year, source, kind, formats=None, page_url="", public_domain=False, pages=None):
    return {"title": title, "author": author, "year": year, "source": source, "kind": kind,
            "formats": formats or {}, "page_url": page_url, "public_domain": public_domain, "pages": pages}


def ia_files(identifier):
    """Files of one Internet Archive item -> ({FMT: {url,size}}, borrow_only)."""
    meta = get_json(f"{BASES['archive']}/metadata/{quote(identifier)}")
    md = meta.get("metadata", {}) or {}
    colls = [str(c).lower() for c in _as_list(md.get("collection"))]
    borrow = "inlibrary" in colls or "printdisabled" in colls or bool(md.get("lending___status"))
    out = {}
    for f in meta.get("files", []) or []:
        name = str(f.get("name", ""))
        fmt = BOOK_EXTS.get(ext_of(name))
        if not fmt or fmt in ("TXT",) and not name.lower().endswith("_djvu.txt"):
            continue
        if name.lower().endswith(("_text.pdf", "_bw.pdf")) and "PDF" in out:
            continue
        size = int(f["size"]) if str(f.get("size", "")).isdigit() else None
        if fmt not in out or (f.get("source") == "original" and not out[fmt].get("orig")):
            out[fmt] = {"url": f"{BASES['archive']}/download/{quote(identifier)}/{quote(name)}", "size": size,
                        "orig": f.get("source") == "original"}
    for v in out.values():
        v.pop("orig", None)
    return out, borrow


def ia_search(query):
    q = urllib.parse.urlencode([("q", f"({query}) AND mediatype:texts"), ("fl[]", "identifier"), ("fl[]", "title"),
                                ("fl[]", "creator"), ("fl[]", "year"), ("rows", "8"), ("output", "json"),
                                ("sort[]", "downloads desc")])
    docs = get_json(f"{BASES['archive']}/advancedsearch.php?{q}").get("response", {}).get("docs", [])
    hits = []
    for d in docs[:6]:
        ident = _first(d.get("identifier"))
        if not ident:
            continue
        try:
            files, borrow = ia_files(ident)
        except Exception:
            continue
        if not files:
            continue
        page = f"{BASES['archive']}/details/{quote(ident)}"
        hits.append(_hit(_first(d.get("title")), _first(d.get("creator")), _year(d.get("year")), "Internet Archive",
                         "borrow" if borrow else "free", {} if borrow else files, page))
    return hits


def ol_search(query):
    q = urllib.parse.urlencode({"q": query, "limit": "8",
                                "fields": "key,title,author_name,first_publish_year,ia,ebook_access,has_fulltext"})
    docs = get_json(f"{BASES['openlibrary']}/search.json?{q}").get("docs", [])
    hits = []
    for d in docs:
        access = d.get("ebook_access", "")
        kind = "free" if access == "public" else "borrow" if access in ("borrowable", "printdisabled") else "info"
        formats = {}
        if kind == "free" and d.get("ia"):
            try:
                formats, borrow = ia_files(_first(d["ia"]))
                if borrow:
                    kind, formats = "borrow", {}
            except Exception:
                formats = {}
        page = BASES["openlibrary"] + str(d.get("key", ""))
        hits.append(_hit(_first(d.get("title")), _first(d.get("author_name")), _year(d.get("first_publish_year")),
                         "Open Library", kind if (kind != "free" or formats) else "info", formats, page))
    return hits


_GUT_FMT = [("application/epub+zip", "EPUB"), ("application/pdf", "PDF"), ("application/x-mobipocket-ebook", "MOBI"),
            ("text/plain", "TXT")]


def gut_search(query):
    q = urllib.parse.urlencode({"search": query})
    res = get_json(f"{BASES['gutendex']}/books/?{q}").get("results", [])
    hits = []
    for b in res[:6]:
        formats = {}
        for mime, url in (b.get("formats") or {}).items():
            for prefix, fmt in _GUT_FMT:
                if mime.startswith(prefix) and fmt not in formats and not str(url).endswith((".zip",)):
                    formats[fmt] = {"url": url, "size": None}
        authors = []
        for a in b.get("authors") or []:
            n = str(a.get("name", ""))
            authors.append(" ".join(reversed([p.strip() for p in n.split(",", 1)])) if "," in n else n)
        if formats:
            hits.append(_hit(str(b.get("title", "")), ", ".join(authors), None, "Project Gutenberg", "free", formats,
                             f"https://www.gutenberg.org/ebooks/{b.get('id', '')}", b.get("copyright") is False))
    return hits


def gb_search(query):
    base = BASES["google"] + "/books/v1/volumes?"
    hits = []
    for flt, kind in (("free-ebooks", "free"), ("paid-ebooks", "buy")):
        q = urllib.parse.urlencode({"q": query, "filter": flt, "printType": "books", "maxResults": "6"})
        try:
            items = get_json(base + q).get("items", [])
        except Exception:
            if kind == "free":
                raise
            continue
        for it in items:
            vi, ai, si = it.get("volumeInfo", {}) or {}, it.get("accessInfo", {}) or {}, it.get("saleInfo", {}) or {}
            formats = {}
            if kind == "free":
                for key, fmt in (("epub", "EPUB"), ("pdf", "PDF")):
                    sec = ai.get(key) or {}
                    if sec.get("isAvailable") and sec.get("downloadLink"):
                        formats[fmt] = {"url": sec["downloadLink"], "size": None}
                if not formats:
                    continue
            hits.append(_hit(str(vi.get("title", "")), ", ".join(vi.get("authors") or []), _year(vi.get("publishedDate")),
                             "Google Books", kind, formats,
                             si.get("buyLink") or vi.get("infoLink") or "", bool(ai.get("publicDomain")),
                             vi.get("pageCount") or None))
    return hits


def oapen_search(query):
    q = urllib.parse.urlencode({"query": query, "expand": "metadata,bitstreams", "limit": "6"})
    items = get_json(f"{BASES['oapen']}/rest/search?{q}")
    hits = []
    for it in items if isinstance(items, list) else []:
        md = {}
        for m in it.get("metadata") or []:
            md.setdefault(m.get("key"), m.get("value"))
        formats = {}
        for bs in it.get("bitstreams") or []:
            fmt = MIME_FMT.get(str(bs.get("mimeType", "")).lower()) or BOOK_EXTS.get(ext_of(str(bs.get("name", ""))))
            if fmt and bs.get("retrieveLink") and fmt not in formats:
                formats[fmt] = {"url": urljoin(BASES["oapen"] + "/", bs["retrieveLink"]), "size": bs.get("sizeBytes")}
        if formats:
            hits.append(_hit(str(md.get("dc.title", it.get("name", ""))), str(md.get("dc.contributor.author", "")),
                             _year(md.get("dc.date.issued")), "OAPEN", "free", formats,
                             BASES["oapen"] + "/handle/" + str(it.get("handle", ""))))
    return hits


CATALOGS = [("Open Library", ol_search), ("Internet Archive", ia_search), ("Project Gutenberg", gut_search),
            ("Google Books", gb_search), ("OAPEN", oapen_search)]


def merge_hits(hits, query, min_relevance=0.6):
    """Group hits from all catalogs into one card per book."""
    cards = {}
    for h in hits:
        if not h["title"] or relevance(query, h["title"], h["author"]) < min_relevance:
            continue
        key = main_title_key(h["title"]) or norm_key(h["title"])
        if not key:
            continue
        c = cards.setdefault(key, {"id": book_id(key), "title": h["title"], "author": "", "year": None, "pages": None,
                                   "sources": [], "links": {}})
        if len(h["title"]) < len(c["title"]):
            c["title"] = h["title"]  # the shorter title is usually the cleaner one
        c["author"] = c["author"] or h["author"]
        if h["year"] and (not c["year"] or h["year"] < c["year"]):
            c["year"] = h["year"]
        c["pages"] = c["pages"] or h.get("pages")
        if h["kind"] == "free" and h["formats"]:
            src = next((s for s in c["sources"] if s["name"] == h["source"] and s["kind"] == "free"), None)
            if src:
                for f, d in h["formats"].items():
                    src["formats"].setdefault(f, dict(d, ok=None, note="Not checked yet"))
                src["public_domain"] = src["public_domain"] or h["public_domain"]
            else:
                c["sources"].append({"name": h["source"], "kind": "free", "page_url": h["page_url"],
                                     "public_domain": h["public_domain"], "trust": TRUST.get(h["source"], 1),
                                     "formats": {f: dict(d, ok=None, note="Not checked yet") for f, d in h["formats"].items()}})
        elif h["kind"] in ("borrow", "buy", "info") and h["page_url"]:
            slot = {"borrow": "borrow", "buy": "buy", "info": "info"}[h["kind"]]
            c["links"].setdefault(slot, {"name": h["source"], "url": h["page_url"]})
    for c in cards.values():
        q = quote(f"{c['title']} {c['author']}".strip())
        c["links"]["worldcat"] = {"name": "WorldCat", "url": f"https://search.worldcat.org/search?q={q}"}
    return list(cards.values())


def _fmt_rank(pref, fmt):
    order = [pref.upper()] + [f for f in FORMAT_ORDER if f != pref.upper()]
    return order.index(fmt) if fmt in order else 99


def finalize(card, pref="EPUB", verified_only=True):
    """Pick the best source and set the badge/status on a card."""
    best, best_key = None, None
    for s in card["sources"]:
        if s["kind"] != "free":
            continue
        for f, d in s["formats"].items():
            if d.get("ok") is False or (verified_only and d.get("ok") is not True):
                continue
            key = (_fmt_rank(pref, f), -s["trust"], 0 if d.get("ok") else 1)
            if best_key is None or key < best_key:
                best_key, best = key, {"source": s["name"], "fmt": f, "url": d["url"], "size": d.get("size"),
                                       "page_url": s["page_url"], "public_domain": s["public_domain"]}
    card["best"] = best
    if best:
        pd = best["public_domain"] or any(s["public_domain"] for s in card["sources"]
                                          if any(d.get("ok") for d in s["formats"].values()))
        card["status"], card["badge"] = ("public" if pd else "free"), ("Public domain" if pd else "Free to download")
    else:
        card["status"] = "borrow_buy" if (card["links"].get("borrow") or card["links"].get("buy")
                                         or card["links"].get("info")) else "none"
        card["badge"] = "Borrow or buy only" if card["status"] == "borrow_buy" else "No copy found"
    return card


def lookup(query, pref="EPUB", on_event=None, cancel=None, verify=True):
    """Search every catalog at once, merge, test the links, rank. Returns the final cards."""
    on_event = on_event or (lambda e: None)
    cancel = cancel or threading.Event()
    hits, answered, t0 = [], 0, time.time()
    lock = threading.Lock()

    def ask(name_fn):
        name, fn = name_fn
        try:
            res = fn(query)
            ok, err = True, ""
        except Exception as e:
            res, ok, err = [], False, str(e)[:100]
        with lock:
            hits.extend(res)
        on_event({"type": "catalog", "name": name, "ok": ok, "n": len(res), "error": err})
        return ok
    with ThreadPoolExecutor(len(CATALOGS)) as ex:
        results = list(ex.map(ask, CATALOGS))
    answered = sum(1 for r in results if r)
    cards = [finalize(c, pref, verified_only=False) for c in merge_hits(hits, query)]
    on_event({"type": "cards", "cards": cards, "final": False, "answered": answered, "total": len(CATALOGS),
              "seconds": round(time.time() - t0, 1)})
    if verify and not cancel.is_set():
        jobs = [(s, f, d) for c in cards for s in c["sources"] if s["kind"] == "free" for f, d in s["formats"].items()]

        def one(job):
            s, f, d = job
            return job, (None if cancel.is_set() else probe(d["url"], f, want_pages=False))
        with ThreadPoolExecutor(6) as ex:
            for job, r in ex.map(one, jobs):
                if r:
                    job[2]["ok"], job[2]["note"] = r["ok"], r["note"]
                    if r["size"]:
                        job[2]["size"] = r["size"]
    cards = [finalize(c, pref, verified_only=True) for c in cards]
    order = {"public": 0, "free": 1, "borrow_buy": 2, "none": 3}
    cards.sort(key=lambda c: (order[c["status"]], -relevance(query, c["title"], c["author"])))
    on_event({"type": "cards", "cards": cards, "final": True, "answered": answered, "total": len(CATALOGS),
              "seconds": round(time.time() - t0, 1)})
    return cards
