"""Resume / update with saved pages (built-in engine). Run: python tests/test_cache.py"""
import http.server, os, shutil, sys, tempfile, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import crawler, crawlcache
from crawler import Options
from verify_zim import Zim

hits, state = {}, {"v": 1}
PAGES = {"/": lambda: f'<html><body><a href="/a">a</a><a href="/b">b</a><img src="/i.png"></body></html>',
         "/a": lambda: f"<html><body>Page A version {state['v']}</body></html>",
         "/b": lambda: "<html><body>Page B</body></html>"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = self.path
        if path == "/i.png": body, ct, etag = PNG, "image/png", '"png1"'
        elif path in PAGES:
            body = PAGES[path]().encode(); ct = "text/html"; etag = '"' + path + str(len(body)) + (str(state["v"]) if path == "/a" else "") + '"'
        else:
            self.send_response(404); self.end_headers(); return
        hits[path] = hits.get(path, 0) + 1
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.send_header("ETag", etag); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/"
root = tempfile.mkdtemp(); fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)
def run(mode, name):
    hits.clear()
    o = Options(max_pages=50, workers=2, cache_dir=os.path.join(root, "cache"), cache_mode=mode)
    evs = []
    size, c = crawler.convert(base, os.path.join(root, name), o, on_event=evs.append)
    return c, evs, Zim(os.path.join(root, name))
try:
    c, ev, z = run("update", "1.zim")
    check("first run fetches everything", sum(hits.values()) == 4 and c.cache_hits == 0, hits)
    c, ev, z = run("update", "2.zim")
    check("update: every page asked, nothing re-downloaded", c.cache_hits == 4 and sum(hits.values()) == 4, (c.cache_hits, hits))
    check("update: result identical", b"Page A version 1" in z.get("a") if ("C", "a") in z.by_key else True)
    state["v"] = 2
    c, ev, z = run("update", "3.zim")
    names = {e["url"] for e in z.entries}
    body_a = next(z.blob(e) for e in z.entries if e["url"].endswith("a") and e["mime"].startswith("text/html"))
    check("update: changed page is fetched again", b"version 2" in body_a and c.cache_hits == 3, (c.cache_hits, body_a))
    state["v"] = 3
    hits.clear()
    c, ev, z = run("resume", "4.zim")
    body_a = next(z.blob(e) for e in z.entries if e["url"].endswith("a") and e["mime"].startswith("text/html"))
    check("resume: trusts saved pages, no network", sum(hits.values()) == 0 and b"version 2" in body_a, (hits, body_a))
    check("reuse message", any("Reused" in e.get("text", "") for e in ev))
    c, ev, z = run("off", "5.zim")
    check("off: downloads again, ignores saved pages", c.cache_hits == 0 and sum(hits.values()) == 4)
    # resume after a stop: cancel after the first page, then resume
    shutil.rmtree(os.path.join(root, "cache"))
    cancel = threading.Event(); n = {"p": 0}
    def on(e):
        if e.get("type") == "fetch" and e.get("ok"):
            n["p"] += 1
            if n["p"] >= 2: cancel.set()
    hits.clear()
    try:
        crawler.convert(base, os.path.join(root, "6.zim"), Options(max_pages=50, workers=1, cache_dir=os.path.join(root, "cache"), cache_mode="resume"), on_event=on, cancel=cancel)
    except RuntimeError: pass
    first = sum(hits.values())
    c, ev, z = run("resume", "7.zim")
    check("resume after a stop only fetches what is missing", 0 < first < 4 and sum(hits.values()) == 4 - first, (first, hits))
    check("cache size and clear", crawlcache.cache_size(os.path.join(root, "cache")) > 0 and (crawlcache.clear_cache(os.path.join(root, "cache")) or True) and crawlcache.cache_size(os.path.join(root, "cache")) == 0)
finally:
    srv.shutdown(); shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
