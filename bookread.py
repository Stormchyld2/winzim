"""Turn e-book files into plain web pages so they can be read inside Kiwix (no outside app needed).

Every converter returns a Readable: the pages/images/styles to store in the ZIM plus the reading order.
  EPUB  - its own pages, styles and pictures are copied across as they are.
  MOBI / AZW3 - the book text is unpacked and cut into chapters (DRM-locked books cannot be read).
  TXT, RTF, FB2, CBZ - converted to simple pages.
DJVU, LIT and CBR have no reader; they stay download-only.
"""
import base64
import html
import os
import shutil
import subprocess
import sys
import tempfile
import mimetypes
import posixpath
import re
import struct
import xml.etree.ElementTree as ET

import prefs
import zipfile
from urllib.parse import unquote

READABLE = ("EPUB", "MOBI", "AZW3", "TXT", "FB2", "CBZ", "RTF", "DJVU", "CBR", "LIT")
MAX_INNER_MB = 600  # an EPUB/CBZ that unpacks bigger than this is not made readable

MIME_BY_EXT = {"html": "text/html", "htm": "text/html", "xhtml": "application/xhtml+xml", "css": "text/css",
               "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
               "svg": "image/svg+xml", "webp": "image/webp", "ttf": "font/ttf", "otf": "font/otf",
               "woff": "font/woff", "woff2": "font/woff2", "xml": "application/xml", "ncx": "application/x-dtbncx+xml",
               "opf": "application/oebps-package+xml", "txt": "text/plain", "js": "application/javascript"}


class Unreadable(Exception):
    pass


class Readable:
    def __init__(self):
        self.files = {}   # path inside the book -> (mime, bytes)
        self.spine = []   # [(path, title)]

    def add(self, path, mime, data):
        self.files[path] = (mime, data)


