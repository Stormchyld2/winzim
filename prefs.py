"""Program settings that other modules read at the moment they need them (timeouts, proxy, download counts...).

The window (settingsui.py) edits them; app.py saves/loads them with the rest of the settings file.
"""
import copy

DEFAULTS = {
    # general
    "remember": True,            # remember choices between runs
    "confirm_all": True,         # ask before "Download all"
    "open_when_done": False,     # open the folder when a job finishes
    "check_docker": True,        # look for Docker Desktop at start
    # downloads
    "downloads_at_once": 6,      # 1 - 6; the rest wait in line and start as others finish
    "retries": 1,                # 0 - 5 extra tries for a failed download
    "skip_over_mb": 0,           # 0 = no limit
    "log_manifest": True,        # write library_manifest.csv
    # network
    "timeout_s": 15,             # 5 - 120
    "pause_ms": 0,               # pause before each request (0 - 5000)
    "proxy": "",                 # e.g. http://127.0.0.1:8080
    "catalog_contact": True,     # tell the big book catalogs who is asking
    # sites
    "site_pause_ms": 0,          # pause between pages on the built-in engine
    "cache_mode": "update",      # off | resume (trust saved pages) | update (ask the site what changed)
    # books -> zim
    "zim_name": "Recon Library",
    "zim_max_file_mb": 2000,
    "zim_readers": True,         # make books readable inside the ZIM
    "tool_ddjvu": "",            # blank = look for it automatically
    "tool_7z": "",
    "tool_calibre": "",
    "tool_docker": "",           # blank = find Docker Desktop automatically
    # zim library (taking books out of a ZIM)
    "lib_multi": False,          # show checkboxes and the bulk buttons
    "lib_zim": "",
    "lib_folder": "",
}
LIMITS = {"downloads_at_once": (1, 6), "retries": (0, 5), "skip_over_mb": (0, 100000), "timeout_s": (5, 120),
          "pause_ms": (0, 5000), "site_pause_ms": (0, 5000), "zim_max_file_mb": (1, 100000)}

_values = copy.deepcopy(DEFAULTS)


def get(key):
    return _values.get(key, DEFAULTS.get(key))


def set(key, value):  # noqa: A001  (module-level setter, named like the getter)
    if key not in DEFAULTS:
        return
    d = DEFAULTS[key]
    if isinstance(d, bool):
        value = bool(value)
    elif isinstance(d, int):
        try:
            value = int(float(str(value).strip() or d))
        except ValueError:
            value = d
        lo, hi = LIMITS.get(key, (0, 10 ** 9))
        value = max(lo, min(hi, value))
    else:
        value = str(value).strip()
        if key == "cache_mode" and value not in ("off", "resume", "update"):
            value = d
    _values[key] = value


def dump():
    return {"pref_" + k: v for k, v in _values.items()}


def load(data):
    for k in DEFAULTS:
        if "pref_" + k in data:
            set(k, data["pref_" + k])


def reset():
    _values.clear()
    _values.update(copy.deepcopy(DEFAULTS))


def apply_network():
    """Install the proxy (if any) for every request the program makes."""
    import urllib.request
    p = get("proxy")
    urllib.request.install_opener(urllib.request.build_opener(proxy_handler()))


def proxy_handler():
    import urllib.request
    p = get("proxy")
    return urllib.request.ProxyHandler({"http": p, "https": p}) if p else urllib.request.ProxyHandler()
