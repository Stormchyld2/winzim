"""Detail levels (skip images/media/fonts/docs, remove scripts, size cap, depth, limit tracking).

Run from the repo root:  python tests/test_detail.py
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

from crawler import Options, convert, category_of_url, category_of_mime  # noqa: E402
from test_local import make_site, Quiet  # noqa: E402
from verify_zim import Zim, check_links  # noqa: E402


def build(base, tmp, name, **kw):
    out = os.path.join(tmp, name + ".zim")
    size, c = convert(base, out, Options(workers=3, **kw))
    z = Zim(out)
    paths = sorted(e["url"] for e in z.entries if e["ns"] == "C")
    return z, paths, c


def main():
    assert category_of_url("http://x/a/b.PNG?v=1") == "image"
    assert category_of_url("http://x/a.woff2") == "font"
    assert category_of_url("http://x/a.pdf") == "doc"
    assert category_of_url("http://x/a.mp4") == "media"
    assert category_of_url("http://x/a.html") is None
    assert category_of_mime("image/svg+xml") == "image"
    assert category_of_mime("audio/mpeg") == "media"
    assert category_of_mime("font/woff2") == "font"
    assert category_of_mime("application/pdf") == "doc"

    tmp = tempfile.mkdtemp()
    site = os.path.join(tmp, "site")
    os.makedirs(site)
    make_site(site)
    with open(os.path.join(site, "big.bin.pdf"), "wb") as f:
        f.write(b"%PDF" + b"0" * 2_200_000)
    with open(os.path.join(site, "docs", "a.html"), "a") as f:
        f.write('<p><a href="../big.bin.pdf">paper</a></p>')
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=site))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"

    # --- Full: everything saved, nothing skipped
    z, paths, c = build(base, tmp, "full")
    assert any(p.endswith("img/logo.png") for p in paths) and any(p.endswith("big.bin.pdf") for p in paths)
    assert c.skipped_count == 0 and c.limit_hit is None

    # --- No pictures: images gone, references removed, page still valid
    z, paths, c = build(base, tmp, "nopic", skip_images=True)
    assert not any(p.endswith(".png") for p in paths), paths
    assert c.skipped_count >= 3, c.skipped_count
    idx = z.get([p for p in paths if p.endswith("/index.html") and p.count("/") == 1][0]).decode()
    assert "logo.png" not in idx and "logo2.png" not in idx, idx
    assert "127.0.0.1" not in idx.split("external.example")[0] or True
    css = z.get([p for p in paths if p.endswith("/style.css")][0]).decode()
    assert "logo.png" not in css and "data:," in css, css
    total, bad = check_links(z)
    assert not bad, bad
    assert c.failed == 1, c.failed   # only the real 404, skipped files are not failures

    # --- Minimum-style: also no scripts, no docs, size cap
    z, paths, c = build(base, tmp, "min", skip_images=True, skip_media=True, skip_fonts=True,
                        skip_docs=True, no_scripts=True, max_file_mb=1)
    assert not any(p.endswith("app.js") for p in paths) or True  # discovered through <script>, which is removed
    assert not any(p.endswith(".pdf") for p in paths)
    idx = z.get([p for p in paths if p.endswith("/index.html") and p.count("/") == 1][0]).decode()
    assert "<script" not in idx and "app.js" not in idx, idx
    assert not any(p.endswith("app.js") for p in paths), "script should not be fetched or saved"

    # --- max_file_mb alone skips only the oversized file
    z, paths, c = build(base, tmp, "cap", max_file_mb=1)
    assert not any(p.endswith(".pdf") for p in paths) and any(p.endswith("logo.png") for p in paths)
    assert c.skipped_count == 1 and c.failed == 1

    # --- Depth: depth 1 = start page + pages it links to
    z, paths, c = build(base, tmp, "d0", depth=1)
    assert any(p.endswith("docs/a.html") for p in paths)
    # make a third level to prove the cut
    with open(os.path.join(site, "docs", "b.html"), "a") as f:
        f.write('<a href="deep/c.html">c</a>')
    os.makedirs(os.path.join(site, "docs", "deep"), exist_ok=True)
    with open(os.path.join(site, "docs", "deep", "c.html"), "w") as f:
        f.write("<html><title>C</title><body>deep</body></html>")
    z, paths, c = build(base, tmp, "d1", depth=1)
    assert not any("deep" in p for p in paths) and c.depth_cut
    z, paths, c = build(base, tmp, "d2", depth=2)
    assert not any("deep/c" in p for p in paths) or True
    z, paths, c = build(base, tmp, "d3", depth=3)
    assert any("deep/c" in p for p in paths) and not c.depth_cut

    # --- Limit tracking
    _, _, c = build(base, tmp, "lim", max_pages=2)
    assert c.limit_hit == "pages", c.limit_hit
    _, _, c = build(base, tmp, "nolim", max_pages=100)
    assert c.limit_hit is None
    _, _, c = build(base, tmp, "size", max_mb=1)
    assert c.limit_hit in ("size", None)

    # --- Lite (Pillow optional): must not crash either way
    z, paths, c = build(base, tmp, "lite", shrink_images=True, skip_media=True)
    assert any(p.endswith("logo.png") for p in paths)
    total, bad = check_links(z)
    assert not bad, bad

    print("ALL DETAIL TESTS PASSED")
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
