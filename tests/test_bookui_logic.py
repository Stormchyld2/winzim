"""Book Finder tab logic (stub tkinter: draws nothing). Run from the repo root: python tests/test_bookui_logic.py"""
import http.server
import os
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stubtk  # noqa: E402,F401  (installs the stub tkinter)
os.environ["APPDATA"] = tempfile.mkdtemp()

import app as appmod  # noqa: E402
import bookui  # noqa: E402
import books  # noqa: E402
import test_books  # noqa: E402

a = appmod.App()
b = a.books
assert a.title is not None and appmod.APP_NAME == "Cindrix Recon"


def book(bid, title, fmts, pages=None, author=""):
    return {"id": bid, "title": title, "author": author, "year": None, "pages": pages, "source": "x.org", "badge": "",
            "formats": {f: {"url": f"http://x.org/{bid}.{f.lower()}", "size": size, "ok": ok, "note": note}
                        for f, (ok, size, note) in fmts.items()}}


# ---------------------------------------------------------------- table rows / filters
good = book("g1", "US Army Survival Manual", {"PDF": (True, 8_000_000, "OK"), "EPUB": (True, 3_000_000, "OK")}, 264)
bad = book("b1", "Premium Tactical Medicine", {"PDF": (False, None, "Needs a login.")})
wait = book("w1", "Still Checking", {"PDF": (None, None, "Not checked yet")})
for x in (good, bad, wait):
    b.on_event({"type": "book", "book": x})
assert set(b.books) == {"g1", "b1", "w1"}
vals, tags = b._row(good)
assert vals == ("US Army Survival Manual", "EPUB  PDF", 264, "2.9 MB", "✔", "Download ▾") and tags == ("ok",), (vals, tags)
b.pref.set("PDF first")
assert b._row(good)[0][3] == "7.6 MB", "size follows the preferred format"
b.pref.set("EPUB first")
vals, tags = b._row(bad)
assert vals[4] == "✖" and vals[5] == "Unavailable" and tags == ("blocked",)
vals, tags = b._row(wait)
assert vals[4] == "…" and tags == ("checking",)
assert b.visible(good) and not b.visible(bad) and b.visible(wait)       # "only show books I can download"
b.only_ok.set(False)
assert b.visible(bad)
b.filter_text.set("army")
assert b.visible(good) and not b.visible(bad)
b.filter_text.set("")
b.fmt_vars["PDF"].set(False)                                              # format filter
assert b.visible(good) and not b.visible(bad)                             # good still has EPUB; bad only had PDF
b.fmt_vars["PDF"].set(True); b.only_ok.set(True)
print("table rows and filters ok")

# ---------------------------------------------------------------- downloads: direct vs format picker
started = []
b.start_download = lambda items: started.append(items)
b.download_book("g1", ask=True)
chosen = {}


class FakeDialog:
    def __init__(self, parent, pal, title, subtitle, options, folder, on_choose, default=0):
        chosen.update(title=title, subtitle=subtitle, options=options, default=default)
        on_choose(1)
bookui.FormatDialog = FakeDialog
b.download_book("g1", ask=True)
assert chosen["title"] == "US Army Survival Manual" and chosen["subtitle"] == "264 pages"
assert [o[0] for o in chosen["options"]] == ["EPUB", "PDF"] and chosen["default"] == 0, chosen["options"]
assert started[-1][0]["fmt"] == "PDF" and started[-1][0]["url"].endswith("g1.pdf"), started[-1]
b.download_book("g1", ask=False)
assert started[-1][0]["fmt"] == "EPUB", "no dialog -> preferred format"
n = len(started)
b.download_book("b1")                                                       # blocked book: message, no download
assert len(started) == n
orig_tree = b.tree
b.tree = types.SimpleNamespace(selection=lambda: ["g1", "b1"], get_children=lambda: [])
b.download_selected()
b.tree = orig_tree
assert [i["title"] for i in started[-1]] == ["US Army Survival Manual"], "blocked books are skipped"
print("download choices ok")

# ---------------------------------------------------------------- lookup cards
free = {"id": "c1", "title": "Where There Is No Doctor", "author": "David Werner", "year": 1977, "pages": 512,
        "sources": [{"name": "Internet Archive", "kind": "free", "page_url": "https://archive.org/x", "public_domain": False,
                     "trust": 3, "formats": {"EPUB": {"url": "http://x/e", "size": 3000, "ok": True, "note": "OK"},
                                             "PDF": {"url": "http://x/p", "size": 9000, "ok": True, "note": "OK"}}}],
        "links": {"worldcat": {"name": "WorldCat", "url": "https://search.worldcat.org/search?q=x"}},
        "best": {"source": "Internet Archive", "fmt": "EPUB", "url": "http://x/e", "size": 3000, "page_url": "", "public_domain": False},
        "status": "free", "badge": "Free to download"}
paid = {"id": "c2", "title": "Modern Homestead Handbook", "author": "J. Carter", "year": 2019, "pages": None, "sources": [],
        "links": {"buy": {"name": "Google Books", "url": "https://b/buy"}, "worldcat": {"name": "WorldCat", "url": "https://w"}},
        "best": None, "status": "borrow_buy", "badge": "Borrow or buy only"}
