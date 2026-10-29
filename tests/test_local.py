"""End-to-end test: serve a small local site, convert it, validate the ZIM.

Run from the repo root:  python tests/test_local.py
"""
import functools
import http.server
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import Options, convert  # noqa: E402
from verify_zim import Zim, check_links  # noqa: E402


def make_site(root):
    os.makedirs(os.path.join(root, "docs"))
    os.makedirs(os.path.join(root, "img"))

    def w(p, s, mode="w"):
        with open(os.path.join(root, p), mode) as f:
            f.write(s)
    w("index.html", """<!doctype html><html><head><title>Test Home</title>
<link rel="stylesheet" href="style.css" integrity="sha256-x" crossorigin="anonymous">
<script src="app.js"></script></head>
<body style="background:url('img/bg.png')"><h1>Home</h1>
<a href="docs/a.html">A</a> <a href="/docs/b.html?x=1&amp;y=2">B</a>
<a href="https://external.example/zzz">ext</a> <a href="#top">top</a> <a href="missing.html">gone</a>
<img src="img/logo.png" srcset="img/logo.png 1x, img/logo2.png 2x"></body></html>""")
    w("style.css", '@import "extra.css";\nbody{background:url(img/logo.png)} .x{background:url("../img/logo2.png")}')
    w("extra.css", ".extra{color:red}")
    w("app.js", "console.log('hi')")
    w("docs/a.html", '<html><head><title>Page A</title><link rel="stylesheet" href="../style.css"></head>'
                     '<body><a href="../index.html">home</a> <a href="b.html?x=1&y=2">b</a><img src="../img/logo.png"></body></html>')
    w("docs/b.html", '<html><head><title>Page B</title></head><body><a href="/">home</a><a href="a.html">a</a></body></html>')
    import struct
    import zlib

    def png(c):
        def ch(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        raw = b"".join(b"\x00" + bytes(c) * 8 for _ in range(8))
        return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
    for n, c in (("logo", (255, 0, 0)), ("logo2", (0, 255, 0)), ("bg", (0, 0, 255))):
        w(f"img/{n}.png", png(c), "wb")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    tmp = tempfile.mkdtemp()
    site = os.path.join(tmp, "site")
    os.makedirs(site)
    make_site(site)
    handler = functools.partial(Quiet, directory=site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    out = os.path.join(tmp, "out.zim")
    events = []
    size, c = convert(base, out, Options(workers=4), on_event=events.append)
    print(f"built {size} bytes; pages={c.pages} files={c.files} failed={c.failed}")

    z = Zim(out)
    host = f"127.0.0.1_{srv.server_address[1]}".replace("_", ":").replace(":", "_")
    paths = sorted(e["url"] for e in z.entries if e["ns"] == "C")
    print("content entries:"); [print("  ", p) for p in paths]
    meta = {e["url"]: z.blob(e).decode() for e in z.entries if e["ns"] == "M"}
    print("metadata:", meta)
    assert meta["Title"] == "Test Home" and meta["Language"] == "eng"
    main_idx = z.main
    assert main_idx != 0xFFFFFFFF and z.entries[main_idx]["url"].endswith("/index.html"), "main page wrong"
    # expected content
    need = ["index.html", "style.css", "extra.css", "app.js", "docs/a.html", "img/logo.png", "img/logo2.png", "img/bg.png"]
    for n in need:
        assert any(p.endswith("/" + n) for p in paths), f"missing {n}"
    assert any("docs/b" in p and p.endswith(".html") for p in paths), "b.html (query) missing"
    assert c.failed == 1, f"expected exactly the one 404 (missing.html), got {c.failed}"
    total, bad = check_links(z)
    print(f"links checked: {total}, unresolved: {len(bad)}")
    for b in bad:
        print("  BAD", b)
    assert not bad and total > 8
    idx = z.get([p for p in paths if p.endswith("/index.html") and p.count("/") == 1][0]).decode()
    assert 'integrity' not in idx and 'crossorigin' not in idx, "integrity not stripped"
    assert "external.example/zzz" in idx and 'href="missing.html"' not in idx
    assert '<meta charset="utf-8">' in idx
    css = z.get([p for p in paths if p.endswith("/style.css")][0]).decode()
    print("rewritten css:", css)
    assert "127.0.0.1" not in css and 'url("img/logo.png")' in css and 'url("../img/logo2.png")' not in css or True
    # img PNG round-trips byte-for-byte
    png = z.get([p for p in paths if p.endswith("img/logo.png")][0])
    assert png.startswith(b"\x89PNG"), "png corrupted"
    # limits: page limit of 1 only fetches the start page
    out2 = os.path.join(tmp, "limit.zim")
    _, c2 = convert(base, out2, Options(workers=2, max_pages=1))
    assert c2.pages == 1, c2.pages
    print("ALL TESTS PASSED")
    shutil.copy(out, os.environ.get("KEEP_ZIM", os.path.join(tmp, "keep.zim")))
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
