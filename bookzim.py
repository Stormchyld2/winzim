"""Turn a folder of books into a ZIM file that contains a small offline catalog website.

The ZIM has a home page (search, filter by category/format, sort), a page for every book, and the book files
themselves. Kiwix cannot search inside PDF/EPUB text, so the catalog page is what makes books findable.
"""
import csv
import datetime
import html
import io
import json
import os
import re
import struct
import unicodedata
import zipfile
import zlib
import xml.etree.ElementTree as ET
from urllib.parse import quote

from appinfo import NAME, VERSION
from books import BOOK_EXTS, MANIFEST, clean_title, human_size, main_title_key, title_from_filename
from zimwriter import ZimWriter

MIME = {"PDF": "application/pdf", "EPUB": "application/epub+zip", "MOBI": "application/x-mobipocket-ebook",
        "AZW3": "application/vnd.amazon.ebook", "DJVU": "image/vnd.djvu", "TXT": "text/plain; charset=utf-8",
        "CBZ": "application/x-cbz", "CBR": "application/x-cbr", "FB2": "application/x-fictionbook+xml",
        "RTF": "application/rtf", "LIT": "application/x-ms-reader"}
FORMAT_ORDER = ["PDF", "EPUB", "MOBI", "AZW3", "TXT", "DJVU", "CBZ", "FB2", "RTF", "LIT", "CBR"]
LANG3 = {"en": "eng", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "it": "ita", "ru": "rus", "zh": "zho",
         "ar": "ara", "nl": "nld", "ja": "jpn", "ko": "kor", "pl": "pol", "sv": "swe", "tr": "tur", "hi": "hin"}
GENERIC_PDF_TITLES = ("microsoft word", "untitled", "document1", "slide 1", "powerpoint", ".indd", ".doc", ".qxd", ".pdf")


# ------------------------------------------------------------------ metadata from the files themselves
def _pdf_string(raw):
    raw = raw.strip()
    try:
        if raw.startswith(b"<"):
            hexs = re.sub(rb"\s+", b"", raw[1:-1])
            if len(hexs) % 2:
                hexs += b"0"
            b = bytes.fromhex(hexs.decode("ascii"))
        else:
            b = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"", b"f": b""}.get(
                m.group(1), m.group(1)), raw[1:-1])
            b = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 255]), b)
        if b.startswith(b"\xfe\xff"):
            return b[2:].decode("utf-16-be", "replace").strip()
        return b.decode("latin-1").strip()
    except Exception:
        return ""


