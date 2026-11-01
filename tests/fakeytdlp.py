#!/usr/bin/env python3
"""A pretend yt-dlp for the tests: lists and 'downloads' made-up videos without touching the internet."""
import json, os, re, sys

a = sys.argv[1:]
target = a[-1]
if "--update" in a:
    print("yt-dlp is up to date (fake)"); sys.exit(0)
if "bad" in target:
    print("ERROR: [youtube] bad: Video unavailable"); sys.exit(1)
if "--flat-playlist" in a:
    m = re.match(r"ytsearch(\d+):(.*)", target)
    if m:
        n = int(m.group(1))
        for i in range(n):
            print(json.dumps({"id": f"s{i}", "title": f"{m.group(2)} {i}", "url": f"https://www.youtube.com/watch?v=s{i}", "duration": 60 * (i + 1), "channel": "Chan"}))
    elif "playlist" in target:
        for i in range(3):
            print(json.dumps({"id": f"p{i}", "title": f"Part {i}", "url": f"https://www.youtube.com/watch?v=p{i}", "duration": 120, "uploader": "Maker"}))
    else:
        vid = re.search(r"v=([\w-]+)", target).group(1)
        print(json.dumps({"id": vid, "title": "One video", "webpage_url": target, "duration": 90, "channel": "Solo"}))
    sys.exit(0)
vid = re.search(r"v=([\w-]+)", target).group(1)
out = a[a.index("-P") + 1] if "-P" in a else "."
os.chdir(out)
if "fail" in vid:
    print("[youtube] extracting"); print("ERROR: [youtube] Private video. Sign in"); sys.exit(1)
for pct in ("10.0", "55.5", "100"):
    print(f"[download]  {pct}% of 1.00MiB at 1.00MiB/s ETA 00:01")
open(vid + ".webm", "wb").write(b"\x1aE\xdf\xa3fake" * 50)
open(vid + ".jpg", "wb").write(b"\xff\xd8fake")
open(vid + ".en.vtt", "w").write("WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n")
json.dump({"id": vid, "title": "Title of " + vid, "channel": "Chan " + vid[0], "duration": 90, "tags": ["Water", "water filter", "diy"],
           "categories": ["Howto & Style"], "description": "About " + vid, "upload_date": "20240131",
           "chapters": [{"title": "Start", "start_time": 0}, {"title": "Middle", "start_time": 30}], "webpage_url": target,
           "view_count": 1234}, open(vid + ".info.json", "w"))