def _mime(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return MIME_BY_EXT.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _page(title, body, css=""):
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title>{css}</head><body>{body}</body></html>').encode("utf-8")


SIMPLE_CSS = ('<style>body{max-width:42em;margin:0 auto;padding:1em 1.2em 3em;font-family:Georgia,serif;line-height:1.6}'
              'img{max-width:100%;height:auto}pre{white-space:pre-wrap}</style>')


# ------------------------------------------------------------------ EPUB
def read_epub(path):
    r = Readable()
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_INNER_MB * 1024 * 1024:
            raise Unreadable("too big to unpack")
        names = set(z.namelist())
        if any(n.endswith("encryption.xml") and b"EncryptedData" in z.read(n) and b"fontobfuscation" not in z.read(n)
               and b"IDPF" not in z.read(n) for n in names if n == "META-INF/encryption.xml"):
            raise Unreadable("locked with DRM")
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile")
        opf = ET.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest, spine_ids, nav_href, ncx_href = {}, [], None, None
        for el in opf.iter():
            t = _local(el.tag)
            if t == "item":
                manifest[el.get("id")] = (el.get("href", ""), el.get("media-type", ""), el.get("properties", ""))
                if "nav" in (el.get("properties") or "").split():
                    nav_href = el.get("href")
                if el.get("media-type") == "application/x-dtbncx+xml":
                    ncx_href = el.get("href")
            elif t == "itemref" and el.get("linear") != "no":
                spine_ids.append(el.get("idref"))
            elif t == "spine" and el.get("toc") in manifest if False else False:
                pass

        def full(h):
            return posixpath.normpath(posixpath.join(base, unquote(h.split("#")[0])))

        titles = {}
        try:
            if nav_href:
                nav = z.read(full(nav_href)).decode("utf-8", "replace")
                nav_dir = posixpath.dirname(full(nav_href))
                for m in re.finditer(r'<a[^>]+href="([^"#]*)[^"]*"[^>]*>(.*?)</a>', nav, re.S):
                    t = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if t and m.group(1):
                        titles.setdefault(posixpath.normpath(posixpath.join(nav_dir, unquote(m.group(1)))), html.unescape(t))
            elif ncx_href:
                ncx = ET.fromstring(z.read(full(ncx_href)))
                ncx_dir = posixpath.dirname(full(ncx_href))
                for np_ in ncx.iter():
                    if _local(np_.tag) == "navPoint":
                        text = next((x.text for x in np_.iter() if _local(x.tag) == "text" and x.text), "")
                        src = next((x.get("src") for x in np_.iter() if _local(x.tag) == "content"), "")
                        if text and src:
                            titles.setdefault(posixpath.normpath(posixpath.join(ncx_dir, unquote(src.split("#")[0]))), text.strip())
        except Exception:
            pass
        for n in z.namelist():
            if n.endswith("/") or n.startswith("META-INF/") or n == "mimetype":
                continue
            data = z.read(n)
            mime = _mime(n)
            if mime in ("text/html", "application/xhtml+xml"):
                data = re.sub(rb"(?is)<script\b.*?</script>", b"", data)
            r.add(n, mime, data)
        for i, iid in enumerate(spine_ids, 1):
            if iid in manifest:
                p = full(manifest[iid][0])
                if p in r.files:
                    r.spine.append((p, titles.get(p) or f"Part {i}"))
    if not r.spine:
        raise Unreadable("no chapters found")
    return r


# ------------------------------------------------------------------ MOBI / AZW3
def palmdoc_decompress(d):
    out = bytearray()
    i, n = 0, len(d)
    while i < n:
        c = d[i]
        i += 1
        if c == 0 or 9 <= c <= 0x7F:
            out.append(c)
        elif 1 <= c <= 8:
            out += d[i:i + c]
            i += c
        elif c >= 0xC0:
            out.append(32)
            out.append(c ^ 0x80)
        else:
            if i >= n:
                break
            c = (c << 8) | d[i]
            i += 1
            dist, ln = (c >> 3) & 0x7FF, (c & 7) + 3
            if dist == 0 or dist > len(out):
                raise Unreadable("damaged text")
            for _ in range(ln):
                out.append(out[-dist])
    return bytes(out)


def _trailing_size(rec, flags):
    num, f = 0, flags >> 1
    size = len(rec)
    while f:
        if f & 1:
            pos, bitpos, result = size - num, 0, 0
            while True:
                if pos <= 0:
                    break
                v = rec[pos - 1]
                result |= (v & 0x7F) << bitpos
                bitpos += 7
                pos -= 1
                if v & 0x80 or bitpos >= 28:
                    break
            num += result
        f >>= 1
    if flags & 1 and size - num - 1 >= 0:
        num += (rec[size - num - 1] & 3) + 1
    return num


def _image_type(b):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if b[:4] == b"GIF8":
        return "image/gif", "gif"
    return None, None


def _b32(s):
    n = 0
    for ch in s.upper():
        n = n * 32 + "0123456789ABCDEFGHIJKLMNOPQRSTUV".index(ch)
    return n


def read_mobi(path):
    data = open(path, "rb").read()
    if len(data) < 100 or data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
        raise Unreadable("not a MOBI/AZW file")
    nrec = struct.unpack(">H", data[76:78])[0]
    offs = [struct.unpack(">I", data[78 + 8 * i:82 + 8 * i])[0] for i in range(nrec)]
    offs.append(len(data))
    rec = lambda i: data[offs[i]:offs[i + 1]]
    r0 = rec(0)
    comp, _, text_len, text_recs, _, enc = struct.unpack(">HHIHHH", r0[:14])
    if enc != 0:
        raise Unreadable("locked with DRM")
    if comp not in (1, 2):
        raise Unreadable("uses an unsupported compression")
    encoding, version, first_img, flags = "cp1252", 0, None, 0
    if r0[16:20] == b"MOBI":
        hlen = struct.unpack(">I", r0[20:24])[0]
        e = struct.unpack(">I", r0[28:32])[0]
        encoding = "utf-8" if e == 65001 else "cp1252"
        version = struct.unpack(">I", r0[36:40])[0]
        fi = struct.unpack(">I", r0[108:112])[0]
        first_img = fi if fi != 0xFFFFFFFF and fi < nrec else None
        if hlen >= 228 and len(r0) >= 244:
            flags = struct.unpack(">H", r0[242:244])[0]
    text = bytearray()
    for i in range(1, min(text_recs, nrec - 1) + 1):
        b = rec(i)
        if flags:
            b = b[:len(b) - _trailing_size(b, flags)]
        text += palmdoc_decompress(b) if comp == 2 else b
    text = bytes(text)
    r = Readable()
    images = {}
    if first_img:
        for i in range(first_img, nrec):
            mime, ext = _image_type(rec(i))
            if mime:
                images[i] = (f"img{i - first_img + 1:05d}.{ext}", mime, rec(i))
    kf8 = version >= 8 and b"<html" in text[:4000].lower()
    if kf8:
        starts = [m.start() for m in re.finditer(rb"(?i)<html[\s>]", text)]
        parts = [text[s:(starts[k + 1] if k + 1 < len(starts) else len(text))] for k, s in enumerate(starts)]
        parts = [p[:p.lower().rfind(b"</html>") + 7] if b"</html>" in p.lower() else p for p in parts]
    else:
        pieces = re.split(rb"(?i)<mbp:pagebreak\s*/?>", text)
        parts, cur = [], b""
        for p in pieces:
            cur += p
            if len(re.sub(rb"<[^>]+>", b"", cur).strip()) > 3000:
                parts.append(cur)
                cur = b""
        if cur.strip():
            parts.append(cur)
    if not parts or not any(re.sub(rb"<[^>]+>", b"", p).strip() for p in parts):
        raise Unreadable("no readable text")
    starts_b, pos = [], 0
    for p in parts:
        starts_b.append(pos)
        pos += len(p)
    used = {}
    for k, p in enumerate(parts):
        s = p.decode(encoding, "replace")
        s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
        s = re.sub(r'(?is)<link\b[^>]*>', "", s)
        s = re.sub(r"(?is)</?(html|head|body|mbp:[a-z]+)[^>]*>", "", s) if not kf8 else re.sub(r"(?is)<head\b.*?</head>", "", s)
        s = re.sub(r'\said="[^"]*"', "", s)

        def img(m):
            tag = m.group(0)
            idx = None
            a = re.search(r'recindex="?(\d+)"?', tag)
            b = re.search(r"kindle:embed:([0-9A-Va-v]+)", tag)
            if a and first_img:
                idx = first_img + int(a.group(1)) - 1
            elif b and first_img:
                idx = first_img + _b32(b.group(1)) - 1
            if idx in images:
                used[idx] = images[idx]
                return f'<img src="{images[idx][0]}" alt="">'
            return ""
        s = re.sub(r"(?is)<img\b[^>]*>", img, s)

        def link(m):
            n = int(m.group(1))
            ci = max(i for i, st in enumerate(starts_b) if st <= n) if not kf8 else None
            return f'href="part{ci + 1:03d}.html"' if ci is not None else 'href="#"'
        s = re.sub(r"filepos=\"?(\d+)\"?", lambda m: link(m), s)
        s = re.sub(r'href="kindle:[^"]*"', 'href="#"', s)
        title = re.search(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", s)
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title.group(1)))).strip()[:80] if title else ""
        name = f"part{k + 1:03d}.html"
        r.add(name, "text/html", _page(t or f"Part {k + 1}", s, SIMPLE_CSS))
        r.spine.append((name, t or f"Part {k + 1}"))
    for name, mime, b in used.values():
        r.add(name, mime, b)
    return r


