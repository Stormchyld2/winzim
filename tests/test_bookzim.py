"""Books -> ZIM: catalog site, in-ZIM readers (EPUB, MOBI, AZW3, TXT, FB2, CBZ, RTF). Run: python tests/test_bookzim.py"""
import base64, io, os, shutil, struct, sys, tempfile, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import bookread, bookzim
from verify_zim import Zim, check_links

PNG = bookzim.make_icon_png()
fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)

def make_epub(path, title="Test Epub", author="Ann Author"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", f'''<package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf"><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:date>2001-05-01</dc:date><dc:language>en</dc:language><dc:description>A short test book.</dc:description><meta name="cover" content="cov"/></metadata><manifest><item id="cov" href="images/cover.png" media-type="image/png"/><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="Text/ch 2.xhtml" media-type="application/xhtml+xml"/><item id="css" href="s.css" media-type="text/css"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine></package>''')
        z.writestr("OEBPS/toc.ncx", '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint><navLabel><text>First chapter</text></navLabel><content src="ch1.xhtml"/></navPoint><navPoint><navLabel><text>Second chapter</text></navLabel><content src="Text/ch%202.xhtml"/></navPoint></navMap></ncx>')
        z.writestr("OEBPS/ch1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="s.css"/><script>alert(1)</script></head><body><h1>One</h1><img src="images/cover.png"/><a href="Text/ch%202.xhtml">next</a></body></html>')
        z.writestr("OEBPS/Text/ch 2.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Two</p></body></html>')
        z.writestr("OEBPS/s.css", "body{color:red}")
        z.writestr("OEBPS/images/cover.png", PNG)

def lz_literal(b):  # PalmDOC literal-only encoding
    out = bytearray()
    for i in range(0, len(b), 8):
        chunk = b[i:i + 8]
        out.append(len(chunk)); out += chunk
    return bytes(out)

def make_mobi(path, html, compress, version=6, images=(), drm=False):
    text = html.encode("utf-8")
    recs_text = [text[i:i + 4096] for i in range(0, len(text), 4096)]
    comp_recs = [lz_literal(r) if compress else r for r in recs_text]
    n_text = len(comp_recs)
    first_img = n_text + 1 if images else 0xFFFFFFFF
    r0 = bytearray(struct.pack(">HHIHHH", 2 if compress else 1, 0, len(text), n_text, 4096, 2 if drm else 0) + b"\0\0")
    mobi = bytearray(232)
    mobi[0:4] = b"MOBI"; struct.pack_into(">I", mobi, 4, 232); struct.pack_into(">I", mobi, 12, 65001)
    struct.pack_into(">I", mobi, 20, version); struct.pack_into(">I", mobi, 92, first_img)
    r0 += mobi
    recs = [bytes(r0)] + comp_recs + list(images)
    head = bytearray(78)
    head[60:68] = b"BOOKMOBI"; struct.pack_into(">H", head, 76, len(recs))
    off = 78 + 8 * len(recs) + 2
    table = b""
    for i, r in enumerate(recs):
        table += struct.pack(">II", off, i * 2); off += len(r)
    open(path, "wb").write(bytes(head) + table + b"\0\0" + b"".join(recs))

root = tempfile.mkdtemp()
try:
    lib = os.path.join(root, "lib"); os.makedirs(os.path.join(lib, "Survival")); os.makedirs(os.path.join(lib, "Radio"))
    make_epub(os.path.join(lib, "Survival", "test-epub.epub"))
    make_mobi(os.path.join(lib, "Survival", "Old Book.mobi"), "<html><body><h1>Chapter A</h1><p>Hello mobi world.</p><mbp:pagebreak/><h1>Chapter B</h1><p>More. <img recindex=\"00001\"></p></body></html>", True, images=[PNG])
    kf = "<html><head></head><body><h1>K One</h1><p>kindle text</p><img src=\"kindle:embed:0001?mime=image/png\"></body></html><html><body><h1>K Two</h1><a href=\"kindle:pos:fid:0001:off:0\">x</a></body></html>"
    make_mobi(os.path.join(lib, "Radio", "Kindle Book.azw3"), kf, False, version=8, images=[PNG])
    make_mobi(os.path.join(lib, "Radio", "Locked.mobi"), "<html><body>secret</body></html>", False, drm=True)
    open(os.path.join(lib, "Radio", "notes.txt"), "w").write("Para one.\n\nPara two with <b>tags</b> & stuff.\n")
    open(os.path.join(lib, "Radio", "story.rtf"), "w").write(r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Hello \b RTF\b0 world\par Second \'e9 line\par}")
    fb2 = '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink"><body><section><title><p>Sec One</p></title><p>fb2 <emphasis>text</emphasis></p><image l:href="#im1"/></section></body><binary id="im1" content-type="image/png">' + base64.b64encode(PNG).decode() + '</binary></FictionBook>'
    open(os.path.join(lib, "Radio", "tale.fb2"), "w").write(fb2)
    with zipfile.ZipFile(os.path.join(lib, "Radio", "comic.cbz"), "w") as z:
        for n in ("page10.png", "page2.png", "page1.png"): z.writestr(n, PNG)
    open(os.path.join(lib, "Radio", "scan.djvu"), "wb").write(b"AT&TFORM" + b"\0" * 50)
    open(os.path.join(lib, "Survival", "Guide.pdf"), "wb").write(b"%PDF-1.4\n1 0 obj<</Title(Field Guide)/Author(Bob)>>endobj\n%%EOF")

    # --- converters
    r = bookread.make_readable("EPUB", os.path.join(lib, "Survival", "test-epub.epub"))
    check("epub spine+titles", [t for _, t in r.spine] == ["First chapter", "Second chapter"], r.spine)
    check("epub scripts removed", b"alert" not in r.files["OEBPS/ch1.xhtml"][1])
    check("epub files kept", "OEBPS/images/cover.png" in r.files and r.files["OEBPS/s.css"][0] == "text/css")
    m = bookread.make_readable("MOBI", os.path.join(lib, "Survival", "Old Book.mobi"))
    all_html = b"".join(v[1] for k, v in m.files.items() if k.endswith(".html"))
    check("mobi text (compressed)", b"Hello mobi world" in all_html and b"More." in all_html)
    check("mobi image extracted", any(k.endswith(".png") for k in m.files) and b"img00001.png" in all_html, list(m.files))
    a = bookread.make_readable("AZW3", os.path.join(lib, "Radio", "Kindle Book.azw3"))
    check("azw3 split in 2 + image", len(a.spine) == 2 and any(k.endswith(".png") for k in a.files), a.spine)
    check("azw3 dead links neutralised", b"kindle:" not in b"".join(v[1] for v in a.files.values()))
    try:
        bookread.make_readable("MOBI", os.path.join(lib, "Radio", "Locked.mobi")); check("DRM refused", False)
    except bookread.Unreadable as e:
        check("DRM refused", "DRM" in str(e), e)
    t = bookread.make_readable("TXT", os.path.join(lib, "Radio", "notes.txt"))
    check("txt escaped", b"&lt;b&gt;tags&lt;/b&gt; &amp; stuff" in t.files["part001.html"][1])
    rt = bookread.make_readable("RTF", os.path.join(lib, "Radio", "story.rtf"))
    rtt = rt.files["part001.html"][1].decode()
    check("rtf text", "Hello RTF" in rtt.replace("world", "") or "Hello" in rtt and "Arial" not in rtt and "é" in rtt, rtt)
    fb = bookread.make_readable("FB2", os.path.join(lib, "Radio", "tale.fb2"))
    check("fb2 text+image", b"fb2 <em>text</em>" in fb.files["part001.html"][1] and any(k.startswith("img_") for k in fb.files))
    cb = bookread.make_readable("CBZ", os.path.join(lib, "Radio", "comic.cbz"))
    check("cbz natural page order", [t for _, t in cb.spine] == ["Page 1", "Page 2", "Page 3"] and len(cb.spine) == 3)
    try:
        bookread.make_readable("DJVU", os.path.join(lib, "Radio", "scan.djvu")); check("djvu says what it needs", False)
    except bookread.Unreadable as e:
        check("djvu says what it needs", "DjVuLibre" in str(e), e)
    # helper programs (fake ones that behave like ddjvu / 7z / ebook-convert)
    import prefs, stat
    fake = os.path.join(root, "fake"); os.makedirs(fake)
    def script(name, body):
        p = os.path.join(fake, name); open(p, "w").write("#!/bin/sh\n" + body); os.chmod(p, 0o755); return p
    prefs.set("tool_ddjvu", script("ddjvu", 'for last; do :; done; printf "%%PDF-1.4 converted pages" > "$last"\n'))
    d = bookread.make_readable("DJVU", os.path.join(lib, "Radio", "scan.djvu"))
    check("djvu via DjVuLibre", d.spine == [("book.pdf", "Book")] and d.files["book.pdf"][1].startswith(b"%PDF"), d.spine)
    open(os.path.join(root, "rar.cbr"), "wb").write(b"Rar!\x1a\x07\x00" + b"0" * 50)
    prefs.set("tool_7z", script("7z", 'for a; do case "$a" in -o*) out="${a#-o}";; esac; done\nprintf PNGDATA > "$out/b2.png"; printf PNGDATA > "$out/b10.png"; printf PNGDATA > "$out/b1.png"\n'))
    c = bookread.make_readable("CBR", os.path.join(root, "rar.cbr"))
    check("cbr via 7-Zip, natural order", [t for _, t in c.spine] == ["Page 1", "Page 2", "Page 3"] and c.files["p0003.png"][1] == b"PNGDATA", c.spine)
    check("cbr that is really a zip needs no tool", len(bookread.make_readable("CBR", os.path.join(lib, "Radio", "comic.cbz")).spine) == 3)
    open(os.path.join(root, "b.lit"), "wb").write(b"ITOLITLS" + b"0" * 40)
    epub_src = os.path.join(lib, "Survival", "test-epub.epub")
    prefs.set("tool_calibre", script("ebook-convert", f'cp "{epub_src}" "$2"\n'))
    l = bookread.make_readable("LIT", os.path.join(root, "b.lit"))
    check("lit via Calibre", [t for _, t in l.spine] == ["First chapter", "Second chapter"], l.spine)
    prefs.set("tool_calibre", script("ebook-convert2", 'exit 3\n'))
    try: bookread.make_readable("LIT", os.path.join(root, "b.lit")); check("failed conversion reported", False)
    except bookread.Unreadable as e: check("failed conversion reported", "Calibre" in str(e), e)
    prefs.set("zim_readers", False)
    try: bookread.make_readable("EPUB", epub_src); check("readers switch off", False)
    except bookread.Unreadable: check("readers switch off", True)
    prefs.reset()
    for k in ("tool_ddjvu", "tool_7z", "tool_calibre"): prefs.set(k, "")
    # real back-reference: "abc" then copy dist 3 len 4 -> abcabca
    d = b"\x03abc" + struct.pack(">H", 0x8000 | (3 << 3) | (4 - 3))
    check("palmdoc back-reference", bookread.palmdoc_decompress(d) == b"abcabca", bookread.palmdoc_decompress(d))

    # --- scan + build
    books_ = bookzim.scan_folder(lib)
    names = {b["title"] for b in books_}
    check("scan metadata from epub/pdf", "Test Epub" in names and "Field Guide" in names, names)
    check("scan categories", {b["category"] for b in books_} == {"Survival", "Radio"})
    out = os.path.join(root, "lib.zim"); ev = []
    res = bookzim.build_books_zim(lib, out, "Test Library", ev.append)
    check("build result", res["books"] == len(books_) and res["readers"] == 7 and len(res["no_reader"]) == 2, res)
    z = Zim(out)
    urls = {e["url"] for e in z.entries if e["ns"] == "C"}
    check("index + detail + reader pages", "index.html" in urls and any(u.startswith("read/") and u.endswith(".html") and u.count("/") == 1 for u in urls))
    idx = z.get("index.html").decode()
    check("category dropdown with counts", "<select id=\"cat\">" in idx and "Radio (" in idx and "Survival (" in idx)
    check("no initials tile", 'class="tile"' not in idx)
    total, bad = check_links(z)
    check("all links resolve", not bad and total > 20, bad[:5])
    sh = next(u for u in urls if u.startswith("read/test-epub-epub") and u.endswith(".html") and u.count("/") == 1)
    shell = z.get(sh).decode()
    check("reader shell has chapters", "First chapter" in shell and "test-epub-epub/OEBPS/ch1.xhtml" in shell, shell[:200])
    check("epub chapter stored", "read/test-epub-epub/OEBPS/Text/ch 2.xhtml" in urls)
    check("cover stored", any(u.startswith("covers/") for u in urls))
    detail = z.get("books/test-epub.html").decode()
    check("detail has Read + Save", ">Read</a>" in detail and ">Save</a>" in detail)
    check("djvu note", "DJVU cannot be read inside Kiwix" in z.get("books/scan.html").decode())
    try:
        bookzim.build_books_zim(os.path.join(root, "nothing"), out + "2"); check("empty folder error", False)
    except RuntimeError as e:
        check("empty folder error", "No book files" in str(e), e)
finally:
    if os.environ.get('KEEP_ZIM') and os.path.exists(os.path.join(root, 'lib.zim')):
        shutil.copy(os.path.join(root, 'lib.zim'), os.environ['KEEP_ZIM'])
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
