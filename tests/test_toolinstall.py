"""Helper-program installer: download from a local fake server, run a fake installer, find the result."""
import http.server, os, sys, tempfile, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["APPDATA"] = tempfile.mkdtemp()
import toolinstall, bookread, prefs

FAILS = []
def check(n, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + n, "" if ok else extra)
    if not ok: FAILS.append(n)

BIG = b"MZ" + b"x" * 1_200_000
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/download.html":
            body = b'<a href="a/7z2603-x64.exe">x</a> <a href="a/7z2603-arm64.exe">y</a>'
        elif self.path == "/tiny":
            body = b"<html>error</html>"
        else:
            body = BIG
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = http.server.HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

try:
    # silent install works
    def run_ok(cmd):
        os.makedirs(toolinstall.tool_dir("ddjvu"), exist_ok=True)
        assert "/S" in cmd and cmd[-1].startswith("/D=")
        open(os.path.join(toolinstall.tool_dir("ddjvu"), "ddjvu"), "w").write("x")
    seen = []
    p = toolinstall.install("ddjvu", url=base + "/setup", run=run_ok, on_progress=lambda d, t: seen.append((d, t)))
    check("silent install found", p == toolinstall.installed_path("ddjvu") and seen and seen[-1][0] == len(BIG))
    check("program now finds it automatically", bookread.find_tool("ddjvu") == p)
    # falls back to the normal installer window when quiet mode installs nothing
    calls = []
    def run_fallback(cmd):
        calls.append(cmd)
        if len(cmd) == 1:
            os.makedirs(toolinstall.tool_dir("7z"), exist_ok=True)
            open(os.path.join(toolinstall.tool_dir("7z"), "7z"), "w").write("x")
    p = toolinstall.install("7z", url=base + "/setup", run=run_fallback)
    check("fallback to normal installer", len(calls) == 2 and p)
    # nothing installed
    try:
        toolinstall.install("7z" if False else "calibre", url=base + "/setup", run=lambda c: None, find=lambda: None); ok = False
    except toolinstall.InstallError as e:
        ok = "was not found" in str(e)
    check("reports when nothing was installed", ok)
    # msi uses msiexec
    got = []
    toolinstall.install("calibre", url=base + "/setup", run=lambda c: got.append(c), find=lambda: "/x/ebook-convert")
    check("calibre uses msiexec", got and got[0][0] == "msiexec" and got[0][1] == "/i")
    # tiny download refused
    try:
        toolinstall.install("ddjvu", url=base + "/tiny", run=lambda c: None); ok = False
    except toolinstall.InstallError as e:
        ok = "too small" in str(e)
    check("error page refused", ok)
    # 7-zip link read from the page, x64 not arm64
    spec = toolinstall.SPECS["7z"]; old = dict(spec)
    spec.update(page=base + "/download.html", base=base + "/")
    check("7-zip link found", toolinstall.resolve_url("7z") == base + "/a/7z2603-x64.exe", toolinstall.resolve_url("7z"))
    spec.update(old)
    # dead server
    try:
        toolinstall.install("ddjvu", url="http://127.0.0.1:9/x", run=lambda c: None); ok = False
    except toolinstall.InstallError as e:
        ok = "Could not download" in str(e)
    check("offline gives a plain message", ok)
    # cancel
    ev = threading.Event(); ev.set()
    try:
        toolinstall.install("ddjvu", url=base + "/setup", run=lambda c: None, cancel=ev); ok = False
    except toolinstall.InstallError as e:
        ok = "Cancelled" in str(e)
    check("cancel", ok)
finally:
    srv.shutdown()
print("ALL PASSED" if not FAILS else "FAILED: " + ", ".join(FAILS)); sys.exit(1 if FAILS else 0)
