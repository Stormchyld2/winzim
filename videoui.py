"""The Videos tab: get videos with yt-dlp (by address or by words) and turn a folder of videos into a ZIM.

All the logic is in videos.py and videozim.py; this file is the window.
"""
import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import prefs
import videos
import videozim
from books import human_size

CHECK, BLANK = "☑", "☐"
QUALITY_CHOICES = list(videos.QUALITIES)
SEARCH_CHOICES = ["10", "20", "30", "50"]


class VideoTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app, self.q = app, app.q
        self.mode = tk.StringVar(value="get")
        # get videos
        self.target = tk.StringVar()
        self.hint = tk.StringVar(value="Paste a video, playlist or channel address, or type words to search for.")
        self.quality = tk.StringVar(value=videos.DEFAULT_QUALITY)
        self.size_note = tk.StringVar()
        self.folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Documents", "Videos"))
        self.subs = tk.BooleanVar(value=prefs.get("video_subs"))
        self.search_n = tk.StringVar(value=str(prefs.get("video_search_count")))
        self.cc = tk.BooleanVar(value=False)
        self.private = tk.BooleanVar(value=False)
        self._loaded = {}              # the saved settings as loaded, written back unchanged while private mode is on
        self.filter_text = tk.StringVar()
        self.total_note = tk.StringVar()
        self.entries = []              # [{id,title,url,channel,duration,pick,status}]
        # videos to zim
        self.zim_folder = tk.StringVar(value=self.folder.get())
        self.zim_name = tk.StringVar(value="Recon Videos")
        self.zim_out = tk.StringVar()
        self.convert = tk.BooleanVar(value=True)
        self.shrink = tk.StringVar(value="Keep as downloaded")
        self.zim_total = tk.StringVar()
        self.zim_items = []            # [{id,title,duration,size,ext,pick,tags}]
        self.status = tk.StringVar(value="Ready.")
        self.helpers = tk.StringVar()
        self.running = False
        self.cancel = threading.Event()
        self.last_zim = None
        self._build()
        self.set_mode("get")
        self.refresh_helpers()
        self.update_totals()
        self.quality.trace_add("write", lambda *_: self.update_totals())
        self.filter_text.trace_add("write", lambda *_: self.render_get())

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 10, "pady": 4}
        mb = ttk.Frame(self)
        mb.pack(fill="x", **pad)
        self.btn_get = ttk.Button(mb, text="Get videos", command=lambda: self.set_mode("get"))
        self.btn_get.pack(side="left")
        self.btn_zim = ttk.Button(mb, text="Videos to ZIM", command=lambda: self.set_mode("zim"))
        self.btn_zim.pack(side="left", padx=4)
        ttk.Checkbutton(mb, text="Private mode (leave no history)", variable=self.private, command=self.toggle_private).pack(side="right")
        self.slot = ttk.Frame(self)
        self.slot.pack(fill="both", expand=True)

        # ---------------- get videos
        g = self.get_frame = ttk.Frame(self.slot)
        top = ttk.Frame(g)
        top.pack(fill="x", padx=10, pady=4)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Address or words:").grid(row=0, column=0, sticky="w")
        e = ttk.Entry(top, textvariable=self.target)
        e.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        e.bind("<Return>", lambda _e: self.find())
        self.find_btn = ttk.Button(top, text="Find videos", style="Primary.TButton", command=self.find)
        self.find_btn.grid(row=0, column=2, padx=(6, 0))
        ttk.Label(top, textvariable=self.hint, style="Muted.TLabel", wraplength=760, justify="left").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(6, 0))
        opt = ttk.Frame(g)
        opt.pack(fill="x", padx=10, pady=4)
        ttk.Label(opt, text="Quality:").pack(side="left")
        ttk.Combobox(opt, textvariable=self.quality, values=QUALITY_CHOICES, state="readonly", width=20).pack(side="left", padx=(6, 0))
        ttk.Label(opt, textvariable=self.size_note, style="Muted.TLabel").pack(side="left", padx=8)
        ttk.Checkbutton(opt, text="Subtitles", variable=self.subs).pack(side="left", padx=(16, 0))
        ttk.Label(opt, text="Search results:").pack(side="left", padx=(24, 4))
        ttk.Combobox(opt, textvariable=self.search_n, values=SEARCH_CHOICES, state="readonly", width=4).pack(side="left")
        ttk.Checkbutton(opt, text="Only Creative Commons", variable=self.cc).pack(side="left", padx=(24, 0))
        fo = ttk.Frame(g)
        fo.pack(fill="x", padx=10, pady=4)
        fo.columnconfigure(1, weight=1)
        ttk.Label(fo, text="Save to folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(fo, textvariable=self.folder).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Button(fo, text="Browse...", command=lambda: self.browse(self.folder, "Choose where to save the videos")).grid(row=0, column=2, padx=(6, 0))
        sh = ttk.Frame(g)
        sh.pack(fill="x", padx=10, pady=4)
        ttk.Label(sh, text="Filter:").pack(side="left")
        ttk.Entry(sh, textvariable=self.filter_text, width=26).pack(side="left", padx=6)
        ttk.Button(sh, text="Tick all", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(sh, text="Tick none", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        ttk.Label(sh, textvariable=self.total_note, style="Amber.TLabel").pack(side="right")
        box = ttk.Frame(g)
        box.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree = ttk.Treeview(box, columns=("pick", "title", "channel", "length", "size", "status"), show="headings",
                                 height=9, selectmode="browse")
        for c, text, w, a in (("pick", "", 34, "center"), ("title", "Title", 330, "w"), ("channel", "Channel", 150, "w"),
                              ("length", "Length", 70, "e"), ("size", "Est. size", 80, "e"), ("status", "Status", 150, "w")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "title"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", lambda ev: self.on_click(ev, self.tree, self.entries, self.update_totals))
        gb = ttk.Frame(g)
        gb.pack(fill="x", padx=10, pady=4)
        self.cancel_btn = ttk.Button(gb, text="Cancel", style="Danger.TButton", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.dl_btn = ttk.Button(gb, text="Download ticked", style="Primary.TButton", command=self.download)
        self.dl_btn.pack(side="left", padx=6)
        ttk.Button(gb, text="Add to queue", command=self.queue_get).pack(side="left")
        ttk.Button(gb, text="Open folder", command=lambda: self.open_folder(self.folder.get())).pack(side="left", padx=6)

        # ---------------- videos to zim
        z = self.zim_frame = ttk.Frame(self.slot)
        zt = ttk.Frame(z)
        zt.pack(fill="x", padx=10, pady=4)
        zt.columnconfigure(1, weight=1)
        ttk.Label(zt, text="Folder of videos:").grid(row=0, column=0, sticky="w")
        ttk.Entry(zt, textvariable=self.zim_folder).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Button(zt, text="Browse...", command=self.browse_zim_folder).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(zt, text="ZIM name:").grid(row=1, column=0, sticky="w")
        ttk.Entry(zt, textvariable=self.zim_name).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Label(zt, text="Save ZIM to:").grid(row=2, column=0, sticky="w")
        ttk.Entry(zt, textvariable=self.zim_out).grid(row=2, column=1, sticky="ew", padx=(6, 0), pady=3)
        ttk.Button(zt, text="Browse...", command=self.browse_zim_out).grid(row=2, column=2, padx=(6, 0))
        zo = ttk.Frame(z)
        zo.pack(fill="x", padx=10, pady=4)
        ttk.Checkbutton(zo, text="Convert other video types to WebM (needs ffmpeg)", variable=self.convert).pack(side="left")
        ttk.Label(zo, text="Shrink to:").pack(side="left", padx=(16, 4))
        ttk.Combobox(zo, textvariable=self.shrink, values=list(videozim.SHRINK_CHOICES), state="readonly", width=18).pack(side="left")
        ttk.Button(zo, text="Tick all", command=lambda: self.set_all(True)).pack(side="left", padx=(24, 0))
        ttk.Button(zo, text="Tick none", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        ttk.Label(zo, textvariable=self.zim_total, style="Amber.TLabel").pack(side="right")
        ttk.Label(z, style="Counts.TLabel", wraplength=860, justify="left",
                  text="What you get: one ZIM with a home page where you can search, pick tags (a video must have every tag you pick), "
                       "filter by channel or length, and group by channel; a page for each video with a player, subtitles, chapters, "
                       "description and tags; and the videos themselves. It opens in Kiwix like your books ZIM. Videos go in as WebM."
                  ).pack(anchor="w", padx=10, pady=4)
        zbox = ttk.Frame(z)
        zbox.pack(fill="both", expand=True, padx=10, pady=4)
        self.ztree = ttk.Treeview(zbox, columns=("pick", "title", "tags", "length", "size", "format"), show="headings", height=9,
                                  selectmode="browse")
        for c, text, w, a in (("pick", "", 34, "center"), ("title", "Video", 320, "w"), ("tags", "Tags", 60, "center"),
                              ("length", "Length", 70, "e"), ("size", "Size", 80, "e"), ("format", "Format", 190, "w")):
            self.ztree.heading(c, text=text)
            self.ztree.column(c, width=w, anchor=a, stretch=(c == "title"))
        zsb = ttk.Scrollbar(zbox, orient="vertical", command=self.ztree.yview)
        self.ztree.configure(yscrollcommand=zsb.set)
        self.ztree.pack(side="left", fill="both", expand=True)
        zsb.pack(side="right", fill="y")
        self.ztree.bind("<Button-1>", lambda ev: self.on_click(ev, self.ztree, self.zim_items, self.update_zim_total))
        zb = ttk.Frame(z)
        zb.pack(fill="x", padx=10, pady=4)
        self.zcancel_btn = ttk.Button(zb, text="Cancel", style="Danger.TButton", command=self.on_cancel, state="disabled")
        self.zcancel_btn.pack(side="left")
        self.build_btn = ttk.Button(zb, text="Build ZIM", style="Primary.TButton", command=self.make_zim)
        self.build_btn.pack(side="left", padx=6)
        ttk.Button(zb, text="Add to queue", command=self.queue_zim).pack(side="left")
        self.test_btn = ttk.Button(zb, text="Test ZIM (3 sample videos)", command=self.test_zim)
        self.test_btn.pack(side="left", padx=6)
        ttk.Button(zb, text="Preview a ZIM", command=self.preview_zim).pack(side="left")

        # ---------------- shared bottom
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=1, value=0)
        self.progress.pack(fill="x", padx=10, pady=4)
        bt = ttk.Frame(self)
        bt.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(bt, textvariable=self.status, style="Amber.TLabel").pack(side="left")
        ttk.Button(bt, text="Update yt-dlp", command=self.update_ytdlp).pack(side="right")
        ttk.Label(bt, textvariable=self.helpers, style="Muted.TLabel").pack(side="right", padx=10)

    # ------------------------------------------------------------ small things
    def apply_theme(self, p):
        for t in (self.tree, self.ztree):
            t.tag_configure("done", foreground=p["blue"])
            t.tag_configure("active", foreground=p["amber"])
            t.tag_configure("bad", foreground=p["red"])
            t.tag_configure("off", foreground=p["dim"])
            t.tag_configure("good", foreground=p["green"])
            t.tag_configure("odd", background=p["alt"])

    def set_mode(self, m):
        self.mode.set(m)
        self.get_frame.pack_forget()
        self.zim_frame.pack_forget()
        (self.get_frame if m == "get" else self.zim_frame).pack(fill="both", expand=True)
        self.btn_get.configure(style="SegOn.TButton" if m == "get" else "Seg.TButton")
        self.btn_zim.configure(style="SegOn.TButton" if m == "zim" else "Seg.TButton")
        if m == "zim" and not self.zim_items:
            self.load_zim_folder()

    def get_settings(self):
        if self.private.get():
            return dict(self._loaded)
        return {"video_mode": self.mode.get(), "video_get_folder": self.folder.get().strip(), "video_quality": self.quality.get(),
                "video_zim_folder": self.zim_folder.get().strip(), "video_zim_out": self.zim_out.get().strip(),
                "video_zim_name": self.zim_name.get().strip(), "video_shrink": self.shrink.get(), "video_cc": self.cc.get()}

    def set_settings(self, s):
        self._loaded = {k: v for k, v in s.items() if k.startswith("video_")}
        if "video_cc" in s:
            self.cc.set(bool(s["video_cc"]))
        if s.get("video_get_folder"):
            self.folder.set(s["video_get_folder"])
        if s.get("video_quality") in videos.QUALITIES:
            self.quality.set(s["video_quality"])
        if s.get("video_zim_folder"):
            self.zim_folder.set(s["video_zim_folder"])
        if s.get("video_zim_out"):
            self.zim_out.set(s["video_zim_out"])
        if s.get("video_zim_name"):
            self.zim_name.set(s["video_zim_name"])
        if s.get("video_shrink") in videozim.SHRINK_CHOICES:
            self.shrink.set(s["video_shrink"])
        if s.get("video_mode") in ("get", "zim"):
            self.set_mode(s["video_mode"])

    def toggle_private(self):
        """Private mode: this run's videos jobs, folders and results are not written anywhere, and yt-dlp keeps no cache."""
        on = self.private.get()
        prefs.set("private_mode", on)
        if on:
            self.status.set("Private mode is on: nothing from this tab is saved for next time. Downloaded files still stay in your folder.")
        else:
            self.target.set("")
            self.entries = []
            self.zim_items = []
            self.render_get()
            self.ztree.delete(*self.ztree.get_children())
            self.hint.set("Private mode is off. The list and address were cleared.")
            self.status.set("Ready.")

    def refresh_helpers(self):
        bits = [f"{n} {'found' if videos.find_tool(k) else 'not found'}" for k, n in (("yt-dlp", "yt-dlp"), ("deno", "Deno"), ("ffmpeg", "ffmpeg"))]
        self.helpers.set("Helpers: " + ", ".join(bits) + ("" if videos.find_tool("yt-dlp") else "  (Settings > Videos > Install it for me)"))

    def browse(self, var, title):
        d = filedialog.askdirectory(initialdir=var.get() or None, title=title)
        if d:
            var.set(os.path.normpath(d))

    def browse_zim_folder(self):
        self.browse(self.zim_folder, "Choose the folder that holds the downloaded videos")
        self.load_zim_folder()

    def browse_zim_out(self):
        p = filedialog.asksaveasfilename(title="Save the ZIM file as", defaultextension=".zim", initialfile="recon-videos.zim",
                                         initialdir=self.zim_folder.get() or None, filetypes=[("ZIM file", "*.zim")])
        if p:
            self.zim_out.set(os.path.normpath(p))

    def open_folder(self, d):
        d = (d or "").strip()
        if d and os.path.isdir(d):
            if os.name == "nt":
                os.startfile(d)
            else:
                subprocess.Popen(["xdg-open", d])

    def emit(self, e):
        self.q.put(("video", e))

    def busy(self, on, text=""):
        self.running = on
        for b in (self.cancel_btn, self.zcancel_btn):
            b.configure(state="normal" if on else "disabled")
        for b in (self.find_btn, self.dl_btn, self.build_btn, self.test_btn):
            b.configure(state="disabled" if on else "normal")
        if text:
            self.status.set(text)
        if not on:
            self.progress.configure(value=0)

    def on_cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")

    def on_click(self, ev, tree, items, after):
        """Click the box in the first column to tick or untick a row."""
        if tree.identify_region(ev.x, ev.y) != "cell" or tree.identify_column(ev.x) != "#1":
            return
        iid = tree.identify_row(ev.y)
        for it in items:
            if it["id"] == iid:
                it["pick"] = not it["pick"]
                tree.set(iid, "pick", CHECK if it["pick"] else BLANK)
        after()

    def set_all(self, on):
        items, tree, after = ((self.entries, self.tree, self.update_totals) if self.mode.get() == "get"
                              else (self.zim_items, self.ztree, self.update_zim_total))
        for it in items:
            if tree.exists(it["id"]):                # only the rows that are showing
                it["pick"] = on
                tree.set(it["id"], "pick", CHECK if on else BLANK)
        after()

    # ------------------------------------------------------------ get videos: find
    def plan(self):
        """What the input box means, or None with a message shown."""
        try:
            n = int(self.search_n.get())
        except ValueError:
            n = prefs.get("video_search_count")
        try:
            return videos.classify(self.target.get(), n, self.cc.get())
        except videos.VideoError as e:
            self.hint.set(str(e))
            return None

    def describe_plan(self, plan):
        if plan["mode"] == "search":
            return f"Searching for \"{plan['query']}\" ({plan['count']} results" + (", Creative Commons only" if plan.get("cc") else "") + ")..."
        kinds = [videos.plain_url_kind(u) for u in plan["urls"]]
        if len(kinds) == 1:
            return {"video": "Looking at one video...", "playlist": "Looking at a playlist...", "channel": "Looking at a channel..."}[kinds[0]]
        return f"Looking at {len(kinds)} addresses..."

    def find(self):
        if self.running:
            return
        plan = self.plan()
        if not plan:
            return
        if not videos.find_tool("yt-dlp"):
            messagebox.showinfo("Videos", "yt-dlp is needed to get videos. Open Settings > Videos and use Install it for me, then try again.")
            return
        self.cancel = threading.Event()
        self.entries = []
        self.render_get()
        self.hint.set(self.describe_plan(plan))
        self.busy(True, "Finding videos...")
        ffmpeg, deno = videos.find_tool("ffmpeg"), videos.find_tool("deno")

        def work():
            try:
                es = videos.list_entries(plan, self.cancel, None, ffmpeg, deno, on_line=lambda e: self.emit({"type": "vfound", "entry": e}))
                self.emit({"type": "vlist_done", "count": len(es), "cancelled": self.cancel.is_set()})
            except videos.VideoError as e:
                self.emit({"type": "vlist_error", "text": str(e)})
            except Exception as e:
                self.emit({"type": "vlist_error", "text": f"Could not look that up: {e}"})
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ get videos: the table
    def visible_entries(self):
        words = self.filter_text.get().lower().split()
        return [e for e in self.entries if all(w in (e["title"] + " " + e["channel"]).lower() for w in words)]

    def _row(self, e):
        per = videos.MB_PER_MIN.get(self.quality.get(), 25)
        est = int((e["duration"] or 600) / 60 * per * 1_000_000)
        return (CHECK if e["pick"] else BLANK, e["title"], e["channel"], videos.fmt_duration(e["duration"]) or "?", human_size(est), e["status"])

    def _tag(self, e):
        s = e["status"]
        return ("done",) if s.startswith("Saved") or s.startswith("Already") else ("active",) if "%" in s or s.startswith("Start") else \
            ("bad",) if s.startswith("Failed") else ()

    def render_get(self):
        self.tree.delete(*self.tree.get_children())
        for e in self.visible_entries():
            self.tree.insert("", "end", iid=e["id"], values=self._row(e), tags=self._tag(e))
        self.update_totals()

    def update_totals(self):
        picked = [e for e in self.entries if e["pick"]]
        self.size_note.set("about " + videos.QUALITIES.get(self.quality.get(), ("", ""))[1].replace("about ", ""))
        self.total_note.set(f"Estimated total: {human_size(videos.estimate_bytes(picked, self.quality.get()))} · {len(picked)} ticked of {len(self.entries)}"
                            if self.entries else "")

    def _set_status(self, vid, text):
        for e in self.entries:
            if e["id"] == vid:
                e["status"] = text
                if self.tree.exists(vid):
                    self.tree.set(vid, "status", text)
                    self.tree.item(vid, tags=self._tag(e))

    # ------------------------------------------------------------ get videos: download
    def picked(self):
        return [{k: e[k] for k in ("id", "title", "url", "channel", "duration")} for e in self.entries if e["pick"]]

    def check_ready(self):
        folder = self.folder.get().strip()
        if not self.picked():
            messagebox.showinfo("Videos", "Tick at least one video first (click the box at the start of its row).")
            return None
        if not folder:
            self.browse(self.folder, "Choose where to save the videos")
            folder = self.folder.get().strip()
        return folder or None

    def download(self):
        if self.running:
            return
        folder = self.check_ready()
        if not folder:
            return
        picks = self.picked()
        ytdlp = videos.find_tool("yt-dlp")
        if not ytdlp:
            messagebox.showinfo("Videos", "yt-dlp is needed to get videos. Open Settings > Videos and use Install it for me, then try again.")
            return
        quality, subs, cc = self.quality.get(), self.subs.get(), self.cc.get()
        ffmpeg, deno = videos.find_tool("ffmpeg"), videos.find_tool("deno")
        self.cancel = threading.Event()
        self.busy(True, "Downloading...")
        self.progress.configure(maximum=len(picks), value=0)

        def on_state(i, n, e, text):
            self.emit({"type": "vprog", "id": e["id"], "i": i, "n": n, "text": text})

        def work():
            try:
                done, failed = videos.download_many(picks, folder, quality, self.cancel, ytdlp, ffmpeg, deno, subs, on_state, cc=cc)
                self.emit({"type": "vdl_done", "done": done, "failed": failed, "folder": folder, "cancelled": self.cancel.is_set()})
            except videos.VideoError as e:
                self.emit({"type": "vlist_error", "text": str(e)})
            except Exception as e:
                self.emit({"type": "vlist_error", "text": f"Could not download: {e}"})
        threading.Thread(target=work, daemon=True).start()

    def queue_get(self):
        folder = self.check_ready()
        if not folder:
            return
        job, waiting = self.app.queue_tab.add("videos_get", {"entries": self.picked(), "folder": folder, "quality": self.quality.get(),
                                                              "subs": self.subs.get(), "cc": self.cc.get()})
        self.status.set(f"Added to the queue ({waiting} waiting). Open the Queue tab to start it.")

    def update_ytdlp(self):
        exe = videos.find_tool("yt-dlp")
        if not exe:
            messagebox.showinfo("Videos", "yt-dlp is not installed yet. Open Settings > Videos and use Install it for me.")
            return
        self.status.set("Updating yt-dlp...")

        def work():
            try:
                self.emit({"type": "vstatus", "text": "yt-dlp: " + videos.update_ytdlp(exe)})
            except Exception as e:
                self.emit({"type": "vlist_error", "text": str(e)})
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ videos to zim
    def load_zim_folder(self):
        folder = self.zim_folder.get().strip()
        self.zim_items = []
        self.ztree.delete(*self.ztree.get_children())
        if folder and os.path.isdir(folder):
            ffmpeg = videos.find_tool("ffmpeg")
            for v in videos.scan_folder(folder):
                ext = os.path.splitext(v["file"])[1].lower()
                dur = v["info"].get("duration")
                self.zim_items.append({"id": v["id"], "title": v["title"], "pick": True, "ext": ext, "audio": videozim.is_audio(v),
                                       "tags": len(videozim.clean_tags(v["info"])), "duration": dur if isinstance(dur, (int, float)) else None,
                                       "size": os.path.getsize(v["file"]), "ffmpeg": bool(ffmpeg)})
        for it in self.zim_items:
            text, tag = self.format_note(it)
            self.ztree.insert("", "end", iid=it["id"], values=(CHECK, it["title"], it["tags"], videos.fmt_duration(it["duration"]) or "?",
                                                              human_size(it["size"]), text), tags=(tag,))
        self.update_zim_total()
        if folder and not self.zim_items:
            self.status.set("No downloaded videos in that folder yet. Use Get videos first.")

    def format_note(self, it):
        if it["audio"]:
            return "Sound only ✔", "good"
        if it["ext"] == ".webm":
            return "WebM ✔", "good"
        name = it["ext"][1:].upper()
        if self.convert.get() and it["ffmpeg"]:
            return f"{name}: will be converted", "active"
        return f"{name}: goes in as it is (may not play everywhere)", "off"

    def update_zim_total(self):
        picked = [i for i in self.zim_items if i["pick"]]
        self.zim_total.set(f"Total: {human_size(sum(i['size'] for i in picked))} · {len(picked)} of {len(self.zim_items)} videos" if self.zim_items else "")

    def zim_ready(self):
        """(folder, out, name, only_ids) or None, with a message when something is missing."""
        folder = self.zim_folder.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("Videos to ZIM", "Choose the folder that holds the downloaded videos first.")
            return None
        if not self.zim_items:
            self.load_zim_folder()
        picked = [i["id"] for i in self.zim_items if i["pick"]]
        if not picked:
            messagebox.showinfo("Videos to ZIM", "No videos are ticked. Tick the ones to put in the ZIM.")
            return None
        out = self.zim_out.get().strip()
        if not out:
            self.browse_zim_out()
            out = self.zim_out.get().strip()
            if not out:
                return None
        name = self.zim_name.get().strip() or "Recon Videos"
        only = None if len(picked) == len(self.zim_items) else picked
        return folder, os.path.normpath(out), name, only

    def zim_params(self, r):
        folder, out, name, only = r
        p = {"folder": folder, "out": out, "name": name, "max_file_mb": prefs.get("zim_max_file_mb"), "convert": self.convert.get(),
             "shrink": videozim.SHRINK_CHOICES.get(self.shrink.get())}
        if only:
            p["only_ids"] = only
        return p

    def queue_zim(self):
        r = self.zim_ready()
        if r:
            job, waiting = self.app.queue_tab.add("videos_zim", self.zim_params(r))
            self.status.set(f"Added to the queue ({waiting} waiting). Open the Queue tab to start it.")

    def make_zim(self):
        if self.running:
            return
        r = self.zim_ready()
        if not r:
            return
        choice = messagebox.askyesnocancel("Videos to ZIM", "Build it now?\n\nYes = build now\nNo = add it to the queue and build later\nCancel = go back")
        if choice is None:
            return
        if choice is False:
            job, waiting = self.app.queue_tab.add("videos_zim", self.zim_params(r))
            self.status.set(f"Added to the queue ({waiting} waiting). Open the Queue tab to start it.")
            return
        self._build_now(r)

    def _build_now(self, r):
        p = self.zim_params(r)
        ffmpeg = videos.find_tool("ffmpeg")
        self.cancel = threading.Event()
        self.busy(True, "Reading the videos...")

        def work():
            try:
                res = videozim.build_videos_zim(p["folder"], p["out"], p["name"], self.emit, self.cancel, p["max_file_mb"], ffmpeg,
                                                p["convert"], set(p["only_ids"]) if p.get("only_ids") else None, p["shrink"])
                self.emit({"type": "vzim_done", "path": p["out"], "res": res})
            except RuntimeError as e:
                self.emit({"type": "vzim_done", "path": None, "res": None} if str(e) == "Cancelled" else {"type": "vlist_error", "text": str(e)})
            except Exception as e:
                self.emit({"type": "vlist_error", "text": f"Could not build the ZIM: {e}"})
        threading.Thread(target=work, daemon=True).start()

    def test_zim(self):
        if self.running:
            return
        ffmpeg = videos.find_tool("ffmpeg")
        if not ffmpeg:
            messagebox.showinfo("Videos to ZIM", "ffmpeg is needed to make the sample videos. Open Settings > Videos and use Install it for me.")
            return
        import tempfile
        out = os.path.join(tempfile.gettempdir(), "recon-test-videos.zim")
        self.cancel = threading.Event()
        self.busy(True, "Making a small test ZIM...")

        def work():
            try:
                res = videozim.build_test_zim(out, ffmpeg)
                self.emit({"type": "vzim_done", "path": out, "res": res, "test": True})
            except Exception as e:
                self.emit({"type": "vlist_error", "text": str(e)})
        threading.Thread(target=work, daemon=True).start()

    def preview_zim(self, path=None):
        path = path if isinstance(path, str) else None
        if not path:
            path = filedialog.askopenfilename(title="Choose a ZIM file to preview", filetypes=[("ZIM files", "*.zim"), ("All files", "*.*")],
                                              initialdir=os.path.dirname(self.last_zim) if self.last_zim else (self.zim_folder.get() or None))
        if not path:
            return
        try:
            from preview import preview_in_browser
            preview_in_browser(path)
        except Exception as ex:
            messagebox.showerror("Videos to ZIM", f"Couldn't preview this file here:\n{ex}\n\nTry opening it in Kiwix instead.")

    # ------------------------------------------------------------ events from the worker threads
    def on_event(self, e):
        t = e.get("type")
        if t == "vfound":
            x = dict(e["entry"], pick=True, status="")
            self.entries.append(x)
            if len(self.entries) % 5 == 1 or len(self.entries) < 30:
                self.render_get()
            self.status.set(f"Found {len(self.entries)}...")
        elif t == "vlist_done":
            self.busy(False)
            self.render_get()
            self.hint.set(f"Found {e['count']} videos. Untick any you do not want, then Download ticked." if e["count"] else "Nothing was found.")
            self.status.set("Cancelled." if e["cancelled"] else "Ready.")
        elif t == "vlist_error":
            self.busy(False)
            self.hint.set(e["text"])
            self.status.set("Something went wrong.")
            messagebox.showwarning("Videos", e["text"])
        elif t == "vstatus":
            self.status.set(e["text"])
        elif t == "vprog":
            self._set_status(e["id"], "Starting" if e["text"] == "Starting" else e["text"] if e["text"] == "Already have it" else "Downloading " + e["text"])
            self.progress.configure(maximum=e["n"], value=e["i"] - 1)
            self.status.set(f"Video {e['i']} of {e['n']}")
        elif t == "vdl_done":
            self.busy(False)
            for vid in e["done"]:
                self._set_status(vid, "Saved")
            for vid, why in e["failed"]:
                self._set_status(vid, "Failed: " + why[:40])
            self.progress.configure(value=self.progress["maximum"] if not e["cancelled"] else 0)
            msg = "Cancelled." if e["cancelled"] else f"Saved {len(e['done'])} video{'s' if len(e['done']) != 1 else ''}" + (f", {len(e['failed'])} failed." if e["failed"] else ".")
            self.status.set(msg)
            self.zim_folder.set(e["folder"])
            self.zim_items = []
            if e["failed"]:
                messagebox.showwarning("Videos", msg + "\n\n" + "\n".join(f"  {vid}: {why}" for vid, why in e["failed"][:6]))
            if prefs.get("open_when_done") and e["done"]:
                self.open_folder(e["folder"])
        elif t == "phase":
            self.status.set(e["text"])
        elif t == "build":
            self.progress.configure(maximum=e["total"], value=e["done"])
        elif t == "vzim_done":
            self.busy(False)
            if not e["path"]:
                self.status.set("Cancelled.")
                return
            self._zim_done(e)

    def _zim_done(self, e):
        r = e["res"]
        self.last_zim = e["path"]
        self.status.set(f"ZIM saved: {r['videos']} videos, {human_size(r['size'])}.")
        msg = (f"Made {os.path.basename(e['path'])} ({human_size(r['size'])}).\n\n{r['videos']} videos, {r['tags']} tags"
               + (f", {r['converted']} converted to WebM" if r["converted"] else "") + ".")
        if r["notes"]:
            msg += "\n\nNotes:\n" + "\n".join(f"  {n[:40]}: {why}" for n, why in r["notes"][:6])
        if r["skipped"]:
            msg += "\n\nLeft out:\n" + "\n".join(f"  {n[:40]}: {why}" for n, why in r["skipped"][:6])
        messagebox.showinfo("Videos to ZIM", msg)
        if messagebox.askyesno("Videos to ZIM", "Preview it in your web browser now?" + (
                "\n\nPlay a video and try the tags. If the video does not play, tell us which program you use to open ZIMs." if e.get("test") else "")):
            self.preview_zim(e["path"])
        elif prefs.get("open_when_done"):
            self.open_folder(os.path.dirname(e["path"]))
