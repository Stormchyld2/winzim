"""Saved copies of pages from earlier runs of the built-in engine, so a site can be resumed or updated without
downloading everything again. One folder per start address inside the cache folder."""
import hashlib
import json
import os
import shutil
from urllib.parse import urlsplit

MAX_ITEM = 8 * 1024 * 1024   # bigger files are always fetched fresh


def default_root():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "CindrixRecon", "cache")


class PageCache:
    def __init__(self, root, start_url):
        host = (urlsplit(start_url).hostname or "site").replace(":", "_")
        self.dir = os.path.join(root, f"{host}-{hashlib.sha1(start_url.encode()).hexdigest()[:8]}")
        os.makedirs(self.dir, exist_ok=True)

    def _key(self, url):
        return os.path.join(self.dir, hashlib.sha1(url.encode("utf-8", "replace")).hexdigest())

    def get(self, url):
        k = self._key(url)
        try:
            with open(k + ".json", "r", encoding="utf-8") as f:
                meta = json.load(f)
            with open(k + ".bin", "rb") as f:
                data = f.read()
            if len(data) != meta.get("size"):
                return None
            return meta, data
        except (OSError, ValueError):
            return None

    def put(self, url, meta, data):
        if len(data) > MAX_ITEM:
            return
        k = self._key(url)
        try:
            with open(k + ".bin.tmp", "wb") as f:
                f.write(data)
            os.replace(k + ".bin.tmp", k + ".bin")
            with open(k + ".json.tmp", "w", encoding="utf-8") as f:
                json.dump(dict(meta, size=len(data), url=url), f)
            os.replace(k + ".json.tmp", k + ".json")
        except OSError:
            pass


def cache_size(root):
    total = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def clear_cache(root):
    shutil.rmtree(root, ignore_errors=True)
