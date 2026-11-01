"""Turn a folder of downloaded videos into a ZIM file with a small offline video site.

Like the books catalog, the ZIM has a home page you can search and filter, a page for every video, and the files. Videos add
tags: every tag the video had on its site is a button on the home page, you can pick several (a video must have all of them),
type words that are looked for in title, channel, tags and description, narrow by channel or length, and group by channel.
Each video page has the player, chapters you can click, subtitles when there are some, the description, its tags (each one
leads back to the home page filtered by that tag) and "more like this".

Videos go in as WebM (VP9 video, Opus sound), which Kiwix and browsers play without extra codecs. Anything else
(MP4, MKV...) is converted with ffmpeg first; without ffmpeg it goes in as it is and may not play everywhere.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import bookzim
import videos
from appinfo import NAME, VERSION
from books import human_size
from bookzim import _e, ascii_slug, page
from zimwriter import ZimWriter

MIME = {".webm": "video/webm", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
AUDIO_MIME = {".webm": "audio/webm", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}
MAX_TAGS_PER_VIDEO = 20
TAGS_SHOWN = 40            # buttons shown before "More tags"
TAGS_MAX = 400             # most tags listed on the home page

VCSS = """
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}.tags button,a.tag{background:var(--field);color:var(--text);border:1px solid var(--border);
border-radius:14px;padding:3px 11px;font:inherit;font-size:13px;cursor:pointer;display:inline-block}.tags button:hover,a.tag:hover{border-color:var(--amber);text-decoration:none}
.tags button.on{background:var(--amber);color:#17110a;border-color:var(--amber);font-weight:700}.tags button i{font-style:normal;color:var(--muted);margin-left:4px;font-size:12px}
.tags button.on i{color:#4a3a14}.moretags{margin:0 0 12px}.moretags summary{cursor:pointer;color:var(--blue);font-size:14px;margin-bottom:8px}
.picked{color:var(--muted);font-size:13px;margin:0 0 8px;min-height:1.2em}.picked a{color:var(--amber);cursor:pointer}
li.vid a{display:block;background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;color:var(--text)}li.vid a:hover{border-color:var(--amber);text-decoration:none}
.thumb{position:relative;aspect-ratio:16/9;background:var(--field);display:flex;align-items:center;justify-content:center;overflow:hidden}.thumb img{width:100%;height:100%;object-fit:cover}
.dur{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.78);color:#fff;font-size:12px;padding:1px 6px;border-radius:3px}
.play{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:30px;color:#fff;opacity:0;background:rgba(0,0,0,.25);transition:opacity .15s}li.vid a:hover .play{opacity:1}
li.grp{grid-column:1/-1;font-weight:700;font-size:17px;color:var(--amber);border-bottom:1px solid var(--border);padding:10px 0 4px;margin-top:6px}
.player{width:100%;max-width:960px;background:#000;border-radius:6px;max-height:70vh}audio.player{background:transparent}
.vdetail h1{margin-top:14px}.chaps{list-style:none;margin:0 0 18px;padding:0;max-width:520px;background:var(--panel);border:1px solid var(--border);border-radius:6px}
.chaps li{padding:7px 14px;border-bottom:1px solid var(--border);cursor:pointer;display:flex;gap:12px}.chaps li:last-child{border-bottom:0}.chaps li:hover{background:var(--sel)}
.chaps b{color:var(--blue);font-weight:600;min-width:56px}.vdesc{white-space:pre-wrap;max-width:80ch;margin:14px 0 18px}
.related{margin-top:26px}.related h2{font-size:17px;margin:0 0 10px}
"""

VJS = """
(function(){
function $(i){return document.getElementById(i);}
var q=$('q'),ch=$('ch'),len=$('len'),sort=$('sort'),grp=$('grp'),list=$('list'),count=$('count'),empty=$('empty'),reset=$('reset'),picked=$('picked');
if(!list)return;
var items=[].slice.call(list.querySelectorAll('li.vid')),chips=[].slice.call(document.querySelectorAll('[data-tag]')),on={};
function val(el,k){return (el.getAttribute('data-'+k)||'');}
function num(el,k){return parseInt(val(el,k))||0;}
function lenOk(d,l){return !l||(l==='s'&&d<300)||(l==='m'&&d>=300&&d<1200)||(l==='l'&&d>=1200);}
function apply(){
  var t=q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean),c=ch.value,l=len.value,shown=0,tags=Object.keys(on);
  [].slice.call(list.querySelectorAll('li.grp')).forEach(function(g){g.parentNode.removeChild(g);});
  items.forEach(function(li){
    var hay=val(li,'title')+' '+val(li,'ch')+' '+val(li,'tags').replace(/\\|/g,' ')+' '+val(li,'text');
    var ok=t.every(function(w){return hay.indexOf(w)>=0;})&&(!c||val(li,'ch')===c)&&lenOk(num(li,'dur'),l)&&
      tags.every(function(g){return val(li,'tags').indexOf('|'+g+'|')>=0;});
    li.style.display=ok?'':'none';if(ok)shown++;
  });
  var s=sort.value,g=grp.checked;
  var sorted=items.slice().sort(function(a,b){
    var r=0;
    if(g)r=val(a,'ch').localeCompare(val(b,'ch'));
    if(!r){
      if(s==='new')r=num(b,'date')-num(a,'date');
      else if(s==='old')r=num(a,'date')-num(b,'date');
      else if(s==='long')r=num(b,'dur')-num(a,'dur');
      else if(s==='short')r=num(a,'dur')-num(b,'dur');
      else if(s==='views')r=num(b,'views')-num(a,'views');
      else if(s==='ch')r=val(a,'ch').localeCompare(val(b,'ch'));
    }
    return r||val(a,'title').localeCompare(val(b,'title'));});
  var last=null;
  sorted.forEach(function(li){
    if(g&&li.style.display!=='none'&&val(li,'ch')!==last){last=val(li,'ch');var h=document.createElement('li');h.className='grp';h.textContent=li.getAttribute('data-chname')||'Unknown channel';list.appendChild(h);}
    list.appendChild(li);});
  count.textContent='Showing '+shown+' of '+items.length+' videos';
  empty.style.display=shown?'none':'';
  chips.forEach(function(b){b.classList.toggle('on',!!on[b.getAttribute('data-tag')]);});
  picked.innerHTML=tags.length?('Tags: '+tags.map(function(x){return '<b>'+x.replace(/</g,'&lt;')+'</b>';}).join(' + ')+' &nbsp; <a id="untag">clear tags</a>'):'';
  var u=$('untag');if(u)u.onclick=function(){on={};apply();};
  reset.disabled=!(q.value||c||l||tags.length||g);
}
chips.forEach(function(b){b.addEventListener('click',function(){var g=b.getAttribute('data-tag');if(on[g])delete on[g];else on[g]=1;apply();});});
[q,ch,len,sort,grp].forEach(function(el){el.addEventListener('input',apply);el.addEventListener('change',apply);});
reset.addEventListener('click',function(){q.value='';ch.value='';len.value='';grp.checked=false;on={};apply();});
function fromHash(){on={};
(location.hash||'').replace(/^#/,'').split('&').forEach(function(p){var kv=p.split('=');
  try{var v=decodeURIComponent(kv[1]||'');if(kv[0]==='t'&&v)on[v.toLowerCase()]=1;else if(kv[0]==='q')q.value=v;else if(kv[0]==='c')ch.value=v;}catch(e){}});}
window.addEventListener('hashchange',function(){fromHash();apply();});
fromHash();
apply();
})();
"""

PJS = """
(function(){var v=document.getElementById('pl');if(!v)return;
[].slice.call(document.querySelectorAll('[data-t]')).forEach(function(li){li.addEventListener('click',function(){
v.currentTime=parseFloat(li.getAttribute('data-t'));v.play();v.scrollIntoView({behavior:'smooth',block:'nearest'});});});
var m=/t=(\\d+)/.exec(location.hash);if(m){v.addEventListener('loadedmetadata',function(){v.currentTime=parseInt(m[1],10);},{once:true});}
})();
"""


# ------------------------------------------------------------------ reading the downloads
def clean_tags(info):
    """Lower-case, de-duplicated tags (the site's tags first, then its categories), without silly long ones."""
    out, seen = [], set()
    for t in list(info.get("tags") or []) + list(info.get("categories") or []):
        t = re.sub(r"\s+", " ", str(t)).strip().lower().strip("#")
        if 2 <= len(t) <= 40 and t not in seen and "|" not in t:
            seen.add(t)
            out.append(t)
    return out[:MAX_TAGS_PER_VIDEO]


def fmt_date(d):
    d = str(d or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if re.fullmatch(r"\d{8}", d) else ""


def is_audio(v):
    ext = os.path.splitext(v["file"])[1].lower()
    return ext in (".opus", ".m4a", ".mp3") or (v["info"].get("vcodec") == "none")


def prepare(folder, only_ids=None):
    """The downloaded videos as catalog records (no files copied yet)."""
    recs, used = [], set()
    for v in videos.scan_folder(folder):
        if only_ids is not None and v["id"] not in only_ids:
            continue
        info = v["info"]
        base = ascii_slug(v["title"], 50) + "-" + ascii_slug(v["id"], 16)
        slug, n = base, 1
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        dur = info.get("duration")
        recs.append({"id": v["id"], "slug": slug, "title": v["title"], "channel": v["channel"], "src": v["file"], "audio": is_audio(v),
                     "thumb_src": v["thumb"], "subs": [(re.match(re.escape(v["id"]) + r"\.(.+)\.vtt$", os.path.basename(p)).group(1), p)
                                                       for p in v["subs"] if re.match(re.escape(v["id"]) + r"\.(.+)\.vtt$", os.path.basename(p))],
                     "tags": clean_tags(info), "description": (info.get("description") or "").strip()[:6000],
                     "date": fmt_date(info.get("upload_date")), "duration": int(dur) if isinstance(dur, (int, float)) else 0,
                     "height": info.get("height") if isinstance(info.get("height"), int) else None,
                     "views": info.get("view_count") if isinstance(info.get("view_count"), int) else 0,
                     "chapters": [c for c in (info.get("chapters") or []) if isinstance(c, dict) and c.get("title") is not None
                                  and isinstance(c.get("start_time"), (int, float))],
                     "url": info.get("webpage_url") or ""})
    recs.sort(key=lambda r: (r["channel"].lower(), r["title"].lower()))
    return recs


def tag_counts(recs):
    counts = {}
    for r in recs:
        for t in r["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


# ------------------------------------------------------------------ converting to WebM
SHRINK_CHOICES = {"Keep as downloaded": None, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


def probe_height(path, ffmpeg):
    """Picture height of a video file, read with ffmpeg (None when it cannot be told)."""
    lines = []
    videos.stream([ffmpeg, "-hide_banner", "-i", path], lines.append)
    for l in lines:
        m = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", l)
        if m:
            return int(m.group(2))
    return None


def to_webm(src, dst, ffmpeg, cancel=None, audio_only=False, height=None):
    """Convert one file to WebM (VP9 + Opus) with ffmpeg. Raises RuntimeError with a plain message on failure."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if height and not audio_only:                 # never makes a small picture bigger
        cmd += ["-vf", f"scale=-2:min(ih\\,{int(height)})"]
    cmd += (["-vn"] if audio_only else ["-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1"])
    cmd += ["-c:a", "libopus", "-b:a", "64k", dst]
    lines = []
    code = videos.stream(cmd, lines.append, cancel)
    if cancel is not None and cancel.is_set():
        raise RuntimeError("Cancelled")
    if code != 0 or not os.path.isfile(dst):
        raise RuntimeError("ffmpeg could not convert " + os.path.basename(src) + (": " + lines[-1] if lines else "."))


# ------------------------------------------------------------------ the website
def _thumb_html(r, prefix=""):
    inner = (f'<img src="{prefix}{_e(r["thumb_url"])}" alt="" loading="lazy">' if r.get("thumb_url")
             else f'<span class="tile">{_e(bookzim.initials(r["title"]) if not r["audio"] else "♪")}</span>')
    dur = f'<span class="dur">{videos.fmt_duration(r["duration"])}</span>' if r["duration"] else ""
    return f'<div class="thumb">{inner}{dur}<span class="play">&#9654;</span></div>'


def render_index(recs, title="Recon Videos", built=None):
    built = built or datetime.date.today().isoformat()
    total = sum(r.get("size", 0) for r in recs)
    chans = sorted({r["channel"] for r in recs if r["channel"]}, key=str.lower)
    cards = []
    for r in recs:
        sub = " · ".join(x for x in (r["channel"], r["date"][:4]) if x)
        text = re.sub(r"\s+", " ", r["description"][:500]).lower()
        cards.append(
            f'<li class="vid" data-title="{_e(r["title"].lower())}" data-ch="{_e(r["channel"].lower())}" data-chname="{_e(r["channel"])}" '
            f'data-tags="|{_e("|".join(r["tags"]))}|" data-text="{_e(text)}" data-dur="{r["duration"]}" '
            f'data-date="{r["date"].replace("-", "")}" data-views="{r["views"]}">'
            f'<a href="videos/{_e(r["slug"])}.html">{_thumb_html(r)}<div class="info"><div class="t">{_e(r["title"])}</div>'
            f'<div class="a">{_e(sub) or "&nbsp;"}</div><div class="meta"><span>{human_size(r.get("size", 0))}</span>'
            + "".join(f'<span class="chip">{_e(t)}</span>' for t in r["tags"][:3]) + '</div></div></a></li>')
    tc = tag_counts(recs)[:TAGS_MAX]

    def chip(t, n):
        return f'<button type="button" data-tag="{_e(t)}">{_e(t)}<i>{n}</i></button>'
    tags_html = ""
    if tc:
        tags_html = f'<div class="tags">{"".join(chip(t, n) for t, n in tc[:TAGS_SHOWN])}</div>'
        if len(tc) > TAGS_SHOWN:
            tags_html += (f'<details class="moretags"><summary>More tags ({len(tc) - TAGS_SHOWN})</summary>'
                          f'<div class="tags">{"".join(chip(t, n) for t, n in tc[TAGS_SHOWN:])}</div></details>')
    opts_c = '<option value="">All channels</option>' + "".join(
        f'<option value="{_e(c.lower())}">{_e(c)} ({sum(1 for r in recs if r["channel"] == c)})</option>' for c in chans)
    body = (f'<header class="top">{bookzim.FLAME}<div class="brand"><b>{_e(title)}</b><span>{len(recs)} videos · {human_size(total)} · '
            f'built {built}</span></div></header><main><div class="controls">'
            f'<input id="q" type="search" placeholder="Search title, channel, tags or description" autocomplete="off">'
            f'<select id="ch">{opts_c}</select>'
            f'<select id="len"><option value="">Any length</option><option value="s">Under 5 min</option>'
            f'<option value="m">5 - 20 min</option><option value="l">Over 20 min</option></select>'
            f'<select id="sort"><option value="title">Title A-Z</option><option value="new">Newest</option><option value="old">Oldest</option>'
            f'<option value="long">Longest</option><option value="short">Shortest</option><option value="views">Most viewed</option>'
            f'<option value="ch">Channel</option></select>'
            f'<label class="chk"><input type="checkbox" id="grp"> Group by channel</label>'
            f'<button type="button" id="reset" class="rst">Clear all</button></div>'
            f'{tags_html}<p class="picked" id="picked"></p>'
            f'<p class="count" id="count"></p><ul class="grid" id="list">{"".join(cards)}</ul>'
            f'<p class="empty" id="empty" style="display:none">No videos match. Try fewer words or clear some tags.</p>'
            f'<footer>Made with {NAME} {VERSION} · {built}</footer></main>')
    return page(title, body, "", script=True)


def _related(r, recs, n=6):
    mine = set(r["tags"])
    if not mine:
        return []
    scored = sorted(((len(mine & set(o["tags"])), o) for o in recs if o is not r), key=lambda x: (-x[0], x[1]["title"].lower()))
    return [o for s, o in scored if s > 0][:n]


def render_detail(r, recs, library="Recon Videos"):
    src = "../" + r["url_path"]
    if r["audio"]:
        player = f'<audio id="pl" class="player" controls preload="metadata" src="{_e(src)}"></audio>'
    else:
        tracks = "".join(f'<track kind="subtitles" src="../{_e(u)}" srclang="{_e(lang.split("-")[0])}" label="{_e(lang)}"{" default" if i == 0 else ""}>'
                         for i, (lang, u) in enumerate(r.get("sub_urls", [])))
        poster = f' poster="../{_e(r["thumb_url"])}"' if r.get("thumb_url") else ""
        player = (f'<video id="pl" class="player" controls preload="metadata"{poster}><source src="{_e(src)}" type="{_e(r["mime"])}">'
                  f'{tracks}Your browser cannot play this video. Use Save video below.</video>')
    chaps = ""
    if r["chapters"]:
        chaps = '<ul class="chaps">' + "".join(
            f'<li data-t="{c["start_time"]}"><b>{videos.fmt_duration(c["start_time"])}</b><span>{_e(c["title"])}</span></li>' for c in r["chapters"]) + "</ul>"
    tags = "".join(f'<a class="tag" href="../index.html#t={_e(t.replace(" ", "%20"))}">{_e(t)}</a>' for t in r["tags"])
    sub = " · ".join(x for x in (r["channel"], r["date"], videos.fmt_duration(r["duration"]), f'{r["views"]:,} views' if r["views"] else "") if x)
    rel = _related(r, recs)
    related = ""
    if rel:
        related = ('<div class="related"><h2>More like this</h2><ul class="grid">' + "".join(
            f'<li class="vid"><a href="{_e(o["slug"])}.html">{_thumb_html(o, "../")}<div class="info"><div class="t">{_e(o["title"])}</div>'
            f'<div class="a">{_e(o["channel"]) or "&nbsp;"}</div></div></a></li>' for o in rel) + "</ul></div>")
    body = (f'<header class="top">{bookzim.FLAME}<div class="brand"><b>{_e(library)}</b><span>Offline videos</span></div></header>'
            f'<main class="vdetail"><a class="back" href="../index.html">&larr; All videos</a>{player}<h1>{_e(r["title"])}</h1>'
            f'<p class="by">{_e(sub)}</p>{chaps}<div class="tags">{tags}</div>'
            + (f'<p class="vdesc">{_e(r["description"])}</p>' if r["description"] else "")
            + f'<p><a class="btn" href="{_e(src)}" download>Save video ({human_size(r.get("size", 0))})</a></p>'
            + (f'<p class="kv"><b>Original address:</b> {_e(r["url"])}</p>' if r["url"] else "") + f'{related}</main>')
    h = page(r["title"], body, "../")
    return h.replace("</body>", f"<script>{PJS}</script></body>", 1)


def catalog_dict(recs, title):
    """Every video in the ZIM, with its tags, for other tools (and a later 'take videos out of a ZIM')."""
    return {"app": NAME, "version": VERSION, "kind": "videos", "title": title, "built": datetime.date.today().isoformat(),
            "tags": [{"tag": t, "count": n} for t, n in tag_counts(recs)],
            "videos": [{"id": r["id"], "title": r["title"], "channel": r["channel"], "date": r["date"], "duration": r["duration"],
                        "tags": r["tags"], "url": r["url_path"], "size": r.get("size", 0), "page": f"videos/{r['slug']}.html",
                        "source": r["url"]} for r in recs]}


# ------------------------------------------------------------------ building the ZIM
def build_videos_zim(folder, out_path, title="Recon Videos", on_event=None, cancel=None, max_file_mb=2000, ffmpeg=None,
                     convert=True, only_ids=None, shrink=None):
    """Build `out_path` from the videos downloaded into `folder`. Returns dict(size, videos, converted, skipped, tags)."""
    emit = on_event or (lambda e: None)
    cancel = cancel or threading.Event()
    emit({"type": "phase", "text": "Reading the videos..."})
    recs = prepare(folder, only_ids)
    if not recs:
        raise RuntimeError("No downloaded videos were found in that folder. Use the Get videos part first.")
    work = tempfile.mkdtemp(prefix="reconvid-")
    part = out_path + ".part"
    skipped, converted, notes = [], 0, []
    langs = {"eng": 1}
    w = ZimWriter(part, {"Title": title, "Description": f"{len(recs)} videos with tags you can search", "Language": "eng",
                         "Creator": NAME, "Publisher": NAME, "Date": datetime.date.today().isoformat(),
                         "Name": ascii_slug(title).replace("-", "_") or "videos", "Tags": "videos", "Scraper": f"{NAME} {VERSION}"})
    try:
        w.add_entry("style.css", "", "text/css", (bookzim.CSS + VCSS).encode("utf-8"))
        w.add_entry("app.js", "", "application/javascript", VJS.encode("utf-8"))
        w.add_entry("Illustration_48x48@1", "", "image/png", bookzim.make_icon_png(), ns="M")
        kept = []
        for i, r in enumerate(recs, 1):
            if cancel.is_set():
                raise RuntimeError("Cancelled")
            emit({"type": "phase", "text": f"Adding video {i} of {len(recs)}: {r['title'][:50]}"})
            src = r["src"]
            ext = os.path.splitext(src)[1].lower()
            if shrink and not r["audio"] and r["height"] is None and ffmpeg:
                r["height"] = probe_height(src, ffmpeg)
            too_big = bool(shrink) and not r["audio"] and (r["height"] is None or r["height"] > shrink)
            if ffmpeg and not r["audio"] and ((convert and ext != ".webm") or too_big):
                dst = os.path.join(work, r["id"] + ".webm")
                emit({"type": "phase", "text": f"{'Shrinking' if too_big else 'Converting'} ({i} of {len(recs)}): {r['title'][:40]}"})
                try:
                    to_webm(src, dst, ffmpeg, cancel, height=shrink if too_big else None)
                    src, ext = dst, ".webm"
                    converted += 1
                except RuntimeError as e:
                    if str(e) == "Cancelled":
                        raise
                    notes.append((r["title"], str(e)))
            elif too_big and not ffmpeg:
                notes.append((r["title"], f"Kept at its original size: ffmpeg is needed to shrink it to {shrink}p."))
            elif ext != ".webm" and not r["audio"] and not ffmpeg:
                notes.append((r["title"], f"Added as {ext[1:].upper()}, which some players cannot play. Install ffmpeg to convert to WebM."))
            size = os.path.getsize(src)
            if size > max_file_mb * 1024 * 1024:
                skipped.append((r["title"], f"larger than {max_file_mb} MB"))
                continue
            r["size"] = size
            r["mime"] = (AUDIO_MIME if r["audio"] else MIME).get(ext, "application/octet-stream")
            r["url_path"] = f"v/{r['slug']}{ext}"
            w.add_file(r["url_path"], "", r["mime"], src)
            if r["thumb_src"]:
                r["thumb_url"] = f"thumbs/{r['slug']}.jpg"
                with open(r["thumb_src"], "rb") as fh:
                    w.add_entry(r["thumb_url"], "", "image/jpeg", fh.read())
            r["sub_urls"] = []
            for lang, p in r["subs"]:
                u = f"subs/{r['slug']}.{ascii_slug(lang, 12)}.vtt"
                with open(p, "rb") as fh:
                    w.add_entry(u, "", "text/vtt; charset=utf-8", fh.read())
                r["sub_urls"].append((lang, u))
            kept.append(r)
            emit({"type": "build", "done": i, "total": len(recs)})
            if src != r["src"]:
                try:
                    os.remove(src)
                except OSError:
                    pass
        if not kept:
            raise RuntimeError("None of the videos could be added" + (f" ({skipped[0][1]})." if skipped else "."))
        for r in kept:
            w.add_entry(f"videos/{r['slug']}.html", r["title"], "text/html", render_detail(r, kept, title).encode("utf-8"))
        w.add_entry("catalog.json", "", "application/json", json.dumps(catalog_dict(kept, title), ensure_ascii=False).encode("utf-8"))
        w.add_entry("index.html", title, "text/html", render_index(kept, title).encode("utf-8"))
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
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"size": os.path.getsize(out_path), "videos": len(kept), "converted": converted, "skipped": skipped, "notes": notes,
            "tags": len(tag_counts(kept))}


# ------------------------------------------------------------------ the test ZIM
def make_sample_video(path, ffmpeg, seconds=4):
    """A tiny made-up WebM (moving test picture and a tone), to check that a ZIM with video plays."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x180:rate=15",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libvpx-vp9", "-crf", "40", "-b:v", "0", "-deadline",
           "realtime", "-cpu-used", "8", "-c:a", "libopus", "-b:a", "32k", "-shortest", path]
    lines = []
    code = videos.stream(cmd, lines.append)
    if code != 0 or not os.path.isfile(path):
        raise RuntimeError("ffmpeg could not make the sample video" + (": " + lines[-1] if lines else "."))


def build_test_zim(out_path, ffmpeg):
    """Make a small ZIM of three sample videos so you can open it in Kiwix and see that video plays and the tags work."""
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to make the test ZIM. Use Settings > Video tools > Get ffmpeg.")
    root = tempfile.mkdtemp(prefix="reconvidtest-")
    try:
        folder = os.path.join(root, "videos")
        os.makedirs(folder)
        base = os.path.join(root, "sample.webm")          # kept out of the folder that gets scanned
        make_sample_video(base, ffmpeg)
        samples = [("sample1", "Test video one", "Test channel", ["water", "test", "sample"]),
                   ("sample2", "Test video two", "Test channel", ["fire", "test"]),
                   ("sample3", "Another test video", "Other channel", ["water", "shelter"])]
        for vid, t, ch, tags in samples:
            shutil.copyfile(base, os.path.join(folder, vid + ".webm"))
            with open(os.path.join(folder, vid + ".info.json"), "w", encoding="utf-8") as f:
                json.dump({"id": vid, "title": t, "channel": ch, "duration": 4, "tags": tags, "upload_date": "20240131",
                           "description": "A short sample made by " + NAME + " to check that videos play.",
                           "chapters": [{"title": "Start", "start_time": 0}, {"title": "Second half", "start_time": 2}]}, f)
        return build_videos_zim(folder, out_path, "Recon test videos", ffmpeg=ffmpeg)
    finally:
        shutil.rmtree(root, ignore_errors=True)
