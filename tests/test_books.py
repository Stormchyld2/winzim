"""Book Finder logic against a fake book site and fake catalogs (all on localhost).

Run from the repo root:  python tests/test_books.py
This does not talk to the real catalogs; it checks Cindrix Recon's own parsing, testing, ranking and downloading.
"""
import csv
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import books  # noqa: E402


def make_pdf(pages, compress=False, pad=0, linearized=None):
    if compress:
        inner = b"2 0 << /Type /Pages /Kids [] /Count %d >>" % pages
        z = zlib.compress(inner)
        body = (b"%%PDF-1.5\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n4 0 obj\n<< /Type /ObjStm /N 1 /First 4 "
                b"/Filter /FlateDecode /Length %d >>\nstream\n" % len(z) + z + b"\nendstream\nendobj\n%%EOF\n")
    else:
        body = (b"%PDF-1.4\n" + (b"<< /Linearized 1 /L 3000000 /N %d /O 5 >>\n" % linearized if linearized else b"")
                + b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count %d >>\n"
                  b"endobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n%%%%EOF\n" % pages)
    return body + b"0" * pad


EPUB = b"PK\x03\x04" + b"epubdata" * 400
LOGIN = b"<html><head><title>Sign in</title></head><body><form>password</form></body></html>"
ERRPAGE = b"<!doctype html><html><body>Oops, something went wrong</body></html>"

