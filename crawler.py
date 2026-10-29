"""Site crawler + offline link rewriter that produces a ZIM file.

Pure standard library. Fetches pages and their assets (CSS, images, scripts,
fonts), rewrites links so the archived copy works offline, and writes a .zim.
"""
import datetime
import gzip
import hashlib
import html as htmllib
import io
import mimetypes
import os
import posixpath
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import prefs
from crawlcache import PageCache
from zimwriter import ZimWriter

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CindrixRecon/1.2"
MAX_RESOURCE = 50 * 1024 * 1024
HTML_MIMES = ("text/html", "application/xhtml+xml")
REMOVE = object()   # sentinel: drop this reference from the saved page

_EXT = {
    "image": {"png", "jpg", "jpeg", "jpe", "gif", "webp", "avif", "bmp", "ico", "svg", "tif", "tiff", "heic"},
    "media": {"mp4", "webm", "mov", "m4v", "avi", "mkv", "mpg", "mpeg", "wmv", "flv", "mp3", "ogg", "oga",
              "ogv", "wav", "m4a", "flac", "aac", "wma", "opus"},
    "font": {"woff", "woff2", "ttf", "otf", "eot"},
    "doc": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "zip", "7z", "rar", "gz", "tar",
            "epub", "iso", "exe", "msi", "dmg", "apk"},
}
_FONT_MIMES = {"application/font-woff", "application/font-woff2", "application/x-font-ttf",
               "application/x-font-woff", "application/vnd.ms-fontobject", "application/x-font-opentype"}
_DOC_MIMES = {"application/pdf", "application/zip", "application/x-zip-compressed", "application/epub+zip",
              "application/msword", "application/x-7z-compressed", "application/vnd.rar", "application/x-rar-compressed",
              "application/x-msdownload", "application/x-iso9660-image"}


def category_of_url(url):
    last = urlsplit(url).path.lower().rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1] if "." in last else ""
    for cat, exts in _EXT.items():
        if ext in exts:
            return cat
    return None


def category_of_mime(mime):
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith(("video/", "audio/")):
        return "media"
    if m.startswith("font/") or m in _FONT_MIMES:
        return "font"
    if m in _DOC_MIMES or m.startswith(("application/vnd.openxmlformats", "application/vnd.ms-")):
        return "doc"
    return None


def skip_category(o, cat):
    return bool((cat == "image" and o.skip_images) or (cat == "media" and o.skip_media)
                or (cat == "font" and o.skip_fonts) or (cat == "doc" and o.skip_docs))