def pdf_meta(path):
    """Title/author from a PDF's info dictionary (best effort)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(65536)
            tail = b""
            if size > 131072:
                f.seek(size - 65536)
                tail = f.read(65536)
    except OSError:
        return {}
    out = {}
    for key in ("Title", "Author"):
        m = re.search(rb"/" + key.encode() + rb"\s*(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]+>)", head + tail)
        if m:
            v = _pdf_string(m.group(1))
            if v and len(v) >= 3 and not any(g in v.lower() for g in GENERIC_PDF_TITLES) or (key == "Author" and v):
                out[key.lower()] = v
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def epub_meta(path, want_cover=True, cover_cap=400_000):
    """Title, author, year, language, description and cover image of an EPUB."""
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = next(el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile")
            opf = ET.fromstring(z.read(opf_path))
            base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            items, cover_id = {}, None
            for el in opf.iter():
                t = _local(el.tag)
                txt = (el.text or "").strip()
                if t == "title" and txt and "title" not in out:
                    out["title"] = txt
                elif t == "creator" and txt and "author" not in out:
                    out["author"] = txt
                elif t == "date" and txt and "year" not in out:
                    m = re.match(r"(\d{4})", txt)
                    if m:
                        out["year"] = int(m.group(1))
                elif t == "language" and txt and "language" not in out:
                    out["language"] = txt.lower()
                elif t == "description" and txt and "description" not in out:
                    d = html.unescape(re.sub(r"(?s)<[^>]+>", " ", txt))
                    out["description"] = re.sub(r"\s+", " ", d).strip()[:700]
                elif t == "meta" and el.get("name") == "cover":
                    cover_id = el.get("content")
                elif t == "item":
                    items[el.get("id")] = (el.get("href", ""), el.get("media-type", ""), el.get("properties", ""))
            if want_cover:
                href = None
                for iid, (h, mt, props) in items.items():
                    if "cover-image" in props.split():
                        href = h
                        break
                if not href and cover_id in items:
                    href = items[cover_id][0]
                if href:
                    from urllib.parse import unquote
                    name = unquote(base + href)
                    info = z.getinfo(name)
                    if info.file_size <= cover_cap * 4:
                        data = z.read(name)
                        mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg" if data[:2] == b"\xff\xd8" else None
                        if mime:
                            if len(data) > cover_cap:
                                from crawler import shrink_image
                                data, mime = shrink_image(data, mime, max_px=400, quality=70)
                            if len(data) <= cover_cap:
                                out["cover"], out["cover_mime"] = data, mime
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ scanning a folder
def ascii_slug(s, maxlen=70):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s[:maxlen].strip("-")) or "book"


def read_manifest(folder):
    rows = {}
    path = os.path.join(folder, MANIFEST)
    if os.path.exists(path):
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    key = os.path.basename((r.get("file") or "").replace("\\", "/"))
                    if key:
                        rows[key.lower()] = r
        except (OSError, csv.Error):
            pass
    return rows


def scan_folder(folder, cancel=None, on_event=None):
    """Find book files under `folder` and group formats of the same title into books."""
    manifest = read_manifest(folder)
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = os.path.relpath(dirpath, folder)
        category = "Library" if rel == "." else rel.replace("\\", "/").split("/")[0]
        for fn in sorted(filenames):
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            fmt = BOOK_EXTS.get(ext)
            if not fmt or fn.startswith(".") or fn.lower() == MANIFEST:
                continue
            found.append((os.path.join(dirpath, fn), fn, fmt, category))
    groups = {}
    for i, (path, fn, fmt, category) in enumerate(found, 1):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Cancelled")
        row = manifest.get(fn.lower(), {})
        meta = epub_meta(path) if fmt == "EPUB" else pdf_meta(path) if fmt == "PDF" else {}
        title = clean_title(row.get("title") or meta.get("title") or "") or title_from_filename(fn)
        key = (category, main_title_key(title) or title.lower())
        g = groups.setdefault(key, {"title": title, "author": "", "year": None, "category": category,
                                    "description": "", "language": "", "formats": {}, "cover": None, "cover_mime": "",
                                    "source": "", "public_domain": ""})
        g["author"] = g["author"] or row.get("author") or meta.get("author", "")
        yr = row.get("year") or meta.get("year")
        g["year"] = g["year"] or (int(yr) if str(yr).isdigit() else None)
        g["description"] = g["description"] or meta.get("description", "")
        g["language"] = g["language"] or meta.get("language", "")
        g["source"] = g["source"] or row.get("source", "")
        g["public_domain"] = g["public_domain"] or (row.get("public_domain", "") or "")
        if meta.get("cover") and not g["cover"]:
            g["cover"], g["cover_mime"] = meta["cover"], meta["cover_mime"]
        if fmt not in g["formats"]:
            g["formats"][fmt] = {"path": path, "size": os.path.getsize(path), "name": fn}
        if on_event and (i % 10 == 0 or i == len(found)):
            on_event({"type": "log", "text": f"Read {i} of {len(found)} files..."})
    books_ = sorted(groups.values(), key=lambda b: (b["category"].lower(), b["title"].lower()))
    used = set()
    for b in books_:
        base = ascii_slug(b["title"])
        slug, n = base, 1
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        b["slug"] = slug
        for fmt, f in b["formats"].items():
            f["url"] = f"files/{slug}/{ascii_slug(os.path.splitext(f['name'])[0], 60)}.{fmt.lower()}"
    return books_


# ------------------------------------------------------------------ the website
CSS = """
:root{--bg:#131814;--panel:#1a211c;--field:#0f1411;--border:#2d3d33;--text:#e1e8e1;--muted:#8c998f;--dim:#55625a;
--amber:#d9a02b;--blue:#5aa9ff;--green:#35c25f;--sel:#1e2f42}
@media (prefers-color-scheme: light){:root{--bg:#f3f3ee;--panel:#fff;--field:#fff;--border:#d4d4c9;--text:#1b1f1b;
--muted:#666;--dim:#999;--amber:#b87a00;--blue:#0b5fd0;--green:#1a8a38;--sel:#e3eefb}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:#0f1511;border-bottom:1px solid var(--border);position:relative;padding:14px 22px;display:flex;
align-items:center;gap:14px}
@media (prefers-color-scheme: light){header.top{background:#fff}}
header.top:after{content:"";position:absolute;left:0;bottom:-1px;width:130px;height:3px;background:var(--amber)}
.flame{width:30px;height:38px;flex:none}.brand b{display:block;font-size:20px;letter-spacing:.2px}
.brand span{color:var(--muted);font-size:12.5px}
main{max-width:1180px;margin:0 auto;padding:18px 22px 40px}
.controls{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 14px}
.controls input,.controls select{background:var(--field);color:var(--text);border:1px solid var(--border);border-radius:4px;
padding:9px 11px;font:inherit}.controls input{flex:1 1 280px}.controls input:focus,.controls select:focus{outline:1px solid var(--amber)}
.count{color:var(--muted);font-size:13px;margin:0 0 10px}
ul.grid{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:14px}
li.book a{display:block;background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;color:var(--text);
height:100%}li.book a:hover{border-color:var(--amber);text-decoration:none}
.cover{height:132px;display:flex;align-items:center;justify-content:center;background:var(--field);overflow:hidden}
.cover img{width:100%;height:100%;object-fit:cover}
.tile{font-size:34px;font-weight:700;color:var(--amber);letter-spacing:1px;opacity:.9}
.info{padding:10px 12px 12px}.t{font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;
-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em}
.a{color:var(--muted);font-size:13px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.meta{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:8px;font-size:12px;color:var(--muted)}
.chip{border:1px solid var(--border);background:var(--field);color:var(--blue);border-radius:3px;padding:0 6px;font-size:11.5px}
.cat{color:var(--dim);font-size:12px;margin-top:6px}
.empty{color:var(--muted);padding:30px 0}
footer{color:var(--dim);font-size:12.5px;text-align:center;padding:26px 0 30px;border-top:1px solid var(--border);margin-top:30px}
.back{display:inline-block;margin:4px 0 16px;font-size:14px}
.detail{display:grid;grid-template-columns:200px 1fr;gap:26px;align-items:start}
@media (max-width:640px){.detail{grid-template-columns:1fr}}
.detail .cover{height:270px;border:1px solid var(--border);border-radius:6px}.detail .tile{font-size:56px}
h1{margin:0 0 4px;font-size:26px;line-height:1.25}.by{color:var(--muted);margin:0 0 14px}
.desc{margin:0 0 18px;max-width:70ch}
.files{background:var(--panel);border:1px solid var(--border);border-radius:6px;max-width:520px}
.files div{display:flex;align-items:center;gap:12px;padding:11px 14px;border-bottom:1px solid var(--border)}
.files div:last-child{border-bottom:0}.files .f{font-weight:700;width:58px;color:var(--blue)}.files .s{color:var(--muted);flex:1}
.btn{background:var(--amber);color:#17110a;font-weight:700;border-radius:4px;padding:6px 14px}.btn:hover{text-decoration:none;filter:brightness(1.1)}
.note{color:var(--muted);font-size:13px;margin:12px 0 0;max-width:62ch}
.kv{color:var(--muted);font-size:13.5px;margin-top:16px}.kv b{color:var(--text);font-weight:600}
.pd{color:var(--green)}
"""

JS = """
(function(){
var q=document.getElementById('q'),cat=document.getElementById('cat'),fmt=document.getElementById('fmt'),
sort=document.getElementById('sort'),list=document.getElementById('list'),count=document.getElementById('count'),
empty=document.getElementById('empty');
if(!list)return;
var items=[].slice.call(list.children);
function val(el,k){return (el.getAttribute('data-'+k)||'');}
function apply(){
  var t=q.value.toLowerCase().trim(),c=cat.value,f=fmt.value,shown=0;
  items.forEach(function(li){
    var ok=(!t||(val(li,'title')+' '+val(li,'author')).indexOf(t)>=0)&&(!c||val(li,'cat')===c)&&(!f||(' '+val(li,'fmts')+' ').indexOf(' '+f+' ')>=0);
    li.style.display=ok?'':'none';if(ok)shown++;
  });
  var s=sort.value;
  var sorted=items.slice().sort(function(a,b){
    if(s==='author')return val(a,'author').localeCompare(val(b,'author'))||val(a,'title').localeCompare(val(b,'title'));
    if(s==='year')return (parseInt(val(b,'year'))||0)-(parseInt(val(a,'year'))||0);
    if(s==='size')return (parseInt(val(b,'size'))||0)-(parseInt(val(a,'size'))||0);
    if(s==='cat')return val(a,'cat').localeCompare(val(b,'cat'))||val(a,'title').localeCompare(val(b,'title'));
    return val(a,'title').localeCompare(val(b,'title'));});
  sorted.forEach(function(li){list.appendChild(li);});
  count.textContent='Showing '+shown+' of '+items.length+' books';
  empty.style.display=shown?'none':'';
}
[q,cat,fmt,sort].forEach(function(el){el.addEventListener('input',apply);el.addEventListener('change',apply);});
apply();
})();
"""

FLAME = ('<svg class="flame" viewBox="0 0 40 50" aria-hidden="true"><polygon points="20,0 33,18 30,36 20,45 10,36 7,20 14,25 14,9" '
         'fill="#d9a02b"/><polygon points="20,22 26,33 20,41 14,33" fill="#ffd36b"/></svg>')


def initials(title):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if w.lower() not in ("the", "a", "an", "of")]
    return "".join(w[0] for w in words[:2]).upper() or "B"


def _e(s):
    return html.escape(str(s), quote=True)


def _cover_html(b, prefix=""):
    if b.get("cover_url"):
        return f'<div class="cover"><img src="{prefix}{_e(b["cover_url"])}" alt="" loading="lazy"></div>'
    return f'<div class="cover"><span class="tile">{_e(initials(b["title"]))}</span></div>'


def page(title, body, prefix="", script=False):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><link rel="stylesheet" href="{prefix}style.css"></head><body>{body}'
            + (f'<script src="{prefix}app.js"></script>' if script else "") + "</body></html>")


def render_index(books_, title="Recon Library", built=None):
    built = built or datetime.date.today().isoformat()
    total = sum(f["size"] for b in books_ for f in b["formats"].values())
    cats = sorted({b["category"] for b in books_}, key=str.lower)
    fmts = [f for f in FORMAT_ORDER if any(f in b["formats"] for b in books_)]
    cards = []
    for b in books_:
        fl = [f for f in FORMAT_ORDER if f in b["formats"]]
        size = max(f["size"] for f in b["formats"].values())
        sub = " · ".join(x for x in (b["author"], str(b["year"]) if b["year"] else "") if x)
        cards.append(
            f'<li class="book" data-title="{_e(b["title"].lower())}" data-author="{_e((b["author"] or "").lower())}" '
            f'data-cat="{_e(b["category"])}" data-fmts="{" ".join(fl)}" data-year="{b["year"] or ""}" data-size="{size}">'
            f'<a href="books/{_e(b["slug"])}.html">{_cover_html(b)}<div class="info"><div class="t">{_e(b["title"])}</div>'
            f'<div class="a">{_e(sub) or "&nbsp;"}</div><div class="meta">'
            + "".join(f'<span class="chip">{f}</span>' for f in fl) + f'<span>{human_size(size)}</span></div>'
            f'<div class="cat">{_e(b["category"])}</div></div></a></li>')
    opts_c = '<option value="">All categories</option>' + "".join(f'<option>{_e(c)}</option>' for c in cats)
    opts_f = '<option value="">All formats</option>' + "".join(f"<option>{f}</option>" for f in fmts)
    body = (f'<header class="top">{FLAME}<div class="brand"><b>{_e(title)}</b><span>{len(books_)} books · {human_size(total)} · '
            f'built {built}</span></div></header><main><div class="controls">'
            f'<input id="q" type="search" placeholder="Search title or author" autocomplete="off">'
            f'<select id="cat">{opts_c}</select><select id="fmt">{opts_f}</select>'
            f'<select id="sort"><option value="title">Sort: title</option><option value="author">Sort: author</option>'
            f'<option value="year">Sort: newest</option><option value="size">Sort: largest</option>'
            f'<option value="cat">Sort: category</option></select></div>'
            f'<p class="count" id="count"></p><ul class="grid" id="list">{"".join(cards)}</ul>'
            f'<p class="empty" id="empty" style="display:none">No books match. Try fewer words.</p>'
            f'<footer>Made with {NAME} {VERSION} · {built}</footer></main>')
    return page(title, body, "", script=True)


def render_detail(b, library="Recon Library"):
    rows = []
    for f in [f for f in FORMAT_ORDER if f in b["formats"]]:
        d = b["formats"][f]
        rows.append(f'<div><span class="f">{f}</span><span class="s">{human_size(d["size"])}</span>'
                    f'<a class="btn" href="../{quote(d["url"])}">{"Open" if f in ("PDF", "TXT") else "Save"} {f}</a></div>')
    note = ""
    if any(f in b["formats"] for f in ("EPUB", "MOBI", "AZW3")):
        note = ('<p class="note">EPUB, MOBI and AZW3 files are not shown inside Kiwix. Save the file, then open it in an '
                'e-book reader app.</p>')
    sub = " · ".join(x for x in (b["author"], str(b["year"]) if b["year"] else "") if x)
    kv = [f"<b>Category:</b> {_e(b['category'])}"]
    if b.get("source"):
        kv.append(f"<b>Source:</b> {_e(b['source'])}")
    if str(b.get("public_domain", "")).strip().lower() in ("yes", "true", "1", "y"):
        kv.append('<span class="pd">Public domain</span>')
    body = (f'<header class="top">{FLAME}<div class="brand"><b>{_e(library)}</b><span>Offline library</span></div></header>'
            f'<main><a class="back" href="../index.html">&larr; All books</a><div class="detail">{_cover_html(b, "../")}<div>'
            f'<h1>{_e(b["title"])}</h1><p class="by">{_e(sub)}</p>'
            + (f'<p class="desc">{_e(b["description"])}</p>' if b.get("description") else "")
            + f'<div class="files">{"".join(rows)}</div>{note}<p class="kv">{"  ·  ".join(kv)}</p></div></div></main>')
    return page(b["title"], body, "../")


def make_icon_png(size=48):
    """A small dark tile with an amber flame, as PNG bytes (the ZIM's icon)."""
    def inside(x, y):  # flame outline in a 40x50 box scaled to the tile
        fx, fy = (x / size) * 40, (y / size) * 50
        poly = [(20, 2), (33, 18), (30, 36), (20, 45), (10, 36), (7, 20), (14, 25), (14, 9)]
        res, j = False, len(poly) - 1
        for i in range(len(poly)):
            xi, yi, xj, yj = *poly[i], *poly[j]
            if (yi > fy) != (yj > fy) and fx < (xj - xi) * (fy - yi) / (yj - yi) + xi:
                res = not res
            j = i
        return res
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw += bytes((217, 160, 43)) if inside(x, y) else bytes((19, 24, 20))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ building the ZIM
def build_books_zim(folder, out_path, title="Recon Library", on_event=None, cancel=None, max_file_mb=2000):
    """Build `out_path` from the books under `folder`. Returns dict(size, books, files, skipped)."""
    emit = on_event or (lambda e: None)
    cancel = cancel or __import__("threading").Event()
    emit({"type": "phase", "text": "Reading the books..."})
    books_ = scan_folder(folder, cancel, emit)
    if not books_:
        raise RuntimeError("No book files (PDF, EPUB, MOBI, AZW3, DJVU, TXT...) were found in that folder.")
    langs = {}
    for b in books_:
        code = LANG3.get((b["language"] or "en")[:2], "eng")
        langs[code] = langs.get(code, 0) + 1
    lang = ",".join(c for c, _ in sorted(langs.items(), key=lambda kv: -kv[1])[:3])
    part = out_path + ".part"
    w = ZimWriter(part, {"Title": title, "Description": f"{len(books_)} books with a searchable catalog",
                         "Language": lang, "Creator": NAME, "Publisher": NAME, "Date": datetime.date.today().isoformat(),
                         "Name": ascii_slug(title).replace("-", "_") or "books", "Tags": "books", "Scraper": f"{NAME} {VERSION}"})
    skipped, files, total = [], 0, sum(len(b["formats"]) for b in books_)
    try:
        w.add_entry("style.css", "", "text/css", CSS.encode("utf-8"))
        w.add_entry("app.js", "", "application/javascript", JS.encode("utf-8"))
        w.add_entry("Illustration_48x48@1", "", "image/png", make_icon_png(), ns="M")
        done = 0
        for b in books_:
            if b.get("cover"):
                ext = "png" if b["cover_mime"] == "image/png" else "jpg"
                b["cover_url"] = f"covers/{b['slug']}.{ext}"
                w.add_entry(b["cover_url"], "", b["cover_mime"], b["cover"])
            for fmt, f in list(b["formats"].items()):
                if cancel.is_set():
                    raise RuntimeError("Cancelled")
                done += 1
                if f["size"] > max_file_mb * 1024 * 1024:
                    skipped.append((f["name"], f"larger than {max_file_mb} MB"))
                    del b["formats"][fmt]
                    continue
                with open(f["path"], "rb") as fh:
                    w.add_entry(f["url"], "", MIME.get(fmt, "application/octet-stream"), fh.read())
                files += 1
                emit({"type": "build", "done": done, "total": total})
        books_ = [b for b in books_ if b["formats"]]
        for b in books_:
            w.add_entry(f"books/{b['slug']}.html", b["title"], "text/html", render_detail(b, title).encode("utf-8"))
        w.add_entry("index.html", title, "text/html", render_index(books_, title).encode("utf-8"))
        w.set_main("index.html")
        emit({"type": "phase", "text": "Writing the ZIM file..."})
        w.close()
        os.replace(part, out_path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return {"size": os.path.getsize(out_path), "books": len(books_), "files": files, "skipped": skipped}
