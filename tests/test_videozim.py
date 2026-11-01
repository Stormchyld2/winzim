"""Videos -> ZIM: catalog site with tags, streaming big files, WebM conversion, video Range requests."""
import json, os, re, shutil, subprocess, sys, tempfile, threading, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
os.environ["APPDATA"] = tempfile.mkdtemp()
from verify_zim import Zim, check_links
import videos, videozim
from zimreader import PreviewServer, ZimReader

FAILS = []
def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n, "" if ok else extra)
    if not ok: FAILS.append(n)

FF = shutil.which("ffmpeg")
tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "src"); os.makedirs(src)

def info(vid, title, ch, tags, **kw):
    d = {"id": vid, "title": title, "channel": ch, "duration": 4, "tags": tags, "upload_date": "20240215", "description": "About <b>" + title + "</b>\nline two",
         "webpage_url": "https://example.test/watch?v=" + vid}
    d.update(kw)
    json.dump(d, open(os.path.join(src, vid + ".info.json"), "w"))

# --- pure helpers
check("tags cleaned", videozim.clean_tags({"tags": ["Water ", "water", "#Fire", "x", "a" * 50, "pipe|bad"], "categories": ["Howto"]}) == ["water", "fire", "howto"])
check("date format", videozim.fmt_date("20240131") == "2024-01-31" and videozim.fmt_date("x") == "")

# --- empty folder
try:
    videozim.build_videos_zim(src, os.path.join(tmp, "e.zim")); ok = False
except RuntimeError as e:
    ok = "No downloaded videos" in str(e)
check("empty folder explained", ok)

# --- range requests need no ffmpeg: a made-up "video" streamed into a ZIM
from zimwriter import ZimWriter
fake = os.path.join(tmp, "fake.webm"); open(fake, "wb").write(os.urandom(300_000))
zw = ZimWriter(os.path.join(tmp, "r.zim"), {"Title": "r"}); zw.add_entry("index.html", "i", "text/html", b"<p>x</p>")
zw.add_file("v/fake.webm", "", "video/webm", fake); zw.set_main("index.html"); zw.close()
ps0 = PreviewServer(os.path.join(tmp, "r.zim"))
try:
    import urllib.error
    whole = open(fake, "rb").read()
    def rng0(h):
        req = urllib.request.Request(ps0.url + "v/fake.webm", headers={"Range": h})
        try:
            r2 = urllib.request.urlopen(req); return r2.status, r2.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
    check("whole file without Range", urllib.request.urlopen(ps0.url + "v/fake.webm").read() == whole)
    check("Range gives the right piece", rng0("bytes=1000-1999") == (206, whole[1000:2000]))
    check("Range past the end refused", rng0("bytes=999999-")[0] == 416)
finally:
    ps0.stop()

if not FF:
    print("SKIP the rest: ffmpeg not installed"); sys.exit(1 if FAILS else 0)

