"""Exercises App logic (check indicator, gating) with a stub tkinter, since CI/sandbox may lack a display.
Does NOT render a real window."""
import os, sys, types, time, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class W:
    def __init__(self, *a, **k):
        self.opts = dict(k)
    def __getattr__(self, n):
        if n in ("get_children", "winfo_children", "selection"):
            return lambda *a, **k: []
        return lambda *a, **k: None
    def __setitem__(self, k, v): self.opts[k] = v
    def __getitem__(self, k): return self.opts.get(k, 0)
    def configure(self, **k): self.opts.update(k)


class Var:
    def __init__(self, master=None, value="", **k):
        self.v, self.cbs = value, []
    def get(self): return self.v
    def set(self, v):
        self.v = v
        for cb in self.cbs: cb()
    def trace_add(self, mode, cb): self.cbs.append(cb)


class Tk(W):
    def after(self, ms, fn=None, *a): return fn
    def mainloop(self): pass


asked = []
tk = types.ModuleType("tkinter"); ttk = types.ModuleType("tkinter.ttk")
tk.Tk, tk.Label, tk.StringVar, tk.BooleanVar, tk.Canvas = Tk, W, Var, Var, W
tk.Frame = tk.Toplevel = W
tk.IntVar = Var
for n in ("Frame","LabelFrame","Entry","Button","Radiobutton","Checkbutton","Spinbox","Progressbar","Combobox","Notebook","Treeview","Scrollbar","Label","Separator","Style"):
    setattr(ttk, n, W)
class Style(W):
    def configure(self, *a, **k): pass
    def map(self, *a, **k): pass
    def theme_use(self, *a, **k): pass
ttk.Style = Style
mb = types.ModuleType("tkinter.messagebox")
mb.showerror = mb.showinfo = lambda *a, **k: None
def askyesno(*a, **k): asked.append(a); return False
mb.askyesno = askyesno
answers = {"yncancel": None}
mb.askyesnocancel = lambda *a, **k: answers["yncancel"]
fd = types.ModuleType("tkinter.filedialog"); fd.askdirectory = lambda **k: ""
st = types.ModuleType("tkinter.scrolledtext"); st.ScrolledText = W
tk.ttk, tk.messagebox, tk.filedialog, tk.scrolledtext = ttk, mb, fd, st
tk.filedialog = fd
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
                    "tkinter.filedialog": fd, "tkinter.scrolledtext": st})

import app as appmod
from test_sitecheck import serve

def pump_until(a, pred, t=15):
    end = time.time() + t
    while time.time() < end:
        while not a.q.empty():
            kind, val = a.q.get()
            if kind == "check": a.on_check_result(*val)
        if pred(): return True
        time.sleep(0.1)
    return False

srv, base = serve()
a = appmod.App()
# green
a.url.set(base + "/ok")
a.run_check((base + "/ok", "zimit"))
assert pump_until(a, lambda: a.check_res is not None)
assert a.check_res.level == "ok" and a.check_icon.opts["text"] == appmod.ICONS["ok"][0], a.check_icon.opts
assert a.check_icon.opts["fg"] == a.pal[appmod.ICONS["ok"][1]] == appmod.DARK["green"]
print("green check shown:", a.check_icon.opts["text"], a.check_msg.get()[:60])
# red + gating: Convert asks, user says no -> nothing starts
a.url.set(base + "/401")
a.run_check((base + "/401", "zimit"))
assert pump_until(a, lambda: a.check_res is not None and a.check_res.level == "bad")
assert a.check_icon.opts["text"] == appmod.ICONS["bad"][0]
a.start()
assert asked and "Convert anyway" in asked[-1][1] and not a.running
print("red X shown, Convert asked for confirmation and stopped on 'No'")
# stale result ignored
a.check_key = (base + "/ok", "zimit"); a.check_res = None
a.on_check_result((base + "/401", "zimit"), a.check_res or appmod.check_site(base + "/401"))
assert a.check_res is None
print("stale result ignored")
# invalid url -> idle
a.url.set("not a url"); a.schedule_check(0)
assert a.check_icon.opts["text"] == appmod.ICONS["idle"][0]
# presets + remembered settings
import tempfile, json
os.environ["APPDATA"] = tempfile.mkdtemp()
b = appmod.App()
b.preset.set("Quick test (first 10 pages)"); b.apply_preset()
assert (b.max_pages.get(), b.max_mb.get(), b.path_scope.get()) == ("10", "100", False)
b.preset.set("This section only (the URL's folder)"); b.apply_preset()
assert b.path_scope.get() is True and b.max_pages.get() == "1000"
b.max_pages.set("77")                      # manual edit -> Custom
assert b.preset.get() == appmod.CUSTOM, b.preset.get()
b.max_pages.set("1000")                    # back to a preset's values -> that preset again
assert b.preset.get() == "This section only (the URL's folder)", b.preset.get()
assert b.dark is True and b.pal is appmod.DARK, "dark must be the default"
b.folder.set("D:\\MyZims"); b.engine.set("builtin"); b.workers.set("7"); b.toggle_adv()
b.save_settings()
c = appmod.App()
assert c.folder.get() == "D:\\MyZims" and c.engine.get() == "builtin" and c.workers.get() == "7" and c.adv_open
assert c.preset.get() == "This section only (the URL's folder)"
print("presets and remembered settings ok")

