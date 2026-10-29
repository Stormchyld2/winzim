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
if a[0] == "image" and mode == "noimage" and not os.path.exists(os.path.join(d, "pulled")):
    sys.exit(1)
if a[0] in ("info", "image"):
    sys.exit(0)
if a[0] == "pull":
    open(os.path.join(d, "pulled"), "w").write("1")
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
    if "--build" in a:
        bdir = os.path.join(mount, "build"); os.makedirs(bdir, exist_ok=True)
        open(os.path.join(bdir, "collected.warc"), "a").write("page\n")
        if mode == "interrupt" and not os.path.exists(os.path.join(d, "second_run")):
            open(os.path.join(d, "second_run"), "w").write("1")
            sys.exit(137)
    if mode == "unknownflag" and "--pageLimit" in a:
        print("usage: zimit [-h]\nzimit: error: unrecognized arguments: --pageLimit", flush=True)
        sys.exit(2)
    if mode == "oldimage" and ("--sizeSoftLimit" in a or "--timeSoftLimit" in a):
        print("zimit: error: unrecognized arguments: --sizeSoftLimit", flush=True)
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
                 f"--sizeSoftLimit {10 * 1024 * 1024}", "--timeSoftLimit 120", "--scopeType prefix", "-v /w:/output"):
        assert frag in s, frag
    old = " ".join(build_command("docker", "c1", "/w", "https://a.org/x/", "n", o, legacy=True))
    assert f"--sizeLimit {10 * 1024 * 1024}" in old and "--timeLimit 120" in old and "SoftLimit" not in old
    s2 = " ".join(build_command("docker", "c", "/w", "https://a.org", "n", Options(max_pages=0, max_mb=0, subdomains=True)))
    assert "--pageLimit" not in s2 and "--sizeSoftLimit" not in s2 and "--timeSoftLimit" not in s2
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


_LAST = {}


def run_fake(mode, cancel_after=None, opts=None, reuse=False):
    tmp = _LAST["tmp"] if reuse else tempfile.mkdtemp()
    _LAST["tmp"] = tmp
    fake_dir = os.path.join(tmp, "fake")
    os.makedirs(fake_dir, exist_ok=True)
    exe = os.path.join(fake_dir, "docker")
    open(exe, "w").write(FAKE)
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
    os.environ["PATH"] = fake_dir + os.pathsep + os.environ["PATH"]
    os.environ["FAKE_DIR"] = fake_dir
    os.environ["FAKE_MODE"] = mode
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)
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

    res, ev, out, out_dir, err = run_fake("oldimage", opts=Options(workers=2, max_mb=5, max_minutes=3))
    assert err is None and res["size"] == 700, err
    calls = open(os.path.join(os.environ["FAKE_DIR"], "calls.log")).read().splitlines()
    runs = [c for c in calls if c.startswith("run")]
    assert len(runs) == 2 and "--sizeSoftLimit" in runs[0] and "--sizeLimit" in runs[1] and "SoftLimit" not in runs[1], runs
    print("fake docker: older crawler names fallback ok")

    res, ev, out, out_dir, err = run_fake("unknownflag", opts=Options(workers=2, max_pages=9))
    assert err is None and res["size"] == 700, err
    assert any("--pageLimit" in e.get("text", "") for e in ev if e["type"] == "warn"), "no warning for dropped flag"
    print("fake docker: unknown option dropped ok")

    # default: look for a newer crawler every run; switched off: use the local copy; missing: always download
    import prefs
    def pulls():
        return [c for c in open(os.path.join(os.environ["FAKE_DIR"], "calls.log")).read().splitlines() if c.startswith("pull")]
    run_fake("ok"); assert pulls(), "default should check for a newer crawler"
    prefs.set("crawler_update", False)
    res, ev, out, out_dir, err = run_fake("ok")
    assert not pulls() and any("already on this computer" in e.get("text", "") for e in ev), "local copy should be used"
    res, ev, out, out_dir, err = run_fake("noimage")
    assert err is None and pulls(), "a missing crawler must be downloaded even with updates off"
    prefs.set("crawler_update", True)
    print("fake docker: crawler download rules ok")

    # resume: an interrupted run keeps its work folder; the next run uses it and cleans it up when done
    import zimit_engine as ze
    res, ev, out, out_dir, err = run_fake("interrupt")
    assert err and not os.path.exists(out), err
    assert ze.unfinished(out, "https://example.com/"), "interrupted work should be kept"
    calls = open(os.path.join(os.environ["FAKE_DIR"], "calls.log")).read().splitlines()
    assert any("--build /output/build" in c for c in calls if c.startswith("run")), calls
    res2, ev2, out2, _, err2 = run_fake("interrupt", reuse=True)
    assert err2 is None and res2["size"] == 700, err2
    assert any("Continuing an earlier" in e.get("text", "") for e in ev2), "no resume notice"
    assert not ze.unfinished(out2, "https://example.com/"), "work folder should be removed after success"
    print("fake docker: resume after interruption ok")

    # "start over" throws the kept work away
    res, ev, out, out_dir, err = run_fake("interrupt")
    # (second_run marker is per fake dir, so this run is interrupted again)
    assert err and ze.unfinished(out, "https://example.com")
    ze.discard_unfinished(out, "https://example.com")
    assert not ze.unfinished(out, "https://example.com")
    print("fake docker: start over discards kept work ok")

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
    assert [f for f in os.listdir(out_dir) if not f.startswith(".recon_resume_")] == [], os.listdir(out_dir)   # no ZIM, only kept work
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
