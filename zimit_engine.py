"""Zimit engine: runs the official openZIM Zimit Docker image (real Chromium
crawl via Browsertrix, handles JavaScript sites) and streams its progress.

Docker Desktop is the only external requirement for this engine.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

IMAGE = "ghcr.io/openzim/zimit"
NOWIN = 0x08000000 if os.name == "nt" else 0
DOCKER_URL = "https://www.docker.com/products/docker-desktop/"

_WIN_DOCKER_TAIL = (r"Docker\Docker\resources\bin\docker.exe",)


def docker_candidates():
    """Every place Docker Desktop's docker.exe is known to live on Windows (all-users and per-user installs)."""
    out = []
    env = os.environ
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = env.get(var)
        if base:
            out += [os.path.join(base, t) for t in _WIN_DOCKER_TAIL]
    out += [os.path.join(r"C:\Program Files", t) for t in _WIN_DOCKER_TAIL]
    out.append(r"C:\ProgramData\DockerDesktop\version-bin\docker.exe")
    if env.get("LOCALAPPDATA"):                      # "install for me only" copy of Docker Desktop
        out.append(os.path.join(env["LOCALAPPDATA"], "Programs", "DockerDesktop", "resources", "bin", "docker.exe"))
        out.append(os.path.join(env["LOCALAPPDATA"], "Docker", "wsl", "docker.exe"))
    out.append(os.path.join(env.get("USERPROFILE", ""), ".docker", "bin", "docker.exe"))
    for root_name, sub in (("HKEY_LOCAL_MACHINE", r"SOFTWARE\Docker Inc.\Docker\1.0"),
                           ("HKEY_CURRENT_USER", r"SOFTWARE\Docker Inc.\Docker\1.0")):
        try:
            import winreg
            with winreg.OpenKey(getattr(winreg, root_name), sub) as k:
                app_path = winreg.QueryValueEx(k, "AppPath")[0]
            out.append(os.path.join(app_path, "resources", "bin", "docker.exe"))
        except Exception:
            pass
    return out


def find_docker(chosen=None):
    """docker.exe: the path chosen in Settings, else PATH, else the usual Windows install places."""
    if chosen is None:
        try:
            import prefs
            chosen = prefs.get("tool_docker")
        except Exception:
            chosen = ""
    if chosen:
        return chosen if os.path.isfile(chosen) else None
    p = shutil.which("docker")
    if p:
        return p
    if os.name == "nt":
        for c in docker_candidates():
            if c and os.path.isfile(c):
                return c
    return None


INFO_TIMEOUT_S = 60
LAST_ERROR = [""]      # why the last Docker check failed, for the message in Settings


_PIPES = (r"\\.\pipe\dockerDesktopLinuxEngine", r"\\.\pipe\docker_engine")


