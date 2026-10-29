"""Run a list of jobs with at most N at the same time (1 - 6); the rest wait in line and start as others finish."""
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_AT_ONCE = 6


def run_jobs(items, worker, at_once, cancel=None, on_state=None):
    """worker(index, item) -> any result, or raises. Calls on_state(index, state, info) with state in
    queued / running / done / failed / skipped. Returns [(state, info)] in the order of `items`."""
    cancel = cancel or threading.Event()
    at_once = max(1, min(MAX_AT_ONCE, int(at_once or 1)))
    n = len(items)
    results = [("queued", None)] * n
    lock = threading.Lock()

    def note(i, state, info=None):
        with lock:
            results[i] = (state, info)
        if on_state:
            on_state(i, state, info)

    def task(i):
        if cancel.is_set():
            note(i, "skipped", "Cancelled")
            return
        note(i, "running")
        try:
            note(i, "done", worker(i, items[i]))
        except Exception as e:  # one failed job never stops the others
            if cancel.is_set() and str(e) == "Cancelled":
                note(i, "skipped", "Cancelled")
            else:
                note(i, "failed", str(e) or type(e).__name__)

    for i in range(n):
        note(i, "queued")
    with ThreadPoolExecutor(max_workers=min(at_once, max(1, n))) as ex:
        for i in range(n):
            ex.submit(task, i)
    return results
