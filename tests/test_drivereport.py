"""Drive report. Run: python tests/test_drivereport.py"""
import csv, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import bookzim, books, drivereport

fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)

root = tempfile.mkdtemp()
try:
    d = os.path.join(root, "drive"); os.makedirs(os.path.join(d, "Medical")); os.makedirs(os.path.join(d, "Radio"))
    good = b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF\n"
    open(os.path.join(d, "Medical", "Where There Is No Doctor.pdf"), "wb").write(good)
    open(os.path.join(d, "Medical", "Where There Is No Doctor (2).pdf"), "wb").write(good)       # duplicate
    open(os.path.join(d, "Radio", "Cut Short.pdf"), "wb").write(b"%PDF-1.4\n" + b"y" * 5000)      # no end marker
    open(os.path.join(d, "Radio", "Login Page.pdf"), "wb").write(b"<html><body>please log in</body></html>")
    open(os.path.join(d, "Radio", "Broken.epub"), "wb").write(b"PK\x03\x04 not really a zip")
    open(os.path.join(d, "Radio", "Empty.pdf"), "wb").write(b"")
    open(os.path.join(d, "notes.docx"), "wb").write(b"z" * 3000)
    books.add_to_manifest(d, {"title": "Where There Is No Doctor", "format": "PDF", "file": os.path.join(d, "Medical", "Where There Is No Doctor.pdf")})
    books.add_to_manifest(d, {"title": "Gone Book", "format": "PDF", "file": os.path.join(d, "Radio", "Gone Book.pdf")})
    # a real ZIM, a damaged copy, and a fake .zim
    small = os.path.join(root, "small"); os.makedirs(small)
    open(os.path.join(small, "A Guide.pdf"), "wb").write(good)
    bookzim.build_books_zim(small, os.path.join(d, "good.zim"), "Good Library")
    data = bytearray(open(os.path.join(d, "good.zim"), "rb").read()); data[len(data) // 2] ^= 0xFF
    open(os.path.join(d, "damaged.zim"), "wb").write(bytes(data))
    open(os.path.join(d, "fake.zim"), "wb").write(b"not a zim at all")

    ev = []
    rep = drivereport.scan(d, on_event=ev.append, failures=["Site job failed: example.org (HTTP 403)"])
    s = drivereport.summary(rep)
    z = {x["path"]: x for x in rep["zims"]}
    check("counts", s["zims"] == 3 and s["books"] == 6, s)
    check("good ZIM verified, with title and book count", z["good.zim"]["ok"] and z["good.zim"]["title"] == "Good Library" and z["good.zim"]["books"] == 1 and "Checksum OK" in z["good.zim"]["note"], z["good.zim"])
    check("damaged ZIM caught by checksum", not z["damaged.zim"]["ok"] and "damaged" in z["damaged.zim"]["note"], z["damaged.zim"])
    check("fake ZIM caught", not z["fake.zim"]["ok"])
    probs = {b["path"]: b["problem"] for b in rep["books"]}
    check("cut-short PDF", "cut short" in probs[os.path.join("Radio", "Cut Short.pdf")])
    check("web page named .pdf", "Not a real PDF" in probs[os.path.join("Radio", "Login Page.pdf")])
    check("bad epub", "EPUB" in probs[os.path.join("Radio", "Broken.epub")])
    check("empty file", probs[os.path.join("Radio", "Empty.pdf")] == "Empty file")
    check("good PDFs have no problem", probs[os.path.join("Medical", "Where There Is No Doctor.pdf")] == "")
    check("missing from folder found", [m["title"] for m in rep["manifest"]["missing"]] == ["Gone Book"], rep["manifest"])
    check("duplicate found", len(rep["duplicates"]) == 1 and rep["duplicates"][0]["wasted"] == len(good), rep["duplicates"])
    check("unlisted books flagged", len(rep["manifest"]["unlisted"]) >= 4)
    check("disk info", rep["disk"] and rep["disk"]["free"] > 0)
    check("other files counted", 3000 <= rep["other_size"] < 4000, rep["other_size"])
    check("progress events", any("Checking" in e.get("text", "") for e in ev))
    check("by category", s["by_category"]["Medical"][0] == 2 and s["by_category"]["Radio"][0] == 4, s["by_category"])
    hp, cp = drivereport.write_reports(rep, d)
    html = open(hp, encoding="utf-8").read()
    check("html has the problems", "Cut Short.pdf" in html and "Gone Book" in html and "damaged.zim" in html and "Site job failed" in html and "Good Library" in html)
    rows = list(csv.DictReader(open(cp, encoding="utf-8-sig")))
    check("csv rows", len(rows) == 3 + 6 + 1 and {r["status"] for r in rows} >= {"ok", "damaged", "problem", "missing"}, {r["status"] for r in rows})
    rep2 = drivereport.scan(d, verify_zims=False)
    check("report files are not counted as content", len(rep2["books"]) == 6 and len(rep2["zims"]) == 3)
    check("fast mode skips checksum", all("Checksum OK" not in z["note"] for z in rep2["zims"]) and any(not z["ok"] for z in rep2["zims"]))
    import threading
    ev2 = threading.Event(); ev2.set()
    try: drivereport.scan(d, cancel=ev2); check("cancel", False)
    except RuntimeError as e: check("cancel", str(e) == "Cancelled")
    empty = os.path.join(root, "empty"); os.makedirs(empty)
    r3 = drivereport.scan(empty)
    check("empty folder warned", any("No ZIM files" in t for _, t in r3["issues"]))
finally:
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
