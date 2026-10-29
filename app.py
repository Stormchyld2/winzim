"""Cindrix Recon - collect websites (as ZIM files) and books before you need them.

Engines:
  * Zimit (Docker Desktop)  - the official openZIM crawler, real browser, handles JavaScript sites.
  * Built-in (no Docker)    - simple pure-Python crawler for ordinary static sites.
"""
import datetime
import os
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from urllib.parse import urlsplit

import flame
import settings as appsettings
from appinfo import CONTACT, VERSION
from bookui import BookTab
from crawler import Options, convert
from preview import find_kiwix, open_in_kiwix, preview_in_browser
from sitecheck import MB_PER_PAGE, check_site, estimate_size_text, whole_site_limits
from zimit_engine import docker_status, run_zimit

APP_NAME = "Cindrix Recon"

# ---------------------------------------------------------------- themes
DARK = dict(bg="#131814", panel="#1a211c", field="#0f1411", border="#2d3d33", btnborder="#415548",
            text="#e1e8e1", muted="#8c998f", dim="#55625a", amber="#d9a02b", amber_hi="#e8b13f",
            amber_dis="#5c4a1e", green="#35c25f", red="#f06a6a", blue="#5aa9ff", sel="#1e2f42",
            alt="#171e19", head="#202a24", banner="#0f1511", hover="#243028", scroll="#3b4d41",
            onamber="#17110a", flame2="#ffd36b")
LIGHT = dict(bg="#f0f0f0", panel="#ffffff", field="#ffffff", border="#c8c8c8", btnborder="#adadad",
             text="#1a1a1a", muted="#666666", dim="#9a9a9a", amber="#d9a02b", amber_hi="#e8b13f",
             amber_dis="#e9d8ae", green="#1a9a3a", red="#c62828", blue="#0b5fd0", sel="#d6e8fb",
             alt="#f6f6f6", head="#ececec", banner="#ffffff", hover="#e6e6e6", scroll="#c1c1c1",
             onamber="#17110a", flame2="#ffd36b")

ICONS = {  # level -> (symbol, palette colour name, tooltip title)
    "idle": ("?", "muted", "Not checked yet"),
    "checking": ("...", "muted", "Checking the site..."),
    "ok": ("✔", "green", "Go ahead"),
    "warn": ("!", "amber", "Proceed with caution"),
    "bad": ("✖", "red", "Likely to fail"),
}

# ------------------------------------------------------- presets and levels
AUTO = "auto"  # "Whole site": limits are worked out from the sitemap
WHOLE = "Whole site (all pages, automatic limits)"
CUSTOM = "Custom (see Advanced)"
PRESETS = {  # name -> (max_pages, max_mb, max_minutes, path_scope, subdomains)
    WHOLE: (AUTO, AUTO, 0, False, False),
    "This section only (the URL's folder)": (1000, 500, 0, True, False),
    "Just this page": (1, 100, 0, False, False),
    "Quick test (first 10 pages)": (10, 100, 0, False, False),
    "Everything, no limits (can be huge)": (0, 0, 0, False, False),
}
PRESET_NAMES = list(PRESETS)

CUSTOM_DETAIL = "Custom (see Advanced)"
FLAG_NAMES = ("skip_images", "skip_media", "skip_fonts", "skip_docs", "shrink_images", "no_scripts")
DETAILS = {  # label -> flags switched on (everything else off); max_file_mb is the per-file size cap
    "Full (everything)": {},
    "Lite (smaller pictures, no video)": {"shrink_images": True, "skip_media": True},
    "No pictures (text and layout only)": {"skip_images": True, "skip_media": True},
    "Minimum (smallest file)": {"skip_images": True, "skip_media": True, "skip_fonts": True,
                                "no_scripts": True, "max_file_mb": 5},
}
DETAIL_NAMES = list(DETAILS)