def _pipe_exists(name):
    """Does a Windows named pipe exist? (os.path.exists can say no for a pipe that is busy.)"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        if k.WaitNamedPipeW(name, 1):
            return True
        return ctypes.GetLastError() != 2          # 2 = no such pipe; 121 (timeout) / 231 (busy) mean it is there
    except Exception:
        return os.path.exists(name)


def engine_socket_exists():
    if os.name == "nt":
        return any(_pipe_exists(p) for p in _PIPES)
    return os.path.exists("/var/run/docker.sock")


def docker_status():
    """Returns (state, docker_path): state is 'ready', 'not_running' or 'missing'."""
    d = find_docker()
    if not d:
        return "missing", None
    # Fast path: when the engine is running, Docker's connection point exists, and `docker info` (slow) is not needed.
    if engine_socket_exists():
        LAST_ERROR[0] = ""
        return "ready", d
    # `docker info` can hang when Docker Desktop is half-started, so it is never allowed to block: no output pipes (a hung
    # child can keep them open and freeze the wait), a short deadline, and the process is killed if it overruns.
    try:
        p = subprocess.Popen([d, "info"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NOWIN)
    except Exception as e:
        LAST_ERROR[0] = f"{e.__class__.__name__}: {e}"
        return "not_running", d
    try:
        rc = p.wait(timeout=INFO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
        except Exception:
            pass
        LAST_ERROR[0] = f"no answer after {INFO_TIMEOUT_S} seconds - Docker Desktop may still be starting"
        return "not_running", d
    LAST_ERROR[0] = "" if rc == 0 else f"docker info failed (exit code {rc})"
    return ("ready" if rc == 0 else "not_running"), d


_BLOCK_EXT = {
    "images": "png|jpe?g|jpe|gif|webp|avif|bmp|ico|svg|tiff?|heic",
    "media": "mp4|webm|mov|m4v|avi|mkv|mpe?g|wmv|flv|mp3|ogg|oga|ogv|wav|m4a|flac|aac|wma|opus",
    "fonts": "woff2?|ttf|otf|eot",
    "docs": "pdf|docx?|xlsx?|pptx?|odt|ods|zip|7z|rar|gz|tar|epub|iso|exe|msi|dmg|apk",
}


def block_patterns(o):
    """Regexes for files the chosen detail level leaves out."""
    flags = (("images", o.skip_images), ("media", o.skip_media), ("fonts", o.skip_fonts), ("docs", o.skip_docs))
    exts = [_BLOCK_EXT[k] for k, on in flags if on]
    if not exts:
        return []
    return [r"\.(" + "|".join(exts) + r")(\?|#|$)"]


def optional_flags(o):
    """Flags that depend on Browsertrix options; they are dropped and retried if Zimit rejects them."""
    out = []
    if o.depth:
        out += ["--depth", str(o.depth)]
    for pat in block_patterns(o):
        out += ["--blockRules", json.dumps({"url": pat, "type": "block"})]
    return out


def build_command(docker, container, work_dir, url, name, o, extras=True, legacy=False, build=False):
    """legacy=True uses the old --sizeLimit/--timeLimit names (older crawler images); the current ones are the Soft variants,
    which stop the crawl and still save the ZIM."""
    cmd = [docker, "run", "--rm", "--name", container, "--shm-size=1g",
           "-v", f"{work_dir}:/output", IMAGE, "zimit",
           "--seeds", url, "--name", name, "--output", "/output",
           "--workers", str(max(1, o.workers))]
    if build:      # a fixed build folder (inside the mounted folder) that the crawler keeps, so a later run can continue
        cmd += ["--build", "/output/build"]
    if o.max_pages:
        cmd += ["--pageLimit", str(o.max_pages)]
    if o.max_mb:
        cmd += ["--sizeLimit" if legacy else "--sizeSoftLimit", str(o.max_mb * 1024 * 1024)]
    if o.max_minutes:
        cmd += ["--timeLimit" if legacy else "--timeSoftLimit", str(o.max_minutes * 60)]
    scope = "prefix" if o.path_scope else ("domain" if o.subdomains else "host")
    cmd += ["--scopeType", scope]
    if extras:
        cmd += optional_flags(o)
    return cmd


def unrecognized_flags(text):
    """Flags named in an argparse 'unrecognized arguments: --a --b' message."""
    m = re.search(r"unrecognized arguments?:\s*(.+)", text)
    return set(re.findall(r"--[A-Za-z][\w-]*", m.group(1))) if m else set()


def drop_flags(cmd, flags):
    """The command without the given flags (and the value after each)."""
    out, i = [], 0
    while i < len(cmd):
        if cmd[i] in flags:
            i += 2 if i + 1 < len(cmd) and not cmd[i + 1].startswith("--") else 1
            continue
        out.append(cmd[i])
        i += 1
    return out


def detect_limit(line):
    """Return 'pages'/'size'/'time' if a log line says a crawl limit was reached, else None."""
    low = line.lower()
    if "limit" not in low or not any(w in low for w in ("reached", "hit", "exceed", "stopping", "interrupt")):
        return None
    if "time" in low:
        return "time"
    if "size" in low:
        return "size"
    if "page" in low:
        return "pages"
    return None


def parse_line(line):
    """Turn one line of output into events, adding a 'limit' event when a limit message is seen."""
    evs = _parse_line(line)
    for e in list(evs):
        if e["type"] == "log":
            w = detect_limit(e["text"])
            if w:
                evs.append({"type": "limit", "which": w})
                break
    return evs


def _parse_line(line):
    """Turn one line of Zimit/Browsertrix output into zero or more GUI events."""
    line = line.strip()
    if not line:
        return []
    if line.startswith("{"):
        try:
            j = json.loads(line)
        except ValueError:
            return [{"type": "log", "text": line}]
        msg = str(j.get("message", "")).strip()
        det = j.get("details")
        det = det if isinstance(det, dict) else {}
        if j.get("context") == "crawlStatus" and "crawled" in det:
            return [{"type": "stats", "crawled": det.get("crawled", 0), "total": det.get("total", 0),
                     "pending": det.get("pending", 0), "failed": det.get("failed", 0)}]
        out = []
        page = det.get("page")
        if isinstance(page, str) and page.startswith("http"):
            if msg == "Page Finished":
                out.append({"type": "row", "status": "Saved", "url": page})
            elif msg.startswith("Page Load Failed") or msg.startswith("Page Load Timeout"):
                out.append({"type": "row", "status": "Failed", "url": page, "error": True})
        if msg:
            out.append({"type": "log", "text": msg + (f"  {page}" if isinstance(page, str) else "")})
        return out
    low = line.lower()
    ev = [{"type": "log", "text": line}]
    if "warc2zim" in low or "converting" in low or "starting zim" in low:
        ev.append({"type": "phase", "text": "Building ZIM file..."})
    return ev


def _stream(proc, on_line):
    for raw in proc.stdout:
        on_line(raw)
    return proc.wait()


def desktop_exe(docker_path):
    """Docker Desktop.exe next to the docker.exe we found (...\\Docker\\resources\\bin\\docker.exe), or None."""
    if not docker_path:
        return None
    cands = []
    here = os.path.dirname(docker_path)
    for up in (3, 2):
        base = here
        for _ in range(up):
            base = os.path.dirname(base)
        cands.append(os.path.join(base, "Docker Desktop.exe"))
    for var in ("ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(var):
            cands.append(os.path.join(os.environ[var], "Docker", "Docker", "Docker Desktop.exe"))
            cands.append(os.path.join(os.environ[var], "Programs", "DockerDesktop", "Docker Desktop.exe"))
    return next((c for c in cands if os.path.isfile(c)), None)


def wake_docker(on_msg=None, cancel=None, timeout_s=150, poll_s=3, status=None):
    """Start Docker Desktop (or wake it from Resource Saver) and wait until the engine answers.

    Returns the final state ('ready', 'not_running' or 'missing'). `status` is replaceable for tests."""
    status = status or docker_status
    say = on_msg or (lambda t: None)
    state, d = status()
    if state != "not_running":
        return state
    say("Starting Docker Desktop...")
    started = False
    try:                                    # newer Docker versions can start themselves from the command line
        subprocess.Popen([d, "desktop", "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=NOWIN)
        started = True
    except Exception:
        pass
    exe = desktop_exe(d)
    if exe:
        try:
            subprocess.Popen([exe], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NOWIN)
            started = True
        except Exception:
            pass
    if not started:
        say("Could not start Docker Desktop by itself. Open it from the Start menu.")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if cancel is not None and cancel.is_set():
            return "not_running"
        time.sleep(poll_s)
        state, d = status()
        if state == "ready":
            say("Docker Desktop is ready.")
            return "ready"
        say(f"Waiting for Docker Desktop to start... {int(time.time() - t0)}s")
    return "not_running"


def resume_dir(out_path, url):
    """The folder where this site's unfinished crawl is kept between runs."""
    import hashlib
    folder = os.path.dirname(os.path.abspath(out_path))
    key = hashlib.sha1(url.strip().lower().rstrip("/").encode("utf-8")).hexdigest()[:10]   # per site, not per file name
    return os.path.join(folder, f".recon_resume_{key}")


