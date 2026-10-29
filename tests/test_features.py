"""Tests for: ZIM reader/preview server, sitemap estimate, settings storage.

Run from the repo root:  python tests/test_features.py
"""
import functools
import gzip
import http.server
import os
import sys
import tempfile
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings as appsettings  # noqa: E402
from crawler import Options, convert  # noqa: E402
from sitecheck import check_site, count_sitemap_pages, estimate_size_text  # noqa: E402
from test_local import Quiet, make_site  # noqa: E402
from zimreader import PreviewServer, ZimReader  # noqa: E402


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "", e.read()


def test_preview():
    tmp = tempfile.mkdtemp()
    site = os.path.join(tmp, "site")
    os.makedirs(site)
    make_site(site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = os.path.join(tmp, "t.zim")
    convert(f"http://127.0.0.1:{srv.server_address[1]}/", out, Options(workers=3))
    z = ZimReader(out)
    assert z.verify_checksum() and z.metadata("Title") == "Test Home" and z.main_path().endswith("index.html")
    host = z.main_path().split("/")[0]
    z.close()
    pv = PreviewServer(out)
    st, ct, body = get(pv.url)                      # "/" -> redirect -> main page
    assert st == 200 and b"<h1>Home</h1>" in body and "text/html" in ct, (st, ct)
    st, ct, css = get(pv.url + host + "/style.css")
    assert st == 200 and b'url("img/logo.png")' in css and "text/css" in ct
    st, ct, png = get(pv.url + host + "/img/logo.png")
    assert st == 200 and png.startswith(b"\x89PNG") and ct.startswith("image/png")
    st, _, _ = get(pv.url + "nope/missing.html")
    assert st == 404
    st, _, page_a = get(pv.url + host + "/docs/a.html")
    assert st == 200 and b"Page A" in page_a
    pv.stop()
    srv.shutdown()
    print("preview server ok")


def test_sitemap():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path == "/robots.txt":
                body, ct = f"User-agent: *\nAllow: /\nSitemap: {base}/sm-index.xml\n", "text/plain"
            elif self.path == "/sm-index.xml":
                body, ct = (f"<sitemapindex><sitemap><loc>{base}/sm1.xml</loc></sitemap>"
                            f"<sitemap><loc>{base}/sm2.xml.gz</loc></sitemap></sitemapindex>"), "application/xml"
            elif self.path == "/sm1.xml":
                body, ct = "<urlset>" + "".join(f"<url><loc>{base}/a{i}</loc></url>" for i in range(40)) + "</urlset>", "application/xml"
            elif self.path == "/sm2.xml.gz":
                body = gzip.compress(("<urlset>" + "".join(f"<url><loc>{base}/b{i}</loc></url>" for i in range(60)) + "</urlset>").encode())
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                return
            elif self.path == "/":
                body, ct = "<html><head><title>x</title></head><body><h1>Hi</h1>" + "text " * 100 + "</body></html>", "text/html"
            else:
                self.send_response(404); self.end_headers(); return
            data = body.encode() if isinstance(body, str) else body
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    robots = urllib.request.urlopen(base + "/robots.txt").read().decode()
    assert count_sitemap_pages(base, robots, "ua") == 100
    r = check_site(base + "/", "zimit")
    assert r.level == "ok" and r.pages == 100, r
    assert any("100 pages" in d for d in r.details), r.details
    assert "100 pages" in estimate_size_text(100) and "GB" in estimate_size_text(5000)
    assert count_sitemap_pages("http://127.0.0.1:1", "", "ua") is None
    s.shutdown()
    print("sitemap estimate ok:", estimate_size_text(100))


def test_settings():
    p = os.path.join(tempfile.mkdtemp(), "x", "settings.json")
    assert appsettings.load(p) == {}
    assert appsettings.save({"folder": "D:\\ZIM", "workers": 6}, p)
    assert appsettings.load(p) == {"folder": "D:\\ZIM", "workers": 6}
    open(p, "w").write("{broken")
    assert appsettings.load(p) == {}
    print("settings ok")


if __name__ == "__main__":
    test_preview()
    test_sitemap()
    test_settings()
    print("ALL FEATURE TESTS PASSED")