# ------------------------------------------------------------------ plain text, RTF
def _decode_text(b):
    for enc in ("utf-8-sig", "utf-16") if b[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("cp1252", "replace")


def _text_to_readable(text, title="Text"):
    r = Readable()
    paras = [p.strip("\n") for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")) if p.strip()]
    chunks, cur, size = [], [], 0
    for p in paras:
        cur.append(p)
        size += len(p)
        if size > 60000:
            chunks.append(cur)
            cur, size = [], 0
    if cur:
        chunks.append(cur)
    for k, ch in enumerate(chunks or [[""]], 1):
        body = "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in ch)
        name = f"part{k:03d}.html"
        r.add(name, "text/html", _page(f"{title} {k}", body, SIMPLE_CSS))
        r.spine.append((name, f"Part {k}"))
    return r


def read_txt(path):
    text = _decode_text(open(path, "rb").read())
    if not text.strip():
        raise Unreadable("empty file")
    return _text_to_readable(text)


def read_rtf(path):
    s = open(path, "rb").read().decode("latin-1")
    if not s.lstrip().startswith("{\\rtf"):
        raise Unreadable("not an RTF file")
    out, depth, skip_depth, i, n = [], 0, None, 0, len(s)
    skip_words = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "generator"}
    while i < n:
        c = s[i]
        if c == "{":
            depth += 1
            m = re.match(r"\{\\\*?\\?([a-z]+)", s[i:i + 40])
            if skip_depth is None and (m and m.group(1) in skip_words or s[i + 1:i + 3] == "\\*"):
                skip_depth = depth
            i += 1
        elif c == "}":
            if skip_depth == depth:
                skip_depth = None
            depth -= 1
            i += 1
        elif c == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if m:
                w = m.group(1)
                if skip_depth is None:
                    if w in ("par", "line"):
                        out.append("\n\n" if w == "par" else "\n")
                    elif w == "tab":
                        out.append("\t")
                    elif w == "u" and m.group(2):
                        v = int(m.group(2))
                        out.append(chr(v if v >= 0 else v + 65536))
                        if s[i + m.end():i + m.end() + 1] == "?":
                            i += 1
                i += m.end()
            elif s[i + 1:i + 2] == "'":
                if skip_depth is None:
                    try:
                        out.append(bytes([int(s[i + 2:i + 4], 16)]).decode("cp1252"))
                    except ValueError:
                        pass
                i += 4
            else:
                if skip_depth is None and s[i + 1:i + 2] in "\\{}":
                    out.append(s[i + 1])
                i += 2
        else:
            if skip_depth is None and c not in "\r\n":
                out.append(c)
            i += 1
    text = "".join(out)
    if not text.strip():
        raise Unreadable("no readable text")
    return _text_to_readable(text)


# ------------------------------------------------------------------ FB2, comics
def read_fb2(path):
    raw = open(path, "rb").read()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise Unreadable("damaged file")
    r = Readable()
    images = {}
    for el in root.iter():
        if _local(el.tag) == "binary" and el.get("id"):
            try:
                b = base64.b64decode(el.text or "")
            except Exception:
                continue
            mime, ext = _image_type(b)
            if mime:
                images[el.get("id")] = (f"img_{len(images) + 1}.{ext}", mime, b)
    used = set()

    def conv(el):
        t = _local(el.tag)
        inner = html.escape(el.text or "") + "".join(conv(c) + html.escape(c.tail or "") for c in el)
        if t == "p":
            return f"<p>{inner}</p>"
        if t in ("emphasis",):
            return f"<em>{inner}</em>"
        if t == "strong":
            return f"<strong>{inner}</strong>"
        if t == "title":
            return f"<h2>{inner}</h2>"
        if t == "subtitle":
            return f"<h3>{inner}</h3>"
        if t == "v":
            return f"{inner}<br>"
        if t == "empty-line":
            return "<br>"
        if t == "image":
            ref = next((v for k, v in el.attrib.items() if k.endswith("href")), "").lstrip("#")
            if ref in images:
                used.add(ref)
                return f'<img src="{images[ref][0]}" alt="">'
            return ""
        if t in ("epigraph", "cite", "poem", "stanza"):
            return f"<blockquote>{inner}</blockquote>"
        if t == "a":
            return inner
        return inner
    bodies = [b for b in root if _local(b.tag) == "body"]
    chapters = []
    for body in bodies[:1]:
        secs = [s for s in body if _local(s.tag) == "section"]
        chapters = secs or [body]
    for k, sec in enumerate(chapters, 1):
        ttl = next((c for c in sec if _local(c.tag) == "title"), None)
        t = re.sub(r"\s+", " ", "".join(ttl.itertext())).strip()[:80] if ttl is not None else ""
        body_html = "".join(conv(c) for c in sec) if sec in list(bodies[0]) else conv(sec)
        name = f"part{k:03d}.html"
        r.add(name, "text/html", _page(t or f"Part {k}", body_html, SIMPLE_CSS))
        r.spine.append((name, t or f"Part {k}"))
    for ref in used:
        n, mime, b = images[ref]
        r.add(n, mime, b)
    if not r.spine:
        raise Unreadable("no readable text")
    return r


def read_cbz(path):
    r = Readable()
    with zipfile.ZipFile(path) as z:
        names = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp")]
        if sum(i.file_size for i in names) > MAX_INNER_MB * 1024 * 1024:
            raise Unreadable("too big to unpack")
        key = lambda i: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", i.filename)]
        for k, info in enumerate(sorted(names, key=key), 1):
            ext = info.filename.rsplit(".", 1)[-1].lower()
            img = f"p{k:04d}.{ext}"
            r.add(img, _mime(img), z.read(info))
            page = f"p{k:04d}.html"
            r.add(page, "text/html", _page(f"Page {k}", f'<div style="text-align:center"><img src="{img}" alt="Page {k}" style="max-width:100%"></div>',
                                           "<style>body{margin:0;background:#000}</style>"))
            r.spine.append((page, f"Page {k}"))
    if not r.spine:
        raise Unreadable("no pages found")
    return r


# ------------------------------------------------------------------ formats that need a free helper program
TOOLS = {"ddjvu": ("tool_ddjvu", "DjVuLibre", ["ddjvu"], [r"DjVuLibre\ddjvu.exe"]),
         "7z": ("tool_7z", "7-Zip", ["7z", "7za", "7zz"], [r"7-Zip\7z.exe"]),
         "calibre": ("tool_calibre", "Calibre", ["ebook-convert"], [r"Calibre2\ebook-convert.exe"])}


def find_tool(key):
    """Path of a helper program (the one chosen in Settings, else found automatically), or None."""
    pref_key, _, names, win_paths = TOOLS[key]
    chosen = prefs.get(pref_key)
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        for rel in win_paths:
            if base and os.path.isfile(os.path.join(base, rel)):
                return os.path.join(base, rel)
    return None


def _run(cmd, timeout=900):
    flags = 0x08000000 if sys.platform == "win32" else 0   # no black console window
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise Unreadable("took too long to convert")
    except OSError as e:
        raise Unreadable(f"the helper program did not start ({e.__class__.__name__})")
    return r


def _need(key):
    p = find_tool(key)
    if not p:
        raise Unreadable(f"needs {TOOLS[key][1]} (free) installed to be readable here")
    return p


def read_djvu(path):
    ddjvu = _need("ddjvu")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "book.pdf")
        r = _run([ddjvu, "-format=pdf", path, out])
        if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 8:
            raise Unreadable("DjVuLibre could not convert this file")
        rd = Readable()
        rd.add("book.pdf", "application/pdf", open(out, "rb").read())
        rd.spine.append(("book.pdf", "Book"))
    return rd


