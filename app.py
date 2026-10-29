"""WinZim - turn any website into a ZIM file.

Engines:
  * Zimit (Docker Desktop)  - the official openZIM crawler, real browser, handles JavaScript sites.
  * Built-in (no Docker)    - simple pure-Python crawler for ordinary static sites.
"""
import datetime
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from urllib.parse import urlsplit

from crawler import Options, convert
from sitecheck import check_site
from zimit_engine import docker_status, run_zimit

ICONS = {  # level -> (symbol, colour, tooltip title)
    "idle": ("?", "#888888", "Not checked yet"),
    "checking": ("...", "#888888", "Checking the site..."),
    "ok": ("✔", "#1a9a3a", "Go ahead"),
    "warn": ("!", "#d98a00", "Proceed with caution"),
    "bad": ("✖", "#c62828", "Likely to fail"),
}

APP_NAME = "WinZim"


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
        self.title(f"{APP_NAME} - Website to ZIM")
        self.geometry("860x740")
        self.minsize(760, 620)
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.out_path = None

        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Website URL:").grid(row=0, column=0, sticky="w")
        self.url = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew", pady=3, padx=(6, 0))
        e.bind("<Return>", lambda _e: self.start())
        e.bind("<FocusOut>", lambda _e: self.schedule_check(0))
        e.focus_set()
        self.convert_btn = ttk.Button(top, text="Convert", command=self.start)
        self.convert_btn.grid(row=0, column=2, padx=(6, 0))
        # site check indicator: fixed spot right of Convert
        self.check_icon = tk.Label(top, text="?", width=3, font=("Segoe UI", 18, "bold"), fg="#888888", cursor="hand2")
        self.check_icon.grid(row=0, column=3, padx=(6, 0))
        self.check_icon.bind("<Button-1>", lambda _e: self.show_check_details())
        self.check_msg = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.check_msg, foreground="gray", wraplength=640, justify="left"
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
        ttk.Label(top, text="blank = automatic", foreground="gray").grid(row=2, column=2, padx=(6, 0))

        eng = ttk.LabelFrame(self, text="Engine")
        eng.pack(fill="x", **pad)
        self.engine = tk.StringVar(value="zimit")
        ttk.Radiobutton(eng, text="Zimit (needs Docker Desktop) - best quality, handles JavaScript sites",
                        variable=self.engine, value="zimit").pack(anchor="w", padx=8, pady=(4, 0))
        ttk.Radiobutton(eng, text="Built-in (no Docker) - simple websites only",
                        variable=self.engine, value="builtin").pack(anchor="w", padx=8)
        self.engine.trace_add("write", lambda *_: self.schedule_check(200))
        self.docker_msg = tk.StringVar(value="Checking Docker Desktop...")
        ttk.Label(eng, textvariable=self.docker_msg, foreground="gray").pack(anchor="w", padx=28, pady=(0, 4))

        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)
        self.max_pages = tk.StringVar(value="1000")
        self.max_mb = tk.StringVar(value="500")
        self.max_minutes = tk.StringVar(value="0")
        self.workers = tk.StringVar(value="4")
        self.path_scope = tk.BooleanVar(value=False)
        self.subdomains = tk.BooleanVar(value=False)
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
                          "(and Zimit uses more memory).", foreground="gray"
                  ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(4, 0))
        ttk.Checkbutton(r, text="Only pages inside this URL's folder",
                        variable=self.path_scope).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(r, text="Include subdomains",
                        variable=self.subdomains).grid(row=2, column=4, columnspan=4, sticky="w", pady=(6, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="left", padx=6)
        self.status = tk.StringVar(value="Ready. Enter a URL and click Convert.")
        ttk.Label(bar, textvariable=self.status).pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", **pad)
        self.counts = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.counts, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10)

        nb = ttk.Notebook(self)
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
        self.tree.tag_configure("err", foreground="#b00020")
        logf = ttk.Frame(nb)
        nb.add(logf, text="Log")
        self.log = scrolledtext.ScrolledText(logf, height=8, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        self.result = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.result, wraplength=820, foreground="#0a6b2b",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)
        threading.Thread(target=self.check_docker, daemon=True).start()

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
        sym, color, _ = ICONS[level]
        self.check_icon.configure(text=sym, fg=color)
        self.check_msg.set(msg)

    def schedule_check(self, delay_ms):
        if self._check_job:
            self.after_cancel(self._check_job)
            self._check_job = None
        url = self.normalized_url()
        if not url:
            self.check_res = self.check_key = None
            self.set_icon("idle", "")
            return
        key = (url, self.engine.get())
        if key == self.check_key and self.check_res is not None:
            return
        self._check_job = self.after(delay_ms, lambda: self.run_check(key))

    def run_check(self, key):
        self._check_job = None
        self.check_key, self.check_res = key, None
        self.set_icon("checking", "Checking the site for logins and blocking...")
        url, engine = key
        threading.Thread(target=lambda: self.q.put(("check", (key, check_site(url, engine)))), daemon=True).start()

    def on_check_result(self, key, res):
        if key != self.check_key:
            return  # URL or engine changed while checking
        self.check_res = res
        self.set_icon(res.level, res.summary + "  (click the icon for details)")

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

        opts = Options(
            max_pages=self.to_int(self.max_pages, 1000),
            max_mb=self.to_int(self.max_mb, 500),
            max_minutes=self.to_int(self.max_minutes, 0),
            workers=min(16, max(1, self.to_int(self.workers, 4))),
            path_scope=self.path_scope.get(),
            subdomains=self.subdomains.get(),
        )
        self.out_path = out
        self.cancel = threading.Event()
        self.running = True
        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.tree.delete(*self.tree.get_children())
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.result.set("")
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
                self.q.put(("done", (out, r["size"], r["pages"], None, r["failed"])))
            else:
                size, c = convert(url, out, opts, on_event=emit, cancel=self.cancel)
                self.q.put(("done", (out, size, c.pages, c.files, c.failed)))
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
                elif kind == "docker":
                    self.docker_msg.set({
                        "ready": "Docker Desktop: ready.",
                        "not_running": "Docker Desktop: installed but not running - start it before using Zimit.",
                        "missing": "Docker Desktop: not found - install it for Zimit, or use the Built-in engine.",
                    }[val])
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def add_row(self, status, url, err=False):
        row = self.tree.insert("", "end", values=(status, url), tags=("err",) if err else ())
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
            self.counts.set(f"Pages: {e['pages']}    Files: {e['files']}    Size: {fmt_size(e['bytes'])}")
        elif t == "row":  # zimit
            self.add_row(e["status"], e["url"], e.get("error", False))
        elif t == "stats":
            self.counts.set(f"Crawled: {e['crawled']} of {e['total']}    Waiting: {e['pending']}    Failed: {e['failed']}")
        elif t == "log":
            self.add_log(e["text"])
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

    def on_done(self, out, size, pages, files, failed):
        self.finish_ui()
        self.progress.configure(mode="determinate", maximum=1, value=1)
        self.status.set("Done")
        detail = f"{fmt_size(size)}, {pages} pages" + (f", {files} files" if files is not None else "")
        if failed:
            detail += f", {failed} failed"
        self.result.set(f"Saved: {out}   ({detail})")
        if messagebox.askyesno(APP_NAME, f"ZIM file created:\n{out}\n\nShow it in its folder?"):
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
        if self.running:
            if not messagebox.askyesno(APP_NAME, "A conversion is running. Cancel it and quit?"):
                return
            self.cancel.set()
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
