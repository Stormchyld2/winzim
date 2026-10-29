"""The Queue tab: sites and books waiting to be fetched one after another."""
import os
import tkinter as tk
from tkinter import messagebox, ttk

import jobqueue
from appinfo import NAME

LABELS = {"queued": "Waiting", "running": "Running", "done": "Done ✔", "failed": "Failed ✖", "cancelled": "Cancelled",
          "interrupted": "Interrupted"}


class QueueTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.q = jobqueue.JobQueue(on_change=lambda: app.q.put(("queue", None)))
        self.info = tk.StringVar()
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="Jobs wait here and run one after another. Add them from the Sites and Books tabs with Add to queue.").pack(side="left")
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=4)
        self.tree = ttk.Treeview(box, columns=("n", "status", "job", "details"), show="headings", selectmode="browse", height=12)
        for c, text, w, a in (("n", "#", 36, "center"), ("status", "Status", 110, "w"), ("job", "Job", 360, "w"), ("details", "Details", 320, "w")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("job", "details")))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(4, 10))
        self.start_btn = ttk.Button(bar, text="Start queue", style="Primary.TButton", command=self.toggle)
        self.start_btn.pack(side="left")
        ttk.Button(bar, text="Cancel current", style="Danger.TButton", command=self.q.cancel_current).pack(side="left", padx=6)
        for text, cmd in (("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1)), ("Retry", self.retry),
                          ("Remove", self.remove), ("Clear finished", self.q.clear_finished)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Label(bar, textvariable=self.info, style="Amber.TLabel").pack(side="right")
        self.refresh()

    def apply_theme(self, p):
        for tag, col in (("done", p["green"]), ("failed", p["red"]), ("running", p["amber"]), ("queued", p["text"]),
                         ("cancelled", p["dim"]), ("interrupted", p["amber"])):
            self.tree.tag_configure(tag, foreground=col)

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def refresh(self):
        keep = self.selected()
        self.tree.delete(*self.tree.get_children())
        counts = {}
        for i, j in enumerate(self.q.jobs, 1):
            counts[j["status"]] = counts.get(j["status"], 0) + 1
            self.tree.insert("", "end", iid=j["id"], values=(i, LABELS.get(j["status"], j["status"]), j["title"], j["message"]),
                             tags=(j["status"],))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        running = self.q.running and not self.q.paused
        self.start_btn.configure(text="Pause after this job" if running else "Start queue")
        self.info.set(", ".join(f"{counts[s]} {LABELS[s].split()[0].lower()}" for s in ("running", "queued", "done", "failed") if counts.get(s))
                      or "The queue is empty.")

    def toggle(self):
        if self.q.running and not self.q.paused:
            self.q.pause()
            self.info.set("Will stop after the current job.")
        else:
            if not any(j["status"] == "queued" for j in self.q.jobs):
                messagebox.showinfo(NAME, "There is nothing waiting. Add jobs with Add to queue on the Sites and Books tabs.")
                return
            self.q.start()
        self.refresh()

    def move(self, d):
        j = self.selected()
        if j:
            self.q.move(j, d)

    def retry(self):
        j = self.selected()
        if j:
            self.q.retry(j)

    def remove(self):
        j = self.selected()
        if j and not self.q.remove(j):
            messagebox.showinfo(NAME, "A running job cannot be removed. Cancel it first.")

    def add(self, kind, params, title=None):
        job = self.q.add(kind, params, title)
        waiting = sum(1 for j in self.q.jobs if j["status"] == "queued")
        return job, waiting

    def stop_all(self):
        self.q.pause()
        self.q.cancel_current()
