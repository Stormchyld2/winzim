"""Pre-flight check: will this site likely archive, or does it need a login /
block downloaders? Pure standard library.

Result levels: 'ok' (green check), 'warn' (amber, will probably be incomplete),
'bad' (red X, likely to fail). It's a best-effort heuristic: green means "no
obvious blockers found", not a guarantee.
"""
import gzip
import html as htmllib
import re
import socket
import ssl
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from crawler import UA as BUILTIN_UA, decode_bytes, to_ascii

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
LEVEL_RANK = {"ok": 0, "warn": 1, "bad": 2}

SSO_HOSTS = ("accounts.google.com", "login.microsoftonline.com", "login.live.com", "okta.com",
             "auth0.com", "login.salesforce.com", "appleid.apple.com", "id.atlassian.com",
             "sso.", "login.", "auth.", "signin.")
LOGIN_PATH_RE = re.compile(r"/(log[-_]?in|sign[-_]?in|signin|sso|oauth2?|auth(?:enticate)?|account/login|users?/sign_in)(?:[/?.]|$)", re.I)
STRONG_BLOCK = ("just a moment...", "cf-chl", "attention required! | cloudflare", "checking your browser",
                "enable javascript and cookies to continue", "px-captcha", "verify you are human",
                "are you a robot", "unusual traffic from your computer", "you have been blocked",
                "access denied", "request blocked", "ddos protection by", "pardon our interruption",
                "human verification", "security check to access")


@dataclass
class CheckResult:
    level: str
    summary: str
    details: list = field(default_factory=list)


