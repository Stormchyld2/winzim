"""Install the free helper programs (DjVuLibre, 7-Zip, Calibre) from inside the program, one button each.

Cindrix Recon does not bundle them. It downloads each program's official Windows installer, on your computer, from the
project's own site, and runs it for you. Nothing is signed or checked against a published checksum, so the program only
uses the official addresses below and tells you what it is doing.
"""
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

import settings as appsettings

MIN_BYTES = 1_000_000          # a real installer is megabytes; anything tiny is an error page

# key -> name, where to get the installer, how to run it quietly into our own folder
SPECS = {
    "ddjvu": {"name": "DjVuLibre", "kind": "exe",
              "url": "https://downloads.sourceforge.net/project/djvu/DjVuLibre_Windows/3.5.27%2B4.10/"
                     "DjVuLibre-3.5.27_DjView-4.10.4_Setup.exe",
              "size": "a few MB"},
    "7z": {"name": "7-Zip", "kind": "exe", "page": "https://www.7-zip.org/download.html",
           "pattern": r'href="(a/7z\d+-x64\.exe)"', "base": "https://www.7-zip.org/", "size": "about 2 MB"},
    "calibre": {"name": "Calibre", "kind": "msi", "url": "https://calibre-ebook.com/dist/win64", "size": "about 200 MB",
                "note": "Windows will ask for permission, because Calibre installs for the whole computer."},
}


class InstallError(Exception):
    pass


def tools_dir():
    return os.path.join(os.path.dirname(appsettings.settings_path()), "tools")


def tool_dir(key):
    return os.path.join(tools_dir(), SPECS[key]["name"])


def installed_path(key):
    exe = {"ddjvu": "ddjvu", "7z": "7z"}.get(key)
    if not exe:
        return None
    p = os.path.join(tool_dir(key), exe + (".exe" if sys.platform == "win32" else ""))
    return p if os.path.isfile(p) else None


def _get(url, opener=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "CindrixRecon"})
    return opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)


def resolve_url(key, opener=None):
    spec = SPECS[key]
    if "url" in spec:
        return spec["url"]
    try:
        with _get(spec["page"], opener) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        raise InstallError(f"Could not read the {spec['name']} download page ({e.__class__.__name__}). Use the Get it button.")
    m = re.search(spec["pattern"], text)
    if not m:
        raise InstallError(f"Could not find the {spec['name']} installer on its download page. Use the Get it button.")
    return spec["base"] + m.group(1)


def download(url, dest, on_progress=None, cancel=None, opener=None):
    try:
        with _get(url, opener) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel and cancel.is_set():
                    raise InstallError("Cancelled.")
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError(f"Could not download it ({e.__class__.__name__}: {e}). Check your connection or use the Get it button.")
    if os.path.getsize(dest) < MIN_BYTES:
        raise InstallError("The download was too small to be the installer. Use the Get it button instead.")


def _real_run(cmd):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=1800, creationflags=0x08000000 if sys.platform == "win32" else 0).returncode


def install(key, on_progress=None, cancel=None, run=None, opener=None, url=None, find=None):
    """Download and install one helper; returns the path of its program. `run(cmd)` and `url` are replaceable for tests."""
    import bookread
    spec = SPECS[key]
    run = run or _real_run
    find = find or (lambda: installed_path(key) or bookread.find_tool(key))
    url = url or resolve_url(key, opener)
    target = tool_dir(key)
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "setup." + spec["kind"])
        download(url, f, on_progress, cancel, opener)
        if spec["kind"] == "msi":
            cmd = ["msiexec", "/i", f, "/qb"]            # shows a small progress box, asks Windows for permission
        else:
            os.makedirs(target, exist_ok=True)
            cmd = [f, "/S", "/D=" + target]               # quiet install into our own folder, no administrator needed
        try:
            run(cmd)
        except Exception:
            pass
        p = find()
        if not p and spec["kind"] == "exe":
            try:
                run([f])                                  # the normal installer window; the person clicks through it
            except Exception as e:
                raise InstallError(f"The installer would not start ({e.__class__.__name__}).")
            p = find()
        if not p:
            raise InstallError(f"The {spec['name']} installer finished but the program was not found. Install it with the Get it "
                               "button, then use Browse to point at it.")
        return p
