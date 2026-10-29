"""Small ZIM reader + local preview server (so a finished ZIM can be checked in a
browser without installing Kiwix).

Supports ZIM 6.x, uncompressed and XZ clusters, and Zstandard clusters when the
optional `zstandard` module is bundled. Both the old ('A') and new ('C')
article namespace layouts are handled.
"""
import hashlib
import http.server
import lzma
import mimetypes
import struct
import threading
from urllib.parse import quote, unquote

try:  # optional
    import zstandard
except Exception:  # pragma: no cover
    zstandard = None

NO_MAIN = 0xFFFFFFFF


class ZimError(Exception):
    pass


class ZimReader:
    def __init__(self, path):
        self.f = open(path, "rb")
        head = self.f.read(80)
        if len(head) < 80:
            raise ZimError("Not a ZIM file")
        (magic, self.major, self.minor, _uuid, self.entry_count, self.cluster_count, self.url_ptr,
         _title_ptr, self.cluster_ptr, mime_pos, self.main, _layout, self.checksum_pos) = struct.unpack("<IHH16sIIQQQQIIQ", head)
        if magic != 0x044D495A:
            raise ZimError("Not a ZIM file")
        self.f.seek(mime_pos)
        buf = self.f.read(4096)
        self.mimes, p = [], 0
        while True:
            e = buf.find(b"\0", p)
            if e == -1:
                buf += self.f.read(4096)
                continue
            if e == p:
                break
            self.mimes.append(buf[p:e].decode())
            p = e + 1
        self._cache = {}
        self._lock = threading.Lock()
        self._io_lock = threading.RLock()
        self.content_ns = "C" if self.minor >= 1 else "A"

    # -- entries
    def _read(self, off, n):
        with self._io_lock:   # several threads read the same file handle
            self.f.seek(off)
            return self.f.read(n)

    def blob_location(self, e):
        """(file offset, length) of an entry's bytes when its cluster is stored uncompressed (books are), else None.
        Lets big files be copied out in pieces instead of loading them into memory."""
        if e["redirect"] is not None:
            return None
        cl, bl = e["cluster"], e["blob"]
        start = struct.unpack("<Q", self._read(self.cluster_ptr + 8 * cl, 8))[0]
        info = self._read(start, 1)[0]
        if info & 0x0F not in (0, 1):
            return None
        w = 8 if info & 0x10 else 4
        fmt = "<Q" if w == 8 else "<I"
        o0 = struct.unpack(fmt, self._read(start + 1 + w * bl, w))[0]
        o1 = struct.unpack(fmt, self._read(start + 1 + w * (bl + 1), w))[0]
        return start + 1 + o0, o1 - o0

    def entries(self, ns="C"):
        """Yield every non-redirect entry of a namespace."""
        for i in range(self.entry_count):
            e = self.dirent(i)
            if e["ns"] == ns and e["redirect"] is None:
                yield e

    def dirent(self, idx):
        off = struct.unpack("<Q", self._read(self.url_ptr + 8 * idx, 8))[0]
        raw = self._read(off, 600)
        mime = struct.unpack_from("<H", raw, 0)[0]
        ns = chr(raw[3])
        if mime == 0xFFFF:
            target = struct.unpack_from("<I", raw, 8)[0]
            p, e = 12, {"redirect": target}
        else:
            cl, bl = struct.unpack_from("<II", raw, 8)
            p, e = 16, {"mime": self.mimes[mime], "cluster": cl, "blob": bl, "redirect": None}
        u = raw.index(b"\0", p)
        e["ns"], e["url"] = ns, raw[p:u].decode("utf-8", "replace")
        return e

    def find(self, ns, url):
        lo, hi = 0, self.entry_count - 1
        key = (ns, url.encode("utf-8"))
        while lo <= hi:
            mid = (lo + hi) // 2
            e = self.dirent(mid)
            k = (e["ns"], e["url"].encode("utf-8"))
            if k == key:
                return mid, e
            if k < key:
                lo = mid + 1
            else:
                hi = mid - 1
        return None, None

    def resolve(self, ns, url, depth=0):
        idx, e = self.find(ns, url)
        while e is not None and e["redirect"] is not None and depth < 10:
            idx = e["redirect"]
            e = self.dirent(idx)
            depth += 1
        return idx, e

    def blob(self, e):
        cl = e["cluster"]
        with self._lock:
            if cl not in self._cache:
                start = struct.unpack("<Q", self._read(self.cluster_ptr + 8 * cl, 8))[0]
                end = (struct.unpack("<Q", self._read(self.cluster_ptr + 8 * (cl + 1), 8))[0]
                       if cl + 1 < self.cluster_count else self.checksum_pos)
                raw = self._read(start, end - start)
                info, body = raw[0], raw[1:]
                comp = info & 0x0F
                if comp == 4:
                    body = lzma.decompress(body)
                elif comp == 5:
                    if zstandard is None:
                        raise ZimError("This ZIM uses Zstandard compression; preview here needs the Kiwix app.")
                    body = zstandard.ZstdDecompressor().decompressobj().decompress(body)
                elif comp not in (0, 1):
                    raise ZimError(f"Unsupported compression {comp}")
                w = 8 if info & 0x10 else 4
                fmt = "<Q" if w == 8 else "<I"
                first = struct.unpack_from(fmt, body, 0)[0]
                n = first // w - 1
                offs = [struct.unpack_from(fmt, body, w * i)[0] for i in range(n + 1)]
                if len(self._cache) > 8:
                    self._cache.clear()
                self._cache[cl] = (body, offs)
            body, offs = self._cache[cl]
        return body[offs[e["blob"]]:offs[e["blob"] + 1]]

    def main_path(self):
        if self.main == NO_MAIN:
            return None
        return self.dirent(self.main)["url"]

    def main_target(self):
        """URL of the page the main-page entry finally points at (following redirects), or None."""
        if self.main == NO_MAIN:
            return None
        e, depth = self.dirent(self.main), 0
        while e["redirect"] is not None and depth < 10:
            e = self.dirent(e["redirect"])
            depth += 1
        return e["url"]

    def metadata(self, key):
        _, e = self.resolve("M", key)
        return self.blob(e).decode("utf-8", "replace") if e and e["redirect"] is None else None

    def verify_checksum(self):
        h = hashlib.md5()
        self.f.seek(0)
        left = self.checksum_pos
        while left:
            chunk = self.f.read(min(1 << 20, left))
            if not chunk:
                return False
            h.update(chunk)
            left -= len(chunk)
        return h.digest() == self.f.read(16)

    def close(self):
        self.f.close()


