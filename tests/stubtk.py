"""A stand-in tkinter so the window logic can be tested without a display (draws nothing)."""
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
sd = types.ModuleType("tkinter.simpledialog"); sd.askstring = lambda *a, **k: None
tk.simpledialog = sd
tk.ttk, tk.messagebox, tk.filedialog, tk.scrolledtext = ttk, mb, fd, st
tk.filedialog = fd
sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
                    "tkinter.filedialog": fd, "tkinter.simpledialog": sd, "tkinter.scrolledtext": st})

