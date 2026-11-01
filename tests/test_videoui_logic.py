"""Videos tab logic (stub tkinter: draws nothing). Run from the repo root: python tests/test_videoui_logic.py"""
import os, pathlib, shutil, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stubtk  # noqa: F401  (installs the stub tkinter)
os.environ["APPDATA"] = tempfile.mkdtemp()
import app as appmod, jobqueue, prefs, videos, videoui, videozim

FAILS = []
def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n, "" if ok else extra)
    if not ok: FAILS.append(n)

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakeytdlp.py")
prefs.set("tool_ytdlp", FAKE)
a = appmod.App()
v = a.videos
tmp = tempfile.mkdtemp()

def pump(secs=20, until=None):
    """Hand the worker threads' events to the tab, like the app's timer does."""
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            k, val = a.q.get(timeout=0.2)
        except Exception:
            if until and until(): return True
            continue
        if k == "video": v.on_event(val)
        if until and until(): return True
    return False

# --- the tab is part of the app, between Books and Queue
check("tab exists with settings keys", v.mode.get() == "get" and set(v.get_settings()) >= {"video_mode", "video_get_folder", "video_quality", "video_zim_name"})
v.set_settings({"video_quality": "720p WebM", "video_zim_name": "Water", "video_mode": "zim", "video_get_folder": tmp})
check("settings round trip", v.quality.get() == "720p WebM" and v.zim_name.get() == "Water" and v.mode.get() == "zim" and v.folder.get() == tmp)
v.set_settings({"video_quality": "nonsense"}); check("bad quality ignored", v.quality.get() == "720p WebM")
v.set_mode("get")

# --- reading the input box
v.target.set("   "); check("empty input gives a hint, no plan", v.plan() is None and "web address" in v.hint.get())
v.target.set("https://www.youtube.com/playlist?list=PLx"); p = v.plan()
check("playlist address", p["mode"] == "urls" and "playlist" in v.describe_plan(p))
v.target.set("https://www.youtube.com/@someone"); check("channel address", "channel" in v.describe_plan(v.plan()))
v.search_n.set("30"); v.target.set("how to purify water"); p = v.plan()
check("words are a search of the chosen size", p["mode"] == "search" and p["count"] == 30 and "30 results" in v.describe_plan(p), p)

# --- finding (runs the pretend yt-dlp in the worker thread)
v.folder.set(os.path.join(tmp, "got"))
v.search_n.set("10"); v.target.set("water")
v.find()
check("finding fills the table", pump(until=lambda: not v.running) and len(v.entries) == 10 and v.entries[0]["pick"] is True, len(v.entries))
check("hint says how many", "Found 10" in v.hint.get())
v.filter_text.set("water 3"); check("filter by words", [e["id"] for e in v.visible_entries()] == ["s3"])
v.filter_text.set("")
check("totals count ticked videos", "10 ticked of 10" in v.total_note.get() and "Estimated total" in v.total_note.get(), v.total_note.get())
for e in v.entries[3:]: e["pick"] = False
v.update_totals(); check("totals follow ticks", "3 ticked of 10" in v.total_note.get())
check("picked records carry what a job needs", [p["id"] for p in v.picked()] == ["s0", "s1", "s2"] and set(v.picked()[0]) == {"id", "title", "url", "channel", "duration"})
v.target.set("https://www.youtube.com/watch?v=bad"); v.find()
check("unavailable video shows a plain message", pump(until=lambda: not v.running) and "not available" in v.hint.get() and len(v.entries) == 0, v.hint.get())

# --- downloading
v.target.set("https://www.youtube.com/playlist?list=PLx"); v.find(); pump(until=lambda: not v.running)
ids = [e["id"] for e in v.entries]; check("playlist found", ids == ["p0", "p1", "p2"], ids)
v.entries[1]["url"] = "https://www.youtube.com/watch?v=fail9"; v.entries[1]["id"] = "fail9"   # one that will fail
v.download()
check("download finishes", pump(until=lambda: not v.running and "Saved" in v.status.get() or "failed" in v.status.get()))
st = {e["id"]: e["status"] for e in v.entries}
check("row states", st["p0"] == "Saved" and st["p2"] == "Saved" and st["fail9"].startswith("Failed: "), st)
check("files landed in the chosen folder", all(os.path.isfile(os.path.join(v.folder.get(), n)) for n in ("p0.webm", "p0.info.json", "p2.webm")))
check("zim tab pointed at the new folder", v.zim_folder.get() == v.folder.get() and v.zim_items == [])
check("status sums up", "Saved 2 videos, 1 failed" in v.status.get(), v.status.get())

# --- nothing ticked / no folder
for e in v.entries: e["pick"] = False
check("nothing ticked: no job", v.check_ready() is None)

# --- queue a download
for e in v.entries: e["pick"] = True
v.queue_get()
j = a.queue_tab.q.jobs[-1]
check("queued download job", j["kind"] == "videos_get" and len(j["params"]["entries"]) == 3 and j["params"]["quality"] == "720p WebM" and "Waiting" not in j["title"]
      and jobqueue.describe(j).startswith("Get 3 videos"), j)
check("queue message", "Added to the queue" in v.status.get())

