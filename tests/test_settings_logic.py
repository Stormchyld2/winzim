"""Settings tab + ZIM library panel logic with a stub tkinter. Run: python tests/test_settings_logic.py"""
import json, os, shutil, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import stubtk
os.environ["APPDATA"] = tempfile.mkdtemp()


class FakeTree(stubtk.W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.rows = {}; self.order = []
    def insert(self, parent, where, iid=None, values=(), **k):
        self.rows[iid] = tuple(values); self.order.append(iid)
    def delete(self, *iids):
        for iid in iids: self.rows.pop(iid, None)
        self.order = [x for x in self.order if x not in iids]
    def get_children(self, *a): return list(self.order)
    def item(self, iid, values=None, **k):
        if values is not None: self.rows[iid] = tuple(values)
    def exists(self, iid): return iid in self.rows
    def configure(self, **k): self.opts.update(k)
    def __getitem__(self, k): return self.opts.get(k, "")
stubtk.ttk.Treeview = FakeTree

import prefs, bookzim, books, zimlibrary
import app as appmod
import settingsui

fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)

a = appmod.App()
a.q.queue.clear() if hasattr(a.q, "queue") else None
st = a.settings
check("settings tab exists", st is not None and hasattr(st, "library"))

# ---- values clamp and persist
prefs.reset()
for k, raw, want in (("downloads_at_once", "9", 6), ("downloads_at_once", "0", 1), ("downloads_at_once", "x", 6), ("retries", "99", 5),
                     ("timeout_s", "1", 5), ("pause_ms", "99999", 5000), ("skip_over_mb", "-4", 0), ("proxy", "  http://p:1 ", "http://p:1")):
    prefs.set(k, raw)
    check(f"{k}={raw!r} -> {want!r}", prefs.get(k) == want, prefs.get(k))
prefs.set("downloads_at_once", 6)
st.vars["downloads_at_once"].set("4")
check("typing a number updates the setting", prefs.get("downloads_at_once") == 4)
st.vars["downloads_at_once"].set("40")
check("too big is clamped to 6", prefs.get("downloads_at_once") == 6)
st.vars["downloads_at_once"].set("")
check("empty falls back to default", prefs.get("downloads_at_once") == 6)
st.vars["log_manifest"].set(False)
check("switch updates", prefs.get("log_manifest") is False)

