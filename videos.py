"""Getting videos with yt-dlp: work out what was typed, list what is there, download the ones you pick.

The input box takes a web address (one video, a playlist or a whole channel), several addresses (one per line), or plain
words, which become a keyword search ("ytsearch" in yt-dlp's language). Nothing here talks to a site itself: every
request is yt-dlp's, and the program only reads what yt-dlp prints. yt-dlp, ffmpeg and Deno are separate free programs
(see toolinstall.py); this module only finds them and runs them.

A downloaded video is three plain files in the folder: <id>.<ext> (the video), <id>.info.json (title, channel, tags,
chapters, description...) and <id>.jpg (the picture). Subtitles, when there are some, are <id>.<lang>.vtt.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import prefs

VIDEO_EXTS = (".webm", ".mp4", ".mkv", ".mov", ".m4v")
SEARCH_LIMITS = (5, 50)

# name -> (yt-dlp format choice, short help). All are "best that fits"; the WebM ones also prefer streams already in WebM so
# nothing has to be converted before they go in a ZIM.
QUALITIES = {
    "360p WebM (small)": ("bv*[height<=360][ext=webm]+ba[ext=webm]/b[height<=360][ext=webm]/bv*[height<=360]+ba/b[height<=360]/w", "about 15 MB a minute"),
    "480p WebM": ("bv*[height<=480][ext=webm]+ba[ext=webm]/b[height<=480][ext=webm]/bv*[height<=480]+ba/b[height<=480]/w", "about 25 MB a minute"),
    "720p WebM": ("bv*[height<=720][ext=webm]+ba[ext=webm]/b[height<=720][ext=webm]/bv*[height<=720]+ba/b[height<=720]/w", "about 45 MB a minute"),
    "1080p WebM (large)": ("bv*[height<=1080][ext=webm]+ba[ext=webm]/b[height<=1080][ext=webm]/bv*[height<=1080]+ba/b[height<=1080]/w", "about 90 MB a minute"),
    "Sound only (Opus)": ("ba[ext=webm]/ba/b", "about 1 MB a minute"),
}
DEFAULT_QUALITY = "480p WebM"
# rough megabytes per minute, to show a "about this big" figure before downloading
MB_PER_MIN = {"360p WebM (small)": 15, "480p WebM": 25, "720p WebM": 45, "1080p WebM (large)": 90, "Sound only (Opus)": 1}


class VideoError(Exception):
    pass


# ---- what was typed -------------------------------------------------------------------------------------------

_URL = re.compile(r"^(https?://|www\.)\S+$", re.I)


def _norm_url(s):
    return ("https://" + s) if s.lower().startswith("www.") else s


def classify(text, search_count=20):
    """Turn the input box into a plan: {"mode": "urls", "urls": [...]} or {"mode": "search", "query": ..., "target": "ytsearchN:..."}.

    Several lines that are all addresses are a list of addresses. Anything else is words to search for.
    """
    lines = [l.strip() for l in (text or "").replace(",", "\n").splitlines() if l.strip()] if "http" in (text or "").lower() or "www." in (text or "").lower() else []
    if lines and all(_URL.match(l) for l in lines):
        seen, urls = set(), []
        for l in lines:
            u = _norm_url(l)
            if u not in seen:
                seen.add(u)
                urls.append(u)
        return {"mode": "urls", "urls": urls}
    q = " ".join((text or "").split())
    if not q:
        raise VideoError("Type a web address or some words to search for.")
    n = max(SEARCH_LIMITS[0], min(SEARCH_LIMITS[1], int(search_count)))
    return {"mode": "search", "query": q, "count": n, "target": f"ytsearch{n}:{q}"}


def plain_url_kind(url):
    """'channel', 'playlist' or 'video' by the shape of the address (a guess for the label only)."""
    u = url.lower()
    if re.search(r"youtube\.com/(@|channel/|c/|user/)", u) or "/videos" in u.split("?")[0] and "youtube" in u:
        return "channel"
    if "list=" in u and "watch?v=" not in u or "/playlist" in u:
        return "playlist"
    return "video"


# ---- finding the helper programs -----------------------------------------------------------------------------------

def _exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def find_tool(name):
    """Path of yt-dlp / ffmpeg / deno: the one chosen in Settings, one we installed, or one on the PATH. None if not found."""
    import toolinstall
    chosen = prefs.get("tool_" + name.replace("-", ""))
    if chosen and os.path.isfile(chosen):
        return chosen
    here = toolinstall.installed_path(name)
    if here:
        return here
    return shutil.which(name)


def tool_env(ffmpeg=None, deno=None):
    """The environment yt-dlp runs in: our helper folders first on PATH, so it finds ffmpeg and Deno by itself."""
    env = dict(os.environ)
    extra = [os.path.dirname(p) for p in (ffmpeg, deno) if p]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def launcher(path):
    """The command that runs a helper: the program itself, or Python for a .py stand-in (used by the tests)."""
    return [sys.executable, path] if path.lower().endswith(".py") else [path]


def base_args(ytdlp=None, ffmpeg=None, deno=None):
    """Arguments every yt-dlp run starts with."""
    ytdlp = ytdlp or find_tool("yt-dlp")
    if not ytdlp:
        raise VideoError("yt-dlp is not installed yet. Use Settings > Video tools > Get yt-dlp.")
    cmd = launcher(ytdlp) + ["--no-colors", "--ignore-config", "--no-warnings", "--newline"]
    if ffmpeg:
        cmd += ["--ffmpeg-location", ffmpeg]
    if deno:
        cmd += ["--js-runtimes", "deno:" + deno]
    proxy = prefs.get("proxy")
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd


def _popen(cmd, env=None):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                            creationflags=0x08000000 if sys.platform == "win32" else 0, text=True, encoding="utf-8", errors="replace")


def stream(cmd, on_line, cancel=None, env=None, popen=None):
    """Run cmd, give each output line to on_line, stop it if cancel is set. Returns the exit code."""
    p = (popen or _popen)(cmd, env)
    try:
        for line in p.stdout:
            on_line(line.rstrip("\r\n"))
            if cancel is not None and cancel.is_set():
                p.terminate()
                break
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
    try:
        return p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
        return p.wait()


# ---- listing ---------------------------------------------------------------------------------------------------------

def parse_entry(d):
    """One line of yt-dlp's flat listing -> the small record the picker shows. None if it is not a video."""
    if not isinstance(d, dict) or not d.get("id"):
        return None
    url = d.get("webpage_url") or d.get("url") or ""
    if not url.startswith("http"):
        ie = (d.get("ie_key") or "").lower()
        url = f"https://www.youtube.com/watch?v={d['id']}" if ie in ("", "youtube") else url
    if not url.startswith("http"):
        return None
    dur = d.get("duration")
    return {"id": str(d["id"]), "title": d.get("title") or str(d["id"]), "url": url,
            "channel": d.get("channel") or d.get("uploader") or "", "duration": int(dur) if isinstance(dur, (int, float)) else None,
            "views": d.get("view_count"), "thumb": d.get("thumbnail") or ""}