def shrink_image(data, mime, max_px=800, quality=60):
    """Downscale/recompress a picture. Returns (data, mime); unchanged if Pillow is missing
    or the result wouldn't be smaller."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return data, mime
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "is_animated", False):
            return data, mime
        im = ImageOps.exif_transpose(im)
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        out = io.BytesIO()
        new_mime = mime
        if mime == "image/jpeg":
            im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
        elif mime == "image/webp":
            im.save(out, "WEBP", quality=quality)
        else:  # PNG
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            if not has_alpha and len(data) > 150_000:
                im.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
                new_mime = "image/jpeg"
            else:
                im.save(out, "PNG", optimize=True)
        new = out.getvalue()
        if len(new) < len(data):
            return new, new_mime
    except Exception:
        pass
    return data, mime


@dataclass
class Options:
    max_pages: int = 1000          # 0 = unlimited
    max_mb: int = 500              # 0 = unlimited
    max_minutes: int = 0           # 0 = unlimited (stops downloading, still builds the ZIM)
    workers: int = 4
    delay: float = 0.0
    path_scope: bool = False       # only pages under the start URL's folder
    subdomains: bool = False
    title: str = ""
    language: str = "eng"
    # --- detail level (smaller ZIMs)
    skip_images: bool = False
    skip_media: bool = False       # video and audio
    skip_fonts: bool = False
    skip_docs: bool = False        # PDFs, archives, office files, installers
    shrink_images: bool = False    # downscale + recompress pictures (built-in engine, needs Pillow)
    no_scripts: bool = False       # drop JavaScript (built-in engine)
    max_file_mb: int = 0           # skip any single file larger than this (0 = no limit)
    depth: int = 0                 # clicks from the start page (0 = no limit)
    cache_dir: str = ""             # where saved pages live (built-in engine)
    cache_mode: str = "off"        # off | resume (trust saved pages) | update (check what changed)


# ---------------------------------------------------------------- URL utils
def normalize(url):
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, p.path or "/", p.query, ""))


def to_ascii(url):
    p = urlsplit(url)
    host = p.netloc
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        host = host.encode("idna").decode("ascii")
    safe = "/%:@!$&'()*+,;=~-._"
    return urlunsplit((p.scheme, host, quote(p.path, safe=safe),
                       quote(p.query, safe=safe + "?"), ""))


def zim_path_for(url):
    p = urlsplit(url)
    path = unquote(p.path) or "/"
    if path.endswith("/"):
        path += "index.html"
    if p.query:
        h = hashlib.md5(p.query.encode("utf-8")).hexdigest()[:8]
        base, ext = posixpath.splitext(path)
        path = f"{base}_{h}{ext}"
    host = p.netloc.replace(":", "_")
    return host + path


# ------------------------------------------------------------ text decoding
def decode_bytes(data, charset=None):
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if not charset:
        m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?([-\w]+)""", data[:4096], re.I)
        if not m:
            m = re.match(rb"""\s*@charset\s+["']([-\w]+)""", data, re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for enc in (charset, "utf-8", "cp1252"):
        if not enc:
            continue
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


# ------------------------------------------------------------ link mapping
BASE_RE = re.compile(r"""<base\b[^>]*?\bhref\s*=\s*["']([^"']+)["']""", re.I)
TAG_RE = re.compile(r"""<([a-zA-Z][a-zA-Z0-9]*)\b((?:"[^"]*"|'[^']*'|[^'">])*)>""", re.S)
ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)""")
STYLE_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
CSS_URL_RE = re.compile(r"""url\(\s*(["']?)(.*?)\1\s*\)""", re.I | re.S)
CSS_IMPORT_RE = re.compile(r"""@import\s+(["'])(.*?)\1""", re.I)
SKIP_PREFIX = ("#", "javascript:", "mailto:", "tel:", "data:", "about:", "blob:", "sms:")
URL_ATTRS = {"href", "src", "poster", "data-src", "data-original", "data-lazy-src"}
PAGE_TAGS = {"a", "area", "iframe", "frame"}
ASSET_TAGS = {"img", "script", "source", "video", "audio", "embed", "track", "input"}
ASSET_RELS = {"stylesheet", "icon", "shortcut", "apple-touch-icon", "preload", "image_src"}


def _esc(s):
    return s.replace("&", "&amp;").replace('"', "%22").replace("<", "%3C").replace(">", "%3E")


def css_map_urls(text, base_url, fn):
    def repl_url(m):
        raw = m.group(2).strip()
        if not raw or raw.lower().startswith(SKIP_PREFIX):
            return m.group(0)
        absu = urljoin(base_url, raw)
        if not absu.lower().startswith(("http://", "https://")):
            return m.group(0)
        new = fn(absu, "asset")
        if new is REMOVE:
            return 'url("data:,")'
        if new is None:
            return m.group(0)
        return 'url("%s")' % new.replace('"', "%22")

    def repl_imp(m):
        raw = m.group(2).strip()
        absu = urljoin(base_url, raw)
        if not absu.lower().startswith(("http://", "https://")):
            return m.group(0)
        new = fn(absu, "asset")
        if new is REMOVE:
            return '@import "data:,"'
        if new is None:
            return m.group(0)
        return '@import "%s"' % new.replace('"', "%22")

    text = CSS_IMPORT_RE.sub(repl_imp, text)
    return CSS_URL_RE.sub(repl_url, text)


def map_html_urls(text, base_url, fn, strip=False, skip_scripts=False):
    """Find every URL in an HTML document and pass it to fn(abs_url, kind).

    kind is 'page' or 'asset'. If fn returns a string the URL is replaced by
    it, if None it is left unchanged. With strip=True, <base>, integrity and
    crossorigin are removed (needed for faithful offline copies). If fn returns
    REMOVE the reference is dropped. With skip_scripts=True all <script> blocks
    are removed from the page.
    """
    if skip_scripts:
        text = re.sub(r"(?is)<script\b.*?</script\s*>", "", text)
    m = BASE_RE.search(text)
    if m:
        base_url = urljoin(base_url, htmllib.unescape(m.group(1)))

    def do_url(raw, kind):
        v = htmllib.unescape(raw).strip()
        if not v or v.lower().startswith(SKIP_PREFIX):
            return None
        absu = urljoin(base_url, v)
        if not absu.lower().startswith(("http://", "https://")):
            return None
        new = fn(absu, kind)
        if new is REMOVE:
            return REMOVE
        return None if new is None else _esc(new)

    def do_srcset(raw):
        out, changed = [], False
        for cand in raw.split(","):
            cand = cand.strip()
            if not cand:
                continue
            parts = cand.split(None, 1)
            new = do_url(parts[0], "asset")
            if new is REMOVE:
                changed = True
                continue
            if new is not None:
                changed = True
                parts[0] = new
            out.append(" ".join(parts))
        if changed and not out:
            return REMOVE
        return ", ".join(out) if changed else None

    def sub_tag(tm):
        name = tm.group(1).lower()
        attrs = tm.group(2)
        if name == "base":
            return "" if strip else tm.group(0)
        rel_tokens = set()
        if name == "link":
            rm = re.search(r"""\brel\s*=\s*["']?([^"'>]*)""", attrs, re.I)
            if rm:
                rel_tokens = set(rm.group(1).lower().split())

        def sub_attr(am):
            an = am.group(1).lower()
            val = am.group(3)
            q = val[0] if val[0] in "\"'" else ""
            raw = val[1:-1] if q else val
            if strip and an in ("integrity", "crossorigin", "nonce"):
                return ""
            new = None
            if an in URL_ATTRS:
                kind = None
                if an == "href":
                    if name in PAGE_TAGS:
                        kind = "page"
                    elif name == "link" and rel_tokens & ASSET_RELS:
                        kind = "asset"
                elif an == "src":
                    if name in PAGE_TAGS:
                        kind = "page"
                    elif name in ASSET_TAGS:
                        kind = "asset"
                elif an == "poster" and name == "video":
                    kind = "asset"
                elif an.startswith("data-"):
                    kind = "asset"
                if kind:
                    new = do_url(raw, kind)
            elif an in ("srcset", "data-srcset") and name in ("img", "source"):
                new = do_srcset(htmllib.unescape(raw))
                if new is not None and new is not REMOVE:
                    new = _esc(new)
            elif an == "style" and "url(" in raw.lower():
                orig = htmllib.unescape(raw)
                res = css_map_urls(orig, base_url, fn)
                if res != orig:
                    new = res.replace("&", "&amp;").replace('"', "'").replace("<", "%3C")
            if new is REMOVE:
                return ""
            if new is None:
                return am.group(0)
            return f'{am.group(1)}{am.group(2)}"{new}"'

        new_attrs = ATTR_RE.sub(sub_attr, attrs)
        if new_attrs == attrs:
            return tm.group(0)
        return f"<{tm.group(1)}{new_attrs}>"

    def sub_style(sm):
        return sm.group(1) + css_map_urls(sm.group(2), base_url, fn) + sm.group(3)

    text = STYLE_RE.sub(sub_style, text)
    return TAG_RE.sub(sub_tag, text)


# ------------------------------------------------------------------ crawler
class Crawler:
    def __init__(self, start_url, options, on_event=None):
        self.o = options
        self.start = normalize(start_url)
        self.on_event = on_event or (lambda e: None)
        self.cancel = threading.Event()
        self.workdir = tempfile.mkdtemp(prefix="recon_")
        self.records = {}   # final url -> dict(file, mime, size)
        self.alias = {}     # requested url -> final url
        self.failed = 0
        self.bytes = 0
        self.pages = 0
        self.files = 0
        self.skipped = set()       # URLs deliberately not saved (detail level / file-size cap)
        self.skipped_count = 0
        self.limit_hit = None      # 'pages' | 'size' | 'time' when a limit stopped the crawl
        self.depth_cut = False     # some pages were deeper than the depth setting
        self.saved_by_shrinking = 0
        sp = urlsplit(self.start)
        host = sp.hostname or ""
        self.start_host = host[4:] if host.startswith("www.") else host
        self.start_dir = sp.path if sp.path.endswith("/") else sp.path.rsplit("/", 1)[0] + "/"
        self.opener = urllib.request.build_opener(prefs.proxy_handler())
        self.cache = PageCache(options.cache_dir, self.start) if options.cache_mode != "off" and options.cache_dir else None
        self.cache_hits = 0

    def emit(self, **kw):
        kw.setdefault("pages", self.pages)
        kw.setdefault("files", self.files)
        kw.setdefault("bytes", self.bytes)
        kw.setdefault("skipped", self.skipped_count)
        self.on_event(kw)

    def in_scope(self, url):
        p = urlsplit(url)
        host = p.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        if host != self.start_host:
            if not (self.o.subdomains and host.endswith("." + self.start_host)):
                return False
        if self.o.path_scope and host == self.start_host and not p.path.startswith(self.start_dir):
            return False
        return True

    def _fetch(self, url):
        """Get one file: from the saved copy when allowed, else from the site (asking only what changed when we can)."""
        cached = self.cache.get(url) if self.cache else None
        if cached and self.o.cache_mode == "resume":
            self.cache_hits += 1
            return {"data": cached[1], "final": cached[0]["final"], "ctype": cached[0]["ctype"]}
        headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                   "Accept-Encoding": "gzip, deflate"}
        if cached:
            if cached[0].get("etag"):
                headers["If-None-Match"] = cached[0]["etag"]
            if cached[0].get("lm"):
                headers["If-Modified-Since"] = cached[0]["lm"]
        req = urllib.request.Request(to_ascii(url), headers=headers)
        try:
            with self.opener.open(req, timeout=30) as r:
                final = normalize(r.geturl())
                cap = MAX_RESOURCE
                if self.o.max_file_mb:
                    cap = min(cap, self.o.max_file_mb * 1024 * 1024)
                data = r.read(cap + 1)
                if len(data) > cap:
                    return {"toolarge": True, "final": final}
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    data = gzip.decompress(data)
                elif enc == "deflate":
                    try:
                        data = zlib.decompress(data)
                    except zlib.error:
                        data = zlib.decompress(data, -15)
                ctype = r.headers.get("Content-Type", "") or ""
                etag, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                self.cache_hits += 1
                return {"data": cached[1], "final": cached[0]["final"], "ctype": cached[0]["ctype"]}
            raise
        if self.cache:
            self.cache.put(url, {"final": final, "ctype": ctype, "etag": etag, "lm": lm}, data)
        return {"data": data, "final": final, "ctype": ctype}

    # -- worker (runs in thread)
    def _work(self, url, kind, n):
        if self.cancel.is_set():
            return {"url": url, "kind": kind, "ok": False, "error": "cancelled"}
        if self.o.delay:
            time.sleep(self.o.delay)
        last = "error"
        for attempt in range(3):
            try:
                got = self._fetch(url)
                final = got["final"]
                if got.get("toolarge"):
                    return {"url": url, "kind": kind, "ok": False, "error": "too large", "skip": True, "final": final}
                data, ctype = got["data"], got["ctype"]
                mime = ctype.split(";")[0].strip().lower()
                cm = re.search(r"charset=([-\w]+)", ctype, re.I)
                charset = cm.group(1) if cm else None
                if not mime or mime == "application/octet-stream":
                    mime = mimetypes.guess_type(urlsplit(final).path)[0] or "application/octet-stream"
                cat = category_of_mime(mime) or category_of_url(final)
                if skip_category(self.o, cat) and url != self.start:
                    return {"url": url, "kind": kind, "ok": False, "error": "skipped", "skip": True, "final": final}
                path = os.path.join(self.workdir, f"{n}.bin")
                with open(path, "wb") as f:
                    f.write(data)
                links = []
                if mime in HTML_MIMES:
                    text = decode_bytes(data, charset)
                    map_html_urls(text, final, lambda u, k: (links.append((u, k)), None)[1], skip_scripts=self.o.no_scripts)
                elif mime == "text/css":
                    text = decode_bytes(data, charset)
                    css_map_urls(text, final, lambda u, k: (links.append((u, k)), None)[1])
                return {"url": url, "kind": kind, "ok": True, "final": final, "mime": mime,
                        "file": path, "size": len(data), "links": links}
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code in (400, 401, 403, 404, 410):
                    break
            except Exception as e:  # network errors etc.
                last = str(e)[:120]
            time.sleep(0.5 * (attempt + 1))
        return {"url": url, "kind": kind, "ok": False, "error": last}

    def crawl(self):
        limit_bytes = self.o.max_mb * 1024 * 1024 if self.o.max_mb else 0
        pending = deque([(self.start, "page", 0)])
        seen = {self.start}
        queued_pages = 1
        counter = 0
        deadline = time.time() + self.o.max_minutes * 60 if self.o.max_minutes else None
        with ThreadPoolExecutor(max(1, self.o.workers)) as ex:
            futs = {}
            while (pending or futs) and not self.cancel.is_set():
                while pending and len(futs) < self.o.workers * 2:
                    if limit_bytes and self.bytes >= limit_bytes:
                        self.limit_hit = self.limit_hit or "size"
                        pending.clear()
                        break
                    if deadline and time.time() >= deadline:
                        self.limit_hit = self.limit_hit or "time"
                        pending.clear()
                        break
                    url, kind, depth = pending.popleft()
                    counter += 1
                    futs[ex.submit(self._work, url, kind, counter)] = (url, kind, depth)
                if not futs:
                    break
                done, _ = wait(list(futs), timeout=0.3, return_when=FIRST_COMPLETED)
                for f in done:
                    url, kind, depth = futs.pop(f)
                    res = f.result()
                    if not res["ok"]:
                        if res.get("skip"):
                            self.skipped.add(url)
                            if res.get("final"):
                                self.skipped.add(res["final"])
                            self.skipped_count += 1
                        elif res.get("error") != "cancelled":
                            self.failed += 1
                            self.emit(type="fetch", ok=False, url=url, kind=kind, error=res["error"])
                        continue
                    final = res["final"]
                    if kind == "page" and not self.in_scope(final):
                        continue
                    self.alias[url] = final
                    seen.add(final)
                    if final not in self.records:
                        self.records[final] = {"file": res["file"], "mime": res["mime"], "size": res["size"]}
                        self.bytes += res["size"]
                        if res["mime"] in HTML_MIMES:
                            self.pages += 1
                        else:
                            self.files += 1
                        self.emit(type="fetch", ok=True, url=final, kind=kind, mime=res["mime"], size=res["size"])
                    for u, k in res["links"]:
                        u = normalize(u)
                        if u in seen:
                            continue
                        cat = category_of_url(u)
                        if cat and skip_category(self.o, cat):
                            seen.add(u)
                            self.skipped.add(u)
                            self.skipped_count += 1
                            continue
                        child_depth = depth
                        if k == "page":
                            if not self.in_scope(u):
                                continue
                            child_depth = depth + 1
                            if self.o.depth and child_depth > self.o.depth:
                                self.depth_cut = True
                                continue
                            if self.o.max_pages and queued_pages >= self.o.max_pages:
                                self.limit_hit = self.limit_hit or "pages"
                                continue
                            queued_pages += 1
                        seen.add(u)
                        pending.append((u, k, child_depth))
            if self.cancel.is_set():
                for f in futs:
                    f.cancel()

    # -- build the ZIM
    def build(self, out_path):
        if self.start not in self.records and self.alias.get(self.start) not in self.records:
            raise RuntimeError("Could not download the start page. Check the URL and your connection.")
        start_final = self.alias.get(self.start, self.start)

        def file_hash(rec):
            with open(rec["file"], "rb") as fh:
                return hashlib.md5(fh.read()).hexdigest()

        paths, used, duplicates = {}, {}, set()
        for url in [start_final] + [u for u in self.records if u != start_final]:
            p = zim_path_for(url)
            if p in used:
                if file_hash(self.records[url]) == file_hash(self.records[used[p]]):
                    paths[url] = p          # same page reached by two URLs (e.g. / and /index.html)
                    duplicates.add(url)
                    continue
                p = p + "_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:6]
            used[p] = url
            paths[url] = p

        def resolver_for(cur_path):
            cur_dir = posixpath.dirname(cur_path) or "."

            def fn(absu, kind):
                frag = ""
                if "#" in absu:
                    absu, frag = absu.split("#", 1)
                    frag = "#" + frag
                u0 = normalize(absu)
                u = self.alias.get(u0, u0)
                if u in paths:
                    rel = posixpath.relpath(paths[u], cur_dir)
                    return quote(rel, safe="/") + frag
                if kind == "asset" and (u0 in self.skipped or u in self.skipped):
                    return REMOVE
                return absu + frag
            return fn

        meta_title = self.o.title
        part = out_path + ".part"
        writer = ZimWriter(part)
        total = len(self.records)
        done = 0
        order = [start_final] + [u for u in self.records if u != start_final]
        title_re = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
        for url in order:
            if self.cancel.is_set():
                writer._clusters.close()
                if os.path.exists(part):
                    os.remove(part)
                raise RuntimeError("Cancelled")
            if url in duplicates:
                done += 1
                continue
            rec = self.records[url]
            path = paths[url]
            with open(rec["file"], "rb") as f:
                data = f.read()
            mime = rec["mime"]
            title = ""
            if mime in HTML_MIMES:
                text = decode_bytes(data)
                tm = title_re.search(text)
                if tm:
                    title = re.sub(r"\s+", " ", htmllib.unescape(tm.group(1))).strip()
                if url == start_final and not meta_title:
                    meta_title = title
                text = map_html_urls(text, url, resolver_for(path), strip=True, skip_scripts=self.o.no_scripts)
                inject = '<meta charset="utf-8">'
                hm = re.search(r"<head\b[^>]*>", text, re.I)
                text = text[:hm.end()] + inject + text[hm.end():] if hm else inject + text
                data = text.encode("utf-8")
                title = title or path
            elif mime == "text/css":
                text = css_map_urls(decode_bytes(data), url, resolver_for(path))
                data = text.encode("utf-8")
            elif self.o.shrink_images and mime in ("image/jpeg", "image/png", "image/webp"):
                before = len(data)
                data, mime = shrink_image(data, mime)
                self.saved_by_shrinking += max(0, before - len(data))
            writer.add_entry(path, title, mime, data)
            done += 1
            if done % 25 == 0 or done == total:
                self.emit(type="build", done=done, total=total)
        writer.set_main(paths[start_final])
        host = urlsplit(self.start).hostname or "website"
        writer.metadata.update({
            "Title": meta_title or host,
            "Description": f"Offline copy of {host}",
            "Language": self.o.language,
            "Creator": host,
            "Publisher": "Cindrix Recon",
            "Date": datetime.date.today().isoformat(),
            "Name": re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_"),
            "Scraper": "Cindrix Recon 1.2",
        })
        writer.close()
        os.replace(part, out_path)
        return os.path.getsize(out_path)

    def cleanup(self):
        shutil.rmtree(self.workdir, ignore_errors=True)


def convert(url, out_path, options, on_event=None, cancel=None):
    """Crawl `url` and write a ZIM to `out_path`. Returns (size_bytes, crawler)."""
    c = Crawler(url, options, on_event)
    if cancel is not None:
        c.cancel = cancel
    try:
        c.crawl()
        if c.cancel.is_set():
            raise RuntimeError("Cancelled")
        if c.cache_hits:
            c.emit(type="phase", text=f"Reused {c.cache_hits} saved pages from an earlier run.")
        c.emit(type="phase", text="Building ZIM file...")
        size = c.build(out_path)
        return size, c
    finally:
        c.cleanup()
