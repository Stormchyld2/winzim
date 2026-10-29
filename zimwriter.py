"""Minimal pure-Python ZIM file writer (ZIM format 6.1, new namespace scheme).

No third-party dependencies. Text clusters are XZ-compressed (stdlib lzma),
binary clusters are stored uncompressed.
"""
import hashlib
import lzma
import struct
import tempfile
import uuid

NO_MAIN = 0xFFFFFFFF
CLUSTER_TARGET = 1 << 20  # ~1 MB of raw data per cluster

_COMPRESSIBLE = {
    "application/javascript", "application/x-javascript", "application/json",
    "application/xml", "application/xhtml+xml", "image/svg+xml",
    "application/rss+xml", "application/atom+xml", "application/manifest+json",
}


def is_compressible(mime):
    mime = mime.split(";")[0].strip().lower()
    return mime.startswith("text/") or mime in _COMPRESSIBLE


class ZimWriter:
    def __init__(self, path, metadata=None):
        self.path = path
        self.metadata = dict(metadata or {})
        self._clusters = tempfile.TemporaryFile()
        self._cluster_sizes = []
        self._entries = []
        self._keys = set()
        self._open = {True: [], False: []}
        self._open_size = {True: 0, False: 0}
        self._main = None

    # ---- adding content -------------------------------------------------
    def add_entry(self, url, title, mime, data, ns="C"):
        key = (ns, url)
        if key in self._keys:
            return False
        self._keys.add(key)
        e = {"ns": ns, "url": url, "title": title or "", "mime": mime,
             "redirect": None, "cluster": 0, "blob": 0}
        self._entries.append(e)
        comp = is_compressible(mime)
        self._open[comp].append((e, data))
        self._open_size[comp] += len(data)
        if self._open_size[comp] >= CLUSTER_TARGET:
            self._flush(comp)
        return True

    def add_redirect(self, url, title, target_url, ns="C", target_ns="C"):
        key = (ns, url)
        if key in self._keys:
            return False
        self._keys.add(key)
        self._entries.append({"ns": ns, "url": url, "title": title or "",
                              "mime": None, "redirect": (target_ns, target_url)})
        return True

    def set_main(self, url):
        self._main = ("C", url)

    # ---- clusters ---------------------------------------------------------
    def _flush(self, comp):
        items = self._open[comp]
        if not items:
            return
        self._open[comp] = []
        self._open_size[comp] = 0
        n = len(items)
        total = sum(len(d) for _, d in items)
        extended = (n + 1) * 4 + total > 0xFFFFFFFF
        fmt, width = ("<Q", 8) if extended else ("<I", 4)
        pos = (n + 1) * width
        offsets = []
        for _, d in items:
            offsets.append(pos)
            pos += len(d)
        offsets.append(pos)
        body = b"".join(struct.pack(fmt, o) for o in offsets) + b"".join(d for _, d in items)
        if comp:
            payload = lzma.compress(body, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32, preset=6)
            info = 4
        else:
            payload = body
            info = 1
        if extended:
            info |= 0x10
        record = bytes([info]) + payload
        idx = len(self._cluster_sizes)
        self._clusters.write(record)
        self._cluster_sizes.append(len(record))
        for i, (e, _) in enumerate(items):
            e["cluster"] = idx
            e["blob"] = i

    # ---- finish -------------------------------------------------------------
    def close(self):
        counts = {}
        for e in self._entries:
            if e["ns"] == "C" and e["mime"]:
                m = e["mime"].split(";")[0]
                counts[m] = counts.get(m, 0) + 1
        if counts:
            self.metadata.setdefault(
                "Counter", ";".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for k, v in self.metadata.items():
            if v is not None and str(v) != "":
                self.add_entry(k, "", "text/plain;charset=utf-8", str(v).encode("utf-8"), ns="M")
        self._flush(True)
        self._flush(False)

        entries = self._entries
        by_key = {(e["ns"], e["url"]): e for e in entries}
        # drop redirects whose target is missing
        entries = [e for e in entries if not e["redirect"] or e["redirect"] in by_key]
        entries.sort(key=lambda e: (e["ns"], e["url"].encode("utf-8")))
        index = {(e["ns"], e["url"]): i for i, e in enumerate(entries)}
        n = len(entries)

        mimes = sorted({e["mime"] for e in entries if e["mime"]})
        mime_idx = {m: i for i, m in enumerate(mimes)}
        mime_blob = b"".join(m.encode("utf-8") + b"\0" for m in mimes) + b"\0"

        dirents = []
        for e in entries:
            url = e["url"].encode("utf-8") + b"\0"
            title = (e["title"] if e["title"] != e["url"] else "").encode("utf-8") + b"\0"
            ns = e["ns"].encode("ascii")
            if e["redirect"]:
                head = struct.pack("<HBcII", 0xFFFF, 0, ns, 0, index[e["redirect"]])
            else:
                head = struct.pack("<HBcIII", mime_idx[e["mime"]], 0, ns, 0, e["cluster"], e["blob"])
            dirents.append(head + url + title)

        title_order = sorted(range(n), key=lambda i: (
            entries[i]["ns"], (entries[i]["title"] or entries[i]["url"]).encode("utf-8")))

        c = len(self._cluster_sizes)
        mime_pos = 80
        url_ptr_pos = mime_pos + len(mime_blob)
        title_ptr_pos = url_ptr_pos + 8 * n
        cluster_ptr_pos = title_ptr_pos + 4 * n
        dirent_pos = cluster_ptr_pos + 8 * c
        cluster_pos = dirent_pos + sum(len(d) for d in dirents)
        checksum_pos = cluster_pos + sum(self._cluster_sizes)

        main_idx = index.get(self._main, NO_MAIN) if self._main else NO_MAIN

        header = struct.pack(
            "<IHH16sIIQQQQIIQ",
            0x044D495A, 6, 1, uuid.uuid4().bytes, n, c,
            url_ptr_pos, title_ptr_pos, cluster_ptr_pos, mime_pos,
            main_idx, NO_MAIN, checksum_pos)
        assert len(header) == 80

        md5 = hashlib.md5()
        with open(self.path, "wb") as f:
            def w(b):
                md5.update(b)
                f.write(b)
            w(header)
            w(mime_blob)
            pos = dirent_pos
            ptrs = []
            for d in dirents:
                ptrs.append(pos)
                pos += len(d)
            w(b"".join(struct.pack("<Q", p) for p in ptrs))
            w(b"".join(struct.pack("<I", i) for i in title_order))
            pos = cluster_pos
            cptrs = []
            for s in self._cluster_sizes:
                cptrs.append(pos)
                pos += s
            w(b"".join(struct.pack("<Q", p) for p in cptrs))
            for d in dirents:
                w(d)
            self._clusters.seek(0)
            while True:
                chunk = self._clusters.read(1 << 20)
                if not chunk:
                    break
                w(chunk)
            f.write(md5.digest())
        self._clusters.close()
        return n