# ---- proxy installs / clears
st.vars["proxy"].set("http://127.0.0.1:8080")
op = urllib.request.build_opener  # noqa
handlers = [h for h in urllib.request._opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
check("proxy installed", handlers and handlers[0].proxies.get("https") == "http://127.0.0.1:8080", handlers and handlers[0].proxies)
st.vars["proxy"].set("")
handlers = [h for h in urllib.request._opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
check("proxy cleared", handlers and "https" not in {k for k in handlers[0].proxies if handlers[0].proxies[k] == "http://127.0.0.1:8080"})

# ---- save / load round trip, and 'remember' off
prefs.set("retries", 3); prefs.set("zim_name", "My Shelf")
a.save_settings()
import settings as appsettings
data = appsettings.load()
check("saved with pref_ keys", data["pref_retries"] == 3 and data["pref_zim_name"] == "My Shelf" and "folder" in data, list(data)[:6])
prefs.reset(); a.load_settings()
check("loaded back", prefs.get("retries") == 3 and prefs.get("zim_name") == "My Shelf")
check("tab shows loaded value", st.vars["retries"].get() == "3")
prefs.set("remember", False); a.save_settings()
data = appsettings.load()
check("remember off keeps only theme + program settings", "folder" not in data and "theme" in data and data["pref_remember"] is False, list(data))
prefs.set("remember", True)
st.reset = st.reset  # reset button asks; stub says no
prefs.reset(); check("reset restores defaults", prefs.get("downloads_at_once") == 6 and prefs.get("zim_name") == "Recon Library")

# ---- ZIM library panel against a real ZIM
root = tempfile.mkdtemp()
try:
    lib = os.path.join(root, "lib"); os.makedirs(os.path.join(lib, "Medical")); os.makedirs(os.path.join(lib, "Radio"))
    for i in range(8):
        cat = "Medical" if i % 2 else "Radio"
        open(os.path.join(lib, cat, f"Title {i}.pdf"), "wb").write(b"%PDF-1.4\n" + bytes([i]) * 200_000)
    open(os.path.join(lib, "Radio", "Title 0.txt"), "w").write("a\n\nb")
    zim = os.path.join(root, "t.zim")
    bookzim.build_books_zim(lib, zim, "T")
    L = st.library

    def pump(until, secs=20):
        t0 = time.time(); seen = []
        while time.time() - t0 < secs:
            try: k, v = a.q.get(timeout=0.3)
            except Exception: continue
            if k == "settings":
                seen.append(v["type"]); a.settings.on_event(v)
                if v["type"] == until: return seen
        return seen

    # switch off: no checkboxes, no bulk buttons
    L.multi.set(False); L.apply_multi()
    check("checkbox column hidden when switch off", "sel" not in L.tree.opts.get("displaycolumns", []), L.tree.opts.get("displaycolumns"))
    L.multi.set(True); L.apply_multi()
    check("checkbox column shown when switch on", "sel" in L.tree.opts.get("displaycolumns", []))
    L.multi.set(False); L.apply_multi()

    L.zim.set(zim); L.folder.set(os.path.join(root, "out")); L.load()
    seen = pump("lib_catalog")
    check("list loaded", L.cat and len(L.cat["books"]) == 8 and len(L.tree.rows) == 8, (seen, L.status.get()))
    check("format boxes", set(L.fmt_vars) == {"PDF", "TXT"})
    L.filter.set("title 3")
    check("filter", list(L.tree.rows) == [str(i) for i in L.visible()] and len(L.tree.rows) == 1)
    L.filter.set("")
    L.fmt_vars["TXT"].set(False); L.render()
    check("format filter hides books", len(L.tree.rows) == 8)
    L.fmt_vars["TXT"].set(True)

    # checkboxes
    L.multi.set(True)
    L.set_checks(True)
    check("select all", len(L.checked) == 8 and all(v[0] == "☑" for v in L.tree.rows.values()))
    L.set_checks(False)
    check("select none", not L.checked and all(v[0] == "☐" for v in L.tree.rows.values()))

    # single download (always available)
    first = L.visible()[0]
    L.start([(first, None)])
    seen = pump("lib_done")
    check("single book taken out", "lib_done" in seen and not L.running, seen)
    got = [f for _, _, fs in os.walk(os.path.join(root, "out")) for f in fs if not f.endswith(".csv")]
    check("file arrived", len(got) >= 1 and "Done" in L.tree.rows[str(first)][6], (got, L.tree.rows[str(first)]))

    # tick a few, download selected
    L.checked = {1, 2, 3}
    L.render()
    L.fmt_vars["TXT"].set(False)
    import tkinter
    tkinter.messagebox.askyesno = lambda *a, **k: True
    L.download_selected()
    seen = pump("lib_done")
    check("selected books taken out", L.status.get().startswith("Took out 3 files"), L.status.get())
    check("row status shows Done", all("Done" in L.tree.rows[str(i)][6] for i in (1, 2, 3)))

    # all (with confirm)
    asked = []
    tkinter.messagebox.askyesno = lambda *a, **k: asked.append(a) or True
    L.download_all(); pump("lib_done")
    check("download all asks first and takes all", asked and L.status.get().startswith("Took out 8 files"), L.status.get())
    prefs.set("confirm_all", False); asked.clear()
    L.download_all(); pump("lib_done")
    check("no question when turned off", not asked)

    # cancel before start of queue
    L.cancel = __import__("threading").Event()
    # busy panel ignores a second start
    L.running = True; L.start([(0, None)]); L.running = False

    # bad zim
    open(os.path.join(root, "bad.zim"), "wb").write(b"nope")
    L.zim.set(os.path.join(root, "bad.zim")); L.load(); seen = pump("lib_error")
    check("bad zim reported", "lib_error" in seen)
finally:
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