def _visible_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", page)
    page = re.sub(r"(?s)<!--.*?-->", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", htmllib.unescape(page)).strip()


def analyze(start_url, status, final_url, headers, body, engine="zimit"):
    """Pure analysis of one fetched page. headers: dict (lower-case keys). Returns list of (level, text)."""
    f = []
    page = body or ""
    low_title = ""
    tm = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    if tm:
        low_title = re.sub(r"\s+", " ", htmllib.unescape(tm.group(1))).strip().lower()
    text = _visible_text(page)
    low_text = text.lower()
    server = headers.get("server", "").lower()
    cf = "cloudflare" in server or "cf-ray" in headers

    # --- HTTP status
    if status == 401 or "www-authenticate" in headers:
        f.append(("bad", "The site asks for a username and password (HTTP 401)."))
    elif status in (403, 429, 451, 503) or (status and status >= 400):
        msg = {403: "The site blocked the request (HTTP 403).",
               429: "The site is rate-limiting requests (HTTP 429).",
               451: "The page is unavailable for legal reasons (HTTP 451).",
               503: "The site is unavailable or showing a bot check (HTTP 503).",
               404: "Page not found (HTTP 404). Check the address.",
               410: "The page is gone (HTTP 410)."}.get(status, f"The site returned an error (HTTP {status}).")
        f.append(("bad", msg))
        if cf and status in (403, 503):
            f.append(("bad", "It's behind Cloudflare's bot protection."))

    # --- bot-wall / challenge pages
    haystack = (low_title + " " + low_text[:6000])
    hit = next((m for m in STRONG_BLOCK if m in haystack), None)
    if hit and (len(text) < 5000 or status in (403, 429, 503)):
        f.append(("bad", "The page looks like a bot check / access-denied screen."))
    elif re.search(r"(?i)(recaptcha|hcaptcha|captcha-delivery|cf-turnstile)", page) and len(text) < 1500:
        f.append(("warn", "A captcha may be shown to automated visitors."))

    # --- login walls
    fp = urlsplit(final_url or start_url)
    sp = urlsplit(start_url)
    redirected_to_login = (
        (fp.netloc != sp.netloc and any(fp.hostname and (fp.hostname == h or fp.hostname.startswith(h) or fp.hostname.endswith("." + h) or h in fp.hostname)
                                         for h in SSO_HOSTS))
        or (LOGIN_PATH_RE.search(fp.path or "") and not LOGIN_PATH_RE.search(sp.path or "")))
    if redirected_to_login:
        f.append(("bad", "The site redirects to a sign-in page, so a login is required."))
    has_pw = re.search(r"""(?i)<input[^>]+type\s*=\s*["']?password""", page) is not None
    links = len(re.findall(r"(?i)<a\s[^>]*href=", page))
    if has_pw and links < 5 and not redirected_to_login:
        f.append(("bad", "The page is just a login form."))
    elif has_pw and re.search(r"(?i)(sign in|log in|login)", low_title):
        f.append(("warn", "The page has a login form; content behind it can't be archived."))

    # --- JavaScript-only shells
    scripts = len(re.findall(r"(?i)<script\b", page))
    if status and status < 400 and len(text) < 300 and scripts >= 1 and not f:
        if engine == "zimit":
            f.append(("ok", "Little text in the raw HTML (JavaScript site), but Zimit renders JavaScript."))
        else:
            f.append(("warn", "The page is almost empty without JavaScript. Use the Zimit engine for this site."))
    return f


def parse_robots_blocks_all(txt):
    star, blocks = False, False
    for raw in txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            star = (v == "*")
        elif star and k == "disallow" and v == "/":
            blocks = True
        elif star and k == "allow" and v not in ("", "/") and blocks:
            blocks = False
    return blocks


def _get(url, ua, timeout, limit):
    req = urllib.request.Request(to_ascii(url), headers={
        "User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl(), dict((k.lower(), v) for k, v in r.headers.items()), _read(r, limit)
    except urllib.error.HTTPError as e:
        try:
            body = _read(e, limit)
        except Exception:
            body = b""
        return e.code, url, dict((k.lower(), v) for k, v in e.headers.items()), body


def _read(r, limit):
    data = r.read(limit)
    enc = (r.headers.get("Content-Encoding") or "").lower()
    try:
        if enc == "gzip":
            data = gzip.decompress(data)
        elif enc == "deflate":
            data = zlib.decompress(data)
    except Exception:
        pass
    return data


def check_site(url, engine="zimit", timeout=15):
    """Fetch the start page (and robots.txt) and return a CheckResult."""
    ua = CHROME_UA if engine == "zimit" else BUILTIN_UA
    try:
        status, final, headers, raw = _get(url, ua, timeout, 400_000)
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
            return CheckResult("bad", "The site's security certificate isn't valid.", [str(reason)])
        if isinstance(reason, socket.gaierror):
            return CheckResult("bad", "Can't find that website. Check the address and your internet connection.", [str(reason)])
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return CheckResult("warn", "The site didn't answer within 15 seconds.", [str(reason)])
        return CheckResult("bad", "Couldn't connect to the site.", [str(reason)])
    except (socket.timeout, TimeoutError):
        return CheckResult("warn", "The site didn't answer within 15 seconds.")
    except Exception as e:
        return CheckResult("bad", "Couldn't check the site.", [str(e)[:150]])

    ctype = headers.get("content-type", "")
    cm = re.search(r"charset=([-\w]+)", ctype, re.I)
    body = decode_bytes(raw, cm.group(1) if cm else None)
    findings = analyze(url, status, final, headers, body, engine)

    # robots.txt (informational)
    try:
        p = urlsplit(final or url)
        rs, _, _, rraw = _get(f"{p.scheme}://{p.netloc}/robots.txt", ua, 8, 100_000)
        if rs == 200 and parse_robots_blocks_all(rraw.decode("utf-8", "replace")):
            findings.append(("warn", "robots.txt asks automated tools not to crawl this site."))
    except Exception:
        pass

    if not any(l in ("warn", "bad") for l, _ in findings):
        links = len(re.findall(r"(?i)<a\s[^>]*href=", body))
        extra = [t for l, t in findings if l == "ok"]
        return CheckResult("ok", "Looks good: the page loads without a login and no blocking was detected.",
                           [f"HTTP {status}, about {links} links found on the first page."] + extra)
    worst = max(findings, key=lambda x: LEVEL_RANK[x[0]])[0]
    first = next(t for l, t in findings if l == worst)
    return CheckResult(worst, first, [t for _, t in findings])
