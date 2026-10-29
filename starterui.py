"""The starter library window (Sites and Books tabs open it). Entries must be approved before they can be used."""
import os
import subprocess
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import starterlib
from appinfo import NAME

PRESET_LABELS = {"whole": "Whole site", "section": "This section only", "page": "Just this page"}
DETAIL_LABELS = {"full": "Full", "lite": "Lite", "text": "No pictures", "min": "Minimum"}


class EntryDialog(tk.Toplevel):
    """Add or change one entry."""

    def __init__(self, parent, entry, on_save):
        super().__init__(parent)
        self.title("Starter library entry")
        self.entry, self.on_save = entry, on_save
        self.kind = tk.StringVar(value=entry.get("kind", "site"))
        self.name = tk.StringVar(value=entry.get("name", ""))
        self.addr = tk.StringVar(value=entry.get("url") or entry.get("query") or "")
        self.preset = tk.StringVar(value=entry.get("preset", "whole"))
        self.detail = tk.StringVar(value=entry.get("detail", "lite"))
        self.note = tk.StringVar(value=entry.get("note", ""))
        f = ttk.Frame(self)
        f.pack(fill="both", expand=True, padx=14, pady=12)
        f.columnconfigure(1, weight=1)
        rows = (("Type", ttk.Combobox(f, textvariable=self.kind, values=["site", "book"], state="readonly", width=10)),
                ("Name", ttk.Entry(f, textvariable=self.name, width=52)),
                ("Web address (site) or title/author (book)", ttk.Entry(f, textvariable=self.addr, width=52)),
                ("Site: how much", ttk.Combobox(f, textvariable=self.preset, values=list(PRESET_LABELS), state="readonly", width=10)),
                ("Site: detail", ttk.Combobox(f, textvariable=self.detail, values=list(DETAIL_LABELS), state="readonly", width=10)),
                ("Note", ttk.Entry(f, textvariable=self.note, width=52)))
        for i, (label, w) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
            w.grid(row=i, column=1, sticky="w", padx=8, pady=3)
        bar = ttk.Frame(f)
        bar.grid(row=len(rows), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="Save", style="Primary.TButton", command=self.save).pack(side="right")

    def save(self):
        e = dict(self.entry)
        e.update(kind=self.kind.get(), name=self.name.get().strip(), note=self.note.get().strip())
        if e["kind"] == "site":
            e.update(url=self.addr.get().strip(), preset=self.preset.get(), detail=self.detail.get())
            e.pop("query", None)
        else:
            e.update(query=self.addr.get().strip())
            for k in ("url", "preset", "detail"):
                e.pop(k, None)
        why = starterlib.problem(e)
        if why:
            messagebox.showerror(NAME, why)
            return
        if e != self.entry:          # changing an entry means it has to be checked and approved again
            e["approved"], e["status"] = False, "unverified"
        self.on_save(e)
        self.destroy()


class StarterDialog(tk.Toplevel):
    def __init__(self, parent, app, path=None):
        super().__init__(parent)
        self.app, self.path = app, path
        self.title("Starter library")
        self.entries = starterlib.load(path)
        ttk.Label(self, text="A draft list, nothing in it is checked. Look at each site, then tick Approve (click the box) "
                             "before you can use it.", wraplength=760, justify="left").pack(anchor="w", padx=12, pady=(10, 4))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=12, pady=4)
        self.tree = ttk.Treeview(box, columns=("ok", "name", "kind", "what", "note"), show="headings", height=14, selectmode="browse")
        for c, text, w in (("ok", "Approved", 70), ("name", "Name", 230), ("kind", "Type", 50), ("what", "Address / lookup", 260), ("note", "Note", 330)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="center" if c == "ok" else "w", stretch=(c == "note"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", lambda _e: self.use())
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(4, 10))
        ttk.Button(bar, text="Use", style="Primary.TButton", command=self.use).pack(side="left")
        for text, cmd in (("Add...", self.add), ("Edit...", self.edit), ("Remove", self.remove)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Reset to draft", command=self.reset).pack(side="right", padx=6)
        ttk.Button(bar, text="Open the file", command=self.open_file).pack(side="right")
        self.render()

    def render(self):
        self.tree.delete(*self.tree.get_children())
        for e in self.entries:
            what = e.get("url") or e.get("query") or ""
            self.tree.insert("", "end", iid=e["id"], values=("☑" if e.get("approved") else "☐", e["name"], e["kind"], what, e.get("note", "")))

    def find(self, iid):
        return next((e for e in self.entries if e["id"] == iid), None)

    def selected(self):
        sel = self.tree.selection()
        return self.find(sel[0]) if sel else None

    def persist(self):
        starterlib.save(self.entries, self.path)
        self.render()

    def on_click(self, ev):
        if self.tree.identify_region(ev.x, ev.y) == "cell" and self.tree.identify_column(ev.x) == "#1":
            e = self.find(self.tree.identify_row(ev.y))
            if e:
                self.toggle(e)

    def toggle(self, e):
        if not e.get("approved"):
            why = starterlib.problem(e)
            if why:
                messagebox.showerror(NAME, why)
                return
            if not messagebox.askyesno(NAME, f"Approve '{e['name']}'?\n\nOnly approve it after you have looked at it and are sure you "
                                             "may copy it and that it fits on your drive."):
                return
        e["approved"] = not e.get("approved")
        e["status"] = "approved by you" if e["approved"] else "unverified"
        self.persist()

    def use(self):
        e = self.selected()
        if not e:
            messagebox.showinfo(NAME, "Click an entry first.")
            return
        if not starterlib.usable(e):
            messagebox.showinfo(NAME, "This entry is not approved yet. Click its box under Approved once you have checked it.")
            return
        self.app.use_starter(e)
        self.destroy()

    def add(self):
        EntryDialog(self, {"id": starterlib.new_id(self.entries, "site"), "kind": "site", "preset": "whole", "detail": "lite",
                           "approved": False, "status": "unverified"}, lambda e: (self.entries.append(e), self.persist()))

    def edit(self):
        e = self.selected()
        if e:
            def save(new, old=e):
                self.entries[self.entries.index(old)] = new
                self.persist()
            EntryDialog(self, e, save)

    def remove(self):
        e = self.selected()
        if e and messagebox.askyesno(NAME, f"Remove '{e['name']}' from the list?"):
            self.entries.remove(e)
            self.persist()

    def reset(self):
        if messagebox.askyesno(NAME, "Replace the whole list with the draft that came with the program? Your changes and approvals will be lost "
                                     "(a copy is kept as starter_library.json.bak)."):
            p = self.path or starterlib.path()
            try:
                if os.path.exists(p):
                    import shutil
                    shutil.copy(p, p + ".bak")
            except OSError:
                pass
            self.entries = starterlib.default_entries()
            self.persist()

    def open_file(self):
        starterlib.load(self.path)
        p = self.path or starterlib.path()
        try:
            if os.name == "nt":
                os.startfile(p)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:
            pass