LIMIT_TEXT = {
    "pages": "Stopped at the page limit; the site has more than was saved.",
    "size": "Stopped at the size limit; the site has more than was saved.",
    "time": "Stopped at the time limit; the site has more than was saved.",
}
DOCKER_TEXT = {"checking": "Docker: checking", "ready": "Docker ready", "not_running": "Docker not running",
               "missing": "Docker not found"}


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def default_filename(url):
    host = urlsplit(url).hostname or "website"
    host = re.sub(r"^www\.", "", host)
    base = re.sub(r"[^a-zA-Z0-9]+", "_", host).strip("_").lower() or "website"
    return f"{base}_{datetime.date.today().isoformat()}.zim"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} - site and book collection")
        self.geometry("880x800")
        self.minsize(800, 680)
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.out_path = None
        self.dark = True
        self.pal = DARK
        self.docker_state = "checking"
        self.icon_level, self.icon_msg = "idle", ""
        self.run_notes = []
        self.row_n = 0

        self.banner = tk.Canvas(self, height=48, highlightthickness=0, bd=0)
        self.banner.pack(fill="x")
        self.banner.bind("<Configure>", lambda _e: self.draw_banner())

        self.footer = tk.Frame(self)
        self.footer.pack(side="bottom", fill="x")
        self.footer_label = tk.Label(self.footer, text=f"{APP_NAME} {VERSION}   ·   {CONTACT}   ·   About", anchor="w",
                                     font=("Segoe UI", 9), cursor="hand2")
        self.footer_label.pack(side="left", padx=10, pady=3)
        self.footer_label.bind("<Button-1>", lambda _e: self.show_about())
        self.main_nb = ttk.Notebook(self, style="Main.TNotebook")
        self.main_nb.pack(fill="both", expand=True)
        zt = ttk.Frame(self.main_nb)
        self.main_nb.add(zt, text="Sites")

        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(zt)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Website URL:").grid(row=0, column=0, sticky="w")
        self.url = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew", pady=3, padx=(6, 0))
        e.bind("<Return>", lambda _e: self.start())
        e.bind("<FocusOut>", lambda _e: self.schedule_check(0))
        e.focus_set()
        self.convert_btn = ttk.Button(top, text="Convert", command=self.start, style="Primary.TButton")
        self.convert_btn.grid(row=0, column=2, padx=(6, 0))
        # site check indicator: fixed spot right of Convert
        self.check_icon = tk.Label(top, text="?", width=3, font=("Segoe UI", 18, "bold"), cursor="hand2")
        self.check_icon.grid(row=0, column=3, padx=(6, 0))
        self.check_icon.bind("<Button-1>", lambda _e: self.show_check_details())
        self.check_msg = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.check_msg, style="Muted.TLabel", wraplength=660, justify="left"
                  ).grid(row=3, column=1, columnspan=3, sticky="w", padx=(6, 0))
        self.check_res = None
        self.check_key = None
        self._check_job = None
        self.url.trace_add("write", lambda *_: self.schedule_check(900))

        ttk.Label(top, text="Save to folder:").grid(row=1, column=0, sticky="w")
        self.folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Documents", "ZIM"))
        ttk.Entry(top, textvariable=self.folder).grid(row=1, column=1, sticky="ew", pady=3, padx=(6, 0))
        ttk.Button(top, text="Browse...", command=self.browse).grid(row=1, column=2, padx=(6, 0))

        ttk.Label(top, text="File name:").grid(row=2, column=0, sticky="w")
        self.fname = tk.StringVar()
        ttk.Entry(top, textvariable=self.fname).grid(row=2, column=1, sticky="ew", pady=3, padx=(6, 0))
        ttk.Label(top, text="blank = automatic", style="Muted.TLabel").grid(row=2, column=2, padx=(6, 0))

        eng = ttk.LabelFrame(zt, text="Engine")
        eng.pack(fill="x", **pad)
        self.engine = tk.StringVar(value="zimit")
        ttk.Radiobutton(eng, text="Zimit (needs Docker Desktop) - best quality, handles JavaScript sites",
                        variable=self.engine, value="zimit").pack(anchor="w", padx=8, pady=(4, 0))
        ttk.Radiobutton(eng, text="Built-in (no Docker) - simple websites only",
                        variable=self.engine, value="builtin").pack(anchor="w", padx=8)
        self.engine.trace_add("write", lambda *_: self.schedule_check(200))
        self.docker_msg = tk.StringVar(value="Checking Docker Desktop...")
        ttk.Label(eng, textvariable=self.docker_msg, style="Muted.TLabel").pack(anchor="w", padx=28, pady=(0, 4))

        self.max_pages = tk.StringVar(value="20000")
        self.max_mb = tk.StringVar(value="20480")
        self.max_minutes = tk.StringVar(value="0")
        self.workers = tk.StringVar(value="4")
        self.path_scope = tk.BooleanVar(value=False)
        self.subdomains = tk.BooleanVar(value=False)
        self.dv = {n: tk.BooleanVar(value=False) for n in FLAG_NAMES}   # detail switches
        self.max_file_mb = tk.StringVar(value="0")
        self.depth = tk.StringVar(value="0")

        self.presetf = ttk.Frame(zt)
        self.presetf.pack(fill="x", **pad)
        ttk.Label(self.presetf, text="What to save:").pack(side="left")
        self.preset = tk.StringVar(value=PRESET_NAMES[0])
        self.preset_cb = ttk.Combobox(self.presetf, textvariable=self.preset, values=PRESET_NAMES + [CUSTOM],
                                      state="readonly", width=34)
        self.preset_cb.pack(side="left", padx=6)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda _e: self.apply_preset())
        ttk.Label(self.presetf, text="Detail level:").pack(side="left", padx=(8, 0))
        self.detail = tk.StringVar(value=DETAIL_NAMES[0])
        self.detail_cb = ttk.Combobox(self.presetf, textvariable=self.detail, values=DETAIL_NAMES + [CUSTOM_DETAIL],
                                      state="readonly", width=32)
        self.detail_cb.pack(side="left", padx=6)
        self.detail_cb.bind("<<ComboboxSelected>>", lambda _e: self.apply_detail())
        self.adv_open = False
        self.adv_btn = ttk.Button(self.presetf, text="Advanced ▸", command=self.toggle_adv)
        self.adv_btn.pack(side="right")
        self._applying = False
        for v in (self.max_pages, self.max_mb, self.max_minutes, self.path_scope, self.subdomains):
            v.trace_add("write", lambda *_: self.mark_custom())
        for v in list(self.dv.values()) + [self.max_file_mb]:
            v.trace_add("write", lambda *_: self.on_detail_edit())
        self.detail.trace_add("write", lambda *_: self.refresh_estimate())

        self.est_msg = tk.StringVar(value="")
        self.est_label = ttk.Label(zt, textvariable=self.est_msg, style="Muted.TLabel")
        self.est_label.pack(anchor="w", padx=10)

        opts = ttk.LabelFrame(zt, text="Advanced options")  # shown by the Advanced button
        self.opts_frame = opts
        r = ttk.Frame(opts)
        r.pack(fill="x", padx=8, pady=6)
        ttk.Label(r, text="Workers (1-16):").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(r, from_=1, to=16, width=4, textvariable=self.workers).grid(row=0, column=1, padx=6)
        ttk.Label(r, text="Limit pages:").grid(row=0, column=2, sticky="w", padx=(14, 0))
        ttk.Entry(r, width=7, textvariable=self.max_pages).grid(row=0, column=3, padx=6)
        ttk.Label(r, text="Limit size (MB):").grid(row=0, column=4, sticky="w", padx=(14, 0))
        ttk.Entry(r, width=7, textvariable=self.max_mb).grid(row=0, column=5, padx=6)
        ttk.Label(r, text="Limit time (min):").grid(row=0, column=6, sticky="w", padx=(14, 0))
        ttk.Entry(r, width=6, textvariable=self.max_minutes).grid(row=0, column=7, padx=6)
        ttk.Label(r, text="0 = no limit. More workers = faster, but heavy use may get you blocked by some sites "
                          "(and Zimit uses more memory).", style="Muted.TLabel"
                  ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(4, 0))
        ttk.Checkbutton(r, text="Only pages inside this URL's folder",
                        variable=self.path_scope).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(r, text="Include subdomains",
                        variable=self.subdomains).grid(row=2, column=4, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Separator(r, orient="horizontal").grid(row=3, column=0, columnspan=8, sticky="ew", pady=8)
        ttk.Label(r, text="Make the file smaller by leaving out:", style="Muted.TLabel"
                  ).grid(row=4, column=0, columnspan=8, sticky="w")
        ttk.Checkbutton(r, text="Pictures", variable=self.dv["skip_images"]
                        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(r, text="Video and audio", variable=self.dv["skip_media"]
                        ).grid(row=5, column=2, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(r, text="Fonts", variable=self.dv["skip_fonts"]
                        ).grid(row=5, column=4, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(r, text="PDFs and other documents", variable=self.dv["skip_docs"]
                        ).grid(row=5, column=6, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(r, text="Shrink pictures (Built-in engine)", variable=self.dv["shrink_images"]
                        ).grid(row=6, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(r, text="Remove scripts (Built-in engine)", variable=self.dv["no_scripts"]
                        ).grid(row=6, column=4, columnspan=4, sticky="w")
        ttk.Label(r, text="Skip files over (MB):").grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Entry(r, width=6, textvariable=self.max_file_mb).grid(row=7, column=2, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(r, text="Depth (clicks from start page):").grid(row=7, column=3, columnspan=3, sticky="w",
                                                                  padx=(14, 0), pady=(6, 0))
        ttk.Entry(r, width=5, textvariable=self.depth).grid(row=7, column=6, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(r, text="Skip-over-MB applies to the Built-in engine only. 0 = no limit.", style="Muted.TLabel"
                  ).grid(row=8, column=0, columnspan=8, sticky="w", pady=(4, 0))

        bar = ttk.Frame(zt)
        bar.pack(fill="x", **pad)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.on_cancel, state="disabled",
                                     style="Danger.TButton")
        self.cancel_btn.pack(side="left")
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.kiwix_btn = ttk.Button(bar, text="Open in Kiwix", command=self.open_kiwix, state="disabled")
        self.kiwix_btn.pack(side="left")
        self.preview_btn = ttk.Button(bar, text="Preview in browser", command=self.open_preview, state="disabled")
        self.preview_btn.pack(side="left", padx=6)
        self.status = tk.StringVar(value="Ready. Enter a URL and click Convert.")
        self.status_label = ttk.Label(bar, textvariable=self.status, style="Amber.TLabel")
        self.status_label.pack(side="right")

        self.progress = ttk.Progressbar(zt, mode="indeterminate")
        self.progress.pack(fill="x", **pad)
        self.counts = tk.StringVar(value="")
        ttk.Label(zt, textvariable=self.counts, style="Counts.TLabel").pack(anchor="w", padx=10)

        nb = ttk.Notebook(zt)
        nb.pack(fill="both", expand=True, **pad)
        pages = ttk.Frame(nb)
        nb.add(pages, text="Pages")
        self.tree = ttk.Treeview(pages, columns=("status", "url"), show="headings", height=8)
        self.tree.heading("status", text="Status")
        self.tree.heading("url", text="Page")
        self.tree.column("status", width=130, stretch=False)
        self.tree.column("url", width=600)
        sb = ttk.Scrollbar(pages, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        logf = ttk.Frame(nb)
        nb.add(logf, text="Log")
        self.log = scrolledtext.ScrolledText(logf, height=8, state="disabled", font=("Consolas", 9), relief="flat")
        self.log.pack(fill="both", expand=True)

        self.result = tk.StringVar(value="")
        self.result_label = ttk.Label(zt, textvariable=self.result, wraplength=840, style="Result.TLabel")
        self.result_label.pack(anchor="w", padx=10, pady=(0, 8))

        self.books = BookTab(self.main_nb, self, ICONS)
        self.main_nb.add(self.books, text="Books")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)
        threading.Thread(target=self.check_docker, daemon=True).start()
        self.load_settings()
        self.apply_theme(self.dark)

    # --------------------------------------------------------------- theme
    def apply_theme(self, dark):
        self.dark = bool(dark)
        p = self.pal = DARK if self.dark else LIGHT
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        self.configure(bg=p["bg"])
        st.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["field"],
                     bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                     troughcolor=p["field"], focuscolor=p["bg"], font=("Segoe UI", 10))
        st.configure("TLabel", background=p["bg"], foreground=p["text"])
        st.configure("Muted.TLabel", foreground=p["muted"])
        st.configure("Amber.TLabel", foreground=p["amber"])
        st.configure("Counts.TLabel", font=("Segoe UI", 10, "bold"))
        st.configure("Result.TLabel", foreground=p["green"], font=("Segoe UI", 10, "bold"))
        st.configure("Warn.TLabel", foreground=p["amber"], font=("Segoe UI", 10, "bold"))
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["text"], insertcolor=p["text"],
                     bordercolor=p["btnborder"], padding=3)
        st.map("TEntry", bordercolor=[("focus", p["amber"])], lightcolor=[("focus", p["amber"])],
               darkcolor=[("focus", p["amber"])])
        st.configure("TSpinbox", fieldbackground=p["field"], foreground=p["text"], arrowcolor=p["amber"],
                     bordercolor=p["btnborder"], background=p["panel"])
        st.configure("TButton", background=p["panel"], foreground=p["text"], bordercolor=p["btnborder"],
                     padding=(10, 4))
        st.map("TButton", background=[("disabled", p["bg"]), ("active", p["hover"])],
               foreground=[("disabled", p["dim"])], bordercolor=[("disabled", p["border"])])
        st.configure("Primary.TButton", background=p["amber"], foreground=p["onamber"], bordercolor=p["amber"],
                     font=("Segoe UI", 10, "bold"), padding=(14, 4))
        st.map("Primary.TButton", background=[("disabled", p["amber_dis"]), ("active", p["amber_hi"])],
               foreground=[("disabled", p["muted"])], bordercolor=[("disabled", p["amber_dis"])])
        st.configure("Danger.TButton", foreground=p["red"], bordercolor="#a24a4a" if self.dark else p["red"])
        st.map("Danger.TButton", foreground=[("disabled", p["dim"])], bordercolor=[("disabled", p["border"])])
        st.configure("TCombobox", fieldbackground=p["field"], background=p["panel"], foreground=p["text"],
                     arrowcolor=p["amber"], bordercolor=p["btnborder"], selectbackground=p["field"],
                     selectforeground=p["text"], padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["text"])],
               bordercolor=[("focus", p["amber"])])
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=p["bg"], foreground=p["text"], indicatorbackground=p["field"],
                         indicatorforeground=p["amber"], indicatorcolor=p["field"])
            st.map(w, background=[("active", p["bg"])], foreground=[("disabled", p["dim"])],
                   indicatorcolor=[("selected", p["amber"]), ("pressed", p["amber"])])
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["border"])
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"])
        st.configure("TSeparator", background=p["border"])
        st.configure("TNotebook", background=p["bg"], bordercolor=p["border"], tabmargins=(0, 2, 0, 0))
        st.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(16, 5),
                     bordercolor=p["border"])
        st.configure("Main.TNotebook", background=p["bg"], bordercolor=p["border"], tabmargins=(8, 6, 0, 0))
        st.configure("Main.TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(26, 8),
                     font=("Segoe UI", 10, "bold"), bordercolor=p["border"])
        st.map("Main.TNotebook.Tab", foreground=[("selected", p["amber"])], background=[("selected", p["panel"])])
        st.configure("Seg.TButton", background=p["field"], foreground=p["muted"], bordercolor=p["border"], padding=(16, 4))
        st.map("Seg.TButton", background=[("active", p["hover"])])
        st.configure("SegOn.TButton", background=p["sel"] if self.dark else "#f3e6c4", foreground=p["amber"],
                     bordercolor=p["amber"], font=("Segoe UI", 10, "bold"), padding=(16, 4))
        st.map("SegOn.TButton", background=[("active", p["sel"] if self.dark else "#f3e6c4")])
        st.map("TNotebook.Tab", foreground=[("selected", p["amber"])], background=[("selected", p["panel"])])
        st.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["text"],
                     rowheight=22, bordercolor=p["border"])
        st.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["text"])])
        st.configure("Treeview.Heading", background=p["head"], foreground=p["muted"], relief="flat",
                     font=("Segoe UI", 10, "bold"))
        st.map("Treeview.Heading", background=[("active", p["head"])])
        st.configure("Horizontal.TProgressbar", troughcolor=p["field"], background=p["green"],
                     bordercolor=p["border"], lightcolor=p["green"], darkcolor=p["green"], thickness=12)
        st.configure("Vertical.TScrollbar", background=p["scroll"], troughcolor=p["field"],
                     arrowcolor=p["muted"], bordercolor=p["field"])
        self.tree.tag_configure("saved", foreground=p["blue"])
        self.tree.tag_configure("err", foreground=p["red"])
        self.tree.tag_configure("odd", background=p["alt"])
        self.log.configure(bg=p["field"], fg=p["text"], insertbackground=p["text"], selectbackground=p["sel"],
                           highlightthickness=1, highlightbackground=p["border"], highlightcolor=p["border"])
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["text"])
        for cb in (self.preset_cb, self.detail_cb):  # popup lists that already exist
            try:
                pd = self.tk.call("ttk::combobox::PopdownWindow", str(cb))
                self.tk.call(f"{pd}.f.l", "configure", "-background", p["field"], "-foreground", p["text"],
                             "-selectbackground", p["sel"], "-selectforeground", p["text"])
            except Exception:
                pass
        self.check_icon.configure(bg=p["bg"])
        self.footer.configure(bg=p["banner"])
        self.footer_label.configure(bg=p["banner"], fg=p["muted"])
        self.set_icon(self.icon_level, self.icon_msg)
        self.books.apply_theme(p)
        self.draw_banner()
        self.dark_titlebar(self.dark)

    def show_about(self):
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {VERSION}\n\nA Cindrix tool for collecting what you'll need before you need it: websites saved as "
            f"offline ZIM files, and books found and downloaded from public libraries.\n\nContact: {CONTACT}\n\n"
            "Only copy sites and books you have the right to copy. It does not log in to anything or get around paywalls.")

    def toggle_theme(self):
        self.apply_theme(not self.dark)

    def dark_titlebar(self, on):
        if os.name != "nt":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if on else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old Windows 10 value)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
        except Exception:
            pass

    def draw_banner(self):
        p, c = self.pal, self.banner
        c.configure(bg=p["banner"])
        c.delete("all")
        try:
            w = max(600, int(c.winfo_width()))
        except (TypeError, ValueError):
            w = 880
        c.create_line(0, 47, w, 47, fill=p["border"])
        c.create_rectangle(0, 46, 120, 48, fill=p["amber"], width=0)
        c.create_polygon(flame.outline(flame.OUTER, 15, 5, 26, 38), fill=p["amber"], outline="")
        c.create_polygon(flame.outline(flame.INNER, 15, 5, 26, 38), fill=p["flame2"], outline="")
        c.create_text(50, 17, text="Cindrix Recon", anchor="w", fill=p["text"], font=("Segoe UI", 14, "bold"))
        c.create_text(51, 35, text="Site and book collection", anchor="w", fill=p["muted"], font=("Segoe UI", 9))
        # theme switch (right side)
        pill_fill = "#1f2c23" if self.dark else "#e8e2d0"
        c.create_text(w - 16, 24, text="Dark" if self.dark else "Light", anchor="e", fill=p["amber"],
                      font=("Segoe UI", 9), tags=("theme",))
        c.create_oval(w - 100, 14, w - 80, 34, fill=pill_fill, outline=p["amber"], tags=("theme",))
        c.create_oval(w - 70, 14, w - 50, 34, fill=pill_fill, outline=p["amber"], tags=("theme",))
        c.create_rectangle(w - 90, 15, w - 60, 33, fill=pill_fill, outline=pill_fill, tags=("theme",))
        c.create_line(w - 90, 14, w - 60, 14, fill=p["amber"], tags=("theme",))
        c.create_line(w - 90, 34, w - 60, 34, fill=p["amber"], tags=("theme",))
        kx = w - 60 if self.dark else w - 90
        c.create_oval(kx - 8, 16, kx + 8, 32, fill=p["amber"], outline=p["amber"], tags=("theme",))
        c.create_text(w - 108, 24, text="Theme", anchor="e", fill=p["muted"], font=("Segoe UI", 9), tags=("theme",))
        c.tag_bind("theme", "<Button-1>", lambda _e: self.toggle_theme())
        c.tag_bind("theme", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("theme", "<Leave>", lambda _e: c.configure(cursor=""))
        # Docker chip
        state = self.docker_state
        col = {"ready": p["green"], "not_running": p["amber"], "missing": p["red"]}.get(state, p["dim"])
        text = DOCKER_TEXT.get(state, "")
        tx = w - 180
        c.create_text(tx, 24, text=text, anchor="e", fill=p["muted"], font=("Segoe UI", 9))
        ox = tx - 7 * len(text) - 16
        c.create_oval(ox, 19, ox + 10, 29, fill=col, outline=col)

    # ------------------------------------------------- presets and settings
    def preset_values(self, name):
        vals = PRESETS.get(name)
        if not vals:
            return None
        if vals[0] == AUTO:
            pages = self.check_res.pages if (self.check_res is not None and self.check_res.level != "bad") else None
            mp, mm = whole_site_limits(pages)
            return (mp, mm, vals[2], vals[3], vals[4])
        return vals

    def apply_preset(self):
        vals = self.preset_values(self.preset.get())
        if not vals:
            return
        self._applying = True
        try:
            self.max_pages.set(str(vals[0]))
            self.max_mb.set(str(vals[1]))
            self.max_minutes.set(str(vals[2]))
            self.path_scope.set(vals[3])
            self.subdomains.set(vals[4])
        finally:
            self._applying = False

    def mark_custom(self):
        if self._applying:
            return
        cur = (self.to_int(self.max_pages, -1), self.to_int(self.max_mb, -1), self.to_int(self.max_minutes, -1),
               self.path_scope.get(), self.subdomains.get())
        for name in PRESETS:
            if cur == self.preset_values(name):
                self.preset.set(name)
                return
        self.preset.set(CUSTOM)

    def apply_detail(self):
        flags = DETAILS.get(self.detail.get())
        if flags is None:
            return
        self._applying = True
        try:
            for n in FLAG_NAMES:
                self.dv[n].set(bool(flags.get(n, False)))
            self.max_file_mb.set(str(flags.get("max_file_mb", 0)))
        finally:
            self._applying = False
        self.refresh_estimate()

    def detail_state(self):
        return tuple(self.dv[n].get() for n in FLAG_NAMES) + (self.to_int(self.max_file_mb, -1),)

    def on_detail_edit(self):
        if self._applying:
            return
        cur = self.detail_state()
        for name, flags in DETAILS.items():
            if cur == tuple(bool(flags.get(n, False)) for n in FLAG_NAMES) + (flags.get("max_file_mb", 0),):
                self.detail.set(name)
                return
        self.detail.set(CUSTOM_DETAIL)

    def level_key(self):
        d = {n: self.dv[n].get() for n in FLAG_NAMES}
        if d["skip_images"]:
            if d["skip_fonts"] or d["no_scripts"] or self.to_int(self.max_file_mb, 0):
                return "min"
            return "nopic"
        if d["shrink_images"] or d["skip_media"]:
            return "lite"
        return "full"

    def refresh_estimate(self):
        r = self.check_res
        if r is not None and r.pages and r.level != "bad":
            self.est_msg.set(f"Estimated size at this detail level: {estimate_size_text(r.pages, self.level_key())}.")
        else:
            self.est_msg.set("")

    def toggle_adv(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.opts_frame.pack(fill="x", padx=10, pady=4, after=self.est_label)
            self.adv_btn.configure(text="Advanced ▾")
        else:
            self.opts_frame.pack_forget()
            self.adv_btn.configure(text="Advanced ▸")

    def load_settings(self):
        s = appsettings.load()
        self._applying = True
        try:
            if s.get("folder"):
                self.folder.set(s["folder"])
            if s.get("engine") in ("zimit", "builtin"):
                self.engine.set(s["engine"])
            self.workers.set(str(s.get("workers", self.workers.get())))
            for key, var in (("max_pages", self.max_pages), ("max_mb", self.max_mb), ("max_minutes", self.max_minutes),
                             ("max_file_mb", self.max_file_mb), ("depth", self.depth)):
                if key in s:
                    var.set(str(s[key]))
            self.path_scope.set(bool(s.get("path_scope", False)))
            self.subdomains.set(bool(s.get("subdomains", False)))
            for n in FLAG_NAMES:
                self.dv[n].set(bool(s.get(n, False)))
        finally:
            self._applying = False
        if not s:
            self.apply_preset()
            self.apply_detail()
        if s.get("preset") in PRESETS or s.get("preset") == CUSTOM:
            self.preset.set(s["preset"])
        if s.get("detail") in DETAILS or s.get("detail") == CUSTOM_DETAIL:
            self.detail.set(s["detail"])
        self.dark = s.get("theme", "dark") != "light"
        self.books.set_settings(s)
        if s.get("advanced") and not self.adv_open:
            self.toggle_adv()

    def save_settings(self):
        data = {
            "folder": self.folder.get().strip(), "engine": self.engine.get(), "preset": self.preset.get(),
            "workers": self.to_int(self.workers, 4), "max_pages": self.to_int(self.max_pages, 20000),
            "max_mb": self.to_int(self.max_mb, 20480), "max_minutes": self.to_int(self.max_minutes, 0),
            "path_scope": self.path_scope.get(), "subdomains": self.subdomains.get(), "advanced": self.adv_open,
            "theme": "dark" if self.dark else "light", "detail": self.detail.get(),
            "max_file_mb": self.to_int(self.max_file_mb, 0), "depth": self.to_int(self.depth, 0),
        }
        for n in FLAG_NAMES:
            data[n] = self.dv[n].get()
        data.update(self.books.get_settings())
        appsettings.save(data)

    # ------------------------------------------------------ open the result
    def open_kiwix(self):
        if not self.out_path or not os.path.exists(self.out_path):
            return
        if not open_in_kiwix(self.out_path):
            messagebox.showinfo(APP_NAME, "Kiwix doesn't seem to be installed.\n\nGet it free at https://kiwix.org, "
                                          "or use \"Preview in browser\".")

    def open_preview(self):
        if not self.out_path or not os.path.exists(self.out_path):
            return
        try:
            preview_in_browser(self.out_path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Couldn't preview this file here:\n{e}\n\nTry \"Open in Kiwix\" instead.")

    # ---------------------------------------------------------- site check
    def normalized_url(self):
        url = self.url.get().strip()
        if url and "://" not in url:
            url = "https://" + url
        p = urlsplit(url)
        if p.scheme in ("http", "https") and p.hostname and ("." in p.hostname or p.hostname == "localhost"):
            return url
        return None

    def set_icon(self, level, msg=""):
        sym, colname, _ = ICONS[level]
        self.icon_level, self.icon_msg = level, msg
        self.check_icon.configure(text=sym, fg=self.pal[colname])
        self.check_msg.set(msg)

    def schedule_check(self, delay_ms):
        if self._check_job:
            self.after_cancel(self._check_job)
            self._check_job = None
        url = self.normalized_url()
        if not url:
            self.check_res = self.check_key = None
            self.set_icon("idle", "")
            self.refresh_estimate()
            return
        key = (url, self.engine.get())
        if key == self.check_key and self.check_res is not None:
            return
        self._check_job = self.after(delay_ms, lambda: self.run_check(key))

    def run_check(self, key):
        self._check_job = None
        self.check_key, self.check_res = key, None
        self.set_icon("checking", "Checking the site for logins and blocking...")
        self.refresh_estimate()
        url, engine = key
        threading.Thread(target=lambda: self.q.put(("check", (key, check_site(url, engine)))), daemon=True).start()

    def on_check_result(self, key, res):
        if key != self.check_key:
            return  # URL or engine changed while checking
        self.check_res = res
        msg = res.summary
        if res.pages and res.level != "bad":
            msg += f" Sitemap lists about {res.pages:,} pages."
        self.set_icon(res.level, msg + "  (click the icon for details)")
        self.refresh_estimate()
        if self.preset.get() == WHOLE and not self.running:
            self.apply_preset()   # automatic limits follow the sitemap size

    def show_check_details(self):
        r = self.check_res
        if r is None:
            self.schedule_check(0)
            return
        title = ICONS[r.level][2]
        body = r.summary + ("\n\n" + "\n".join("- " + d for d in r.details) if r.details else "")
        body += ("\n\nThis is a quick check of the first page. A green check means no obvious blockers were found, "
                 "not a guarantee.")
        messagebox.showinfo(f"{APP_NAME} - {title}", body)

    # ------------------------------------------------------------ helpers
    def check_docker(self):
        state, _ = docker_status()
        self.q.put(("docker", state))

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder.get() or None, title="Choose where to save the ZIM file")
        if d:
            self.folder.set(os.path.normpath(d))

    def open_folder(self):
        d = os.path.dirname(self.out_path) if self.out_path else self.folder.get()
        if d and os.path.isdir(d):
            if os.name == "nt":
                if self.out_path and os.path.exists(self.out_path):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(self.out_path)])
                else:
                    os.startfile(d)
            else:
                subprocess.Popen(["xdg-open", d])

    def to_int(self, var, default):
        try:
            return max(0, int(var.get().strip()))
        except ValueError:
            return default

    def add_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- actions
    def start(self):
        if self.running:
            return
        url = self.url.get().strip()
        if url and "://" not in url:
            url = "https://" + url
            self.url.set(url)
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname or ("." not in p.hostname and p.hostname != "localhost"):
            messagebox.showerror(APP_NAME, "Please enter a full website address, like https://example.com")
            return
        res = self.check_res if self.check_key == (url, self.engine.get()) else None
        if res is not None and res.level == "bad":
            if not messagebox.askyesno(
                    APP_NAME, f"The site check says this will probably fail:\n\n{res.summary}\n\nConvert anyway?",
                    icon="warning"):
                return
        if self.engine.get() == "zimit" and self.dv["shrink_images"].get():
            ans = messagebox.askyesnocancel(
                APP_NAME, "Shrinking pictures (Lite) only works with the Built-in engine.\n\n"
                          "Yes = switch to the Built-in engine (simple sites only)\n"
                          "No = keep Zimit and save pictures at full size\nCancel = go back")
            if ans is None:
                return
            if ans:
                self.engine.set("builtin")
        folder = self.folder.get().strip()
        if not folder:
            self.browse()
            folder = self.folder.get().strip()
            if not folder:
                return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Can't use that folder:\n{e}")
            return
        name = self.fname.get().strip() or default_filename(url)
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        if not name.lower().endswith(".zim"):
            name += ".zim"
        out = os.path.join(folder, name)
        if os.path.exists(out) and not messagebox.askyesno(APP_NAME, f"{name} already exists. Replace it?"):
            return
        try:
            free_mb = shutil.disk_usage(folder).free / (1024 * 1024)
            per_page = MB_PER_PAGE[self.level_key()][0]
            need_mb = (res.pages * per_page * 2) if (res is not None and res.pages) else 0  # temp files + ZIM
            if free_mb < max(1024, need_mb) and not messagebox.askyesno(
                    APP_NAME, f"Only {free_mb / 1024:.1f} GB is free on that drive"
                              + (f", and this site may need about {need_mb / 1024:.1f} GB or more" if need_mb > 1024 else "")
                              + ".\n\nContinue anyway?", icon="warning"):
                return
        except OSError:
            pass
        if self.preset.get() == WHOLE:
            self.apply_preset()
        self.save_settings()

        self.run_notes = []
        if self.engine.get() == "zimit":
            if self.dv["shrink_images"].get():
                self.run_notes.append("Zimit can't shrink pictures; they were saved at full size.")
            if self.dv["no_scripts"].get():
                self.run_notes.append("Zimit can't remove scripts; they were kept.")
            if self.to_int(self.max_file_mb, 0):
                self.run_notes.append("Zimit can't skip big files; the size cap was ignored.")
        opts = Options(
            max_pages=self.to_int(self.max_pages, 20000),
            max_mb=self.to_int(self.max_mb, 0),
            max_minutes=self.to_int(self.max_minutes, 0),
            workers=min(16, max(1, self.to_int(self.workers, 4))),
            path_scope=self.path_scope.get(),
            subdomains=self.subdomains.get(),
            skip_images=self.dv["skip_images"].get(), skip_media=self.dv["skip_media"].get(),
            skip_fonts=self.dv["skip_fonts"].get(), skip_docs=self.dv["skip_docs"].get(),
            shrink_images=self.dv["shrink_images"].get(), no_scripts=self.dv["no_scripts"].get(),
            max_file_mb=self.to_int(self.max_file_mb, 0), depth=self.to_int(self.depth, 0),
        )
        self.out_path = out
        self.cancel = threading.Event()
        self.running = True
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.kiwix_btn.configure(state="disabled")
        self.preview_btn.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.row_n = 0
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.result.set("")
        self.result_label.configure(style="Result.TLabel")
        self.counts.set("")
        self.status.set("Starting...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        threading.Thread(target=self.work, args=(self.engine.get(), url, out, opts), daemon=True).start()

    def work(self, engine, url, out, opts):
        emit = lambda e: self.q.put(("event", e))  # noqa: E731
        try:
            if engine == "zimit":
                r = run_zimit(url, out, opts, emit, self.cancel)
                self.q.put(("done", (out, r["size"], r["pages"], None, r["failed"], r.get("limit_hit"), False, 0)))
            else:
                if opts.shrink_images:
                    try:
                        import PIL  # noqa: F401
                    except Exception:
                        emit({"type": "warn", "text": "Picture shrinking needs the Pillow module, which isn't "
                                                      "available here; pictures were saved at full size."})
                size, c = convert(url, out, opts, on_event=emit, cancel=self.cancel)
                self.q.put(("done", (out, size, c.pages, c.files, c.failed, c.limit_hit, c.depth_cut,
                                     c.skipped_count)))
        except Exception as e:
            self.q.put(("error", str(e)))

    def on_cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")
        self.cancel_btn.configure(state="disabled")

    def pump(self):
        try:
            for _ in range(300):
                kind, val = self.q.get_nowait()
                if kind == "event":
                    self.on_event(val)
                elif kind == "done":
                    self.on_done(*val)
                elif kind == "error":
                    self.on_error(val)
                elif kind == "check":
                    self.on_check_result(*val)
                elif kind == "book":
                    self.books.on_event(val)
                elif kind == "bookcheck":
                    self.books.on_check_result(*val)
                elif kind == "docker":
                    self.docker_state = val
                    self.docker_msg.set({
                        "ready": "Docker Desktop: ready.",
                        "not_running": "Docker Desktop: installed but not running - start it before using Zimit.",
                        "missing": "Docker Desktop: not found - install it for Zimit, or use the Built-in engine.",
                    }[val])
                    self.draw_banner()
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def add_row(self, status, url, err=False):
        self.row_n += 1
        tags = ["err" if err else "saved"]
        if self.row_n % 2 == 0:
            tags.append("odd")
        row = self.tree.insert("", "end", values=(status, url), tags=tuple(tags))
        self.tree.see(row)

    def on_event(self, e):
        t = e.get("type")
        if t == "fetch":  # built-in engine
            if e["ok"]:
                if str(e.get("mime", "")).startswith(("text/html", "application/xhtml")):
                    self.add_row("Saved", e["url"])
            else:
                self.add_row(f"Failed ({e['error']})", e["url"], True)
                self.add_log(f"FAILED {e['url']}  ({e['error']})")
            txt = f"Pages: {e['pages']}    Files: {e['files']}    Size: {fmt_size(e['bytes'])}"
            if e.get("skipped"):
                txt += f"    Skipped: {e['skipped']}"
            self.counts.set(txt)
        elif t == "row":  # zimit
            self.add_row(e["status"], e["url"], e.get("error", False))
        elif t == "stats":
            self.counts.set(f"Crawled: {e['crawled']} of {e['total']}    Waiting: {e['pending']}    Failed: {e['failed']}")
            if e.get("total"):
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=max(1, e["total"]), value=e["crawled"])
        elif t == "log":
            self.add_log(e["text"])
        elif t == "warn":
            self.run_notes.append(e["text"])
            self.add_log("NOTE: " + e["text"])
        elif t == "phase":
            self.status.set(e["text"])
        elif t == "build":
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(1, e["total"]), value=e["done"])
            self.status.set(f"Building ZIM... {e['done']}/{e['total']}")

    def finish_ui(self):
        self.running = False
        self.progress.stop()
        self.convert_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def on_done(self, out, size, pages, files, failed, limit=None, depth_cut=False, skipped=0):
        self.finish_ui()
        self.progress.configure(mode="determinate", maximum=1, value=1)
        self.status.set("Done")
        detail = f"{fmt_size(size)}, {pages} pages" + (f", {files} files" if files is not None else "")
        if failed:
            detail += f", {failed} failed"
        if skipped:
            detail += f", {skipped} left out"
        notes = []
        if limit in LIMIT_TEXT:
            notes.append(LIMIT_TEXT[limit])
        if depth_cut:
            notes.append("Some pages were more clicks away than the Depth setting allows, so they were left out.")
        notes += self.run_notes
        msg = f"Saved: {out}   ({detail})"
        if notes:
            msg += "\n" + "\n".join("! " + n for n in notes)
            self.result_label.configure(style="Warn.TLabel")
        self.result.set(msg)
        self.kiwix_btn.configure(state="normal")
        self.preview_btn.configure(state="normal")
        ask = f"ZIM file created:\n{out}\n\n"
        if notes:
            ask += "\n".join(notes) + "\n\n"
        if messagebox.askyesno(APP_NAME, ask + "Show it in its folder?"):
            self.open_folder()

    def on_error(self, text):
        self.finish_ui()
        self.progress.configure(mode="determinate", maximum=1, value=0)
        if text == "Cancelled":
            self.status.set("Cancelled")
        else:
            self.status.set("Error")
            self.add_log("ERROR: " + text)
            messagebox.showerror(APP_NAME, text)

    def on_close(self):
        if self.running or self.books.running:
            if not messagebox.askyesno(APP_NAME, "Something is still running. Cancel it and quit?"):
                return
            self.cancel.set()
            self.books.cancel.set()
        self.save_settings()
        self.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
