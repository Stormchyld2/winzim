"""Tests for the Zimit (Docker) engine wiring.

Uses a *fake* `docker` executable that imitates Zimit's output, so this checks
Cindrix Recon's orchestration (command line, progress parsing, output handling,
cancel, errors) - not Zimit itself. POSIX only for the fake-docker part.

Run from the repo root:  python tests/test_zimit_engine.py
"""
import json
import os
import stat
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import Options  # noqa: E402
from zimit_engine import build_command, parse_line, run_zimit  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
a = sys.argv[1:]
d = os.environ["FAKE_DIR"]
mode = os.environ.get("FAKE_MODE", "ok")
open(os.path.join(d, "calls.log"), "a").write(" ".join(a) + "\n")
if a[0] in ("info", "image"):
    sys.exit(0)
if a[0] == "pull":
    print("Pulling from openzim/zimit"); sys.exit(0)
if a[0] == "kill":
    open(os.path.join(d, "killed"), "w").write("1"); sys.exit(0)
if a[0] == "run":
    mount = a[a.index("-v") + 1].rsplit(":/output", 1)[0]
    name = a[a.index("--name", a.index("zimit")) + 1]
    def j(msg, ctx="general", **det):
        print(json.dumps({"timestamp": "t", "logLevel": "info", "context": ctx, "message": msg, "details": det}), flush=True)
    if mode == "reject" and ("--blockRules" in a or "--depth" in a):
        print("error: unknown argument --blockRules", flush=True)
        sys.exit(2)
    j("Crawl status", "crawlStatus", crawled=1, total=3, pending=2, failed=0)
    j("Page Finished", "pageStatus", page="https://example.com/")
    j("Page Load Failed", "pageStatus", page="https://example.com/bad")
    if mode == "slow":
        while not os.path.exists(os.path.join(d, "killed")):
            time.sleep(0.1)
        sys.exit(137)
    j("Crawl status", "crawlStatus", crawled=3, total=3, pending=0, failed=1)
    if mode == "limit":
        j("Crawl page limit reached, stopping")
    print("Starting warc2zim conversion")
    if mode == "nozim":
        sys.exit(1)
    open(os.path.join(mount, name + "_2026-10.zim"), "wb").write(b"ZIMDATA" * 100)
    sys.exit(0)
