"""Finding docker.exe: Settings choice first, then PATH, then the usual Windows install places."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["APPDATA"] = tempfile.mkdtemp()
import zimit_engine as z, prefs
d = tempfile.mkdtemp(); fake = os.path.join(d, "docker.exe"); open(fake, "w").write("x")
os.environ["PATH"] = ""
prefs.set("tool_docker", fake); assert z.find_docker() == fake, "chosen path used"
prefs.set("tool_docker", os.path.join(d, "nope.exe")); assert z.find_docker() is None, "bad chosen path is not silently replaced"
prefs.set("tool_docker", "")
os.environ["LOCALAPPDATA"] = d; os.environ["ProgramFiles"] = d
c = z.docker_candidates()
assert any("Programs" in x and "DockerDesktop" in x for x in c), "per-user install checked"
assert any(x.startswith(d) and "Docker\\Docker" in x for x in c), "ProgramFiles variable honoured"
import time
slow = os.path.join(d, "docker"); open(slow, "w").write("#!/bin/sh\nexec /bin/sleep 30\n"); os.chmod(slow, 0o755)
prefs.set("tool_docker", slow); z.INFO_TIMEOUT_S = 2
t0 = time.time(); st = z.docker_status()
assert st[0] == "not_running" and time.time() - t0 < 6, "a hung docker info must not block the check"
assert "no answer" in z.LAST_ERROR[0], (st, z.LAST_ERROR)
prefs.set("tool_docker", "")
# waking: not running at first, ready after a few checks; already-ready and missing are left alone
calls = []
seq = iter([("not_running", "/x/docker"), ("not_running", "/x/docker"), ("ready", "/x/docker")])
msgs = []
r = z.wake_docker(msgs.append, status=lambda: next(seq), poll_s=0)
assert r == "ready" and any("ready" in m for m in msgs), (r, msgs)
assert z.wake_docker(status=lambda: ("ready", "/x"), poll_s=0) == "ready"
assert z.wake_docker(status=lambda: ("missing", None), poll_s=0) == "missing"
# never comes up: gives up
assert z.wake_docker(status=lambda: ("not_running", "/x/docker"), poll_s=0, timeout_s=0.05) == "not_running"
print("ALL PASSED")