def make_handler(zim):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = unquote(self.path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
            if path == "":
                target = zim.main_target()
                if target is None:
                    return self._send(404, b"This ZIM has no main page.", "text/plain")
                return self._redirect("/" + quote(target))
            _, e = zim.resolve(zim.content_ns, path)
            if e is None and zim.content_ns == "C":
                _, e = zim.resolve("A", path)
            if e is None:     # crawler ZIMs keep their start page as a redirect in the "W" area (W/mainPage)
                w_idx, w = zim.find("W", path)
                if w is not None:
                    _, final = zim.resolve("W", path)
                    if final is not None and final["redirect"] is None:
                        return self._redirect("/" + quote(final["url"]))
            if e is None:
                return self._send(404, b"Not in this ZIM file.", "text/plain")
            try:
                data = zim.blob(e)
            except ZimError as err:
                return self._send(500, str(err).encode(), "text/plain")
            mime = e["mime"] or mimetypes.guess_type(path)[0] or "application/octet-stream"
            if mime.startswith("text/") and "charset" not in mime:
                mime += "; charset=utf-8"
            self._send(200, data, mime)

        def _redirect(self, where):
            self.send_response(302)
            self.send_header("Location", where)
            self.end_headers()

        def _send(self, code, data, mime):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H


class PreviewServer:
    def __init__(self, zim_path):
        self.zim = ZimReader(zim_path)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.zim))
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.zim.close()