def list_entries(plan, cancel=None, ytdlp=None, ffmpeg=None, deno=None, on_line=None, popen=None, limit=500):
    """Everything the plan points at, as a list of records (see parse_entry). Nothing is downloaded."""
    cmd = base_args(ytdlp, ffmpeg, deno) + ["--flat-playlist", "--dump-json", "--playlist-end", str(limit)]
    targets = plan["urls"] if plan["mode"] == "urls" else [plan["target"]]
    out, errors, seen = [], [], set()

    def line(s):
        if s.startswith("{"):
            try:
                e = parse_entry(json.loads(s))
            except ValueError:
                return
            if e and e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
                if on_line:
                    on_line(e)
        elif s.strip():
            errors.append(s.strip())
    for t in targets:
        if cancel is not None and cancel.is_set():
            break
        code = stream(cmd + [t], line, cancel, tool_env(ffmpeg, deno), popen)
        if code not in (0, None) and not out:
            raise VideoError(friendly_error(errors))
    if not out:
        raise VideoError(friendly_error(errors) if errors else "Nothing was found.")
    return out


def friendly_error(lines):
    """Plain words for the most useful line yt-dlp printed."""
    text = " ".join(lines[-6:]).lower()
    if "sign in to confirm" in text or "not a bot" in text:
        return "The site asked for a sign-in. Try again later, or pick a browser to borrow its sign-in in Settings > Video tools."
    if "private video" in text:
        return "That video is private."
    if "video unavailable" in text or "not available" in text:
        return "That video is not available (removed, or not shown in your country)."
    if "unsupported url" in text:
        return "That address is not one the program can get videos from."
    if "javascript runtime" in text or "challenge" in text or "n challenge" in text:
        return "The site needs Deno to work out its video address. Use Settings > Video tools > Get Deno."
    if "http error 429" in text or "too many requests" in text:
        return "The site asked us to slow down. Wait a few minutes and try again."
    if "ffmpeg" in text and ("not found" in text or "not installed" in text):
        return "ffmpeg is needed to join the picture and sound. Use Settings > Video tools > Get ffmpeg."
    if "urlopen error" in text or "unable to download" in text and "resolve" in text:
        return "Could not reach the site. Check the connection."
    last = next((l for l in reversed(lines) if l.strip()), "")
    return ("yt-dlp said: " + last[:160]) if last else "yt-dlp stopped without saying why. Try Update yt-dlp in Settings."


