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
import bookread
import flame

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
padding:9px 11px;font:inherit}.controls input[type=search]{flex:2 1 220px}
.chk{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:14px;padding:0 4px}.controls .chk input{flex:none;width:auto;padding:0;accent-color:var(--amber)}.chk{white-space:nowrap}
.sp{flex:1 1 20px}.rst{background:transparent;color:var(--amber);border:1px solid var(--border);border-radius:4px;padding:8px 12px;font:inherit;cursor:pointer}
.controls input{flex:1 1 220px}
.fp{position:relative}.fp summary{list-style:none;cursor:pointer;background:var(--field);border:1px solid var(--border);border-radius:4px;padding:9px 14px;color:var(--text);user-select:none}
.fp summary::-webkit-details-marker{display:none}.fp summary:after{content:' \\25BE';color:var(--muted)}.fp[open] summary{border-color:var(--amber)}
.panel{position:absolute;right:0;top:calc(100% + 6px);z-index:5;width:290px;background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:14px;display:flex;flex-direction:column;gap:12px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.panel label{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:13px}.panel label.chk{flex-direction:row;font-size:14px}
.panel select{width:100%}.controls input:focus,.controls select:focus{outline:1px solid var(--amber)}
.count{color:var(--muted);font-size:13px;margin:0 0 10px}
ul.grid{list-style:none;margin:0;padding:0;display:grid;align-items:start;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:14px}
li.book a{display:block;height:auto!important;background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;color:var(--text);
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
.detail.nocover{grid-template-columns:1fr}
.detail{display:grid;grid-template-columns:200px 1fr;gap:26px;align-items:start}
@media (max-width:640px){.detail{grid-template-columns:1fr}}
.detail .cover{height:270px;border:1px solid var(--border);border-radius:6px}.detail .tile{font-size:56px}
h1{margin:0 0 4px;font-size:26px;line-height:1.25}.by{color:var(--muted);margin:0 0 14px}
.desc{margin:0 0 18px;max-width:70ch}
.files{background:var(--panel);border:1px solid var(--border);border-radius:6px;max-width:520px}
.files div{display:flex;align-items:center;gap:12px;padding:11px 14px;border-bottom:1px solid var(--border)}
.files div:last-child{border-bottom:0}.files .f{font-weight:700;width:58px;color:var(--blue)}.files .s{color:var(--muted);flex:1}
.btn{background:var(--amber);color:#17110a;font-weight:700;border-radius:4px;padding:6px 14px}.btn.ghost{background:transparent;color:var(--blue);border:1px solid var(--border);font-weight:600}
.files .btn+.btn{margin-left:-4px}
.btn:hover{text-decoration:none;filter:brightness(1.1)}
.note{color:var(--muted);font-size:13px;margin:12px 0 0;max-width:62ch}
.kv{color:var(--muted);font-size:13.5px;margin-top:16px}.kv b{color:var(--text);font-weight:600}
.pd{color:var(--green)}
body.reader{height:100vh;display:flex;flex-direction:column;overflow:hidden}
.rbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;background:#0f1511;border-bottom:1px solid var(--border)}
@media (prefers-color-scheme: light){.rbar{background:#fff}}
.rbar button,.rbar select{background:var(--field);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:6px 11px;font:inherit;cursor:pointer}
.rbar button:hover{border-color:var(--amber)}.rbar select{flex:1 1 160px;min-width:0}.rbar .ttl{font-weight:600;max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#fr{flex:1;width:100%;border:0;background:#fff}
"""

JS = """
(function(){
function $(i){return document.getElementById(i);}
var q=$('q'),cat=$('cat'),fmt=$('fmt'),lang=$('lang'),size=$('size'),pd=$('pd'),sort=$('sort'),dir=$('dir'),
list=$('list'),count=$('count'),empty=$('empty'),reset=$('reset');
if(!list)return;
var items=[].slice.call(list.children);
function val(el,k){return (el.getAttribute('data-'+k)||'');}
function num(el,k){return parseInt(val(el,k))||0;}
var MB=1048576;
function sizeOk(n,s){return !s||(s==='s'&&n<10*MB)||(s==='m'&&n>=10*MB&&n<100*MB)||(s==='l'&&n>=100*MB);}
function apply(){
  var t=q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean),c=cat.value,f=fmt.value,l=lang?lang.value:'',
  sz=size.value,p=pd.checked,shown=0;
  items.forEach(function(li){
    var hay=val(li,'title')+' '+val(li,'author')+' '+val(li,'cat');
    var ok=t.every(function(w){return hay.indexOf(w)>=0;})&&(!c||val(li,'cat')===c)&&
      (!f||(' '+val(li,'fmts')+' ').indexOf(' '+f+' ')>=0)&&(!l||val(li,'lang')===l)&&sizeOk(num(li,'size'),sz)&&(!p||val(li,'pd')==='1');
    li.style.display=ok?'':'none';if(ok)shown++;
  });
  var s=sort.value,m=dir.value==='desc'?-1:1;
  var sorted=items.slice().sort(function(a,b){
    var r;
    if(s==='author')r=val(a,'author').localeCompare(val(b,'author'));
    else if(s==='year')r=num(a,'year')-num(b,'year');
    else if(s==='size')r=num(a,'size')-num(b,'size');
    else if(s==='cat')r=val(a,'cat').localeCompare(val(b,'cat'));
    else r=0;
    return (r||val(a,'title').localeCompare(val(b,'title')))*(r?m:m);});
  sorted.forEach(function(li){list.appendChild(li);});
  count.textContent='Showing '+shown+' of '+items.length+' books';
  empty.style.display=shown?'none':'';
  var on=q.value||c||f||l||sz||p;reset.disabled=!on;
}
[q,cat,fmt,lang,size,pd,sort,dir].forEach(function(el){if(!el)return;el.addEventListener('input',apply);el.addEventListener('change',apply);});
reset.addEventListener('click',function(){q.value='';cat.value='';fmt.value='';if(lang)lang.value='';size.value='';pd.checked=false;apply();});
apply();
})();
"""

FLAME = flame.svg()


LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
              "ru": "Russian", "zh": "Chinese", "ar": "Arabic", "nl": "Dutch", "ja": "Japanese", "ko": "Korean",
              "pl": "Polish", "sv": "Swedish", "tr": "Turkish", "hi": "Hindi"}


def lang_of(b):
    return ((b.get("language") or "")[:2]).lower()


def is_pd(b):
    return str(b.get("public_domain", "")).strip().lower() in ("yes", "true", "1", "y")


def initials(title):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if w.lower() not in ("the", "a", "an", "of")]
    return "".join(w[0] for w in words[:2]).upper() or "B"


def _e(s):
    return html.escape(str(s), quote=True)


def _cover_html(b, prefix=""):
    if b.get("cover_url"):
        return f'<div class="cover"><img src="{prefix}{_e(b["cover_url"])}" alt="" loading="lazy"></div>'
    return ""


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
            f'data-cat="{_e(b["category"])}" data-fmts="{" ".join(fl)}" data-year="{b["year"] or ""}" data-size="{size}" data-lang="{_e(lang_of(b))}" data-pd="{"1" if is_pd(b) else ""}">'
            f'<a href="books/{_e(b["slug"])}.html">{_cover_html(b)}<div class="info"><div class="t">{_e(b["title"])}</div>'
            f'<div class="a">{_e(sub) or "&nbsp;"}</div><div class="meta">'
            + "".join(f'<span class="chip">{f}</span>' for f in fl) + f'<span>{human_size(size)}</span></div>'
            f'<div class="cat">{_e(b["category"])}</div></div></a></li>')
    counts = {c: sum(1 for b in books_ if b["category"] == c) for c in cats}
    opts_c = f'<option value="">All categories ({len(books_)})</option>' + "".join(
        f'<option value="{_e(c)}">{_e(c)} ({counts[c]})</option>' for c in cats)
    opts_f = '<option value="">All formats</option>' + "".join(f"<option>{f}</option>" for f in fmts)
    any_pd = any(is_pd(b) for b in books_)
    langs = sorted({lang_of(b) for b in books_ if lang_of(b)})
    lang_row = ('<label>Language<select id="lang"><option value="">All languages</option>' + "".join(
        f'<option value="{c}">{_e(LANG_NAMES.get(c, c))}</option>' for c in langs) + "</select></label>") if len(langs) > 1 else ""
    body = (f'<header class="top">{FLAME}<div class="brand"><b>{_e(title)}</b><span>{len(books_)} books · {human_size(total)} · '
            f'built {built}</span></div></header><main><div class="controls">'
            f'<input id="q" type="search" placeholder="Search title, author or category" autocomplete="off">'
            f'<select id="cat">{opts_c}</select><select id="fmt">{opts_f}</select>'
            f'<select id="size"><option value="">Any size</option><option value="s">Under 10 MB</option>'
            f'<option value="m">10 - 100 MB</option><option value="l">Over 100 MB</option></select>'
            f'<details class="fp"><summary>Filter &amp; sort</summary><div class="panel">'
            f'{lang_row}'
            f'<label class="chk"><input type="checkbox" id="pd"{"" if any_pd else " disabled"}> Public domain only</label>'
            f'<label>Sort by<select id="sort"><option value="title">Title</option><option value="author">Author</option>'
            f'<option value="year">Year</option><option value="size">Size</option><option value="cat">Category</option></select></label>'
            f'<label>Order<select id="dir"><option value="asc">Ascending (A-Z, oldest, smallest)</option>'
            f'<option value="desc">Descending (Z-A, newest, largest)</option></select></label>'
            f'<button type="button" id="reset" class="rst">Clear all filters</button></div></details></div>'
            f'<p class="count" id="count"></p><ul class="grid" id="list">{"".join(cards)}</ul>'
            f'<p class="empty" id="empty" style="display:none">No books match. Try fewer words.</p>'
            f'<footer>Made with {NAME} {VERSION} · {built}</footer></main>')
    return page(title, body, "", script=True)


def render_detail(b, library="Recon Library"):
    rows = []
    for f in [f for f in FORMAT_ORDER if f in b["formats"]]:
        d = b["formats"][f]
        if d.get("reader"):
            btns = (f'<a class="btn" href="../{quote(d["reader"])}">Read</a>'
                    f'<a class="btn ghost" href="../{quote(d["url"])}">Save</a>')
        elif f == "PDF":
            btns = f'<a class="btn" href="../{quote(d["url"])}">Open PDF</a>'
        else:
            btns = f'<a class="btn" href="../{quote(d["url"])}">Save {f}</a>'
        rows.append(f'<div><span class="f">{f}</span><span class="s">{human_size(d["size"])}</span>{btns}</div>')
    note = ""
    unread = [f for f, d in b["formats"].items() if f != "PDF" and not d.get("reader")]
    if unread:
        note = (f'<p class="note">{", ".join(unread)} cannot be read inside Kiwix. Use Save, then open the file in an '
                'e-book reader app.</p>')
    sub = " · ".join(x for x in (b["author"], str(b["year"]) if b["year"] else "") if x)
    kv = [f"<b>Category:</b> {_e(b['category'])}"]
    if b.get("source"):
        kv.append(f"<b>Source:</b> {_e(b['source'])}")
    if str(b.get("public_domain", "")).strip().lower() in ("yes", "true", "1", "y"):
        kv.append('<span class="pd">Public domain</span>')
    body = (f'<header class="top">{FLAME}<div class="brand"><b>{_e(library)}</b><span>Offline library</span></div></header>'
            f'<main><a class="back" href="../index.html">&larr; All books</a><div class="detail{"" if b.get("cover_url") else " nocover"}">{_cover_html(b, "../")}<div>'
            f'<h1>{_e(b["title"])}</h1><p class="by">{_e(sub)}</p>'
            + (f'<p class="desc">{_e(b["description"])}</p>' if b.get("description") else "")
            + f'<div class="files">{"".join(rows)}</div>{note}<p class="kv">{"  ·  ".join(kv)}</p></div></div></main>')
    return page(b["title"], body, "../")


def render_reader(b, fmt, rd, folder):
    spine = [[quote(f"{folder}/{p}"), t] for p, t in rd.spine]
    js = (bookread.READER_JS.replace("%SPINE%", json.dumps(spine)).replace("%KEY%", json.dumps(f"{b['slug']}-{fmt}"))
          .replace("%%", "%"))
    body = (f'<div class="rbar"><a href="../books/{_e(b["slug"])}.html">&larr; Book</a><span class="ttl">{_e(b["title"])}</span>'
            '<button id="prev" title="Previous (left arrow)">&lsaquo; Prev</button><select id="ch" aria-label="Chapter"></select>'
            '<button id="next" title="Next (right arrow)">Next &rsaquo;</button><button id="smaller" title="Smaller text">A-</button>'
            '<button id="bigger" title="Bigger text">A+</button><button id="theme" title="Page color">Light/Dark</button></div>'
            '<iframe id="fr" title="Book"></iframe>')
    h = page(b["title"], body, "../")
    return h.replace("<body>", '<body class="reader">', 1).replace("</body>", f"<script>{js}</script></body>").encode("utf-8")


def catalog_dict(books_, title):
    """A list of every book and file in the ZIM, so Cindrix Recon can take books back out later (Settings > ZIM library)."""
    return {"app": NAME, "version": VERSION, "title": title, "built": datetime.date.today().isoformat(),
            "books": [{"title": b["title"], "author": b["author"], "year": b["year"], "category": b["category"],
                       "language": b["language"], "source": b.get("source", ""), "public_domain": is_pd(b),
                       "formats": {f: {"url": d["url"], "size": d["size"], "name": d["name"]} for f, d in b["formats"].items()}}
                      for b in books_]}


def make_icon_png(size=48):
    """A small dark tile with the amber flame, as PNG bytes (the ZIM's icon)."""
    def poly(segs):
        pts = flame.outline(segs, size * 0.17, size * 0.07, size * 0.66, size * 0.86, 10)
        return list(zip(pts[0::2], pts[1::2]))
    outer, inner = poly(flame.OUTER), poly(flame.INNER)

    def inside(pts, fx, fy):
        res, j = False, len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > fy) != (yj > fy) and fx < (xj - xi) * (fy - yi) / (yj - yi) + xi:
                res = not res
            j = i
        return res
    bg, amber, light = (19, 24, 20), (217, 160, 43), (255, 211, 107)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            acc = [0, 0, 0]
            for sy in (0.17, 0.5, 0.83):
                for sx in (0.17, 0.5, 0.83):
                    col = light if inside(inner, x + sx, y + sy) else amber if inside(outer, x + sx, y + sy) else bg
                    for k in range(3):
                        acc[k] += col[k]
            raw += bytes(v // 9 for v in acc)

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
    skipped, files, readers, no_reader, total = [], 0, 0, [],  sum(len(b["formats"]) for b in books_)
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
                if fmt in bookread.READABLE:
                    try:
                        rd = bookread.make_readable(fmt, f["path"])
                        folder = f"read/{b['slug']}-{fmt.lower()}"
                        for pth, (mm, data) in rd.files.items():
                            w.add_entry(f"{folder}/{pth}", "", mm, data)
                        w.add_entry(folder + ".html", b["title"], "text/html", render_reader(b, fmt, rd, b["slug"] + "-" + fmt.lower()))
                        f["reader"] = folder + ".html"
                        readers += 1
                    except bookread.Unreadable as e:
                        no_reader.append((f["name"], str(e)))
                emit({"type": "build", "done": done, "total": total})
        books_ = [b for b in books_ if b["formats"]]
        for b in books_:
            w.add_entry(f"books/{b['slug']}.html", b["title"], "text/html", render_detail(b, title).encode("utf-8"))
        w.add_entry("catalog.json", "", "application/json", json.dumps(catalog_dict(books_, title), ensure_ascii=False).encode("utf-8"))
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
    return {"size": os.path.getsize(out_path), "books": len(books_), "files": files, "skipped": skipped,
            "readers": readers, "no_reader": no_reader}
