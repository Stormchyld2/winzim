"""The Book Finder tab (all the window code for it; the logic is in books.py)."""
import copy
import os
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

import books
from books import DEFAULT_FORMATS, FORMAT_ORDER, human_size
from sitecheck import check_site

SHOWN_FORMATS = ("PDF", "EPUB", "MOBI", "AZW3", "DJVU", "TXT")
PREF_CHOICES = [f + " first" for f in ("EPUB", "PDF", "MOBI", "AZW3")]
BADGE_COLOR = {"public": "blue", "free": "green", "borrow_buy": "amber", "none": "dim"}


class FormatDialog(tk.Toplevel):
    """'Choose a format' window. Calls on_choose(index) when the user clicks Download."""

    def __init__(self, parent, pal, title, subtitle, options, folder, on_choose, default=0):
        super().__init__(parent)
        self.title("Choose a format")
        self.configure(bg=pal["bg"], highlightthickness=2, highlightbackground=pal["amber"], highlightcolor=pal["amber"])
        try:
            self.transient(parent.winfo_toplevel())
            self.resizable(False, False)
        except Exception:
            pass
        self.on_choose = on_choose
        self.var = tk.IntVar(value=default)
        box = ttk.Frame(self, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Choose a format", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(box, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 0))
        if subtitle:
            ttk.Label(box, text=subtitle, style="Muted.TLabel").pack(anchor="w")
        self.opts = options
        for i, (label, size, ok, note) in enumerate(options):
            line = f"{label}      {human_size(size)}      " + ("Can download" if ok else note)
            ttk.Radiobutton(box, text=line, variable=self.var, value=i,
                            state="normal" if ok else "disabled").pack(anchor="w", pady=4)
        ttk.Label(box, text="Saves to: " + folder, style="Muted.TLabel").pack(anchor="w", pady=(10, 8))
        row = ttk.Frame(box)
        row.pack(anchor="e")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 8))
        self.go = ttk.Button(row, text="Download", style="Primary.TButton", command=self.choose)
        self.go.pack(side="left")
        self.var.trace_add("write", lambda *_: self.relabel())
        self.relabel()
        self.bind("<Escape>", lambda _e: self.destroy())
        try:
            self.update_idletasks()
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
            y = parent.winfo_rooty() + 120
            self.geometry(f"+{x}+{y}")
            self.after(60, self.grab_set)
        except Exception:
            pass

    def relabel(self):
        i = self.var.get()
        if 0 <= i < len(self.opts):
            self.go.configure(text="Download " + self.opts[i][0].split("  ")[0].split(" - ")[0])

    def choose(self):
        i = self.var.get()
        self.destroy()
        self.on_choose(i)