'''


def test_pure():
    o = Options(workers=3, max_pages=50, max_mb=10, max_minutes=2, path_scope=True)
    cmd = build_command("docker", "c1", "/w", "https://a.org/x/", "n", o)
    s = " ".join(cmd)
    for frag in ("--seeds https://a.org/x/", "--name n", "--output /output", "--workers 3", "--pageLimit 50",
                 f"--sizeLimit {10 * 1024 * 1024}", "--timeLimit 120", "--scopeType prefix", "-v /w:/output"):
        assert frag in s, frag
    s2 = " ".join(build_command("docker", "c", "/w", "https://a.org", "n", Options(max_pages=0, max_mb=0, subdomains=True)))
    assert "--pageLimit" not in s2 and "--sizeLimit" not in s2 and "--timeLimit" not in s2
    assert "--scopeType domain" in s2
    ev = parse_line(json.dumps({"context": "crawlStatus", "message": "Crawl statistics",
                                "details": {"crawled": 5, "total": 9, "pending": 2, "failed": 1}}))
    assert ev == [{"type": "stats", "crawled": 5, "total": 9, "pending": 2, "failed": 1}], ev
    ev = parse_line(json.dumps({"message": "Page Finished", "details": {"page": "https://x.org/a"}}))
    assert ev[0] == {"type": "row", "status": "Saved", "url": "https://x.org/a"}
    assert parse_line("not json {")[0]["type"] == "log"
    assert parse_line("") == []
    o = Options(skip_images=True, skip_fonts=True, depth=3)
    cmd = build_command("docker", "c", "/w", "https://a.org", "n", o)
    assert cmd[cmd.index("--depth") + 1] == "3"
    rules = [json.loads(cmd[i + 1]) for i, x in enumerate(cmd) if x == "--blockRules"]
    assert len(rules) == 1 and rules[0]["type"] == "block"
    import re
    rx = re.compile(rules[0]["url"])
    assert rx.search("https://a.org/x/pic.PNG".lower()) and rx.search("https://a.org/f.woff2?v=1")
    assert not rx.search("https://a.org/page.html") and not rx.search("https://a.org/video.mp4")
    assert "--blockRules" not in build_command("docker", "c", "/w", "https://a.org", "n", o, extras=False)
    assert "--blockRules" not in build_command("docker", "c", "/w", "https://a.org", "n", Options())
    ev = parse_line(json.dumps({"message": "Crawl size limit reached, interrupting", "details": {}}))
    assert {"type": "limit", "which": "size"} in ev, ev
    assert parse_line("nothing to see")[0]["type"] == "log" and len(parse_line("nothing to see")) == 1
    print("pure tests ok")


def run_fake(mode, cancel_after=None, opts=None):
    tmp = tempfile.mkdtemp()
    fake_dir = os.path.join(tmp, "fake")
    os.makedirs(fake_dir)
    exe = os.path.join(fake_dir, "docker")
    open(exe, "w").write(FAKE)
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
    os.environ["PATH"] = fake_dir + os.pathsep + os.environ["PATH"]
    os.environ["FAKE_DIR"] = fake_dir
    os.environ["FAKE_MODE"] = mode
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir)
    out = os.path.join(out_dir, "site.zim")
    events, cancel = [], threading.Event()
    if cancel_after:
        threading.Timer(cancel_after, cancel.set).start()
    try:
        res = run_zimit("https://example.com", out, opts or Options(workers=2), events.append, cancel)
        return res, events, out, out_dir, None
    except RuntimeError as e:
        return None, events, out, out_dir, str(e)


def test_fake_docker():
    res, ev, out, out_dir, err = run_fake("ok")
    assert err is None, err
    assert os.path.exists(out) and res["size"] == 700, res
    assert res["pages"] == 3 and res["failed"] == 1, res
    assert os.listdir(out_dir) == ["site.zim"], os.listdir(out_dir)   # work dir cleaned up
    kinds = {e["type"] for e in ev}
    assert {"stats", "row", "log", "phase"} <= kinds, kinds
    assert any(e.get("status") == "Failed" for e in ev if e["type"] == "row")
    assert any("Building ZIM" in e.get("text", "") for e in ev if e["type"] == "phase")
    print("fake docker: success path ok")

    res, ev, out, out_dir, err = run_fake("reject", opts=Options(workers=2, skip_images=True, depth=2))
    assert err is None and res["size"] == 700, err
    assert any(e["type"] == "warn" for e in ev), "no warning after retry"
    calls = open(os.path.join(os.environ["FAKE_DIR"], "calls.log")).read().splitlines()
    runs = [c for c in calls if c.startswith("run")]
    assert len(runs) == 2 and "--blockRules" in runs[0] and "--blockRules" not in runs[1], runs
    print("fake docker: rejected-options retry ok")

    res, ev, out, out_dir, err = run_fake("limit")
    assert res["limit_hit"] == "pages", res
    res, ev, out, out_dir, err = run_fake("ok", opts=Options(workers=2, max_pages=3))
    assert res["limit_hit"] == "pages", res
    res, ev, out, out_dir, err = run_fake("ok")
    assert res["limit_hit"] is None, res
    print("fake docker: limit detection ok")

    res, ev, out, out_dir, err = run_fake("nozim")
    assert err and "without creating a ZIM" in err and not os.path.exists(out)
    print("fake docker: no-output error path ok")

    res, ev, out, out_dir, err = run_fake("slow", cancel_after=1.5)
    assert err == "Cancelled", err
    assert os.listdir(out_dir) == [], os.listdir(out_dir)
    print("fake docker: cancel path ok")


def test_no_docker():
    os.environ["PATH"] = "/nonexistent"
    try:
        run_zimit("https://example.com", "/tmp/x.zim", Options(), lambda e: None, threading.Event())
    except RuntimeError as e:
        assert "Docker Desktop is required" in str(e)
        print("missing-docker message ok")
        return
    raise AssertionError("expected error")


if __name__ == "__main__":
    test_pure()
    if os.name == "posix":
        test_fake_docker()
        test_no_docker()
    print("ALL ZIMIT ENGINE TESTS PASSED")
