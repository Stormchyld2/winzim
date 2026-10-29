"""Remember the user's choices between runs (JSON file in the user's app-data folder)."""
import json
import os


def settings_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "CindrixRecon", "settings.json")


def old_settings_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "WinZim", "settings.json")   # the app's earlier name


def load(path=None):
    if path is None and not os.path.exists(settings_path()) and os.path.exists(old_settings_path()):
        path = old_settings_path()    # carry settings over from the old name
    try:
        with open(path or settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data, path=None):
    path = path or settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
