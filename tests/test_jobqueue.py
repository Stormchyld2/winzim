"""Job queue: order, failures, cancel, pause, saving, and the real job kinds. Run: python tests/test_jobqueue.py"""
import http.server, os, shutil, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import books, bookzim, jobqueue, prefs
import test_books
from jobqueue import JobQueue

fails = []
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (" " + str(extra) if extra and not ok else ""))
    if not ok: fails.append(name)
def wait(q, secs=30):
    t0 = time.time()
    while q.running and time.time() - t0 < secs: time.sleep(0.05)
    return not q.running

root = tempfile.mkdtemp()
try:
    # ---- ordering, failure isolation, persistence (fake runner)
    order, gate = [], threading.Event()
    def runner(job, emit, cancel):
        order.append(job["params"]["n"])
        emit({"type": "phase", "text": f"working {job['params']['n']}"})
        if job["params"].get("fail"): raise RuntimeError("boom")
        if job["params"].get("block"):
            while not cancel.is_set() and not gate.is_set(): time.sleep(0.02)
            if cancel.is_set(): raise RuntimeError("Cancelled")
        return "ok " + str(job["params"]["n"])
    path = os.path.join(root, "q.json"); changes = []
    q = JobQueue(path, runner, lambda: changes.append(1))
    for n in (1, 2, 3):
        q.add("site", {"url": f"http://x/{n}", "n": n, **({"fail": True} if n == 2 else {})})
    check("nothing runs until started", order == [] and q.jobs[0]["status"] == "queued")
    q.start(); wait(q)
    check("runs one after another in order", order == [1, 2, 3], order)
    check("a failed job does not stop the rest", [j["status"] for j in q.jobs] == ["done", "failed", "done"] and q.jobs[1]["message"] == "boom", [j["status"] for j in q.jobs])
    check("results kept", q.jobs[0]["message"] == "ok 1")
    check("failures listed", q.failures() == ["Site to ZIM: http://x/2: boom"], q.failures())
    q2 = JobQueue(path, runner)
    check("saved and loaded", [j["status"] for j in q2.jobs] == ["done", "failed", "done"])
    q.retry(q.jobs[1]["id"]); check("retry requeues", q.jobs[1]["status"] == "queued")
    q.clear_finished(); check("clear finished keeps the waiting one", [j["params"]["n"] for j in q.jobs] == [2])

    # ---- move / remove
    q = JobQueue(os.path.join(root, "q2.json"), runner)
    ids = [q.add("site", {"url": f"u{n}", "n": n})["id"] for n in (1, 2, 3)]
    q.move(ids[2], -2); check("move up", [j["params"]["n"] for j in q.jobs] == [3, 1, 2])
    q.move(ids[2], 5); check("move clamps", [j["params"]["n"] for j in q.jobs] == [1, 2, 3])
    q.remove(ids[1]); check("remove", [j["params"]["n"] for j in q.jobs] == [1, 3])

    # ---- pause finishes the current job only; cancel stops it
    order.clear(); gate.clear()
    q = JobQueue(os.path.join(root, "q3.json"), runner)
    q.add("site", {"url": "a", "n": 1, "block": True}); q.add("site", {"url": "b", "n": 2})
    q.start(); time.sleep(0.3)
    check("running status", q.jobs[0]["status"] == "running" and q.current is q.jobs[0])
    check("cannot remove a running job", q.remove(q.jobs[0]["id"]) is False)
    q.pause(); gate.set(); wait(q)
    check("pause: current finished, next not started", [j["status"] for j in q.jobs] == ["done", "queued"] and order == [1], order)
    gate.clear(); q.jobs[1]["params"]["block"] = True
    q.start(); time.sleep(0.3); q.cancel_current(); wait(q)
    check("cancel marks cancelled", q.jobs[1]["status"] == "cancelled", q.jobs[1])

    # ---- closed while running -> interrupted
    open(os.path.join(root, "q4.json"), "w").write('{"jobs":[{"id":"x","kind":"site","params":{"url":"u"},"status":"running","title":"t","message":""}]}')
    q = JobQueue(os.path.join(root, "q4.json"), runner)
    check("interrupted after a crash", q.jobs[0]["status"] == "interrupted")
    q.retry("x"); check("interrupted can be retried", q.jobs[0]["status"] == "queued")

    # ---- real job kinds
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), test_books.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    orig = books.get_json
    books.get_json = lambda url, timeout=15: test_books.fix_base(orig(url, timeout), base)
    books.BASES.update({"openlibrary": base + "/ol", "archive": base + "/ia", "gutendex": base + "/gut", "google": base + "/gb", "oapen": base + "/oapen"})
    prefs.reset()
    out = os.path.join(root, "books"); zim = os.path.join(root, "out.zim"); got = os.path.join(root, "back")
    real = JobQueue(os.path.join(root, "real.json"))
    real.add("books_site", {"url": base + "/", "folder": out, "formats": ["PDF", "EPUB"], "pref": "PDF first", "max_pages": 50})
    real.add("books_find", {"query": "where there is no doctor", "folder": out, "pref": "EPUB first"})
    real.add("books_zim", {"folder": out, "out": zim, "name": "Q Library", "max_file_mb": 2000})
    real.add("extract", {"zim": zim, "folder": got})
    real.add("books_find", {"query": "zzzz qqqq unknown thing", "folder": out, "pref": "EPUB first"})
    real.add("site", {"url": base + "/", "out": os.path.join(root, "site.zim"), "engine": "builtin", "opts": {"max_pages": 10, "workers": 2, "bogus": 1}})
    real.start(); wait(real, 90)
    st = {j["kind"] + str(i): (j["status"], j["message"]) for i, j in enumerate(real.jobs)}
    check("books_site downloaded", real.jobs[0]["status"] == "done" and "books saved" in real.jobs[0]["message"], real.jobs[0])
    check("books_find downloaded", real.jobs[1]["status"] == "done" and "1 books saved" in real.jobs[1]["message"], real.jobs[1])
    check("books_zim built", real.jobs[2]["status"] == "done" and os.path.exists(zim), real.jobs[2])
    check("extract took them back out", real.jobs[3]["status"] == "done" and "files taken out" in real.jobs[3]["message"], real.jobs[3])
    check("nothing found is a clear failure", real.jobs[4]["status"] == "failed" and "No free copy" in real.jobs[4]["message"], real.jobs[4])
    check("site job built a ZIM (unknown option ignored)", real.jobs[5]["status"] == "done" and os.path.exists(os.path.join(root, "site.zim")), real.jobs[5])
    srv.shutdown()
finally:
    shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASSED" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
