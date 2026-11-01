"""Getting videos: what was typed, listing, downloading, errors, folder scan (all against a pretend yt-dlp)."""
import os, pathlib, sys, tempfile, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["APPDATA"] = tempfile.mkdtemp()
import prefs, toolinstall, videos

FAILS = []
def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n, "" if ok else extra)
    if not ok: FAILS.append(n)

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakeytdlp.py")
os.chmod(FAKE, 0o755)
tmp = tempfile.mkdtemp()

# --- classify
c = videos.classify("https://www.youtube.com/watch?v=abc123")
check("one address", c == {"mode": "urls", "urls": ["https://www.youtube.com/watch?v=abc123"]}, c)
c = videos.classify("https://youtu.be/a\nwww.youtube.com/watch?v=b\nhttps://youtu.be/a")
check("several addresses, www fixed, repeats dropped", c["urls"] == ["https://youtu.be/a", "https://www.youtube.com/watch?v=b"], c)
c = videos.classify("how to purify water", 10)
check("words are a search", c["mode"] == "search" and c["target"] == "ytsearch10:how to purify water", c)
check("search count limited", videos.classify("x", 999)["target"].startswith("ytsearch50:") and videos.classify("x", 1)["target"].startswith("ytsearch5:"))
c = videos.classify("water https://youtu.be/a")
check("words with an address are still a search", c["mode"] == "search")
try:
    videos.classify("   "); ok = False
except videos.VideoError:
    ok = True
check("empty input refused", ok)
check("kind guesses", videos.plain_url_kind("https://www.youtube.com/@someone") == "channel"
      and videos.plain_url_kind("https://www.youtube.com/playlist?list=PL1") == "playlist"
      and videos.plain_url_kind("https://www.youtube.com/watch?v=abc") == "video")

# --- helpers found
prefs.set("tool_ytdlp", FAKE)
check("tool chosen in settings wins", videos.find_tool("yt-dlp") == FAKE)
try:
    prefs.set("tool_ytdlp", ""); old = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
    try:
        videos.base_args(); ok = False
    except videos.VideoError as e:
        ok = "Get yt-dlp" in str(e)
finally:
    os.environ["PATH"] = old
check("missing yt-dlp gives a plain message", ok)
prefs.set("tool_ytdlp", FAKE)
a = videos.base_args(None, "/x/ffmpeg", "/y/deno")
check("base args carry ffmpeg and deno", "--ffmpeg-location" in a and "deno:/y/deno" in a and a[:2] == [sys.executable, FAKE], a)
check("env puts helper folders first", videos.tool_env("/x/ffmpeg", "/y/deno")["PATH"].startswith("/x" + os.pathsep + "/y"))

# --- listing
got = []
es = videos.list_entries(videos.classify("water", 7), on_line=got.append, popen=None)
check("search lists results", len(es) == 7 and es[0]["title"] == "water 0" and es[0]["duration"] == 60 and len(got) == 7, es[:1])
es = videos.list_entries(videos.classify("https://www.youtube.com/playlist?list=PLx"))
check("playlist lists its videos", [e["id"] for e in es] == ["p0", "p1", "p2"] and es[0]["channel"] == "Maker", es)
es = videos.list_entries(videos.classify("https://www.youtube.com/watch?v=one111"))
check("single video is a list of one", len(es) == 1 and es[0]["url"].endswith("v=one111") and es[0]["channel"] == "Solo")
es = videos.list_entries(videos.classify("https://youtu.be/watch?v=a1\nhttps://www.youtube.com/playlist?list=q"))
check("two addresses are merged", len(es) == 4)
try:
    videos.list_entries(videos.classify("https://www.youtube.com/watch?v=bad")); ok = False
except videos.VideoError as e:
    ok = "not available" in str(e)
check("unavailable video is explained", ok)
cancel = threading.Event(); cancel.set()
try:
    videos.list_entries(videos.classify("x"), cancel=cancel); ok = False
except videos.VideoError:
    ok = True
check("cancel before listing returns nothing", ok)
check("entry without id ignored", videos.parse_entry({"title": "x"}) is None and videos.parse_entry("x") is None)
check("bare id becomes a watch address", videos.parse_entry({"id": "zz", "ie_key": "Youtube"})["url"] == "https://www.youtube.com/watch?v=zz")
check("duration text", videos.fmt_duration(75) == "1:15" and videos.fmt_duration(3725) == "1:02:05" and videos.fmt_duration(None) == "")
check("size estimate grows with quality", videos.estimate_bytes([{"duration": 600}], "720p WebM") > videos.estimate_bytes([{"duration": 600}], "360p WebM (small)"))

