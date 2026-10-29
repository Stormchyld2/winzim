"""Exercises App logic (check indicator, gating) with a stub tkinter, since CI/sandbox may lack a display.
Does NOT render a real window."""
import os, sys, types, time, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class W:
    def __init__(self, *a, **k):
        self.opts = dict(k)
    def __getattr__(self, n):
        if n == "get_children":
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
tk.Tk, tk.Label, tk.StringVar, tk.BooleanVar = Tk, W, Var, Var
for n in ("Frame","LabelFrame","Entry","Button","Radiobutton","Checkbutton","Spinbox","Progressbar","Notebook","Treeview","Scrollbar","Label"):
    setattr(ttk, n, W)
mb = types.ModuleType("tkinter.messagebox")
mb.showerror = mb.showinfo = lambda *a, **k: None
def askyesno(*a, **k): asked.append(a); return False
mb.askyesno = askyesno
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
assert a.check_icon.opts["fg"] == appmod.ICONS["ok"][1]
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
print("ALL APP LOGIC TESTS PASSED")