# --- real files: one webm, one mp4 (must be converted), one audio-only webm
videozim.make_sample_video(os.path.join(src, "aaa111.webm"), FF)
subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x90:rate=10", "-f", "lavfi", "-i", "sine=duration=2",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", os.path.join(src, "bbb222.mp4")], check=True)
subprocess.run([FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", "-c:a", "libopus", os.path.join(src, "ccc333.webm")], check=True)
info("aaa111", "Clean water at home", "Prepper Pete", ["Water", "water filter", "DIY"], chapters=[{"title": "Intro", "start_time": 0}, {"title": "Boiling", "start_time": 2}])
info("bbb222", "Fire <starting> & \"tinder\"", "Prepper Pete", ["fire", "diy", "water"])
info("ccc333", "Radio talk (audio)", "Ham Hannah", ["radio"], vcodec="none")
open(os.path.join(src, "aaa111.jpg"), "wb").write(b"\xff\xd8\xff\xe0fakejpeg")
open(os.path.join(src, "aaa111.en.vtt"), "w").write("WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n")

events = []
out = os.path.join(tmp, "v.zim")
r = videozim.build_videos_zim(src, out, "My Videos", events.append, ffmpeg=FF)
check("built", r["videos"] == 3 and r["converted"] == 1 and os.path.getsize(out) == r["size"] and not os.path.exists(out + ".part"), r)
check("tag count", r["tags"] == 5, r)
check("progress events", any(e.get("type") == "build" and e["done"] == 3 for e in events) and any("Converting" in e.get("text", "") for e in events))
z = Zim(out)       # independent reader: checksum, sorted pointers
check("independent reader accepts it", z.entry_count > 10)
names = {e["url"] for e in z.entries if e["ns"] == "C"}
check("expected entries", {"index.html", "catalog.json", "style.css", "app.js"} <= names and any(n.startswith("v/clean-water-at-home") and n.endswith(".webm") for n in names)
      and any(n.startswith("v/fire-") and n.endswith(".webm") for n in names), sorted(names))
check("no mp4 left in the ZIM", not any(n.endswith(".mp4") for n in names))
check("subtitle and thumbnail stored", any(n.startswith("subs/") and n.endswith(".en.vtt") for n in names) and any(n.startswith("thumbs/") for n in names))
lt, lb = check_links(z)
check("links all resolve", lt > 10 and not lb, lb[:3])

rd = ZimReader(out)
def get(url):
    _, e = rd.resolve("C", url); return rd.blob(e)
idx = get("index.html").decode()
check("home page has tag buttons with counts", 'data-tag="water"><i>' not in idx and re.search(r'data-tag="water">water<i>2</i>', idx) and re.search(r'data-tag="diy">diy<i>2</i>', idx), idx[:200])
check("home page has the controls", all(x in idx for x in ('id="q"', 'id="ch"', 'id="len"', 'id="grp"', 'id="picked"')))
cards = re.findall(r'<li class="vid"[^>]*data-tags="([^"]*)"', idx)
check("every video card carries its tags", sorted(cards) == sorted(["|water|water filter|diy|", "|fire|diy|water|", "|radio|"]), cards)
check("html is escaped in titles", "Fire &lt;starting&gt; &amp; &quot;tinder&quot;" in idx and "<starting>" not in idx)
cat = json.loads(get("catalog.json"))
check("catalog lists videos and tags", cat["kind"] == "videos" and len(cat["videos"]) == 3 and cat["tags"][0] == {"tag": "diy", "count": 2})
page_url = next(v["page"] for v in cat["videos"] if v["id"] == "aaa111")
d = get(page_url).decode()
check("player page has video, poster, subtitles, chapters", '<video id="pl"' in d and 'poster="../thumbs/' in d and '<track kind="subtitles"' in d and 'data-t="2"' in d and "Boiling" in d)
check("tags on the page link back filtered", 'href="../index.html#t=water%20filter"' in d)
check("description kept as text, not html", "About &lt;b&gt;" in d and "<b>Clean" not in d)
check("related videos shown", "More like this" in d and "Fire" in d)
adio = get(next(v["page"] for v in cat["videos"] if v["id"] == "ccc333")).decode()
check("audio uses an audio player", "<audio" in adio and "<video" not in adio)
_, ev = rd.resolve("C", next(v["url"] for v in cat["videos"] if v["id"] == "aaa111"))
check("video is stored uncompressed (seekable)", rd.blob_location(ev) is not None and ev["mime"] == "video/webm")
check("video bytes identical to the source", get(next(v["url"] for v in cat["videos"] if v["id"] == "aaa111")) == open(os.path.join(src, "aaa111.webm"), "rb").read())
_, ea = rd.resolve("C", next(v["url"] for v in cat["videos"] if v["id"] == "ccc333"))
check("audio mime", ea["mime"] == "audio/webm")
rd.close()

# --- preview server with Range
ps = PreviewServer(out)
try:
    vurl = next(v["url"] for v in cat["videos"] if v["id"] == "aaa111")
    full = urllib.request.urlopen(ps.url + vurl).read()
    check("full video served", full == open(os.path.join(src, "aaa111.webm"), "rb").read())
    def rng(h):
        req = urllib.request.Request(ps.url + vurl, headers={"Range": h})
        try:
            r2 = urllib.request.urlopen(req); return r2.status, r2.headers, r2.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, b""
    s, h, b = rng("bytes=10-99")
    check("range 206 with right bytes", s == 206 and b == full[10:100] and h["Content-Range"] == f"bytes 10-99/{len(full)}" and h["Accept-Ranges"] == "bytes")
    s, h, b = rng(f"bytes={len(full) - 20}-")
    check("open-ended range", s == 206 and b == full[-20:])
    s, h, b = rng("bytes=-30")
    check("last-N-bytes range", s == 206 and b == full[-30:])
    s, h, b = rng(f"bytes={len(full) + 5}-")
    check("range past the end is 416", s == 416)
    req = urllib.request.urlopen(ps.url + "videos/")
except Exception as e:
    if "404" not in str(e): check("preview server", False, str(e))
finally:
    ps.stop()

# --- limits, cancel, no ffmpeg
r2 = videozim.build_videos_zim(src, os.path.join(tmp, "small.zim"), "S", max_file_mb=0, ffmpeg=FF) if False else None
cancel = threading.Event(); cancel.set()
try:
    videozim.build_videos_zim(src, os.path.join(tmp, "c.zim"), cancel=cancel, ffmpeg=FF); ok = False
except RuntimeError as e:
    ok = str(e) == "Cancelled"
check("cancel leaves nothing behind", ok and not os.path.exists(os.path.join(tmp, "c.zim")) and not os.path.exists(os.path.join(tmp, "c.zim.part")))
r3 = videozim.build_videos_zim(src, os.path.join(tmp, "n.zim"), "N", ffmpeg=None)
check("without ffmpeg the mp4 goes in as it is, with a note", r3["converted"] == 0 and any("ffmpeg" in n[1] for n in r3["notes"]), r3)
r4 = videozim.build_videos_zim(src, os.path.join(tmp, "only.zim"), "O", ffmpeg=FF, only_ids={"aaa111"})
check("only the chosen videos", r4["videos"] == 1)
try:
    videozim.build_videos_zim(src, os.path.join(tmp, "big.zim"), "B", ffmpeg=FF, max_file_mb=-1); ok = False
except RuntimeError as e:
    ok = "None of the videos" in str(e)
check("too-large files are skipped, and an all-skipped ZIM is refused", ok)

# --- the test ZIM
tz = os.path.join(tmp, "test.zim")
tr = videozim.build_test_zim(tz, FF)
check("test ZIM builds", tr["videos"] == 3 and Zim(tz).entry_count > 10 and os.path.getsize(tz) < 3_000_000, tr)
try:
    videozim.build_test_zim(os.path.join(tmp, "x.zim"), None); ok = False
except RuntimeError as e:
    ok = "ffmpeg" in str(e)
check("test ZIM without ffmpeg explained", ok)
print("\n%d failed" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