# --- theme: dark by default, toggle, persisted
c.toggle_theme()
assert c.dark is False and c.pal is appmod.LIGHT and c.check_icon.opts["bg"] == appmod.LIGHT["bg"]
c.save_settings()
d = appmod.App()
assert d.dark is False and d.pal is appmod.LIGHT, "theme not remembered"
d.toggle_theme(); assert d.dark is True and d.check_icon.opts["bg"] == appmod.DARK["bg"]
print("theme default/toggle/remember ok")

# --- smarter Whole site limits
from sitecheck import CheckResult
e = appmod.App()
e.preset.set(appmod.WHOLE); e.apply_preset()
assert (e.max_pages.get(), e.max_mb.get()) == ("20000", "20480"), (e.max_pages.get(), e.max_mb.get())   # no sitemap fallback
e.check_key = ("u", "zimit")
e.on_check_result(("u", "zimit"), CheckResult("ok", "fine", [], 1000))
assert (e.max_pages.get(), e.max_mb.get()) == ("1210", "0"), (e.max_pages.get(), e.max_mb.get())
assert e.preset.get() == appmod.WHOLE, e.preset.get()
assert "1,000 pages" in e.est_msg.get() and "Full" not in e.est_msg.get()
e.max_pages.set("5"); assert e.preset.get() == appmod.CUSTOM
e.on_check_result(("u", "zimit"), CheckResult("ok", "fine", [], 4000))
assert e.max_pages.get() == "5", "custom limits must not be overwritten"
e.preset.set("Everything, no limits (can be huge)"); e.apply_preset()
assert (e.max_pages.get(), e.max_mb.get()) == ("0", "0")
print("whole-site automatic limits ok")

# --- detail levels
f = appmod.App()
f.check_key = ("u", "zimit")
f.on_check_result(("u", "zimit"), CheckResult("ok", "fine", [], 1000))
sizes = {}
for name, key in (("Full (everything)", "full"), ("Lite (smaller pictures, no video)", "lite"),
                  ("No pictures (text and layout only)", "nopic"), ("Minimum (smallest file)", "min")):
    f.detail.set(name); f.apply_detail()
    assert f.level_key() == key, (name, f.level_key())
    sizes[key] = f.est_msg.get()
assert len(set(sizes.values())) == 4, sizes
f.detail.set("Minimum (smallest file)"); f.apply_detail()
assert f.dv["skip_images"].get() and f.dv["skip_media"].get() and f.dv["skip_fonts"].get() and f.dv["no_scripts"].get()
assert not f.dv["skip_docs"].get() and not f.dv["shrink_images"].get() and f.max_file_mb.get() == "5"
f.dv["skip_fonts"].set(False)                       # manual edit -> Custom
assert f.detail.get() == appmod.CUSTOM_DETAIL, f.detail.get()
f.dv["skip_fonts"].set(True)                        # back to the level's exact settings -> that level
assert f.detail.get() == "Minimum (smallest file)", f.detail.get()
f.detail.set("Full (everything)"); f.apply_detail()
assert not any(v.get() for v in f.dv.values()) and f.max_file_mb.get() == "0"
print("detail levels, custom flip and estimates ok")

# --- options reach the engine, remembered, and Lite on Zimit asks first
import tempfile as _t
captured = {}
appmod.threading.Thread = lambda target=None, args=(), daemon=None: types.SimpleNamespace(
    start=lambda: captured.update(args=args), target=target)
g = appmod.App()
captured.clear()
g.url.set("https://example.org"); g.folder.set(_t.mkdtemp())
g.detail.set("Lite (smaller pictures, no video)"); g.apply_detail(); g.depth.set("3"); g.engine.set("zimit")
asked.clear(); answers["yncancel"] = None                    # Cancel -> nothing starts
g.start(); assert not g.running and not captured
answers["yncancel"] = True                                   # Yes -> switches to Built-in and starts
g.start()
assert g.running and g.engine.get() == "builtin", (g.running, g.engine.get())
o = captured["args"][3]
assert o.shrink_images and o.skip_media and not o.skip_images and o.depth == 3, o
g.finish_ui()
g.save_settings()
h = appmod.App()
assert h.detail.get() == "Lite (smaller pictures, no video)" and h.dv["shrink_images"].get() and h.depth.get() == "3"
print("options passed to engine and remembered ok")

# --- limit-reached warning in the finished message
h.run_notes = []
h.on_done("x.zim", 1000, 50, 10, 0, "pages", False, 4)
assert "Stopped at the page limit; the site has more than was saved." in h.result.get(), h.result.get()
assert h.result_label.opts["style"] == "Warn.TLabel"
h.run_notes = []; h.result_label.configure(style="Result.TLabel")
h.on_done("x.zim", 1000, 50, 10, 0, None, False, 0)
assert "Stopped" not in h.result.get() and h.result_label.opts["style"] == "Result.TLabel"
h.run_notes = []
h.on_done("x.zim", 1000, 50, 10, 0, "time", True, 0)
assert "time limit" in h.result.get() and "Depth setting" in h.result.get()
print("limit warning ok")

# --- footer / about
assert "cindrixops@proton.me" in h.footer_label.opts["text"] and "About" in h.footer_label.opts["text"]
shown = []
appmod.messagebox.showinfo = lambda *a, **k: shown.append(a)
h.show_about()
assert "cindrixops@proton.me" in shown[-1][1] and "Cindrix Recon" in shown[-1][0]
print("about/footer ok")
print("ALL APP LOGIC TESTS PASSED")