b.set_mode("lookup")
b.on_event({"type": "cards", "cards": [free, paid], "final": True, "answered": 4, "total": 5, "seconds": 3.2})
assert b.sel_card == "c1" and "4 of 5 catalogs in 3.2 seconds. 2 books matched" in b.lookup_msg.get(), b.lookup_msg.get()
assert not b.running and "1 with a working free download" in b.status.get()
b.download_selected()
assert started[-1][0]["fmt"] == "EPUB" and started[-1][0]["source"] == "Internet Archive" and started[-1][0]["title"] == free["title"]
b.show_borrow.set(False)
assert b._card_visible(free) and not b._card_visible(paid)
b.show_borrow.set(True)
b.select_card("c2")
b.download_selected()                                                       # card without a free copy: nothing starts
assert started[-1][0]["title"] == free["title"], "no new download for a borrow/buy-only card"
b.download_card(free)                                                       # two formats -> the picker (FakeDialog picks #1)
assert started[-1][0]["fmt"] in ("EPUB", "PDF")
print("lookup cards ok")

# ---------------------------------------------------------------- settings
b.set_mode("lookup"); b.folder.set("D:\\Books"); b.pref.set("PDF first"); b.max_pages.set("123")
b.fmt_vars["TXT"].set(False); b.only_ok.set(False)
st = b.get_settings()
assert st["book_mode"] == "lookup" and st["book_folder"] == "D:\\Books" and "TXT" not in st["book_formats"] and st["book_max_pages"] == 123
a.save_settings()
a2 = appmod.App()
b2 = a2.books
assert b2.folder.get() == "D:\\Books" and b2.pref.get() == "PDF first" and b2.mode.get() == "lookup" and not b2.fmt_vars["TXT"].get()
assert not b2.only_ok.get() and b2.max_pages.get() == "123"
print("book settings remembered ok")

# ---------------------------------------------------------------- settings carried over from the old name
import settings
old = settings.old_settings_path()
os.makedirs(os.path.dirname(old), exist_ok=True)
os.remove(settings.settings_path())
open(old, "w").write('{"folder": "E:\\\\OldZims", "theme": "light"}')
a3 = appmod.App()
assert a3.folder.get() == "E:\\OldZims" and a3.dark is False, "old WinZim settings should carry over"
print("old settings carried over ok")

# ---------------------------------------------------------------- the real download worker, end to end
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), test_books.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
dest = os.path.join(tempfile.mkdtemp(), "Books")
items = [{"title": "Survival", "author": "Army", "year": 1992, "fmt": "PDF", "url": base + "/books/survival.pdf", "size": 1,
          "source": "x", "public_domain": "yes"},
         {"title": "Broken", "author": "", "year": None, "fmt": "PDF", "url": base + "/books/broken.pdf", "size": 1, "source": "x",
          "public_domain": ""}]
b.cancel = threading.Event()
while not b.q.empty():
    b.q.get()
b._dl_worker(items, dest)
evs = []
while not b.q.empty():
    k, v = b.q.get()
    assert k == "book"
    evs.append(v)
done = [e for e in evs if e["type"] == "dl_done"][0]
assert len(done["paths"]) == 1 and os.path.exists(done["paths"][0]) and len(done["errors"]) == 1 and done["errors"][0][0] == "Broken", done
assert any(e["type"] == "dl_progress" for e in evs)
import csv
rows = list(csv.DictReader(open(os.path.join(dest, books.MANIFEST), encoding="utf-8-sig")))
assert len(rows) == 1 and rows[0]["title"] == "Survival" and rows[0]["public_domain"] == "yes" and rows[0]["file"].endswith(".pdf")
b.running = False
srv.shutdown()
print("download worker + manifest ok")

# ---------------------------------------------------------------- Books -> ZIM button
import time
zroot = tempfile.mkdtemp()
open(os.path.join(zroot, "Notes.txt"), "w").write("Hello.\n\nWorld.\n")
zout = os.path.join(zroot, "out.zim")
b.folder.set(zroot)
bookui.simpledialog.askstring = lambda *a, **k: "My Lib"
bookui.filedialog.asksaveasfilename = lambda **k: zout
shown = []
bookui.messagebox.showinfo = lambda *a, **k: shown.append(a)
b.running = False
b.make_zim()
zev = []
t0 = time.time()
while time.time() - t0 < 20 and not any(e["type"] == "zim_done" for e in zev):
    try:
        k, v = b.q.get(timeout=0.5)
        if k == "book":
            zev.append(v)
            b.on_event(v)
    except Exception:
        pass
zd = [e for e in zev if e["type"] == "zim_done"]
assert zd and zd[0]["res"]["books"] == 1 and zd[0]["res"]["readers"] == 1 and os.path.exists(zout), zev
assert shown and "1 books" in shown[0][1] and b.running is False and "ZIM saved" in b.status.get()
print("books -> ZIM button ok")
print("ALL BOOK UI LOGIC TESTS PASSED")