# --- the job runner does the same work
out = os.path.join(tmp, "q"); entries = [{"id": "z1", "title": "Zed one", "url": "https://x/watch?v=z1", "channel": "", "duration": 5}]
msgs = []
r = jobqueue.run_job({"kind": "videos_get", "params": {"entries": entries, "folder": out, "quality": "480p WebM"}}, msgs.append, threading.Event())
check("videos_get job", r == "1 of 1 videos saved" and os.path.isfile(os.path.join(out, "z1.webm")) and any("Video 1 of 1" in m["text"] for m in msgs), (r, msgs))
try:
    jobqueue.run_job({"kind": "videos_get", "params": {"entries": [dict(entries[0], id="fail1", url="https://x/watch?v=fail1")], "folder": out}}, lambda e: None, threading.Event()); ok = False
except RuntimeError as e:
    ok = "private" in str(e).lower()
check("failed job says why", ok)
prefs.set("tool_ytdlp", ""); old = os.environ["PATH"]; os.environ["PATH"] = os.path.dirname(os.path.abspath(__file__)) + os.pathsep + "/nonexistent"
try:
    try:
        jobqueue.run_job({"kind": "videos_get", "params": {"entries": entries, "folder": out}}, lambda e: None, threading.Event()); ok = False
    except RuntimeError as e:
        ok = "Install it for me" in str(e)
finally:
    os.environ["PATH"] = old; prefs.set("tool_ytdlp", FAKE)
check("job without yt-dlp explains how to get it", ok)

# --- videos to zim
v.set_mode("zim")
v.zim_folder.set(v.folder.get()); v.zim_items = []; v.load_zim_folder()
check("zim list reads the folder", sorted(i["id"] for i in v.zim_items) == ["p0", "p2"] and all(i["pick"] for i in v.zim_items), v.zim_items)
check("format notes", v.format_note(v.zim_items[0])[0] == "WebM ✔")
it = dict(v.zim_items[0], ext=".mp4", ffmpeg=True); check("mp4 will be converted", "converted" in v.format_note(it)[0])
it["ffmpeg"] = False; check("mp4 without ffmpeg warns", "may not play" in v.format_note(it)[0])
check("total line", "2 of 2 videos" in v.zim_total.get())
v.zim_out.set(""); 
v.zim_items[1]["pick"] = False; v.zim_out.set(os.path.join(tmp, "out.zim"))
r = v.zim_ready()
check("zim_ready returns only the ticked", r and r[3] == ["p0"] and r[1].endswith("out.zim"), r)
v.queue_zim(); j = a.queue_tab.q.jobs[-1]
check("queued zim job", j["kind"] == "videos_zim" and j["params"]["only_ids"] == ["p0"] and j["params"]["convert"] is True and jobqueue.describe(j).startswith("Videos to ZIM"), j)
v.zim_items[0]["pick"] = False
shown = []
import tkinter.messagebox as mbx
mbx.showinfo = lambda *x, **k: shown.append(x)
check("nothing ticked is explained", v.zim_ready() is None and shown and "No videos are ticked" in shown[-1][1])
v.zim_items[0]["pick"] = v.zim_items[1]["pick"] = True

if shutil.which("ffmpeg"):
    prefs.set("tool_ffmpeg", shutil.which("ffmpeg"))
    # a real build through the job runner, with a real sample made by ffmpeg
    real = os.path.join(tmp, "real"); os.makedirs(real)
    videozim.make_sample_video(os.path.join(real, "r1.webm"), shutil.which("ffmpeg"))
    import json
    json.dump({"id": "r1", "title": "Real one", "tags": ["alpha", "beta"], "duration": 4}, open(os.path.join(real, "r1.info.json"), "w"))
    zout = os.path.join(tmp, "job.zim")
    res = jobqueue.run_job({"kind": "videos_zim", "params": {"folder": real, "out": zout, "name": "J"}}, lambda e: None, threading.Event())
    check("videos_zim job", res.startswith("1 videos,") and os.path.isfile(zout), res)
    # the test ZIM button
    shown.clear(); prompts = []
    mbx.showinfo = lambda *x, **k: shown.append(x); mbx.askyesno = lambda *x, **k: (prompts.append(x), False)[1]
    import tkinter
    v.test_zim()
    check("test ZIM built and reported", pump(60, until=lambda: not v.running) and shown and "3 videos" in shown[-1][1] and "tags" in shown[-1][1], shown)
    check("test ZIM offers a preview", prompts and "Preview" in prompts[-1][1] and "Play a video" in prompts[-1][1])
    # building from the tab
    mbx.askyesnocancel = lambda *x, **k: True
    shown.clear(); v.zim_folder.set(real); v.zim_items = []; v.load_zim_folder(); v.zim_out.set(os.path.join(tmp, "tab.zim"))
    v.make_zim()
    check("Build ZIM from the tab", pump(60, until=lambda: not v.running) and os.path.isfile(os.path.join(tmp, "tab.zim")) and "1 videos, 2 tags" in shown[-1][1], shown)
    check("status after build", "ZIM saved" in v.status.get())
    mbx.askyesnocancel = lambda *x, **k: False; n = len(a.queue_tab.q.jobs); v.make_zim()
    check("answering No queues it instead", len(a.queue_tab.q.jobs) == n + 1 and not v.running)
else:
    print("SKIP the ffmpeg parts")

check("helper line", v.helpers.get().startswith("Helpers: yt-dlp found"), v.helpers.get())
v.cancel.clear(); v.on_cancel(); check("cancel sets the flag", v.cancel.is_set() and v.status.get() == "Cancelling...")
print("\n%d failed" % len(FAILS) if FAILS else "\nALL VIDEO UI LOGIC TESTS PASSED")
sys.exit(1 if FAILS else 0)