def read_cbr(path):
    if zipfile.is_zipfile(path):        # many .cbr files are really zip files
        return read_cbz(path)
    sz = _need("7z")
    with tempfile.TemporaryDirectory() as tmp:
        r = _run([sz, "x", "-y", "-o" + tmp, path])
        if r.returncode != 0:
            raise Unreadable("7-Zip could not open this comic (damaged or locked)")
        imgs = []
        for dp, _, fns in os.walk(tmp):
            imgs += [os.path.join(dp, f) for f in fns if f.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp")]
        if not imgs:
            raise Unreadable("no pages found")
        key = lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.relpath(p, tmp))]
        rd = Readable()
        for k, p in enumerate(sorted(imgs, key=key), 1):
            ext = p.rsplit(".", 1)[-1].lower()
            img = f"p{k:04d}.{ext}"
            rd.add(img, _mime(img), open(p, "rb").read())
            page = f"p{k:04d}.html"
            rd.add(page, "text/html", _page(f"Page {k}", f'<div style="text-align:center"><img src="{img}" alt="Page {k}" style="max-width:100%"></div>',
                                            "<style>body{margin:0;background:#000}</style>"))
            rd.spine.append((page, f"Page {k}"))
    return rd


def read_lit(path):
    conv = _need("calibre")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "book.epub")
        r = _run([conv, path, out])
        if r.returncode != 0 or not os.path.isfile(out):
            raise Unreadable("Calibre could not convert this file (it may be locked)")
        return read_epub(out)


