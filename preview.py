"""Open a finished ZIM: in Kiwix if installed, or in the browser via a local preview server."""
import glob
import os
import shutil
import subprocess
import webbrowser

from zimreader import PreviewServer

_servers = []


def find_kiwix():
    p = shutil.which("kiwix-desktop") or shutil.which("kiwix-desktop.exe")
    if p:
        return p
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")]
        for r in roots:
            if not r:
                continue
            for pat in ("Kiwix*\\kiwix-desktop.exe", "Kiwix*\\*\\kiwix-desktop.exe", "Programs\\Kiwix*\\kiwix-desktop.exe"):
                hits = glob.glob(os.path.join(r, pat))
                if hits:
                    return hits[0]
    return None


def open_in_kiwix(zim_path):
    exe = find_kiwix()
    if not exe:
        return False
    subprocess.Popen([exe, zim_path])
    return True


def preview_in_browser(zim_path):
    """Start a local server for the ZIM and open it. Returns the URL (raises ZimError if unreadable)."""
    srv = PreviewServer(zim_path)
    _servers.append(srv)
    webbrowser.open(srv.url)
    return srv.url
