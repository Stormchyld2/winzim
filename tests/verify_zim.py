"""Independent ZIM reader/validator (written from the ZIM spec, not sharing code
with zimwriter.py). Used by the tests to check the writer's output."""
import hashlib
import lzma
import posixpath
import re
import struct
import sys
from urllib.parse import unquote, urljoin


class Zim:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        d = self.data
        (self.magic, self.major, self.minor, self.uuid, self.entry_count, self.cluster_count,
         self.url_ptr, self.title_ptr, self.cluster_ptr, self.mime_pos, self.main,
         self.layout, self.checksum_pos) = struct.unpack("<IHH16sIIQQQQIIQ", d[:80])
        assert self.magic == 0x044D495A, "bad magic"
        assert self.checksum_pos + 16 == len(d), "checksum position mismatch"
        assert hashlib.md5(d[:self.checksum_pos]).digest() == d[self.checksum_pos:], "MD5 mismatch"
        # mime list
        self.mimes, p = [], self.mime_pos
        while d[p] != 0:
            e = d.index(b"\0", p)
            self.mimes.append(d[p:e].decode())
            p = e + 1
        self.entries = []
        for i in range(self.entry_count):
            off = struct.unpack_from("<Q", d, self.url_ptr + 8 * i)[0]
            self.entries.append(self._dirent(off))
        keys = [(e["ns"], e["url"].encode()) for e in self.entries]
        assert keys == sorted(keys), "URL pointer list not sorted"
        titles = [struct.unpack_from("<I", d, self.title_ptr + 4 * i)[0] for i in range(self.entry_count)]
        tkeys = [(self.entries[i]["ns"], (self.entries[i]["title"] or self.entries[i]["url"]).encode()) for i in titles]
        assert tkeys == sorted(tkeys), "title pointer list not sorted"
        assert sorted(titles) == list(range(self.entry_count)), "title list not a permutation"
        self.by_key = {(e["ns"], e["url"]): e for e in self.entries}
        self._cache = {}

    def _dirent(self, off):
        d = self.data
        mime = struct.unpack_from("<H", d, off)[0]
        ns = chr(d[off + 3])
        if mime == 0xFFFF:
            target = struct.unpack_from("<I", d, off + 8)[0]
            p = off + 12
            e = {"redirect": target}
        else:
            cl, bl = struct.unpack_from("<II", d, off + 8)
            p = off + 16
            e = {"mime": self.mimes[mime], "cluster": cl, "blob": bl, "redirect": None}
        u = d.index(b"\0", p)
        t = d.index(b"\0", u + 1)
        e["ns"], e["url"], e["title"] = ns, d[p:u].decode(), d[u + 1:t].decode()
        return e

    def blob(self, e):
        if e["redirect"] is not None:
            return self.blob(self.entries[e["redirect"]])
        cl = e["cluster"]
        if cl not in self._cache:
            start = struct.unpack_from("<Q", self.data, self.cluster_ptr + 8 * cl)[0]
            end = (struct.unpack_from("<Q", self.data, self.cluster_ptr + 8 * (cl + 1))[0]
                   if cl + 1 < self.cluster_count else self.checksum_pos)
            raw = self.data[start:end]
            info, body = raw[0], raw[1:]
            comp = info & 0x0F
            if comp == 4:
                body = lzma.decompress(body)
            else:
                assert comp in (0, 1), f"unexpected compression {comp}"
            w = 8 if info & 0x10 else 4
            fmt = "<Q" if w == 8 else "<I"
            first = struct.unpack_from(fmt, body, 0)[0]
            n = first // w - 1
            offs = [struct.unpack_from(fmt, body, w * i)[0] for i in range(n + 1)]
            self._cache[cl] = [body[offs[i]:offs[i + 1]] for i in range(n)]
        return self._cache[cl][e["blob"]]

    def get(self, url, ns="C"):
        return self.blob(self.by_key[(ns, url)])


def check_links(z):
    """Every relative link in every HTML entry must resolve to an entry."""
    bad, total = [], 0
    attr = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
    for e in z.entries:
        if e["ns"] != "C" or e["redirect"] is not None or not e["mime"].startswith("text/html"):
            continue
        html = z.blob(e).decode("utf-8")
        for m in attr.finditer(html):
            v = m.group(1).replace("&amp;", "&")
            if v.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(e["url"]), unquote(v.split("#")[0])))
            total += 1
            if ("C", target) not in z.by_key:
                bad.append((e["url"], v, target))
    return total, bad


if __name__ == "__main__":
    z = Zim(sys.argv[1])
    print(f"OK: ZIM {z.major}.{z.minor}, {z.entry_count} entries, {z.cluster_count} clusters, checksum valid")
    for e in z.entries:
        print(f"  {e['ns']}/{e['url']}  [{e.get('mime', 'redirect')}]")
    total, bad = check_links(z)
    print(f"links checked: {total}, unresolved: {len(bad)}")
    for b in bad:
        print("  BAD", b)