READERS = {"EPUB": read_epub, "MOBI": read_mobi, "AZW3": read_mobi, "TXT": read_txt, "FB2": read_fb2,
           "CBZ": read_cbz, "RTF": read_rtf, "DJVU": read_djvu, "CBR": read_cbr, "LIT": read_lit}


def make_readable(fmt, path):
    """Return a Readable or raise Unreadable (with a short plain reason)."""
    fn = READERS.get(fmt)
    if not fn or not prefs.get("zim_readers"):
        raise Unreadable("no reader for this format")
    try:
        return fn(path)
    except Unreadable:
        raise
    except Exception as e:
        raise Unreadable(f"could not be read ({type(e).__name__})")


# ------------------------------------------------------------------ the reader page (shell with chapter menu)
READER_JS = """
(function(){
var S=%SPINE%,KEY=%KEY%,fr=document.getElementById('fr'),sel=document.getElementById('ch'),
cur=0,fs=100,th='auto';
try{var sv=JSON.parse(localStorage.getItem('reader:'+KEY)||'{}');if(sv.i<S.length)cur=sv.i|0;
var pf=JSON.parse(localStorage.getItem('reader:prefs')||'{}');fs=pf.fs||100;th=pf.th||'auto';}catch(e){}
S.forEach(function(s,i){var o=document.createElement('option');o.value=i;o.textContent=s[1];sel.appendChild(o);});
function go(i){if(i<0||i>=S.length)return;cur=i;fr.src=S[i][0];sel.value=i;
 try{localStorage.setItem('reader:'+KEY,JSON.stringify({i:i}));}catch(e){}}
function style(){try{var d=fr.contentDocument;if(!d||!d.documentElement)return;var st=d.getElementById('rs');
 if(!st){st=d.createElementNS('http://www.w3.org/1999/xhtml','style');st.id='rs';(d.head||d.documentElement).appendChild(st);}
 var c=th==='dark'?'html,body{background:#131814!important;color:#e1e8e1!important}a{color:#5aa9ff!important}':
 th==='light'?'html,body{background:#fff!important;color:#111!important}':'';
 st.textContent='html{font-size:'+fs+'%%!important}body{font-size:'+fs+'%%!important}'+c;}catch(e){}}
fr.addEventListener('load',function(){style();
 try{var p=decodeURIComponent(fr.contentWindow.location.pathname);
 for(var i=0;i<S.length;i++){if(p.slice(-S[i][0].length)===S[i][0]||p.slice(-decodeURIComponent(S[i][0]).length)===decodeURIComponent(S[i][0])){cur=i;sel.value=i;
 try{localStorage.setItem('reader:'+KEY,JSON.stringify({i:i}));}catch(e){}break;}}}catch(e){}});
function save(){try{localStorage.setItem('reader:prefs',JSON.stringify({fs:fs,th:th}));}catch(e){}style();}
document.getElementById('prev').onclick=function(){go(cur-1);};
document.getElementById('next').onclick=function(){go(cur+1);};
sel.onchange=function(){go(parseInt(sel.value));};
document.getElementById('smaller').onclick=function(){fs=Math.max(70,fs-10);save();};
document.getElementById('bigger').onclick=function(){fs=Math.min(220,fs+10);save();};
document.getElementById('theme').onclick=function(){th=th==='auto'?'dark':th==='dark'?'light':'auto';save();};
document.addEventListener('keydown',function(e){if(e.key==='ArrowRight')go(cur+1);if(e.key==='ArrowLeft')go(cur-1);});
go(cur);
})();
"""