# --- download
folder = os.path.join(tmp, "vids")
entries = [{"id": "ok1", "title": "A", "url": "https://www.youtube.com/watch?v=ok1", "channel": "", "duration": 90},
           {"id": "fail2", "title": "B", "url": "https://www.youtube.com/watch?v=fail2", "channel": "", "duration": 90},
           {"id": "ok3", "title": "C", "url": "https://www.youtube.com/watch?v=ok3", "channel": "", "duration": 90}]
states = []
done, failed = videos.download_many(entries, folder, "480p WebM", None, on_state=lambda i, n, e, txt: states.append((i, n, txt)))
check("downloads the good ones", done == ["ok1", "ok3"], done)
check("failure is explained, others continue", len(failed) == 1 and failed[0][0] == "fail2" and "private" in failed[0][1].lower(), failed)
check("progress is reported", (1, 3, "55.5%") in states and (3, 3, "100%") in states, states[:6])
check("files are where expected", all(os.path.isfile(os.path.join(folder, n)) for n in ("ok1.webm", "ok1.info.json", "ok1.jpg", "ok1.en.vtt")))
states.clear()
done, failed = videos.download_many(entries[:1], folder, "480p WebM", None, on_state=lambda i, n, e, t: states.append(t))
check("already-downloaded video is skipped", done == ["ok1"] and states == ["Already have it"], states)
cancel = threading.Event(); cancel.set()
done, failed = videos.download_many(entries[2:], os.path.join(tmp, "v2"), "480p WebM", cancel)
check("cancel stops before starting", done == [] and failed == [])
a = videos.download_args("720p WebM", subs=False, archive="a.txt", folder="/f")
check("download args", "height<=720" in a[1] and "--write-subs" not in a and a[a.index("--download-archive") + 1] == "a.txt" and a[-2:] == ["-P", "/f"], a)
check("every quality has a size guess", set(videos.QUALITIES) == set(videos.MB_PER_MIN) and videos.DEFAULT_QUALITY in videos.QUALITIES)

# --- folder scan
vs = videos.scan_folder(folder)
check("scan finds both videos with their info", [v["id"] for v in vs] == ["ok1", "ok3"] and vs[0]["title"] == "Title of ok1"
      and vs[0]["info"]["tags"][0] == "Water" and vs[0]["thumb"].endswith("ok1.jpg") and len(vs[0]["subs"]) == 1, vs[:1])
open(os.path.join(folder, "lone.webm"), "wb").write(b"x")
lone = [v for v in videos.scan_folder(folder) if v["id"] == "lone"][0]
check("video without info still listed", lone["title"] == "lone" and lone["info"] == {} and lone["thumb"] is None)
check("update runs yt-dlp --update", "up to date" in videos.update_ytdlp(FAKE))

# --- plain-words errors
fe = videos.friendly_error
check("error words", "sign-in" in fe(["Sign in to confirm you're not a bot"]) and "Deno" in fe(["no JavaScript runtime"])
      and "slow down" in fe(["HTTP Error 429"]) and "ffmpeg" in fe(["ffmpeg not found"]) and "Unsupported URL" not in fe(["ERROR: Unsupported URL: x"]))

# --- toolinstall for the video helpers
import zipfile, http.server
BIG = b"MZ" + b"x" * 5000
zp = os.path.join(tmp, "ff.zip")
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("ffmpeg-master/bin/ffmpeg" + (".exe" if sys.platform == "win32" else ""), BIG)
    z.writestr("ffmpeg-master/LICENSE.txt", "gpl")
p = toolinstall.install_download("ffmpeg", url=pathlib.Path(zp).as_uri(), min_bytes=100)
check("zip tool unpacked and found in bin/", p.endswith("ffmpeg" + (".exe" if sys.platform == "win32" else "")) and os.path.isfile(p) and videos.find_tool("ffmpeg") == p, p)
fp = os.path.join(tmp, "yt.bin"); open(fp, "wb").write(BIG)
p = toolinstall.install_download("yt-dlp", url=pathlib.Path(fp).as_uri(), min_bytes=100)
check("single-file tool placed and found", os.path.isfile(p) and toolinstall.installed_path("yt-dlp") == p)
evil = os.path.join(tmp, "evil.zip")
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("../../escape.txt", "x")
try:
    toolinstall.install_download("deno", url=pathlib.Path(evil).as_uri(), min_bytes=1); ok = False
except toolinstall.InstallError as e:
    ok = "unsafe" in str(e)
check("zip with outside paths refused", ok and not os.path.exists(os.path.join(tmp, "escape.txt")))
bad = os.path.join(tmp, "bad.zip"); open(bad, "wb").write(b"not a zip" * 100)
try:
    toolinstall.install_download("deno", url=pathlib.Path(bad).as_uri(), min_bytes=1); ok = False
except toolinstall.InstallError as e:
    ok = "not a complete zip" in str(e)
check("broken zip explained", ok)

print("\n%d failed" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
