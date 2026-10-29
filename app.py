"""WinZim - turn any website into a ZIM file. Self-contained, no Docker needed."""
import datetime
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

from crawler import Options, convert

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
        self.geometry("820x680")
        self.minsize(720, 560)
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
        e.focus_set()
        self.convert_btn = ttk.Button(top, text="Convert", command=self.start)
        self.convert_btn.grid(row=0, column=2, padx=(6, 0))

        ttk.Label(top, text="Save to folder:").grid(row=1, column=0, sticky="w")
        self.folder = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Documents", "ZIM"))
        ttk.Entry(top, textvariable=self.folder).grid(row=1, column=1, sticky="ew", pady=3, padx=(6, 0))
        ttk.Button(top, text="Browse...", command=self.browse).grid(row=1, column=2, padx=(6, 0))

        ttk.Label(top, text="File name:").grid(row=2, column=0, sticky="w")
        self.fname = tk.StringVar()
        ttk.Entry(top, textvariable=self.fname).grid(row=2, column=1, sticky="ew", pady=3, padx=(6, 0))
        ttk.Label(top, text="blank = automatic", foreground="gray").grid(row=2, column=2, padx=(6, 0))

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
        ttk.Label(r, text="0 = no limit. More workers = faster, but some sites may block heavy traffic.",
                  foreground="gray").grid(row=1, column=0, columnspan=8, sticky="w", pady=(4, 0))
        ttk.Checkbutton(r, text="Only pages inside this URL's folder",
                        variable=self.path_scope).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(r, text="Include subdomains",
                        variable=self.subdomains).grid(row=2, column=4, columnspan=4, sticky="w", pady=(6, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.on_cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.open_btn = ttk.Button(bar, text="Open folder", command=self.open_folder)
        self.open_btn.pack(side="left", padx=6)
        self.status = tk.StringVar(value="Ready. Enter a URL and click Convert.")
        ttk.Label(bar, textvariable=self.status).pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", **pad)
        self.counts = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.counts, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, **pad)
        cols = ("status", "url")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=10)
        self.tree.heading("status", text="Status")
        self.tree.heading("url", text="Page")
        self.tree.column("status", width=110, stretch=False)
        self.tree.column("url", width=600)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("err", foreground="#b00020")

        self.result = tk.StringVar(value="")
        self.result_lbl = ttk.Label(self, textvariable=self.result, wraplength=780, foreground="#0a6b2b",
                                    font=("Segoe UI", 10, "bold"))
        self.result_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)

    # ------------------------------------------------------------ helpers
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

    # ------------------------------------------------------------- actions
    def start(self):
        if self.running:
            return
        url = self.url.get().strip()
        if url and "://" not in url:
            url = "https://" + url
            self.url.set(url)
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname and p.hostname != "localhost":
            messagebox.showerror(APP_NAME, "Please enter a full website address, like https://example.com")
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
        self.result.set("")
        self.counts.set("Pages: 0    Files: 0    Size: 0 B")
        self.status.set("Downloading site...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        threading.Thread(target=self.work, args=(url, out, opts), daemon=True).start()

    def work(self, url, out, opts):
        try:
            size, c = convert(url, out, opts, on_event=lambda e: self.q.put(("event", e)), cancel=self.cancel)
            self.q.put(("done", (out, size, c.pages, c.files, c.failed)))
        except Exception as e:
            self.q.put(("error", str(e)))

    def on_cancel(self):
        self.cancel.set()
        self.status.set("Cancelling...")
        self.cancel_btn.configure(state="disabled")

    def pump(self):
        try:
            for _ in range(200):
                kind, val = self.q.get_nowait()
                if kind == "event":
                    self.on_event(val)
                elif kind == "done":
                    self.on_done(*val)
                elif kind == "error":
                    self.on_error(val)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def on_event(self, e):
        t = e.get("type")
        if t == "fetch":
            if e["ok"]:
                if e.get("mime", "").startswith("text/html") or e.get("mime") == "application/xhtml+xml":
                    row = self.tree.insert("", "end", values=("Saved", e["url"]))
                    self.tree.see(row)
            else:
                row = self.tree.insert("", "end", values=(f"Failed ({e['error']})", e["url"]), tags=("err",))
                self.tree.see(row)
            self.counts.set(f"Pages: {e['pages']}    Files: {e['files']}    Size: {fmt_size(e['bytes'])}")
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
        self.progress.configure(mode="determinate", value=self.progress["maximum"])
        self.status.set("Done")
        msg = f"Saved: {out}   ({fmt_size(size)}, {pages} pages, {files} files"
        msg += f", {failed} failed)" if failed else ")"
        self.result.set(msg)
        if messagebox.askyesno(APP_NAME, f"ZIM file created:\n{out}\n\nShow it in its folder?"):
            self.open_folder()

    def on_error(self, text):
        self.finish_ui()
        self.progress.configure(mode="determinate", value=0)
        if text == "Cancelled":
            self.status.set("Cancelled")
        else:
            self.status.set("Error")
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