def fmt_duration(sec):
    if sec is None:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def estimate_bytes(entries, quality):
    """Rough size of these videos at this quality. Unknown lengths count as 10 minutes."""
    per = MB_PER_MIN.get(quality, 25)
    mins = sum((e.get("duration") or 600) / 60 for e in entries)
    return int(mins * per * 1_000_000)


# ---- downloading ------------------------------------------------------------------------------------------------------

_PCT = re.compile(r"\[download\]\s+([\d.]+)%")


def download_args(quality, subs=True, archive=None, folder=None):
    fmt = QUALITIES.get(quality, QUALITIES[DEFAULT_QUALITY])[0]
    a = ["-f", fmt, "--merge-output-format", "webm/mp4/mkv", "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
         "-o", "%(id)s.%(ext)s", "--no-playlist", "--restrict-filenames", "--retries", "5", "--fragment-retries", "5"]
    if subs:
        a += ["--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--convert-subs", "vtt"]
    if archive:
        a += ["--download-archive", archive]
    if folder:
        a += ["-P", folder]
    return a


def media_for(folder, vid):
    """The video file for an id in a folder (not the info, picture or subtitle files), or None."""
    for ext in VIDEO_EXTS + (".opus", ".m4a", ".mp3"):
        p = os.path.join(folder, vid + ext)
        if os.path.isfile(p):
            return p
    return None


def download_many(entries, folder, quality, cancel, ytdlp=None, ffmpeg=None, deno=None, subs=True, on_state=None, popen=None):
    """Download each entry into folder. Returns (done ids, [(id, reason)]). on_state(i, n, entry, text) reports progress."""
    os.makedirs(folder, exist_ok=True)
    done, failed = [], []
    base = base_args(ytdlp, ffmpeg, deno) + download_args(quality, subs, folder=folder)
    for i, e in enumerate(entries, 1):
        if cancel is not None and cancel.is_set():
            break
        if media_for(folder, e["id"]) and os.path.isfile(os.path.join(folder, e["id"] + ".info.json")):
            done.append(e["id"])
            if on_state:
                on_state(i, len(entries), e, "Already have it")
            continue
        lines = []

        def line(s, e=e, i=i):
            lines.append(s)
            m = _PCT.search(s)
            if m and on_state:
                on_state(i, len(entries), e, f"{m.group(1)}%")
        if on_state:
            on_state(i, len(entries), e, "Starting")
        code = stream(base + [e["url"]], line, cancel, tool_env(ffmpeg, deno), popen)
        if cancel is not None and cancel.is_set():
            break
        if media_for(folder, e["id"]):
            done.append(e["id"])
            _ensure_info(folder, e)
        else:
            failed.append((e["id"], friendly_error([l for l in lines if not l.startswith("[download]")] or lines)))
    return done, failed


def _ensure_info(folder, e):
    """If yt-dlp left no info file, write a small one from what we know so the video still gets a catalog page."""
    p = os.path.join(folder, e["id"] + ".info.json")
    if not os.path.isfile(p):
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"id": e["id"], "title": e["title"], "channel": e["channel"], "duration": e["duration"],
                       "webpage_url": e["url"], "tags": []}, f)


def update_ytdlp(ytdlp, on_line=None, popen=None):
    """Ask yt-dlp to update itself (YouTube changes often). Returns its last line."""
    last = []
    code = stream(launcher(ytdlp) + ["--update"], lambda s: (last.append(s), on_line and on_line(s)), None, None, popen)
    if code not in (0, None):
        raise VideoError("yt-dlp could not update itself: " + (last[-1] if last else "no reason given"))
    return last[-1] if last else "Done"


# ---- reading what is in a folder -------------------------------------------------------------------------------------------

def read_info(folder, vid):
    try:
        with open(os.path.join(folder, vid + ".info.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scan_folder(folder):
    """Every downloaded video in a folder, as a list of dicts with the info merged in. Videos without a file are skipped."""
    out = []
    ids = set()
    for ext in VIDEO_EXTS:
        for p in glob.glob(os.path.join(folder, "*" + ext)):
            ids.add(os.path.splitext(os.path.basename(p))[0])
    for vid in sorted(ids):
        media = media_for(folder, vid)
        if not media:
            continue
        info = read_info(folder, vid)
        thumb = os.path.join(folder, vid + ".jpg")
        subs = sorted(glob.glob(os.path.join(folder, vid + ".*.vtt")))
        out.append({"id": vid, "file": media, "info": info, "title": info.get("title") or vid,
                    "channel": info.get("channel") or info.get("uploader") or "",
                    "thumb": thumb if os.path.isfile(thumb) else None, "subs": subs})
    return out