def unfinished(out_path, url):
    """The kept folder of an earlier unfinished run of this site (with something collected), or None."""
    d = resume_dir(out_path, url)
    b = os.path.join(d, "build")
    try:
        if os.path.isdir(b) and any(True for _ in os.scandir(b)):
            return d
    except OSError:
        pass
    return None


def discard_unfinished(out_path, url):
    shutil.rmtree(resume_dir(out_path, url), ignore_errors=True)


def run_zimit(url, out_path, o, on_event, cancel, resume=True):
    """Run Zimit for `url` and place the finished ZIM at `out_path`.

    Returns a dict: size, pages, failed. Raises RuntimeError with a readable
    message on any problem (including 'Cancelled').
    """
    state, docker = docker_status()
    if state == "not_running":      # asleep (Resource Saver) or not started: wake it instead of making the person do it
        state = wake_docker(lambda t: on_event({"type": "phase", "text": t}), cancel)
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        state, docker = docker_status() if state == "ready" else (state, docker)
    if state == "missing":
        raise RuntimeError(
            "Docker Desktop is required for the Cindrix Recon engine but wasn't found.\n\n"
            f"Install it from {DOCKER_URL}, start it, then click Convert again.\n"
            "(Or switch the engine to 'Built-in' for simple sites.)")
    if state == "not_running":
        raise RuntimeError(
            "Docker Desktop is installed but not running.\n\n"
            "The program tried to start it but it did not come up in time. Open Docker Desktop yourself, wait until it says the engine is running, then click Convert again.")

    folder = os.path.dirname(os.path.abspath(out_path))
    name = os.path.splitext(os.path.basename(out_path))[0]
    stamp = int(time.time())
    container = f"recon-{stamp}"
    work = resume_dir(out_path, url)
    if not resume:
        shutil.rmtree(work, ignore_errors=True)       # the person chose to start over
    elif unfinished(out_path, url):
        on_event({"type": "log", "text": "Continuing an earlier unfinished run: pages already collected are reused."})
        on_event({"type": "phase", "text": "Continuing an earlier run of this site..."})
    os.makedirs(work, exist_ok=True)
    finished = []
    tail, stats = [], {"crawled": 0, "failed": 0, "limit": None}

    def on_line(raw):
        for ev in parse_line(raw):
            if ev["type"] == "stats":
                stats["crawled"], stats["failed"] = ev["crawled"], ev["failed"]
            if ev["type"] == "limit":
                stats["limit"] = stats["limit"] or ev["which"]
            if ev["type"] == "log":
                tail.append(ev["text"])
                del tail[:-12]
            on_event(ev)

    def run(cmd, label):
        on_event({"type": "phase", "text": label})
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=NOWIN)
        done = threading.Event()

        def watch():
            while not done.is_set():
                if cancel.is_set():
                    if cmd[1] == "run":
                        on_event({"type": "phase", "text": "Stopping the crawler..."})
                        for attempt in range(3):         # the container, by every means, until it is really gone
                            for stop in ([docker, "kill", container], [docker, "rm", "-f", container]):
                                try:
                                    r = subprocess.run(stop, capture_output=True, text=True, timeout=30,
                                                       stdin=subprocess.DEVNULL, creationflags=NOWIN)
                                    if r.returncode != 0 and (r.stderr or "").strip():
                                        on_event({"type": "log", "text": "docker: " + r.stderr.strip().splitlines()[-1]})
                                except Exception as e:
                                    on_event({"type": "log", "text": f"docker stop attempt failed ({e.__class__.__name__})"})
                            try:
                                proc.wait(timeout=5)
                                return
                            except subprocess.TimeoutExpired:
                                pass
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return
                time.sleep(0.3)
        threading.Thread(target=watch, daemon=True).start()
        try:
            return _stream(proc, on_line)
        finally:
            done.set()

    try:
        rc = run([docker, "pull", IMAGE], "Getting the Cindrix Recon crawler (first run downloads about 1 GB)...")
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        if rc != 0:
            have = subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NOWIN)
            if have.returncode != 0:
                raise RuntimeError("Couldn't download the crawler image. Check your internet connection and that "
                                   "Docker Desktop is running.\n\n" + "\n".join(tail[-4:]))
            on_event({"type": "log", "text": "Using the previously downloaded crawler image."})

        def list_zims():
            return [os.path.join(work, f) for f in os.listdir(work) if f.lower().endswith(".zim")]

        extras, legacy, dropped = bool(optional_flags(o)), False, set()
        for _attempt in range(4):
            cmd = build_command(docker, container, work, url, name, o, extras=extras, legacy=legacy, build=True)
            if dropped:
                cmd = drop_flags(cmd, dropped)
            on_event({"type": "log", "text": "> " + " ".join(cmd)})
            del tail[:]
            for f in os.listdir(work):                    # a ZIM left by an earlier attempt must not be mistaken for this run's
                if f.lower().endswith(".zim"):
                    try:
                        os.remove(os.path.join(work, f))
                    except OSError:
                        pass
            rc = run(cmd, "Crawling the site with Cindrix Recon...")
            if cancel.is_set():
                raise RuntimeError("Cancelled")
            if rc == 0 or list_zims():
                break
            text = " ".join(tail)
            if "unrecognized arguments" in text and ("SoftLimit" in text) and not legacy and (o.max_mb or o.max_minutes):
                legacy = True       # an older crawler image: it knows the old size/time option names
                on_event({"type": "log", "text": "Older crawler version - retrying with its size/time option names."})
                continue
            bad = unrecognized_flags(text) - dropped - {"--seeds", "--name", "--output"}
            if bad:     # this crawler version does not know some option: leave it out and carry on
                dropped |= bad
                on_event({"type": "warn", "text": "This crawler version does not know the option(s) " + ", ".join(sorted(bad))
                                                  + ", so they were left out of this run."})
                continue
            if extras:
                on_event({"type": "log", "text": "The crawler didn't accept the detail/depth options - retrying without them. "
                                                 "Pictures and files will not be filtered this run."})
                on_event({"type": "warn", "text": "The crawler ignored the detail level / depth options, so this run saved "
                                                  "everything it normally would."})
                extras = False
                continue
            break

        zims = list_zims()
        if not zims:
            raise RuntimeError(f"The crawler ended (exit code {rc}) without creating a ZIM file.\n\n"
                               + "\n".join(tail[-6:]))
        newest = max(zims, key=os.path.getmtime)
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.move(newest, out_path)
        finished.append(True)
        limit = stats["limit"]
        if not limit and o.max_pages and stats["crawled"] >= o.max_pages:
            limit = "pages"
        return {"size": os.path.getsize(out_path), "pages": stats["crawled"], "failed": stats["failed"],
                "limit_hit": limit}
    finally:
        if finished or not resume:
            shutil.rmtree(work, ignore_errors=True)       # done: nothing to continue. Interrupted: the work is kept.
