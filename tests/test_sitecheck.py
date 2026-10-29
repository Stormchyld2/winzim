"""Tests for the pre-flight site check, against local servers.

Run from the repo root:  python tests/test_sitecheck.py
"""
import http.server
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sitecheck import check_site, parse_robots_blocks_all  # noqa: E402

LINKS = "".join(f'<a href="/p{i}">Page {i}</a> ' for i in range(12))
PAGES = {
    "/ok": (200, {}, f"<html><head><title>Docs</title></head><body><h1>Welcome</h1><p>{'Real content. ' * 60}</p>{LINKS}</body></html>"),
    "/401": (401, {"WWW-Authenticate": 'Basic realm="x"'}, "Unauthorized"),
    "/cf": (403, {"Server": "cloudflare", "CF-RAY": "abc"}, "<html><head><title>Just a moment...</title></head><body>Checking your browser before accessing</body></html>"),
    "/login": (200, {}, '<html><head><title>Sign in</title></head><body><form><input type="text" name="u"><input type="password" name="p"></form><a href="/forgot">Forgot?</a></body></html>'),
    "/js": (200, {}, '<html><head><title>App</title></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'),
    "/404x": (404, {}, "<html><body>Nothing here</body></html>"),
    "/captcha": (200, {}, '<html><body><div class="g-recaptcha"></div><script src="https://www.google.com/recaptcha/api.js"></script></body></html>'),
}


def make_handler(robots):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/robots.txt":
                code, hdr, body = (200, {}, robots) if robots else (404, {}, "no")
            elif self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            else:
                code, hdr, body = PAGES.get(self.path, (404, {}, "nf"))
            data = body.encode()
            self.send_response(code)
            for k, v in hdr.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H


def serve(robots=None):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(robots))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_address[1]}"


def expect(base, path, level, engine="zimit", contains=None):
    r = check_site(base + path, engine)
    ok = r.level == level and (contains is None or contains.lower() in (r.summary + " ".join(r.details)).lower())
    print(("PASS" if ok else "FAIL"), f"{path:9} [{engine}] -> {r.level}: {r.summary}")
    assert ok, (path, engine, r)


def main():
    a, base = serve()
    expect(base, "/ok", "ok")
    expect(base, "/401", "bad", contains="password")
    expect(base, "/cf", "bad", contains="cloudflare")
    expect(base, "/redir", "bad", contains="sign-in")
    expect(base, "/login", "bad", contains="login form")
    expect(base, "/js", "ok", engine="zimit")
    expect(base, "/js", "warn", engine="builtin", contains="Cindrix Recon engine")
    expect(base, "/404x", "bad", contains="404")
    expect(base, "/captcha", "warn", contains="captcha")
    b, base_b = serve(robots="User-agent: *\nDisallow: /\n")
    expect(base_b, "/ok", "warn", contains="robots.txt")
    # connection refused
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = check_site(f"http://127.0.0.1:{port}/", "zimit")
    assert r.level == "bad", r
    print("PASS refused  ->", r.level, r.summary)
    # robots parser
    assert parse_robots_blocks_all("User-agent: *\nDisallow: /")
    assert not parse_robots_blocks_all("User-agent: *\nDisallow: /admin")
    assert not parse_robots_blocks_all("User-agent: Googlebot\nDisallow: /")
    assert not parse_robots_blocks_all("User-agent: *\nDisallow:")
    print("ALL SITECHECK TESTS PASSED")


if __name__ == "__main__":
    main()
