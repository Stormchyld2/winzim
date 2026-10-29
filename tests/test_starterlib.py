"""Starter library: draft file, approval rules, damaged file, and filling the tabs. Run: python tests/test_starterlib.py"""
import json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import stubtk
os.environ["APPDATA"] = tempfile.mkdtemp()
import starterlib

fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)

root = tempfile.mkdtemp()
try:
    p = os.path.join(root, "starter_library.json")
    ents = starterlib.load(p)
    check("draft file created", os.path.exists(p) and len(ents) >= 15)
    data = json.load(open(p, encoding="utf-8"))
    check("file says addresses were not live-checked", "NOT opened live" in data["about"] and data["version"] == 1)
    check("shipped entries are reviewed and approved, none claim live checking", all(e["approved"] and "not live-checked" in e["status"] for e in ents))
    check("shipped entries are usable", all(starterlib.usable(e) for e in ents))
    check("entries are valid", all(starterlib.problem(e) == "" for e in ents), [e["name"] for e in ents if starterlib.problem(e)])
    check("ids are unique", len({e["id"] for e in ents}) == len(ents))
    check("no duplicate addresses", len({e["url"] for e in ents if e["kind"] == "site"}) == sum(1 for e in ents if e["kind"] == "site"))
    check("site entries are https", all(e["url"].startswith("https://") for e in ents if e["kind"] == "site"))
    ents[1]["approved"] = False; starterlib.save(ents, p)
    again = starterlib.load(p)
    check("un-approval is kept", starterlib.usable(again[0]) and not starterlib.usable(again[1]))
    bad = dict(again[0], url="not a url")
    check("bad address refused", starterlib.problem(bad) != "" and not starterlib.usable(bad))
    check("book without query refused", starterlib.problem({"name": "x", "kind": "book", "query": " "}) != "")
    check("nameless refused", starterlib.problem({"name": "", "kind": "site", "url": "https://a.org"}) != "")
    # user edits survive
    again.append({"id": "mine1", "kind": "site", "name": "My site", "url": "https://example.org/", "approved": True})
    starterlib.save(again, p)
    check("user entries survive a reload", any(e["id"] == "mine1" for e in starterlib.load(p)))
    # a bare list is accepted, a damaged file is kept and replaced
    json.dump([{"id": "a", "kind": "book", "name": "N", "query": "q"}], open(p, "w"))
    only = starterlib.load(p)
    check("bare list accepted, defaults filled in", len(only) == 1 and only[0]["approved"] is False)
    open(p, "w").write("{ this is not json")
    rec = starterlib.load(p)
    check("damaged file replaced by the draft, old copy kept", len(rec) >= 15 and os.path.exists(p + ".bad") and "not json" in open(p + ".bad").read())
    check("new ids", starterlib.new_id(rec, "site") not in {e["id"] for e in rec})

    # ---- filling the tabs (stub tkinter)
    class FakeTree(stubtk.W):
        pass
    import app as appmod, starterui
    a = appmod.App()
    site = dict(next(e for e in starterlib.default_entries() if e["kind"] == "site"), approved=True)
    a.use_starter(site)
    check("site entry fills URL, preset and detail", a.url.get() == site["url"] and a.preset.get() == appmod.WHOLE and a.detail.get().startswith("Lite"), (a.url.get(), a.preset.get(), a.detail.get()))
    check("nothing started by itself", not a.running and not a.queue_tab.q.jobs)
    book = dict(next(e for e in starterlib.default_entries() if e["kind"] == "book"), approved=True)
    a.use_starter(book)
    check("book entry fills the lookup", a.books.mode.get() == "lookup" and a.books.query.get() == book["query"])
    # dialog refuses unapproved
    import tkinter
    shown = []
    tkinter.messagebox.showinfo = lambda *x, **k: shown.append(x)
    d = starterui.StarterDialog.__new__(starterui.StarterDialog)
    d.app, d.path = a, p
    d.entries = starterlib.default_entries(); d.entries[0]["approved"] = False; d.tree = stubtk.W(); d.tree.selection = lambda: [d.entries[0]["id"]]
    d.destroy = lambda: None; d.render = lambda: None
    used = []; a.use_starter = lambda e: used.append(e)
    d.use()
    check("Use is refused while unapproved", not used and shown and "not approved" in shown[0][1])
    d.entries[0]["approved"] = True; d.use()
    check("Use works once approved", used and used[0]["id"] == d.entries[0]["id"])
finally:
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
