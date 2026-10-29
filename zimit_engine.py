"""Zimit engine: runs the official openZIM Zimit Docker image (real Chromium
crawl via Browsertrix, handles JavaScript sites) and streams its progress.

Docker Desktop is the only external requirement for this engine.
"""
import json
import os
import shutil
import subprocess
import threading
import time

IMAGE = "ghcr.io/openzim/zimit"
NOWIN = 0x08000000 if os.name == "nt" else 0
DOCKER_URL = "https://www.docker.com/products/docker-desktop/"

_WIN_DOCKER_PATHS = (
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\ProgramData\DockerDesktop\version-bin\docker.exe",
)


def find_docker():
    p = shutil.which("docker")
    if p:
        return p
    if os.name == "nt":
        for c in _WIN_DOCKER_PATHS:
            if os.path.exists(c):
                return c
    return None


def docker_status():
    """Returns (state, docker_path): state is 'ready', 'not_running' or 'missing'."""
    d = find_docker()
    if not d:
        return "missing", None
    try:
        r = subprocess.run([d, "info"], capture_output=True, text=True, timeout=25, creationflags=NOWIN)
    except Exception:
        return "not_running", d
    return ("ready" if r.returncode == 0 else "not_running"), d


def build_command(docker, container, work_dir, url, name, o):
    cmd = [docker, "run", "--rm", "--name", container, "--shm-size=1g",
           "-v", f"{work_dir}:/output", IMAGE, "zimit",
           "--seeds", url, "--name", name, "--output", "/output",
           "--workers", str(max(1, o.workers))]
    if o.max_pages:
        cmd += ["--pageLimit", str(o.max_pages)]
    if o.max_mb:
        cmd += ["--sizeLimit", str(o.max_mb * 1024 * 1024)]
    if o.max_minutes:
        cmd += ["--timeLimit", str(o.max_minutes * 60)]
    scope = "prefix" if o.path_scope else ("domain" if o.subdomains else "host")
    cmd += ["--scopeType", scope]
    return cmd


def parse_line(line):
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


def run_zimit(url, out_path, o, on_event, cancel):
    """Run Zimit for `url` and place the finished ZIM at `out_path`.

    Returns a dict: size, pages, failed. Raises RuntimeError with a readable
    message on any problem (including 'Cancelled').
    """
    state, docker = docker_status()
    if state == "missing":
        raise RuntimeError(
            "Docker Desktop is required for the Zimit engine but wasn't found.\n\n"
            f"Install it from {DOCKER_URL}, start it, then click Convert again.\n"
            "(Or switch the engine to 'Built-in' for simple sites.)")
    if state == "not_running":
        raise RuntimeError(
            "Docker Desktop is installed but not running.\n\n"
            "Start Docker Desktop, wait until it says the engine is running, then click Convert again.")

    folder = os.path.dirname(os.path.abspath(out_path))
    name = os.path.splitext(os.path.basename(out_path))[0]
    stamp = int(time.time())
    container = f"winzim-{stamp}"
    work = os.path.join(folder, f".winzim_work_{stamp}")
    os.makedirs(work, exist_ok=True)
    tail, stats = [], {"crawled": 0, "failed": 0}

    def on_line(raw):
        for ev in parse_line(raw):
            if ev["type"] == "stats":
                stats["crawled"], stats["failed"] = ev["crawled"], ev["failed"]
            if ev["type"] == "log":
                tail.append(ev["text"])
                del tail[:-12]
            on_event(ev)

    def run(cmd, label):
        on_event({"type": "phase", "text": label})
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=NOWIN)
        done = threading.Event()

        def watch():
            while not done.is_set():
                if cancel.is_set():
                    if cmd[1] == "run":
                        subprocess.run([docker, "kill", container], capture_output=True, creationflags=NOWIN)
                    else:
                        proc.kill()
                    return
                time.sleep(0.3)
        threading.Thread(target=watch, daemon=True).start()
        try:
            return _stream(proc, on_line)
        finally:
            done.set()

    try:
        rc = run([docker, "pull", IMAGE], "Getting Zimit (first run downloads about 1 GB)...")
        if cancel.is_set():
            raise RuntimeError("Cancelled")
        if rc != 0:
            have = subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True, creationflags=NOWIN)
            if have.returncode != 0:
                raise RuntimeError("Couldn't download the Zimit image. Check your internet connection and that "
                                   "Docker Desktop is running.\n\n" + "\n".join(tail[-4:]))
            on_event({"type": "log", "text": "Using the previously downloaded Zimit image."})

        cmd = build_command(docker, container, work, url, name, o)
        on_event({"type": "log", "text": "> " + " ".join(cmd)})
        rc = run(cmd, "Crawling the site with Zimit...")
        if cancel.is_set():
            raise RuntimeError("Cancelled")

        zims = [os.path.join(work, f) for f in os.listdir(work) if f.lower().endswith(".zim")]
        if not zims:
            raise RuntimeError(f"Zimit ended (exit code {rc}) without creating a ZIM file.\n\n"
                               + "\n".join(tail[-6:]))
        newest = max(zims, key=os.path.getmtime)
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.move(newest, out_path)
        return {"size": os.path.getsize(out_path), "pages": stats["crawled"], "failed": stats["failed"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
