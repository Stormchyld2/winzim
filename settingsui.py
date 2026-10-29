"""The Settings tab: general, downloads, network, sites, Books -> ZIM (and helper programs), and the ZIM library
(taking books back out of a ZIM). Everything here is stored in prefs.py and saved with the other settings."""
import json
import os
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import bookread
import books
import crawlcache
import jobs
import prefs
import settings as appsettings
import zimlibrary
from appinfo import CONTACT, NAME, VERSION
from books import human_size

PAD = {"padx": 12, "pady": 4}
CACHE_LABELS = {"update": "Update (check for changes)", "resume": "Resume (trust saved pages)", "off": "Off"}


def _open_path(path):
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class SettingsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.vars = {}
        self._loading = True
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._general()
        self._downloads()
        self._network()
        self._sites()
        self._zimpage()
        self.library = LibraryPanel(self.nb, app)
        self.nb.add(self.library, text="ZIM library")
        self._loading = False
        self.refresh()

    # ------------------------------------------------------------ small builders
    def _page(self, title):
        f = ttk.Frame(self.nb)
        f.columnconfigure(1, weight=1)
        self.nb.add(f, text=title)
        return f

    def _head(self, f, row, text):
        ttk.Label(f, text=text, style="Amber.TLabel").grid(row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(14, 2))

    def _note(self, f, row, text):
        ttk.Label(f, text=text, style="Counts.TLabel", wraplength=760, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 4))

    def _var(self, key, kind):
        v = tk.BooleanVar() if kind == "bool" else tk.StringVar()
        self.vars[key] = v
        v.trace_add("write", lambda *_: self._changed(key))
        return v

    def _check(self, f, row, key, text):
        ttk.Checkbutton(f, text=text, variable=self._var(key, "bool")).grid(row=row, column=0, columnspan=3, sticky="w", **PAD)

    def _num(self, f, row, key, label, lo, hi, unit=""):
        ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", **PAD)
        sp = ttk.Spinbox(f, from_=lo, to=hi, width=8, textvariable=self._var(key, "str"))
        sp.grid(row=row, column=1, sticky="w", **PAD)
        sp.bind("<FocusOut>", lambda _e, k=key: self._normalize(k))
        if unit:
            ttk.Label(f, text=unit, style="Counts.TLabel").grid(row=row, column=2, sticky="w")

    def _text(self, f, row, key, label, width=48, browse=False):
        ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", **PAD)
        e = ttk.Entry(f, textvariable=self._var(key, "str"), width=width)
        e.grid(row=row, column=1, sticky="ew", **PAD)
        if browse:
            ttk.Button(f, text="Browse...", command=lambda k=key: self._browse_file(k)).grid(row=row, column=2, padx=(0, 12))

    def _changed(self, key):
        if self._loading:
            return
        prefs.set(key, self.vars[key].get())
        if key == "proxy":
            prefs.apply_network()
        if key.startswith("tool_"):
            self.refresh_tools()
        self.app.save_settings()

    def _normalize(self, key):
        self._loading = True
        try:
            self.vars[key].set(str(prefs.get(key)))
        finally:
            self._loading = False

    def _browse_file(self, key):
        p = filedialog.askopenfilename(title="Choose the program")
        if p:
            self.vars[key].set(os.path.normpath(p))

    def refresh(self):
        """Show the stored values."""
        self._loading = True
        try:
            for k, v in self.vars.items():
                val = prefs.get(k)
                v.set(bool(val) if isinstance(v, tk.BooleanVar) else str(val))
            self.theme.set("dark" if self.app.dark else "light")
            self.cache_label.set(CACHE_LABELS.get(prefs.get("cache_mode"), CACHE_LABELS["update"]))
        finally:
            self._loading = False
        self.refresh_tools()
        self.refresh_cache()
        self.library.refresh_from_prefs()

    # ------------------------------------------------------------ pages
    def _general(self):
        f = self._page("General")
        self._head(f, 0, "Appearance")
        self.theme = tk.StringVar(value="dark")
        row = ttk.Frame(f)
        row.grid(row=1, column=0, columnspan=3, sticky="w", **PAD)
        for text, val in (("Dark", "dark"), ("Light", "light")):
            ttk.Radiobutton(row, text=text, value=val, variable=self.theme,
                            command=lambda: self.app.apply_theme(self.theme.get() == "dark")).pack(side="left", padx=(0, 16))
        self._head(f, 2, "Behavior")
        self._check(f, 3, "remember", "Remember my choices between runs (folders, detail level, formats...)")
        self._check(f, 4, "confirm_all", "Ask before 'Download all' and 'Download selected' for several books")
        self._check(f, 5, "open_when_done", "Open the folder when a download or a ZIM build finishes")
        self._check(f, 6, "check_docker", "Look for Docker Desktop when the program starts (needed for the Cindrix Recon engine)")
        self._head(f, 7, "Settings file")
        self._note(f, 8, settings_where())
        row = ttk.Frame(f)
        row.grid(row=9, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(row, text="Open settings folder", command=lambda: _open_path(os.path.dirname(appsettings.settings_path()))).pack(side="left")
        ttk.Button(row, text="Export...", command=self.export).pack(side="left", padx=8)
        ttk.Button(row, text="Import...", command=self.import_).pack(side="left")
        ttk.Button(row, text="Reset everything", style="Danger.TButton", command=self.reset).pack(side="left", padx=8)
        self._head(f, 10, "About")
        self._note(f, 11, f"{NAME} {VERSION}.  Questions or problems: {CONTACT}")

    def _downloads(self):
        f = self._page("Downloads")
        self._head(f, 0, "How many at once")
        self._num(f, 1, "downloads_at_once", "Downloads at the same time", 1, 6, "1 to 6")
        self._note(f, 2, "Up to this many run together. The rest wait in line, and each one starts the moment another finishes, "
                         "so the number you pick stays busy until the whole list is done. Used for book downloads and for "
                         "taking books out of a ZIM.")
        self._head(f, 3, "Safety")
        self._num(f, 4, "retries", "Extra tries for a failed download", 0, 5)
        self._num(f, 5, "skip_over_mb", "Skip books bigger than", 0, 100000, "MB  (0 = no limit)")
        self._check(f, 6, "log_manifest", "Keep a list of everything downloaded (library_manifest.csv in the save folder)")
        self._head(f, 7, "Where books are saved")
        ttk.Label(f, text="Books folder").grid(row=8, column=0, sticky="w", **PAD)
        ttk.Entry(f, textvariable=self.app.books.folder).grid(row=8, column=1, sticky="ew", **PAD)
        ttk.Button(f, text="Browse...", command=self.app.books.browse).grid(row=8, column=2, padx=(0, 12))

    def _network(self):
        f = self._page("Network")
        self._head(f, 0, "Connection")
        self._num(f, 1, "timeout_s", "Wait for a server up to", 5, 120, "seconds")
        self._num(f, 2, "pause_ms", "Pause before each request", 0, 5000, "milliseconds (be gentler on small sites)")
        self._text(f, 3, "proxy", "Proxy address")
        self._note(f, 4, "Leave the proxy empty for a normal connection. Example: http://127.0.0.1:8080")
        self._head(f, 5, "Book catalogs")
        self._check(f, 6, "catalog_contact", f"Tell the big book catalogs who is asking ({CONTACT}). They ask programs to do this.")
        self._note(f, 7, "Only Open Library, Internet Archive, Project Gutenberg, Google Books and OAPEN get it, only when you search "
                         "or look up a book. It is never sent to ordinary websites and never stored in ZIM files.")
        row = ttk.Frame(f)
        row.grid(row=8, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(row, text="Test connection", command=self.test_network).pack(side="left")
        self.nettest = tk.StringVar()
        ttk.Label(row, textvariable=self.nettest).pack(side="left", padx=12)

    def _sites(self):
        f = self._page("Sites")
        self._head(f, 0, "Built-in engine")
        ttk.Label(f, text="Pages fetched at the same time").grid(row=1, column=0, sticky="w", **PAD)
        sp = ttk.Spinbox(f, from_=1, to=16, width=8, textvariable=self.app.workers)
        sp.grid(row=1, column=1, sticky="w", **PAD)
        ttk.Label(f, text="1 to 16 (more is faster but harder on the site)", style="Counts.TLabel").grid(row=1, column=2, sticky="w")
        self._num(f, 2, "site_pause_ms", "Pause between pages", 0, 5000, "milliseconds")
        self._note(f, 3, "A pause makes the crawl slower and kinder to small websites that might otherwise block you.")
        self._head(f, 4, "Saved pages (built-in engine)")
        ttk.Label(f, text="Remember pages between runs").grid(row=5, column=0, sticky="w", **PAD)
        self.cache_label = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=self.cache_label, values=list(CACHE_LABELS.values()), state="readonly", width=44)
        cb.grid(row=5, column=1, sticky="w", **PAD)
        self.cache_label.trace_add("write", lambda *_: self._cache_changed())
        self._note(f, 6, "Update: run the same site again and only changed pages are downloaded again; an interrupted run "
                         "carries on where it stopped. Resume: trust the saved pages without asking the site (fastest, may be out "
                         "of date). Off: always download everything. Saved pages take disk space, about as much as the site itself. "
                         "The Cindrix Recon engine cannot do this; it always starts over.")
        row = ttk.Frame(f)
        row.grid(row=7, column=0, columnspan=3, sticky="w", **PAD)
        self.cache_info = tk.StringVar()
        ttk.Button(row, text="Open folder", command=lambda: _open_path(crawlcache.default_root())).pack(side="left")
        ttk.Button(row, text="Clear saved pages", command=self.clear_cache).pack(side="left", padx=8)
        ttk.Label(row, textvariable=self.cache_info).pack(side="left", padx=8)
        self._head(f, 8, "Cindrix Recon engine (Docker)")
        ttk.Label(f, textvariable=self.app.docker_msg).grid(row=9, column=0, columnspan=3, sticky="w", **PAD)
        ttk.Button(f, text="Check again", command=lambda: threading.Thread(target=self.app.check_docker, daemon=True).start()).grid(
            row=10, column=0, sticky="w", **PAD)

    def _cache_changed(self):
        if self._loading:
            return
        inv = {v: k for k, v in CACHE_LABELS.items()}
        prefs.set("cache_mode", inv.get(self.cache_label.get(), "update"))
        self.app.save_settings()

    def refresh_cache(self):
        self.cache_info.set("Using " + human_size(crawlcache.cache_size(crawlcache.default_root())))

    def clear_cache(self):
        if messagebox.askyesno(NAME, "Delete all saved pages? The next run of each site will download everything again."):
            crawlcache.clear_cache(crawlcache.default_root())
            self.refresh_cache()

    def _zimpage(self):
        f = self._page("Books to ZIM")
        self._head(f, 0, "New library")
        self._text(f, 1, "zim_name", "Default library name", 36)
        self._num(f, 2, "zim_max_file_mb", "Leave out single files bigger than", 1, 100000, "MB")
        self._check(f, 3, "zim_readers", "Make books readable inside the ZIM (EPUB, MOBI, AZW3, TXT, FB2, comics, RTF)")
        self._head(f, 4, "Helper programs (free, optional)")
        self._note(f, 5, "These let the ZIM read more formats. Leave a box empty to look for the program automatically.")
        self.tool_status = {}
        self.tool_btn = {}
        for i, (key, pref, what, url) in enumerate((("ddjvu", "tool_ddjvu", "DjVuLibre  (DJVU books)", "https://djvu.sourceforge.net"),
                                                   ("7z", "tool_7z", "7-Zip  (comics in .cbr)", "https://www.7-zip.org"),
                                                   ("calibre", "tool_calibre", "Calibre  (LIT books)", "https://calibre-ebook.com"))):
            base = 6 + i * 2
            self._text(f, base, pref, what, 48, browse=True)
            st = tk.StringVar()
            self.tool_status[key] = st
            line = ttk.Frame(f)
            line.grid(row=base + 1, column=1, columnspan=2, sticky="w", padx=12)
            ttk.Label(line, textvariable=st, style="Counts.TLabel").pack(side="left")
            ttk.Button(line, text="Get it", command=lambda u=url: webbrowser.open(u)).pack(side="left", padx=8)
            btn = ttk.Button(line, text="Install it for me", command=lambda k=key: self.install_tool(k))
            btn.pack(side="left")
            self.tool_btn[key] = btn

    def install_tool(self, key):
        import threading
        import toolinstall
        spec = toolinstall.SPECS[key]
        msg = (f"Download the official {spec['name']} installer ({spec['size']}) from the project's own site and install it?\n\n"
               f"{spec['name']} is free software and is installed as a separate program.")
        if spec.get("note"):
            msg += "\n\n" + spec["note"]
        if not messagebox.askyesno(NAME, msg):
            return
        name = spec["name"]
        self.tool_status[key].set(f"Downloading {name}...")
        self.tool_btn[key].state(["disabled"])
        result = {}

        def progress(d, t):
            self.tool_status[key].set(f"Downloading {name}... {d * 100 // t}%" if t else f"Downloading {name}...")

        def work():
            try:
                result["path"] = toolinstall.install(key, on_progress=progress)
            except Exception as e:
                result["err"] = str(e)

        def poll():
            if not result:
                self.after(300, poll)
                return
            self.tool_btn[key].state(["!disabled"])
            self.refresh_tools()
            if "err" in result:
                messagebox.showwarning(NAME, result["err"])
            else:
                messagebox.showinfo(NAME, f"{name} is installed.")
        threading.Thread(target=work, daemon=True).start()
        self.after(300, poll)

    def refresh_tools(self):
        for key, st in self.tool_status.items():
            p = bookread.find_tool(key)
            st.set(f"Found: {p}" if p else f"Not found (the chosen path does not exist)" if prefs.get(bookread.TOOLS[key][0]) else "Not found")

    # ------------------------------------------------------------ actions
    def reset(self):
        if not messagebox.askyesno(NAME, "Put every setting back to how it was when you first installed the program?"):
            return
        prefs.reset()
        prefs.apply_network()
        self.refresh()
        self.app.save_settings()

    def export(self):
        p = filedialog.asksaveasfilename(defaultextension=".json", initialfile="cindrix-recon-settings.json",
                                         filetypes=[("Settings", "*.json")])
        if p:
            self.app.save_settings()
            data = appsettings.load()
            try:
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
            except OSError as e:
                messagebox.showerror(NAME, f"Could not save the file:\n{e}")

    def import_(self):
        p = filedialog.askopenfilename(filetypes=[("Settings", "*.json")])
        if not p:
            return
        data = appsettings.load(p)
        if not data:
            messagebox.showerror(NAME, "That file has no settings in it.")
            return
        appsettings.save(data)
        self.app.load_settings()
        self.app.apply_theme(self.app.dark)
        messagebox.showinfo(NAME, "Settings loaded.")

    def test_network(self):
        self.nettest.set("Testing...")

        def work():
            res = []
            for name, url in (("Open Library", "https://openlibrary.org/"), ("Internet Archive", "https://archive.org/"),
                              ("Project Gutenberg", "https://www.gutenberg.org/"), ("Google Books", "https://www.googleapis.com/books/v1/volumes?q=test&maxResults=1")):
                st, _, _, body = books.http_get(url, 10, 2000)
                res.append((name, st, body.decode("utf-8", "replace")[:80] if not st else ""))
            self.app.q.put(("settings", {"type": "nettest", "results": res}))
        threading.Thread(target=work, daemon=True).start()

    def on_event(self, e):
        if e["type"] == "nettest":
            ok = [n for n, st, _ in e["results"] if 200 <= st < 400]
            bad = [(n, st, why) for n, st, why in e["results"] if not 200 <= st < 400]
            self.nettest.set(f"Reached {len(ok)} of {len(e['results'])} catalogs." + ("" if not bad else
                             "  Problem: " + "; ".join(f"{n} ({st or why})" for n, st, why in bad[:2])))
        else:
            self.library.on_event(e)


def settings_where():
    return "Settings are saved here: " + appsettings.settings_path()


# =================================================================================== ZIM library
class LibraryPanel(ttk.Frame):
    COLS = ("sel", "title", "author", "category", "formats", "size", "status", "dl")

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.zim = tk.StringVar()
        self.folder = tk.StringVar()
        self.multi = tk.BooleanVar(value=False)
        self.filter = tk.StringVar()
        self.fmt_vars = {}
        self.cat = None
        self.checked = set()
        self.jobs, self.job_state, self.job_pct = [], {}, {}
        self.running = False
        self.cancel = threading.Event()
        self.last_folder = None
        self.status = tk.StringVar(value="Choose a ZIM file made by Books to ZIM.")
        self._build()
        self.zim.trace_add("write", lambda *_: self._store())
        self.folder.trace_add("write", lambda *_: self._store())

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 2))
        top.columnconfigure(1, weight=1)
        self.multi_chk = ttk.Checkbutton(top, text="Select several books at once  (shows checkboxes and the Download all button)",
                                         variable=self.multi, command=self._multi_clicked)
        self.multi_chk.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(top, text="ZIM file").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.zim).grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(top, text="Browse...", command=self.browse_zim).grid(row=1, column=2)
        ttk.Label(top, text="Save books to").grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.folder).grid(row=2, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(top, text="Browse...", command=self.browse_folder).grid(row=2, column=2)
        mid = ttk.Frame(self)
        mid.pack(fill="x", padx=12, pady=2)
        ttk.Label(mid, text="Take out:").pack(side="left")
        self.fmt_frame = ttk.Frame(mid)
        self.fmt_frame.pack(side="left", padx=6)
        ent = ttk.Entry(mid, textvariable=self.filter, width=26)
        ent.pack(side="right")
        ttk.Label(mid, text="Filter:").pack(side="right", padx=(0, 4))
        self.filter.trace_add("write", lambda *_: self.render())
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=12, pady=4)
        self.tree = ttk.Treeview(box, columns=self.COLS, show="headings", height=9, selectmode="browse")
        for c, text, w, a in (("sel", "", 34, "center"), ("title", "Title", 280, "w"), ("author", "Author", 130, "w"),
                              ("category", "Category", 90, "w"), ("formats", "Formats", 100, "w"), ("size", "Size", 70, "w"),
                              ("status", "Status", 120, "w"), ("dl", "Download", 80, "center")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "title"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(2, 8))
        self.cancel_btn = ttk.Button(bar, text="Cancel", style="Danger.TButton", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.sel_btn = ttk.Button(bar, text="Download selected", style="Primary.TButton", command=self.download_selected)
        self.all_btn = ttk.Button(bar, text="Download all", command=self.download_all)
        self.pick_all = ttk.Button(bar, text="Select all", command=lambda: self.set_checks(True))
        self.pick_none = ttk.Button(bar, text="Select none", command=lambda: self.set_checks(False))
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="right")
        ttk.Button(bar, text="Add all to queue", command=self.queue_all).pack(side="right", padx=6)
        ttk.Label(bar, textvariable=self.status, style="Amber.TLabel").pack(side="right", padx=10)
        self.apply_multi()

    # ------------------------------------------------------------ prefs
    def refresh_from_prefs(self):
        self.multi.set(bool(prefs.get("lib_multi")))
        self.zim.set(prefs.get("lib_zim"))
        self.folder.set(prefs.get("lib_folder") or self.app.books.folder.get())
        self.apply_multi()

    def _store(self):
        prefs.set("lib_zim", self.zim.get())
        prefs.set("lib_folder", self.folder.get())

    def _multi_clicked(self):
        prefs.set("lib_multi", self.multi.get())
        self.apply_multi()
        self.app.save_settings()

    def apply_multi(self):
        on = self.multi.get()
        cols = list(self.COLS) if on else [c for c in self.COLS if c != "sel"]
        try:
            self.tree.configure(displaycolumns=cols)
        except Exception:
            pass
        for b in (self.sel_btn, self.all_btn, self.pick_all, self.pick_none):
            b.pack_forget()
        if on:
            self.sel_btn.pack(side="left", padx=6)
            self.all_btn.pack(side="left")
            self.pick_all.pack(side="left", padx=(12, 4))
            self.pick_none.pack(side="left")

    # ------------------------------------------------------------ loading the list
    def browse_zim(self):
        p = filedialog.askopenfilename(title="Choose a ZIM file", filetypes=[("ZIM file", "*.zim"), ("All files", "*.*")])
        if p:
            self.zim.set(os.path.normpath(p))
            self.load()

    def browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or None, title="Where should the books go?")
        if d:
            self.folder.set(os.path.normpath(d))

    def load(self):
        path = self.zim.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showinfo(NAME, "Choose a ZIM file first.")
            return
        self.status.set("Reading the list of books...")

        def work():
            try:
                cat = zimlibrary.load_catalog(path)
                self.app.q.put(("settings", {"type": "lib_catalog", "cat": cat}))
            except zimlibrary.LibraryError as e:
                self.app.q.put(("settings", {"type": "lib_error", "text": str(e)}))
            except Exception as e:
                self.app.q.put(("settings", {"type": "lib_error", "text": f"Could not read that file: {e}"}))
        threading.Thread(target=work, daemon=True).start()

    def populate(self, cat):
        self.cat = cat
        self.checked = set()
        self.job_state, self.job_pct = {}, {}
        for w in self.fmt_frame.winfo_children():
            w.destroy()
        fmts = sorted({f for b in cat["books"] for f in b["formats"]}, key=lambda f: (f != "PDF", f))
        self.fmt_vars = {f: tk.BooleanVar(value=True) for f in fmts}
        for f in fmts:
            ttk.Checkbutton(self.fmt_frame, text=f, variable=self.fmt_vars[f], command=self.render).pack(side="left", padx=3)
        n = len(cat["books"])
        total = sum(d.get("size") or 0 for b in cat["books"] for d in b["formats"].values())
        self.status.set(f"{n} books, {human_size(total)}." + ("" if cat.get("listed") else "  (No catalog inside; listed from the files.)"))
        self.render()

    def wanted(self):
        return {f for f, v in self.fmt_vars.items() if v.get()}

    def visible(self):
        if not self.cat:
            return []
        t = self.filter.get().strip().lower()
        want = self.wanted()
        out = []
        for i, b in enumerate(self.cat["books"]):
            if not (want & set(b["formats"])):
                continue
            if t and t not in (b["title"] + " " + (b.get("author") or "") + " " + (b.get("category") or "")).lower():
                continue
            out.append(i)
        return out

    def row_values(self, i):
        b = self.cat["books"][i]
        want = self.wanted()
        fm = [f for f in sorted(b["formats"], key=lambda f: (f != "PDF", f)) if f in want]
        size = sum(b["formats"][f].get("size") or 0 for f in fm)
        return ("☑" if i in self.checked else "☐", b["title"], b.get("author") or "", b.get("category") or "",
                "  ".join(fm), human_size(size) if size else "", self.book_status(i), "Download")

    def render(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i in self.visible():
            self.tree.insert("", "end", iid=str(i), values=self.row_values(i))

    def book_status(self, bi):
        states = [(self.job_state.get(ji), self.job_pct.get(ji, 0)) for ji, j in enumerate(self.jobs) if j["book"] == bi]
        if not states:
            return ""
        kinds = [s for s, _ in states]
        if "running" in kinds:
            return f"Downloading {int(sum(p for _, p in states) / len(states))}%"
        if "queued" in kinds:
            return "Waiting in line"
        if "failed" in kinds:
            return "Failed"
        if all(k == "skipped" for k in kinds):
            return "Cancelled"
        return "Done ✔"

    # ------------------------------------------------------------ clicking
    def on_click(self, ev):
        if self.tree.identify_region(ev.x, ev.y) != "cell":
            return
        shown = [c for c in self.tree["displaycolumns"]] if self.tree["displaycolumns"] else list(self.COLS)
        if shown == ["#all"]:
            shown = list(self.COLS)
        try:
            col = shown[int(self.tree.identify_column(ev.x)[1:]) - 1]
        except (ValueError, IndexError):
            return
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        i = int(iid)
        if col == "sel":
            self.checked ^= {i}
            self.tree.item(iid, values=self.row_values(i))
        elif col == "dl":
            self.start([(i, None)])

    def set_checks(self, on):
        self.checked = set(self.visible()) if on else set()
        self.render()

    # ------------------------------------------------------------ downloading
    def download_selected(self):
        picks = [(i, None) for i in self.visible() if i in self.checked]
        if not picks:
            messagebox.showinfo(NAME, "Tick the books you want first, or use Download all.")
            return
        self.start(picks, confirm=True)

    def download_all(self):
        self.start([(i, None) for i in self.visible()], confirm=True)

    def start(self, picks, confirm=False):
        if self.running or not self.cat or not picks:
            return
        folder = self.folder.get().strip()
        if not folder:
            self.browse_folder()
            folder = self.folder.get().strip()
            if not folder:
                return
        job_list = zimlibrary.make_jobs(self.cat, picks, self.wanted())
        if not job_list:
            messagebox.showinfo(NAME, "None of those books has a format you ticked under 'Take out'.")
            return
        if confirm and len(picks) > 1 and prefs.get("confirm_all"):
            total = sum(j["size"] for j in job_list)
            if not messagebox.askyesno(NAME, f"Take {len(picks)} books ({len(job_list)} files"
                                       + (f", {human_size(total)}" if total else "") + f") out of the ZIM into\n{folder} ?"):
                return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror(NAME, f"Can't use that folder:\n{e}")
            return
        self.jobs, self.job_state, self.job_pct = job_list, {}, {}
        self.cancel = threading.Event()
        self.running = True
        self.last_folder = folder
        self.cancel_btn.configure(state="normal")
        zim, at_once, cancel = self.zim.get().strip(), prefs.get("downloads_at_once"), self.cancel
        last = {}

        def on_state(i, state, info):
            self.app.q.put(("settings", {"type": "lib_state", "i": i, "state": state, "info": info}))

        def on_prog(i, done, total):
            pct = int(done * 100 / total) if total else 100
            if pct - last.get(i, -10) >= 5 or pct == 100:
                last[i] = pct
                self.app.q.put(("settings", {"type": "lib_prog", "i": i, "pct": pct}))

        def work():
            res = zimlibrary.extract(zim, job_list, folder, at_once, cancel, on_state, on_prog, prefs.get("log_manifest"))
            self.app.q.put(("settings", {"type": "lib_done", "results": res}))
        threading.Thread(target=work, daemon=True).start()
        self.update_counts()

    def queue_all(self):
        zim, folder = self.zim.get().strip(), self.folder.get().strip()
        if not zim or not os.path.isfile(zim) or not folder:
            messagebox.showinfo(NAME, "Choose the ZIM file and the folder first.")
            return
        job, waiting = self.app.queue_tab.add("extract", {"zim": zim, "folder": folder})
        self.status.set(f"Added to the queue ({waiting} waiting).")

    def on_cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")
        self.cancel_btn.configure(state="disabled")

    def update_counts(self):
        st = list(self.job_state.values())
        total = len(self.jobs)
        done = sum(1 for s in st if s in ("done", "failed", "skipped"))
        running = sum(1 for s in st if s == "running")
        self.status.set(f"{done} of {total} done, {running} running, {total - done - running} waiting")

    def open_folder(self):
        d = self.last_folder or self.folder.get().strip()
        if d and os.path.isdir(d):
            _open_path(d)

    # ------------------------------------------------------------ events from the worker threads
    def on_event(self, e):
        t = e["type"]
        if t == "lib_catalog":
            self.populate(e["cat"])
        elif t == "lib_error":
            self.status.set("Could not read that ZIM.")
            messagebox.showerror(NAME, e["text"])
        elif t in ("lib_state", "lib_prog"):
            i = e["i"]
            if t == "lib_state":
                self.job_state[i] = e["state"]
                if e["state"] == "done":
                    self.job_pct[i] = 100
            else:
                self.job_pct[i] = e["pct"]
            bi = self.jobs[i]["book"]
            if self.tree.exists(str(bi)):
                self.tree.item(str(bi), values=self.row_values(bi))
            self.update_counts()
        elif t == "lib_done":
            self.running = False
            self.cancel_btn.configure(state="disabled")
            res = e["results"]
            ok = sum(1 for s, _ in res if s == "done")
            bad = [(self.jobs[i], info) for i, (s, info) in enumerate(res) if s == "failed"]
            cancelled = self.cancel.is_set()
            self.status.set(("Cancelled. " if cancelled else "") + f"Took out {ok} file{'' if ok == 1 else 's'}"
                            + (f", {len(bad)} failed." if bad else "."))
            if bad:
                messagebox.showwarning(NAME, "Some files could not be taken out:\n\n" + "\n".join(
                    f"{j['title'][:45]}: {why}" for j, why in bad[:6]))
            elif prefs.get("open_when_done") and ok and not cancelled:
                self.open_folder()
