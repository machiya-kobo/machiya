"""Response and request safety for the rooms (v0.22, the sweep of 2026-10): one place for the rules every room needs.

Headers (send them on EVERY response, not only HTML):
  base_headers()            nosniff, X-Frame-Options SAMEORIGIN, Referrer-Policy same-origin: for JSON, text, CSS,
                            images, redirects (shell.security_headers() adds the CSP for HTML pages)
  asset_headers(name)       a vault file served from /a/: Content-Type by extension, sandboxed (an SVG can't run
                            script in the room's origin: KURA-1, NIWA-1), anything not an image as an attachment
  header_value(text)        the text, or ValueError when it holds CR, LF or another control character (KONB-2)
  location(path)            a local redirect target, percent-encoded: never a header injection, never `//host`

Outgoing requests:
  public_address(ip)        is this address on the public internet? (not loopback, RFC 1918, CGNAT/Tailscale,
                            link-local, ULA, multicast, reserved, or an IPv6 form of one of those)
  vet(host, port, allow)    resolve and require every address to be public -> the addresses; Blocked otherwise
  public_opener(allow=())   a urllib opener for fetching OUTSIDE addresses (link checkers, probes of user-given URLs):
                            every connection goes to an address vetted at connect time (so DNS rebinding can't swap
                            it), redirects are vetted the same way, http(s) only, at most 5
  token_opener()            a urllib opener for calls that carry a credential: never follows a redirect (a 3xx is
                            an HTTPError), so a token never reaches another host (NIWA-6, KONB-10)

Standard library only. smallweb and feed-import keep their own copy of the address rule in stack/smallweb/web.py
(they don't vendor vaultkit); tests/test_websafe.py checks that the two agree.
"""
import http.client
import ipaddress
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

# -- headers ------------------------------------------------------------------------------------------------------------

BASE = [("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "SAMEORIGIN"), ("Referrer-Policy", "same-origin")]

# A vault file is never a document of the room: no script, no plugins, no forms, no framing, its own opaque origin.
# Inline style stays (SVG diagrams draw with it).
ASSET_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
ASSET_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml", ".avif": "image/avif"}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def base_headers():
    """[(header, value)] for every response a room sends (HTML pages add shell.security_headers())."""
    return list(BASE)


def asset_headers(name):
    """[(header, value)] for serving the vault file `name` (its Content-Type included). Images are shown inline with a
    sandboxing CSP, an SVG too (as an image it draws; opened on its own it runs nothing); anything else is a download
    (application/octet-stream, attachment). Never sniffed, never framed by another site."""
    ext = os.path.splitext(name or "")[1].lower()
    out = [("Content-Security-Policy", ASSET_CSP)] + base_headers()
    ctype = ASSET_TYPES.get(ext)
    if ctype:
        return [("Content-Type", ctype)] + out
    safe = re.sub(r"[^\w.-]", "_", os.path.basename(name or "")) or "download"
    return [("Content-Type", "application/octet-stream"),
            ("Content-Disposition", 'attachment; filename="%s"' % safe)] + out


def header_value(text):
    """`text` for a response header, or ValueError when it has CR, LF, NUL or another control character (which would
    end the header and start another: Set-Cookie on the room's domain, KONB-2)."""
    text = str(text)
    if _CONTROL.search(text):
        raise ValueError("control character in a header value")
    return text


def location(path, fallback="/"):
    """A same-site redirect target for Location: a local path (with its query), percent-encoded so no control
    character, space or non-ASCII byte gets through, and never `//host` or `/\\host` (which browsers read as another
    site). Anything else gives `fallback`."""
    path = str(path or "")
    if not path.startswith("/") or path[1:2] in ("/", "\\"):
        return fallback
    out = quote(path, safe="/%?=&#:@!$'()*+,;~-._")
    if out[1:2] in ("/", "\\") or _CONTROL.search(out):
        return fallback
    return out


# -- addresses ----------------------------------------------------------------------------------------------------------

class Blocked(Exception):
    """A private address (or a redirect off http(s)): never fetched."""


_EXTRA = [ipaddress.ip_network(n) for n in ("0.0.0.0/8", "100.64.0.0/10", "255.255.255.255/32", "fc00::/7")]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def private(ip):
    """Is this an address a room must never reach on a user's behalf? The same rule as smallweb's web.private."""
    ip = ipaddress.ip_address(ip)
    if ip.version == 6:
        inner = [ip.ipv4_mapped, ip.sixtofour]
        if ip in _NAT64:
            inner.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(a is not None and private(a) for a in inner):
            return True
        if ip.is_site_local:
            return True
    return (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or any(ip.version == n.version and ip in n for n in _EXTRA))


def public_address(ip):
    try:
        return not private(ip)
    except ValueError:
        return False


resolve = socket.getaddrinfo        # tests patch this


def vet(host, port, allow=()):
    """Resolve `host` -> its addresses, every one of them public, or Blocked. `allow`: host names (exact, lowercase)
    and CIDR strings a deployment lets through (its own LAN service, say). Unresolvable: Blocked too."""
    host = (host or "").strip().rstrip(".").lower()
    if not host:
        raise Blocked("no host")
    names = {a for a in allow if "/" not in a}
    nets = [ipaddress.ip_network(a, strict=False) for a in allow if "/" in a]
    try:
        infos = resolve(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise Blocked("%s can't be resolved: %s" % (host, e))
    ips = list(dict.fromkeys(info[4][0].split("%", 1)[0] for info in infos))
    if not ips:
        raise Blocked("%s has no address" % host)
    if host not in names:
        for ip in ips:
            if private(ip) and not any(ipaddress.ip_address(ip).version == n.version and ipaddress.ip_address(ip) in n
                                       for n in nets):
                raise Blocked("%s is a private address" % host)
    return ips


# -- openers ------------------------------------------------------------------------------------------------------------

CREDENTIALS = ("authorization", "x-access-token", "cookie", "proxy-authorization")


def _pinned(base, allow):
    class Conn(base):
        def __init__(self, host, *args, **kwargs):
            super().__init__(host, *args, **kwargs)

            def create(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                ips = vet(address[0], address[1], allow)
                last = None
                for ip in ips:
                    try:
                        return socket.create_connection((ip, address[1]), timeout, source_address)
                    except OSError as e:
                        last = e
                raise last
            self._create_connection = create
    return Conn


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if any(k.lower() in CREDENTIALS for k in list(req.headers) + list(req.unredirected_hdrs)):
            raise urllib.error.HTTPError(req.full_url, code, "redirect refused: the request carries a credential",
                                         headers, fp)
        if urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise urllib.error.HTTPError(req.full_url, code, "redirect refused: not http(s)", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused (a credential never follows one)",
                                     headers, fp)


def public_opener(allow=(), context=None):
    """An opener for outside URLs: public addresses only, checked when each connection opens (redirects included,
    at most 5, http(s) only); a request with a credential header never follows a redirect (HTTPError). A refused
    address raises Blocked."""
    allow = tuple(a.strip().lower() for a in allow if a and a.strip())

    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_pinned(http.client.HTTPConnection, allow), req)

    class S(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_pinned(http.client.HTTPSConnection, allow), req, context=self._context)

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), H(), S(context=context), _Redirects())


def token_opener(context=None):
    """An opener for a call that carries a token (to Hister, a room, the board): any redirect is an HTTPError."""
    handlers = [_NoRedirects()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)