INDEX = b"""<html><head><title>Manuals Library</title>
<link rel="alternate" type="application/atom+xml;profile=opds-catalog" href="/opds"></head><body>
<h2>US Army Survival Manual</h2>
<a href="/books/survival.pdf">US Army Survival Manual (PDF)</a> <a href="/books/survival.epub">US Army Survival Manual [EPUB]</a>
<h2>Where There Is No Doctor</h2><p><a href="/books/medicine.pdf">Download</a></p>
<a href="/members/premium.pdf">Premium Tactical Medicine</a>
<a href="/books/broken.pdf">Broken Book</a>
<a href="/robots.txt">robots.txt</a>
<a href="/about.html">About</a> <a href="/private/secret.html">Secret</a> <a href="https://elsewhere.example/x.pdf">off-site</a>
</body></html>"""
ABOUT = b"""<html><body><a href="/books/water.pdf" title="Water Purification Handbook">PDF</a>
<a href="/books/big.pdf">Linearized Giant</a><a href="/books/compressed.pdf">Compressed Pages Book</a>
<a href="/private/hidden.pdf">Hidden Book</a></body></html>"""
OPDS1 = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
<link rel="next" href="/opds?page=2" type="application/atom+xml;profile=opds-catalog"/>
<entry><title>Ham Radio License Manual</title><author><name>R. Operator</name></author><dc:issued>2015-03-01</dc:issued>
<link rel="http://opds-spec.org/acquisition" href="/books/ham.pdf" type="application/pdf" length="3000"/>
<link rel="http://opds-spec.org/acquisition" href="/books/ham.epub" type="application/epub+zip"/></entry>
<entry><title>Shelf: Science</title><link rel="subsection" href="/opds/science" type="application/atom+xml;profile=opds-catalog"/></entry>
</feed>"""
OPDS2 = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Basic Gardening for Beginners</title><author><name>G. Green</name></author>
<link rel="http://opds-spec.org/acquisition/open-access" href="/books/garden.epub" type="application/epub+zip"/></entry></feed>"""
OPDS_SCI = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Edible Wild Plants</title><link rel="http://opds-spec.org/acquisition" href="/books/plants.pdf" type="application/pdf"/></entry></feed>"""

FILES = {
    "/books/survival.pdf": ("application/pdf", make_pdf(264)),
    "/books/survival.epub": ("application/epub+zip", EPUB),
    "/books/medicine.pdf": ("application/pdf", make_pdf(512, pad=20000)),
    "/books/broken.pdf": ("text/html", ERRPAGE),
    "/books/water.pdf": ("application/pdf", make_pdf(96)),
    "/books/big.pdf": ("application/pdf", make_pdf(1, pad=3_000_000, linearized=42)),
    "/books/compressed.pdf": ("application/pdf", make_pdf(77, compress=True)),
    "/books/ham.pdf": ("application/pdf", make_pdf(340)),
    "/books/ham.epub": ("application/epub+zip", EPUB),
    "/books/garden.epub": ("application/epub+zip", EPUB),
    "/books/plants.pdf": ("application/pdf", make_pdf(402)),
    "/private/hidden.pdf": ("application/pdf", make_pdf(5)),
    # catalog downloads
    "/ia/download/wtind/wtind.pdf": ("application/pdf", make_pdf(512, pad=5000)),
    "/ia/download/wtind/wtind.epub": ("application/epub+zip", EPUB),
    "/gut/files/walden.epub": ("application/epub+zip", EPUB),
    "/gut/files/walden.txt": ("text/plain; charset=utf-8", b"Walden by Thoreau. " * 3000),
}
hits_log = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        hits_log.append(self.path)
        j = lambda obj, code=200: self.send(code, "application/json", json.dumps(obj).encode())  # noqa: E731
        if p == "/":
            return self.send(200, "text/html", INDEX)
        if p == "/about.html":
            return self.send(200, "text/html", ABOUT)
        if p == "/robots.txt":
            return self.send(200, "text/plain", b"User-agent: *\nDisallow: /private/\n")
        if p == "/opds":
            return self.send(200, "application/atom+xml", OPDS2 if q.get("page") == ["2"] else OPDS1)
        if p == "/opds/science":
            return self.send(200, "application/atom+xml", OPDS_SCI)
        if p == "/members/premium.pdf":
            return self.send(302, "text/html", b"", {"Location": "/login?next=/members/premium.pdf"})
        if p == "/login":
            return self.send(200, "text/html", LOGIN)
        if p in FILES:
            ctype, body = FILES[p]
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes=0-") and p.startswith("/books/ham"):   # this server honours Range
                end = int(rng.split("-")[1])
                return self.send(206, ctype, body[:end + 1], {"Content-Range": f"bytes 0-{min(end, len(body) - 1)}/{len(body)}"})
            return self.send(200, ctype, body)
        # ---------------- fake catalogs
        query = (q.get("q") or q.get("search") or q.get("query") or [""])[0].lower()
        if p == "/ol/search.json":
            if "no doctor" in query:
                return j({"docs": [{"key": "/works/OL1W", "title": "Where There Is No Doctor", "author_name": ["David Werner"],
                                     "first_publish_year": 1977, "ia": ["wtind"], "ebook_access": "public"}]})
            if "homestead" in query:
                return j({"docs": [{"key": "/works/OL2W", "title": "Modern Homestead Handbook", "author_name": ["J. Carter"],
                                     "first_publish_year": 2019, "ia": ["mhh"], "ebook_access": "borrowable"}]})
            return j({"docs": [{"key": "/works/OL3W", "title": "Walden", "author_name": ["Henry David Thoreau"],
                                 "first_publish_year": 1854, "ebook_access": "no_ebook"}]} if "walden" in query else {"docs": []})
        if p == "/ia/advancedsearch.php":
            if "no doctor" in query:
                return j({"response": {"docs": [{"identifier": "wtind", "title": "Where there is no doctor", "creator": "Werner, David",
                                                  "year": "1977"}, {"identifier": "junk", "title": "Unrelated Cooking Show"}]}})
            if "homestead" in query:
                return j({"response": {"docs": [{"identifier": "mhh", "title": "Modern Homestead Handbook", "creator": ["J. Carter"]}]}})
            return j({"response": {"docs": []}})
        if p == "/ia/metadata/wtind":
            return j({"metadata": {"collection": ["opensource"]}, "files": [
                {"name": "wtind.pdf", "format": "Text PDF", "size": "5600", "source": "original"},
                {"name": "wtind.epub", "format": "EPUB", "size": "3200", "source": "derivative"},
                {"name": "wtind_meta.xml", "format": "Metadata", "size": "10"}]})
        if p == "/ia/metadata/mhh":
            return j({"metadata": {"collection": ["inlibrary", "printdisabled"]}, "files": [
                {"name": "mhh_bw.pdf", "format": "Text PDF", "size": "9999"}]})
        if p == "/ia/metadata/junk":
            return j({"metadata": {}, "files": []})
        if p == "/gut/books/":
            if "walden" in query:
                return j({"results": [{"id": 205, "title": "Walden, and On The Duty Of Civil Disobedience",
                                       "authors": [{"name": "Thoreau, Henry David"}], "copyright": False,
                                       "formats": {"application/epub+zip": "BASE/gut/files/walden.epub",
                                                   "text/plain; charset=utf-8": "BASE/gut/files/walden.txt",
                                                   "text/html": "BASE/gut/files/walden.html"}}]})
            return j({"results": []})
        if p == "/gb/books/v1/volumes":
            flt = q.get("filter", [""])[0]
            if "walden" in query and flt == "free-ebooks":
                return j({"items": [{"volumeInfo": {"title": "Walden", "authors": ["Henry David Thoreau"], "publishedDate": "1854",
                                                    "pageCount": 352},
                                     "accessInfo": {"publicDomain": True, "epub": {"isAvailable": True, "downloadLink": "BASE/members/premium.pdf"}}}]})
            if "homestead" in query and flt == "paid-ebooks":
                return j({"items": [{"volumeInfo": {"title": "Modern Homestead Handbook", "authors": ["J. Carter"], "publishedDate": "2019"},
                                     "saleInfo": {"buyLink": "https://books.example/buy/mhh"}, "accessInfo": {}}]})
            return j({"items": []})
        if p == "/oapen/rest/search":
            return self.send(500, "text/plain", b"boom")
        self.send(404, "text/plain", b"not found")


def fix_base(obj, base):
    return json.loads(json.dumps(obj).replace("BASE", base)) if obj else obj


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    # make the fake gutendex/google answers point at this server
    orig_get_json = books.get_json
    books.get_json = lambda url, timeout=15: fix_base(orig_get_json(url, timeout), base)
    books.BASES.update({"openlibrary": base + "/ol", "archive": base + "/ia", "gutendex": base + "/gut",
                        "google": base + "/gb", "oapen": base + "/oapen"})
    tmp = tempfile.mkdtemp()

    # ---------------------------------------------------------------- pure helpers
    assert books.pdf_page_count(make_pdf(12)) == 12
    assert books.pdf_page_count(make_pdf(77, compress=True)) == 77, "page count inside compressed object stream"
    assert books.pdf_page_count(b"%PDF-1.4 nothing here") is None
    assert books.norm_key("The US Army Survival Manual (PDF)") == "us army survival manual"
    assert books.main_title_key("Where There Is No Doctor: A village health care handbook") == "where there is no doctor"
    assert books.clean_title("  Some Book  [EPUB] (2.3 MB) ") == "Some Book"
    assert books.relevance("where there is no doctor", "Where there is no doctor", "Werner") == 1.0
    assert books.relevance("water purification", "Unrelated Cooking Show") == 0.0
    r = books.Robots("User-agent: *\nDisallow: /private/\nAllow: /private/ok\n")
    assert not r.allowed("http://x/private/a") and r.allowed("http://x/private/ok/b") and r.allowed("http://x/public")
    b, pages = books.extract_page(INDEX.decode(), base + "/")
    titles = sorted((x["title"], x["fmt"]) for x in b)
    assert ("US Army Survival Manual", "PDF") in titles and ("US Army Survival Manual", "EPUB") in titles, titles
    assert ("Where There Is No Doctor", "PDF") in titles, "generic 'Download' text should use the heading"
    assert not any("robots" in x["title"].lower() for x in b), "robots.txt is not a book"
    assert base + "/about.html" in pages
    ents, nav, nxt = books.parse_opds(OPDS1.decode(), base + "/opds")
    assert len(ents) == 1 and ents[0]["author"] == "R. Operator" and ents[0]["year"] == 2015 and len(ents[0]["files"]) == 2
    assert nxt == base + "/opds?page=2" and nav == [base + "/opds/science"], (nxt, nav)
    print("helpers ok")

    # ---------------------------------------------------------------- probe
    ok = books.probe(base + "/books/survival.pdf")
    assert ok["ok"] and ok["pages"] == 264 and ok["size"] == len(FILES["/books/survival.pdf"][1]), ok
    ham = books.probe(base + "/books/ham.pdf")                       # server honours Range -> size from Content-Range
    assert ham["ok"] and ham["size"] == len(FILES["/books/ham.pdf"][1]) and ham["pages"] == 340, ham
    lin = books.probe(base + "/books/big.pdf")                        # 3 MB: page count from the linearized header only
    assert lin["ok"] and lin["pages"] == 42, lin
    assert books.probe(base + "/books/compressed.pdf")["pages"] == 77
    bad = books.probe(base + "/members/premium.pdf")
    assert not bad["ok"] and "sign-in" in bad["note"], bad
    err = books.probe(base + "/books/broken.pdf")
    assert not err["ok"] and "web page" in err["note"], err
    nf = books.probe(base + "/books/nope.pdf")
    assert not nf["ok"] and "not found" in nf["note"].lower(), nf
    assert books.probe(base + "/books/survival.epub")["ok"]
    print("probe ok")

    # ---------------------------------------------------------------- site finder
    events = []
    found = books.find_on_site(base + "/", max_pages=50, workers=3, on_event=events.append)
    by = {b["title"]: b for b in found}
    print("site books:", sorted(by))
    sv = by["US Army Survival Manual"]
    assert set(sv["formats"]) == {"PDF", "EPUB"} and sv["pages"] == 264 and all(f["ok"] for f in sv["formats"].values())
    assert by["Where There Is No Doctor"]["pages"] == 512
    assert by["Premium Tactical Medicine"]["formats"]["PDF"]["ok"] is False
    assert by["Broken Book"]["formats"]["PDF"]["ok"] is False
    assert by["Water Purification Handbook"]["formats"]["PDF"]["ok"], "title should come from the title attribute"
    assert by["Ham Radio License Manual"]["author"] == "R. Operator", "OPDS entries are found"
    assert "Basic Gardening for Beginners" in by and "Edible Wild Plants" in by, "OPDS next page and sub-catalog followed"
    assert not any("Hidden" in t or "Secret" in t for t in by), "robots.txt Disallow must be respected"
    assert not any(p.startswith("/private") for p in hits_log), [p for p in hits_log if p.startswith("/private")]
    off = next(b for b in found if "elsewhere" in str(b["formats"]))   # files on another host (CDN) are listed, pages are not crawled
    assert off["formats"]["PDF"]["ok"] is False and "reach" in off["formats"]["PDF"]["note"], off
    assert not any("elsewhere" in p for p in hits_log)
    last = [e for e in events if e["type"] == "done"][-1]
    assert last["books"] == len(found) and last["blocked"] == 3 and last["ok"] == len(found) - 3, last
    only_pdf = books.find_on_site(base + "/", formats=("PDF",), max_pages=50, workers=3)
    assert all(set(b["formats"]) == {"PDF"} for b in only_pdf) and not any(b["title"] == "Basic Gardening for Beginners" for b in only_pdf)
    print("site finder ok")

    # ---------------------------------------------------------------- download
    prog = []
    path = books.download_file(base + "/books/survival.pdf", os.path.join(tmp, "dl"), "US Army: Survival Manual", "PDF",
                               lambda d, t: prog.append((d, t)))
    assert os.path.basename(path) == "US Army Survival Manual.pdf" and open(path, "rb").read().startswith(b"%PDF")
    assert prog and prog[-1][0] == prog[-1][1]
    p2 = books.download_file(base + "/books/survival.pdf", os.path.join(tmp, "dl"), "US Army: Survival Manual", "PDF")
    assert os.path.basename(p2) == "US Army Survival Manual (2).pdf", "no overwriting"
    for url in ("/books/broken.pdf", "/members/premium.pdf", "/books/nope.pdf"):
        try:
            books.download_file(base + url, os.path.join(tmp, "dl"), "X", "PDF")
            raise AssertionError("expected failure for " + url)
        except RuntimeError as e:
            assert str(e)
    assert not [f for f in os.listdir(os.path.join(tmp, "dl")) if f.endswith(".part")], "leftover .part file"
    cancel = threading.Event()
    cancel.set()
    try:
        books.download_file(base + "/books/survival.pdf", os.path.join(tmp, "dl"), "Y", "PDF", cancel=cancel)
        raise AssertionError("expected cancel")
    except RuntimeError as e:
        assert str(e) == "Cancelled"
    books.add_to_manifest(os.path.join(tmp, "dl"), {"title": "US Army Survival Manual", "format": "PDF", "size_bytes": 5, "source": "x"})
    books.add_to_manifest(os.path.join(tmp, "dl"), {"title": "Another", "format": "EPUB"})
    rows = list(csv.DictReader(open(os.path.join(tmp, "dl", books.MANIFEST), encoding="utf-8-sig")))
    assert [r["title"] for r in rows] == ["US Army Survival Manual", "Another"] and rows[0]["date"]
    print("download and manifest ok")

    # ---------------------------------------------------------------- catalog lookup
    ev = []
    cards = books.lookup("where there is no doctor", pref="EPUB", on_event=ev.append)
    assert len(cards) == 1, [c["title"] for c in cards]
    c = cards[0]
    assert c["status"] == "free" and c["best"]["fmt"] == "EPUB" and c["best"]["source"] == "Internet Archive", c["best"]
    srcs = {s["name"]: s for s in c["sources"]}
    assert set(srcs) == {"Internet Archive", "Open Library"} or set(srcs) <= {"Internet Archive", "Open Library"}
    assert all(d["ok"] for s in c["sources"] for d in s["formats"].values())
    assert c["links"]["worldcat"]["url"].startswith("https://search.worldcat.org/search?q=")
    fin = [e for e in ev if e["type"] == "cards"]
    assert fin[0]["final"] is False and fin[-1]["final"] is True and fin[-1]["answered"] == 4 and fin[-1]["total"] == 5, fin[-1]
    assert any(e["type"] == "catalog" and e["name"] == "OAPEN" and not e["ok"] for e in ev), "a failing catalog must not break the rest"
    pdf_pref = books.finalize(c, "PDF")
    assert pdf_pref["best"]["fmt"] == "PDF"
    print("lookup (free book) ok:", c["badge"], "-", c["best"]["source"], c["best"]["fmt"])

    cards = books.lookup("modern homestead handbook")
    assert len(cards) == 1 and cards[0]["status"] == "borrow_buy" and cards[0]["best"] is None, cards[0]
    assert cards[0]["badge"] == "Borrow or buy only"
    assert cards[0]["links"]["buy"]["url"] == "https://books.example/buy/mhh" and "borrow" in cards[0]["links"]
    print("lookup (borrow/buy only) ok")

    cards = books.lookup("walden thoreau")
    assert len(cards) == 1 and cards[0]["title"] == "Walden", [c["title"] for c in cards]   # same book from 3 catalogs = one card
    w = cards[0]
    assert w["status"] == "public" and w["best"]["source"] == "Project Gutenberg", w["best"]
    gb = [s for s in w["sources"] if s["name"] == "Google Books"]
    assert gb and gb[0]["formats"]["EPUB"]["ok"] is False and "sign-in" in gb[0]["formats"]["EPUB"]["note"], "bad link gets a red X"
    assert books.lookup("zzzz qqqq unknown thing") == []
    print("lookup (public domain, bad link ignored, no results) ok")

    books.get_json = orig_get_json
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL BOOK TESTS PASSED")


if __name__ == "__main__":
    main()
