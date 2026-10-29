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
print("ALL PASSED")
