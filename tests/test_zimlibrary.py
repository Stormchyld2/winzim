"""Taking books out of a ZIM: catalog, parallel extraction (max 6, the rest queue), manifest. Run: python tests/test_zimlibrary.py"""
import csv, os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import bookzim, books, jobs, zimlibrary
from zimwriter import ZimWriter

fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)

root = tempfile.mkdtemp()
try:
    lib = os.path.join(root, "lib"); os.makedirs(os.path.join(lib, "Medical")); os.makedirs(os.path.join(lib, "Radio"))
    contents = {}
    for i in range(10):
        cat = "Medical" if i % 2 else "Radio"
        path = os.path.join(lib, cat, f"Book number {i}.pdf")
        data = b"%PDF-1.4\n" + os.urandom(300_000 + i * 1000) + b"\n%%EOF"
        open(path, "wb").write(data); contents[f"Book number {i}.pdf"] = data
    open(os.path.join(lib, "Radio", "notes.txt"), "w").write("Hello\n\nWorld")
    books.add_to_manifest(lib, {"title": "Book number 3", "author": "Zed", "year": 1999, "format": "PDF", "size_bytes": 1,
                                "source": "Internet Archive", "public_domain": "yes", "url": "http://x/y.pdf",
                                "file": os.path.join(lib, "Medical", "Book number 3.pdf")})
    zim = os.path.join(root, "lib.zim")
    bookzim.build_books_zim(lib, zim, "T", None)

    cat = zimlibrary.load_catalog(zim)
    check("catalog listed", cat["listed"] and len(cat["books"]) == 11 and cat["title"] == "T", len(cat["books"]))
    b3 = next(b for b in cat["books"] if b["title"] == "Book number 3")
    check("catalog keeps metadata", b3["author"] == "Zed" and b3["year"] == 1999 and b3["public_domain"] and b3["category"] == "Medical" and b3["source"] == "Internet Archive", b3)

    # ---- parallel extraction: never more than 6 at once, all 11 files arrive intact
    dest = os.path.join(root, "out")
    picks = [(i, None) for i in range(len(cat["books"]))]
    jl = zimlibrary.make_jobs(cat, picks)
    check("one job per file", len(jl) == 11)
    run, peak, lock, states = 0, [0], threading.Lock(), {}
    orig = zimlibrary.extract_one
    def slow(*a, **k):
        global run
        with lock:
            run += 1; peak[0] = max(peak[0], run)
        time.sleep(0.15)
        try: return orig(*a, **k)
        finally:
            with lock: run -= 1
    zimlibrary.extract_one = slow
    res = zimlibrary.extract(zim, jl, dest, 6, on_state=lambda i, s, info: states.setdefault(i, []).append(s))
    zimlibrary.extract_one = orig
    check("all done", all(s == "done" for s, _ in res), [r for r in res if r[0] != "done"])
    check("at most 6 at once", 2 <= peak[0] <= 6, peak[0])
    check("queued then running then done", all(v[0] == "queued" and v[-1] == "done" and "running" in v for v in states.values()))
    same = all(open(os.path.join(dest, "Medical" if int(n.split()[-1].split(".")[0]) % 2 else "Radio", n), "rb").read() == d for n, d in contents.items())
    check("files identical, in category folders", same)
    check("txt extracted", open(os.path.join(dest, "Radio", "notes.txt")).read() == "Hello\n\nWorld")
    rows = list(csv.DictReader(open(os.path.join(dest, books.MANIFEST), encoding="utf-8-sig")))
    r3 = next(r for r in rows if r["title"] == "Book number 3")
    check("manifest restored with source", len(rows) == 11 and r3["source"] == "Internet Archive" and r3["public_domain"] == "yes" and r3["author"] == "Zed", r3)
    check("no .part left", not [f for _, _, fs in os.walk(dest) for f in fs if f.endswith(".part")])

    # ---- extracting again never overwrites: new files get (2)
    one = zimlibrary.make_jobs(cat, [(0, None)])
    zimlibrary.extract(zim, one, dest, 1)
    check("repeat gets a new name", any("(2)" in f for _, _, fs in os.walk(dest) for f in fs))

    # ---- one format only
    two = zimlibrary.make_jobs(cat, [(0, ["EPUB"])])
    check("format filter", two == [])

    # ---- cancel stops queued jobs
    ev = threading.Event(); ev.set()
    res = zimlibrary.extract(zim, jl[:3], os.path.join(root, "c"), 2, cancel=ev)
    check("cancel skips", all(s == "skipped" for s, _ in res))

    # ---- missing file fails alone
    bad = [dict(jl[0], url="files/nope/nope.pdf")] + jl[1:3]
    res = zimlibrary.extract(zim, bad, os.path.join(root, "m"), 3)
    check("failure isolated", res[0][0] == "failed" and "not in the ZIM" in res[0][1] and res[1][0] == "done" and res[2][0] == "done", res)

    # ---- ZIM without catalog.json (older / other)
    z2 = os.path.join(root, "old.zim")
    w = ZimWriter(z2, {"Title": "old"})
    w.add_entry("files/my-book/my-book.pdf", "", "application/pdf", b"%PDF-1.4 hi")
    w.add_entry("index.html", "x", "text/html", b"<html></html>"); w.set_main("index.html"); w.close()
    c2 = zimlibrary.load_catalog(z2)
    check("fallback listing", not c2["listed"] and c2["books"][0]["title"] == "My Book" and "PDF" in c2["books"][0]["formats"], c2)
    res = zimlibrary.extract(zim and z2, zimlibrary.make_jobs(c2, [(0, None)]), os.path.join(root, "o2"), 1)
    check("fallback extraction", res[0][0] == "done" and open(res[0][1], "rb").read() == b"%PDF-1.4 hi", res)

    # ---- not a zim / empty
    open(os.path.join(root, "x.zim"), "wb").write(b"nope")
    try: zimlibrary.load_catalog(os.path.join(root, "x.zim")); check("bad zim refused", False)
    except zimlibrary.LibraryError as e: check("bad zim refused", "ZIM" in str(e), e)

    # ---- jobs.run_jobs limits
    peak2, cur = [0], [0]; lk = threading.Lock()
    def w2(i, it):
        with lk: cur[0] += 1; peak2[0] = max(peak2[0], cur[0])
        time.sleep(0.05)
        with lk: cur[0] -= 1
    jobs.run_jobs(list(range(20)), w2, 99)
    check("limit capped at 6", peak2[0] <= 6 and peak2[0] >= 3, peak2[0])
    # sliding window: every job after the first 6 starts the moment one finishes, so 6 are always running until the line is empty
    import random
    at_start, cur3, lk3 = [], [0], threading.Lock()
    def w3(i, it):
        with lk3:
            cur3[0] += 1; at_start.append(cur3[0])
        time.sleep(random.uniform(0.02, 0.12))
        with lk3: cur3[0] -= 1
    jobs.run_jobs(list(range(30)), w3, 6)
    check("6 always running until the line is empty", at_start[:6] == [1, 2, 3, 4, 5, 6] and all(x == 6 for x in at_start[6:]), at_start)
    check("default is 6 at a time", __import__("prefs").get("downloads_at_once") == 6)
    peak2[0] = 0
    jobs.run_jobs(list(range(8)), w2, 1)
    check("limit 1 is one at a time", peak2[0] == 1, peak2[0])
finally:
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