class BookTab(ttk.Frame):
    def __init__(self, master, app, icons):
        super().__init__(master)
        self.app, self.icons, self.q = app, icons, app.q
        self.mode = tk.StringVar(value="site")
        self.url = tk.StringVar()
        self.query = tk.StringVar()
        self.folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Documents", "Books"))
        self.pref = tk.StringVar(value=PREF_CHOICES[0])
        self.max_pages = tk.StringVar(value="300")
        self.fmt_vars = {f: tk.BooleanVar(value=f in DEFAULT_FORMATS) for f in SHOWN_FORMATS}
        self.only_ok = tk.BooleanVar(value=True)
        self.filter_text = tk.StringVar()
        self.show_free = tk.BooleanVar(value=True)
        self.show_borrow = tk.BooleanVar(value=True)
        self.books, self.cards, self.sel_card = {}, [], None
        self.running = False
        self.cancel = threading.Event()
        self.last_file = None
        self.check_res = self.check_key = self._job = None
        self.icon_level, self.icon_msg = "idle", ""
        self.empty_text = ""
        self._pct = -1
        self._build()
        self.set_mode("site")

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 10, "pady": 4}
        mb = ttk.Frame(self)
        mb.pack(fill="x", **pad)
        self.btn_site = ttk.Button(mb, text="Search a site", command=lambda: self.set_mode("site"))
        self.btn_site.pack(side="left")
        self.btn_lookup = ttk.Button(mb, text="Look up a book", command=lambda: self.set_mode("lookup"))
        self.btn_lookup.pack(side="left", padx=4)

        self.slot = ttk.Frame(self)
        self.slot.pack(fill="x")

        # --- site mode
        sf = self.site_frame = ttk.Frame(self.slot)
        sf.columnconfigure(1, weight=1)
        ttk.Label(sf, text="Library URL:").grid(row=0, column=0, sticky="w")
        e = ttk.Entry(sf, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        e.bind("<Return>", lambda _e: self.start())
        e.bind("<FocusOut>", lambda _e: self.schedule_check(0))
        self.find_btn = ttk.Button(sf, text="Find books", style="Primary.TButton", command=self.start)
        self.find_btn.grid(row=0, column=2, padx=(6, 0))
        self.check_icon = tk.Label(sf, text="?", width=3, font=("Segoe UI", 18, "bold"), cursor="hand2")
        self.check_icon.grid(row=0, column=3, padx=(6, 0))
        self.check_icon.bind("<Button-1>", lambda _e: self.show_check_details())
        self.check_msg = tk.StringVar()
        ttk.Label(sf, textvariable=self.check_msg, style="Muted.TLabel", wraplength=680, justify="left"
                  ).grid(row=1, column=1, columnspan=3, sticky="w", padx=(6, 0))
        self.url.trace_add("write", lambda *_: self.schedule_check(900))
        fr = ttk.Frame(sf)
        fr.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(fr, text="Formats:").pack(side="left")
        for f in SHOWN_FORMATS:
            ttk.Checkbutton(fr, text=f, variable=self.fmt_vars[f]).pack(side="left", padx=(10, 0))
        ttk.Label(fr, text="Search:").pack(side="left", padx=(24, 4))
        ttk.Entry(fr, textvariable=self.filter_text, width=22).pack(side="left")
        fr2 = ttk.Frame(sf)
        fr2.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        ttk.Checkbutton(fr2, text="Only show books I can download", variable=self.only_ok).pack(side="left")
        ttk.Label(fr2, text="Pages to look through:").pack(side="left", padx=(24, 4))
        ttk.Entry(fr2, textvariable=self.max_pages, width=6).pack(side="left")
        for v in list(self.fmt_vars.values()) + [self.only_ok, self.filter_text]:
            v.trace_add("write", lambda *_: self.render_books())

        # --- lookup mode
        lf = self.lookup_frame = ttk.Frame(self.slot)
        lf.columnconfigure(1, weight=1)
        ttk.Label(lf, text="Title / author:").grid(row=0, column=0, sticky="w")
        qe = ttk.Entry(lf, textvariable=self.query)
        qe.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        qe.bind("<Return>", lambda _e: self.start())
        self.lookup_btn = ttk.Button(lf, text="Look up", style="Primary.TButton", command=self.start)
        self.lookup_btn.grid(row=0, column=2, padx=(6, 0))
        self.lookup_msg = tk.StringVar()
        ttk.Label(lf, textvariable=self.lookup_msg, style="Muted.TLabel", wraplength=700, justify="left"
                  ).grid(row=1, column=1, columnspan=2, sticky="w", padx=(6, 0))
        lr = ttk.Frame(lf)
        lr.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(lr, text="Free to download", variable=self.show_free).pack(side="left")
        ttk.Checkbutton(lr, text="Borrow / buy links", variable=self.show_borrow).pack(side="left", padx=(16, 0))
        for v in (self.show_free, self.show_borrow):
            v.trace_add("write", lambda *_: self.render_cards())

        # --- common
        cm = ttk.Frame(self)
        cm.pack(fill="x", **pad)
        cm.columnconfigure(1, weight=1)
        ttk.Label(cm, text="Save to folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(cm, textvariable=self.folder).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Button(cm, text="Browse...", command=self.browse).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(cm, text="Prefer:").grid(row=1, column=0, sticky="w")
        self.pref_cb = ttk.Combobox(cm, textvariable=self.pref, values=PREF_CHOICES, state="readonly", width=14)
        self.pref_cb.grid(row=1, column=1, sticky="w", padx=(6, 0))
        self.pref_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_pref())

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.on_cancel, state="disabled", style="Danger.TButton")
        self.cancel_btn.pack(side="left")
        self.dl_btn = ttk.Button(bar, text="Download selected", command=self.download_selected, style="Primary.TButton")
        self.dl_btn.pack(side="left", padx=6)
        self.dl_all_btn = ttk.Button(bar, text="Download all", command=self.download_all)
        self.dl_all_btn.pack(side="left")
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.status = tk.StringVar(value="Enter a library address and click Find books.")
        ttk.Label(bar, textvariable=self.status, style="Amber.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", **pad)
        self.counts = tk.StringVar()
        ttk.Label(self, textvariable=self.counts, style="Counts.TLabel").pack(anchor="w", padx=10)

        # --- results: a table (site mode) or cards (lookup mode)
        self.results = ttk.Frame(self)
        self.results.pack(fill="both", expand=True, **pad)
        self.tree_frame = ttk.Frame(self.results)
        cols = ("title", "formats", "pages", "size", "avail", "dl")
        self.tree = ttk.Treeview(self.tree_frame, columns=cols, show="headings", height=10, selectmode="extended")
        for c, text, w, anchor in (("title", "Title", 330, "w"), ("formats", "Formats", 130, "w"), ("pages", "Pages", 60, "center"),
                                   ("size", "Size", 80, "w"), ("avail", "Available", 70, "center"), ("dl", "Download", 100, "center")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "title"))
        sb = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double)

        self.cards_frame = ttk.Frame(self.results)
        self.cards_canvas = tk.Canvas(self.cards_frame, highlightthickness=0, bd=0)
        csb = ttk.Scrollbar(self.cards_frame, orient="vertical", command=self.cards_canvas.yview)
        self.cards_canvas.configure(yscrollcommand=csb.set)
        self.cards_inner = tk.Frame(self.cards_canvas)
        self._win = self.cards_canvas.create_window((0, 0), window=self.cards_inner, anchor="nw")
        self.cards_inner.bind("<Configure>", lambda _e: self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.bind("<Configure>", lambda e: self.cards_canvas.itemconfigure(self._win, width=e.width))
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self.cards_canvas.bind("<Enter>", lambda _e: self.cards_canvas.bind_all("<MouseWheel>", self._wheel))
        self.cards_canvas.bind("<Leave>", lambda _e: self.cards_canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.cards_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    # ------------------------------------------------------------ theme / modes / settings
    def apply_theme(self, p):
        self.check_icon.configure(bg=p["bg"])
        self.set_icon(self.icon_level, self.icon_msg)
        self.tree.tag_configure("blocked", foreground=p["dim"])
        self.tree.tag_configure("checking", foreground=p["muted"])
        self.tree.tag_configure("odd", background=p["alt"])
        self.cards_canvas.configure(bg=p["bg"])
        self.cards_inner.configure(bg=p["bg"])
        self.render_cards()

    def set_mode(self, m):
        self.mode.set(m)
        self.site_frame.pack_forget()
        self.lookup_frame.pack_forget()
        self.tree_frame.pack_forget()
        self.cards_frame.pack_forget()
        if m == "site":
            self.site_frame.pack(fill="x", padx=10, pady=4)
            self.tree_frame.pack(fill="both", expand=True)
            self.dl_btn.configure(text="Download selected")
            self.dl_all_btn.pack(side="left", after=self.dl_btn)
        else:
            self.lookup_frame.pack(fill="x", padx=10, pady=4)
            self.cards_frame.pack(fill="both", expand=True)
            self.dl_btn.configure(text="Download best match")
            self.dl_all_btn.pack_forget()
        self.btn_site.configure(style="SegOn.TButton" if m == "site" else "Seg.TButton")
        self.btn_lookup.configure(style="SegOn.TButton" if m == "lookup" else "Seg.TButton")

    def get_settings(self):
        return {"book_mode": self.mode.get(), "book_folder": self.folder.get().strip(), "book_pref": self.pref.get(),
                "book_formats": [f for f, v in self.fmt_vars.items() if v.get()], "book_only_ok": self.only_ok.get(),
                "book_max_pages": self.to_int(self.max_pages, 300)}

    def set_settings(self, s):
        if s.get("book_folder"):
            self.folder.set(s["book_folder"])
        if s.get("book_pref") in PREF_CHOICES:
            self.pref.set(s["book_pref"])
        if isinstance(s.get("book_formats"), list):
            for f, v in self.fmt_vars.items():
                v.set(f in s["book_formats"])
        if "book_only_ok" in s:
            self.only_ok.set(bool(s["book_only_ok"]))
        if "book_max_pages" in s:
            self.max_pages.set(str(s["book_max_pages"]))
        if s.get("book_mode") in ("site", "lookup"):
            self.set_mode(s["book_mode"])

    def pref_fmt(self):
        return self.pref.get().split()[0].upper()

    def on_pref(self):
        self.render_books()
        self.render_cards()

    def to_int(self, var, default):
        try:
            return max(0, int(var.get().strip()))
        except ValueError:
            return default

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or None, title="Choose where to save the books")
        if d:
            self.folder.set(os.path.normpath(d))

    def open_folder(self):
        d = self.folder.get().strip()
        if self.last_file and os.path.exists(self.last_file):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_file)])
                return
            d = os.path.dirname(self.last_file)
        if d and os.path.isdir(d):
            if os.name == "nt":
                os.startfile(d)
            else:
                subprocess.Popen(["xdg-open", d])

    # ------------------------------------------------------------ site check (green check / red X)
    def normalized_url(self):
        url = self.url.get().strip()
        if url and "://" not in url:
            url = "https://" + url
        p = urlsplit(url)
        if p.scheme in ("http", "https") and p.hostname and ("." in p.hostname or p.hostname == "localhost"):
            return url
        return None

    def set_icon(self, level, msg=""):
        sym, colname, _ = self.icons[level]
        self.icon_level, self.icon_msg = level, msg
        self.check_icon.configure(text=sym, fg=self.app.pal[colname])
        self.check_msg.set(msg)

    def schedule_check(self, delay_ms):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        url = self.normalized_url()
        if not url:
            self.check_res = self.check_key = None
            self.set_icon("idle", "")
            return
        if url == self.check_key and self.check_res is not None:
            return
        self._job = self.after(delay_ms, lambda: self.run_check(url))

    def run_check(self, url):
        self._job = None
        self.check_key, self.check_res = url, None
        self.set_icon("checking", "Checking the site for logins and blocking...")
        threading.Thread(target=lambda: self.q.put(("bookcheck", (url, check_site(url, "builtin")))), daemon=True).start()

    def on_check_result(self, key, res):
        if key != self.check_key:
            return
        self.check_res = res
        self.set_icon(res.level, res.summary + "  (click the icon for details)")

    def show_check_details(self):
        r = self.check_res
        if r is None:
            self.schedule_check(0)
            return
        body = r.summary + ("\n\n" + "\n".join("- " + d for d in r.details if "sitemap" not in d.lower()) if r.details else "")
        body += ("\n\nThis checks the first page only. Books behind a login or paywall can't be downloaded; "
                 "they get a red X in the list.")
        messagebox.showinfo("Book Finder - " + self.icons[r.level][2], body)

    # ------------------------------------------------------------ starting a search
    def emit(self, e):
        if e.get("type") == "book":
            e = {"type": "book", "book": copy.deepcopy(e["book"])}
        elif e.get("type") == "cards":
            e = dict(e, cards=copy.deepcopy(e["cards"]))
        self.q.put(("book", e))

    def busy_ui(self, on, text=""):
        self.running = on
        self.cancel_btn.configure(state="normal" if on else "disabled")
        self.find_btn.configure(state="disabled" if on else "normal")
        self.lookup_btn.configure(state="disabled" if on else "normal")
        if on:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.status.set(text)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=1, value=1 if text == "done" else 0)

    def start(self):
        if self.running:
            return
        self.cancel = threading.Event()
        if self.mode.get() == "site":
            url = self.normalized_url()
            if not url:
                messagebox.showerror("Book Finder", "Please enter a full website address, like https://library.example.org")
                return
            fmts = [f for f, v in self.fmt_vars.items() if v.get()]
            if not fmts:
                messagebox.showerror("Book Finder", "Tick at least one format to look for.")
                return
            res = self.check_res if self.check_key == url else None
            if res is not None and res.level == "bad" and not messagebox.askyesno(
                    "Book Finder", f"The site check says this will probably fail:\n\n{res.summary}\n\nTry anyway?", icon="warning"):
                return
            self.url.set(url)
            self.books = {}
            self.tree.delete(*self.tree.get_children())
            self.counts.set("")
            self.busy_ui(True, "Looking through the site...")
            threading.Thread(target=self._run_site, args=(url, fmts, max(1, self.to_int(self.max_pages, 300))),
                             daemon=True).start()
        else:
            q = self.query.get().strip()
            if len(q) < 3:
                messagebox.showerror("Book Finder", "Type a book title or an author (at least 3 letters).")
                return
            self.cards, self.sel_card = [], None
            self.empty_text = "Searching the catalogs..."
            self.render_cards()
            self.lookup_msg.set("")
            self.counts.set("")
            self.busy_ui(True, "Asking the catalogs...")
            threading.Thread(target=self._run_lookup, args=(q, self.pref_fmt()), daemon=True).start()

    def _run_site(self, url, fmts, max_pages):
        try:
            books.find_on_site(url, fmts, max_pages, 4, on_event=self.emit, cancel=self.cancel)
        except Exception as e:
            self.emit({"type": "error", "text": str(e)})

    def _run_lookup(self, q, pref):
        try:
            books.lookup(q, pref, on_event=self.emit, cancel=self.cancel)
        except Exception as e:
            self.emit({"type": "error", "text": str(e)})

    def on_cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")
        self.cancel_btn.configure(state="disabled")

    # ------------------------------------------------------------ events from the worker threads
    def on_event(self, e):
        t = e.get("type")
        if t == "book":
            self.upsert(e["book"])
        elif t == "stats":
            self.counts.set(f"Pages looked at: {e['scanned']}    Books found: {e['books']}    "
                            f"Downloadable: {e['ok']}    Blocked: {e['blocked']}")
        elif t == "done":
            self.busy_ui(False, "done")
            msg = f"Done: {e['books']} books, {e['ok']} you can download."
            if e.get("limit_hit"):
                msg += " Stopped at the page limit; the site has more pages than were looked through."
            if e.get("cancelled"):
                msg = "Cancelled."
            self.status.set(msg)
            if not e["books"]:
                self.status.set("No books found. Try a page that lists the books, or the Zimit-style JavaScript sites may need a different approach.")
        elif t == "catalog":
            self.status.set(f"Asked {e['name']}..." if e["ok"] else f"{e['name']} didn't answer.")
        elif t == "cards":
            self.cards = e["cards"]
            if self.sel_card is None or not any(c["id"] == self.sel_card for c in self.cards):
                self.sel_card = self.cards[0]["id"] if self.cards else None
            self.empty_text = "No matches. Try fewer words, or just the title or just the author."
            self.render_cards()
            n = len(self.cards)
            self.lookup_msg.set(f"Searched {e['answered']} of {e['total']} catalogs in {e['seconds']} seconds. {n} book"
                                f"{'' if n == 1 else 's'} matched; the best place to get each one is listed first."
                                + ("" if e["final"] else "  (checking the download links...)"))
            if e["final"]:
                self.busy_ui(False, "done")
                self.status.set(f"{sum(1 for c in self.cards if c['best'])} with a working free download.")
        elif t == "dl_progress":
            self.status.set(f"Downloading {e['i']} of {e['n']}: {e['title'][:40]}  {e['pct']}%")
        elif t == "dl_done":
            self.busy_ui(False, "done")
            if e["paths"]:
                self.last_file = e["paths"][-1]
            msg = f"Saved {len(e['paths'])} book{'' if len(e['paths']) == 1 else 's'} to {self.folder.get()}"
            self.status.set("Cancelled." if e["cancelled"] and not e["paths"] else msg)
            if e["errors"]:
                messagebox.showwarning("Book Finder", "Some downloads failed:\n\n" + "\n".join(
                    f"{t[:50]}: {m}" for t, m in e["errors"][:6]))
        elif t == "error":
            self.busy_ui(False)
            self.status.set("Error")
            messagebox.showerror("Book Finder", e["text"])

    # ------------------------------------------------------------ site mode table
    def chosen(self):
        return {f for f, v in self.fmt_vars.items() if v.get()}

    def visible(self, b):
        ch = self.chosen()
        fm = [f for f in b["formats"] if f in ch]
        if not fm:
            return False
        if self.only_ok.get() and not any(b["formats"][f]["ok"] is not False for f in fm):
            return False
        t = self.filter_text.get().strip().lower()
        return not t or t in b["title"].lower() or t in (b["author"] or "").lower()

    def _row(self, b):
        ch = self.chosen()
        fm = [f for f in FORMAT_ORDER if f in b["formats"] and f in ch]
        oks = [f for f in fm if b["formats"][f]["ok"]]
        checking = any(b["formats"][f]["ok"] is None for f in fm)
        pick = self.best_fmt(oks) if oks else (fm[0] if fm else None)
        size = b["formats"][pick]["size"] if pick else None
        avail = "✔" if oks else ("…" if checking else "✖")
        dl = ("Download ▾" if len(oks) > 1 else "Download") if oks else ("…" if checking else "Unavailable")
        tag = "ok" if oks else ("checking" if checking else "blocked")
        return (b["title"], "  ".join(fm), b["pages"] or "—", human_size(size), avail, dl), (tag,)

    def best_fmt(self, fmts):
        pref = self.pref_fmt()
        return sorted(fmts, key=lambda f: (f != pref, FORMAT_ORDER.index(f) if f in FORMAT_ORDER else 99))[0]

    def upsert(self, b):
        self.books[b["id"]] = b
        exists = self.tree.exists(b["id"])
        if not self.visible(b):
            if exists:
                self.tree.delete(b["id"])
            return
        vals, tags = self._row(b)
        if exists:
            self.tree.item(b["id"], values=vals, tags=tags)
        else:
            self.tree.insert("", "end", iid=b["id"], values=vals, tags=tags)

    def render_books(self):
        self.tree.delete(*self.tree.get_children())
        for b in self.books.values():
            if self.visible(b):
                vals, tags = self._row(b)
                self.tree.insert("", "end", iid=b["id"], values=vals, tags=tags)

    def on_tree_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid and self.tree.identify_column(ev.x) == "#6":
            self.download_book(iid, ask=True)
            return "break"

    def on_tree_double(self, ev):
        iid = self.tree.identify_row(ev.y)
        if iid:
            self.download_book(iid, ask=True)

    def book_item(self, b, fmt):
        d = b["formats"][fmt]
        return {"title": b["title"], "author": b["author"], "year": b["year"], "fmt": fmt, "url": d["url"],
                "size": d["size"], "source": b["source"], "public_domain": ""}

    def download_book(self, bid, ask=True):
        b = self.books.get(bid)
        if not b or self.running:
            return
        ch = self.chosen()
        oks = [f for f in FORMAT_ORDER if f in b["formats"] and f in ch and b["formats"][f]["ok"]]
        if not oks:
            notes = {b["formats"][f]["note"] for f in b["formats"]}
            messagebox.showinfo("Book Finder", "This book can't be downloaded from here.\n\n" + "\n".join(sorted(notes)))
            return
        if len(oks) == 1 or not ask:
            self.start_download([self.book_item(b, self.best_fmt(oks))])
            return
        options = [(f, b["formats"][f]["size"], True, "") for f in oks]
        default = oks.index(self.best_fmt(oks))
        FormatDialog(self, self.app.pal, b["title"], f"{b['pages']} pages" if b["pages"] else "", options,
                     self.folder.get(), lambda i: self.start_download([self.book_item(b, oks[i])]), default)

    def download_selected(self):
        if self.running:
            return
        if self.mode.get() == "lookup":
            c = next((c for c in self.cards if c["id"] == self.sel_card), None) or (self.cards[0] if self.cards else None)
            if not c or not c.get("best"):
                messagebox.showinfo("Book Finder", "Pick a book that has a working free download first.")
                return
            self.start_download([self.card_item(c, c["best"])])
            return
        sel = self.tree.selection()
        items = []
        for bid in sel:
            b = self.books.get(bid)
            if b:
                oks = [f for f in b["formats"] if f in self.chosen() and b["formats"][f]["ok"]]
                if oks:
                    items.append(self.book_item(b, self.best_fmt(oks)))
        if not items:
            messagebox.showinfo("Book Finder", "Click one or more books in the list first (Ctrl or Shift to pick several).")
            return
        self.start_download(items)

    def download_all(self):
        if self.running:
            return
        items = []
        for b in self.books.values():
            if self.visible(b):
                oks = [f for f in b["formats"] if f in self.chosen() and b["formats"][f]["ok"]]
                if oks:
                    items.append(self.book_item(b, self.best_fmt(oks)))
        if not items:
            messagebox.showinfo("Book Finder", "Nothing to download yet.")
            return
        total = sum(i["size"] or 0 for i in items)
        if not messagebox.askyesno("Book Finder", f"Download {len(items)} books"
                                   + (f" (about {human_size(total)})" if total else "") + f" to\n{self.folder.get()} ?"):
            return
        self.start_download(items)

    # ------------------------------------------------------------ downloading
    def start_download(self, items):
        if self.running or not items:
            return
        folder = self.folder.get().strip()
        if not folder:
            self.browse()
            folder = self.folder.get().strip()
            if not folder:
                return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Book Finder", f"Can't use that folder:\n{e}")
            return
        self.cancel = threading.Event()
        self._pct = -1
        self.busy_ui(True, "Starting download...")
        threading.Thread(target=self._dl_worker, args=(items, folder), daemon=True).start()

    def _dl_worker(self, items, folder):
        paths, errors = [], []
        for i, it in enumerate(items, 1):
            if self.cancel.is_set():
                break
            self.emit({"type": "dl_progress", "i": i, "n": len(items), "title": it["title"], "pct": 0})

            def prog(done, total, i=i, it=it):
                pct = int(done * 100 / total) if total else 0
                if pct - self._pct >= 3 or pct == 100:
                    self._pct = pct
                    self.emit({"type": "dl_progress", "i": i, "n": len(items), "title": it["title"], "pct": pct})
            try:
                path = books.download_file(it["url"], folder, it["title"], it["fmt"], prog, self.cancel)
                books.add_to_manifest(folder, {"title": it["title"], "author": it.get("author") or "", "year": it.get("year") or "",
                                               "format": it["fmt"], "size_bytes": os.path.getsize(path),
                                               "source": it.get("source", ""), "public_domain": it.get("public_domain", ""),
                                               "url": it["url"], "file": path})
                paths.append(path)
            except RuntimeError as e:
                if str(e) == "Cancelled":
                    break
                errors.append((it["title"], str(e)))
        self.emit({"type": "dl_done", "paths": paths, "errors": errors, "cancelled": self.cancel.is_set()})

    # ------------------------------------------------------------ lookup mode cards
    def card_item(self, c, best):
        return {"title": c["title"], "author": c["author"], "year": c["year"], "fmt": best["fmt"], "url": best["url"],
                "size": best["size"], "source": best["source"], "public_domain": "yes" if best["public_domain"] else ""}

    def select_card(self, cid):
        self.sel_card = cid
        self.render_cards()

    def _card_visible(self, c):
        free = c["status"] in ("public", "free")
        return (free and self.show_free.get()) or (not free and self.show_borrow.get())

    def download_card(self, c):
        if self.running:
            return
        pairs = [(s, f, d) for s in c["sources"] if s["kind"] == "free" for f, d in s["formats"].items() if d.get("ok")]
        if not pairs:
            return
        if len(pairs) == 1:
            s, f, d = pairs[0]
            self.start_download([self.card_item(c, {"fmt": f, "url": d["url"], "size": d.get("size"), "source": s["name"],
                                                    "public_domain": s["public_domain"]})])
            return
        pairs.sort(key=lambda t: (t[1] != self.pref_fmt(), -t[0]["trust"]))
        options = [(f"{f} - {s['name']}", d.get("size"), True, "") for s, f, d in pairs]

        def chosen(i):
            s, f, d = pairs[i]
            self.start_download([self.card_item(c, {"fmt": f, "url": d["url"], "size": d.get("size"), "source": s["name"],
                                                    "public_domain": s["public_domain"]})])
        FormatDialog(self, self.app.pal, c["title"], f"{c['pages']} pages" if c.get("pages") else "", options,
                     self.folder.get(), chosen, 0)

    def render_cards(self):
        p = self.app.pal
        for w in self.cards_inner.winfo_children():
            w.destroy()
        self.cards_inner.configure(bg=p["bg"])
        shown = [c for c in self.cards if self._card_visible(c)]
        if not shown:
            tk.Label(self.cards_inner, text=self.empty_text, bg=p["bg"], fg=p["muted"], font=("Segoe UI", 10)
                     ).pack(anchor="w", padx=10, pady=14)
        for c in shown:
            self._card(c, c["id"] == self.sel_card, p)

    def _link(self, parent, text, url, bg, p, color=None):
        lb = tk.Label(parent, text=text, bg=bg, fg=color or p["muted"], font=("Segoe UI", 9), cursor="hand2")
        lb.bind("<Button-1>", lambda _e: webbrowser.open(url))
        return lb

    def _card(self, c, sel, p):
        bg = p["sel"] if sel else p["panel"]
        box = tk.Frame(self.cards_inner, bg=bg, highlightthickness=1,
                       highlightbackground=p["amber"] if sel else p["border"], highlightcolor=p["amber"])
        box.pack(fill="x", padx=4, pady=4)
        head = tk.Frame(box, bg=bg)
        head.pack(fill="x", padx=14, pady=(10, 4))
        left = tk.Frame(head, bg=bg)
        left.pack(side="left")
        tk.Label(left, text=c["title"], bg=bg, fg=p["text"], font=("Segoe UI", 10, "bold"), anchor="w").pack(anchor="w")
        meta = "  ·  ".join(x for x in (c["author"], str(c["year"]) if c["year"] else "",
                                       f"{c['pages']} pages" if c.get("pages") else "") if x)
        tk.Label(left, text=meta, bg=bg, fg=p["muted"], font=("Segoe UI", 9), anchor="w").pack(anchor="w")
        col = p[BADGE_COLOR[c["status"]]]
        tk.Label(head, text=c["badge"], bg=p["field"], fg=col, font=("Segoe UI", 9), highlightthickness=1,
                 highlightbackground=col, padx=10, pady=2).pack(side="right")
        tk.Frame(box, bg=p["border"], height=1).pack(fill="x")
        row = tk.Frame(box, bg=bg)
        row.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(row, text="Best source", bg=bg, fg=p["dim"], font=("Segoe UI", 9), width=11, anchor="w").pack(side="left")
        best = c.get("best")
        if best:
            tk.Label(row, text=best["source"], bg=bg, fg=p["text"], font=("Segoe UI", 10, "bold")).pack(side="left")
            fmts = [f for s in c["sources"] if s["name"] == best["source"] for f, d in s["formats"].items() if d.get("ok")]
            for f in sorted(set(fmts), key=lambda x: FORMAT_ORDER.index(x) if x in FORMAT_ORDER else 99):
                tk.Label(row, text=f, bg=p["field"], fg=p["blue"], font=("Segoe UI", 9), padx=6,
                         highlightthickness=1, highlightbackground=p["border"]).pack(side="left", padx=(8, 0))
            tk.Label(row, text=human_size(best["size"]) if best["size"] else "", bg=bg, fg=p["muted"],
                     font=("Segoe UI", 9)).pack(side="left", padx=10)
            many = sum(1 for s in c["sources"] if s["kind"] == "free" for d in s["formats"].values() if d.get("ok")) > 1
            ttk.Button(row, text="Download ▾" if many else "Download", style="Primary.TButton" if sel else "TButton",
                       command=lambda c=c: self.download_card(c)).pack(side="right")
            tk.Label(row, text="✔", bg=bg, fg=p["green"], font=("Segoe UI", 11, "bold")).pack(side="right", padx=14)
        else:
            tk.Label(row, text="No free download found", bg=bg, fg=p["red"], font=("Segoe UI", 10, "bold")).pack(side="left")
            tk.Label(row, text="✖", bg=bg, fg=p["red"], font=("Segoe UI", 10, "bold")).pack(side="left", padx=8)
            lk = c["links"]
            buy = lk.get("buy") or lk.get("worldcat")
            ttk.Button(row, text="Buy / find ▸", command=lambda u=buy["url"]: webbrowser.open(u)).pack(side="right")
            if lk.get("borrow") or lk.get("info"):
                bw = lk.get("borrow") or lk.get("info")
                ttk.Button(row, text="Borrow ▸" if lk.get("borrow") else "Open ▸",
                           command=lambda u=bw["url"]: webbrowser.open(u)).pack(side="right", padx=6)
        also = tk.Frame(box, bg=bg)
        also.pack(fill="x", padx=14, pady=(2, 10))
        tk.Label(also, text="Also at:", bg=bg, fg=p["dim"], font=("Segoe UI", 9), width=11, anchor="w").pack(side="left")
        for s in c["sources"]:
            oks = [f for f, d in s["formats"].items() if d.get("ok")]
            if s["kind"] == "free" and oks and not (best and s["name"] == best["source"]):
                self._link(also, f"{s['name']}  ✔  {'/'.join(oks)}", s["page_url"] or "", bg, p, p["green"]).pack(side="left", padx=(0, 18))
        for key, label in (("borrow", "borrow"), ("buy", "buy"), ("info", "details")):
            if c["links"].get(key):
                lk = c["links"][key]
                self._link(also, f"{lk['name']}  {label}", lk["url"], bg, p).pack(side="left", padx=(0, 18))
        w = c["links"]["worldcat"]
        self._link(also, "WorldCat  find in a library", w["url"], bg, p).pack(side="left")
        for wdg in (box, head, left):
            wdg.bind("<Button-1>", lambda _e, cid=c["id"]: self.select_card(cid))
