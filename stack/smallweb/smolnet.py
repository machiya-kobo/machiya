"""The small web's two protocols, client side: Gemini (TLS, TOFU) and Gopher, optionally through a SOCKS5 proxy
(socks5h: the proxy resolves names, so no DNS leaks from the server). Politeness lives here too: one connection per
host at a time, a minimum gap between requests to the same host, and 44 SLOW DOWN honoured.

Specs: gemini://geminiprotocol.net/docs/protocol-specification.gmi, RFC 1436 (gopher), RFC 4266 (gopher URLs).
"""
import datetime
import hashlib
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from urllib.parse import quote, unquote, urlsplit

for _s in ("gemini",):                         # teach urljoin/urlsplit that gemini URLs have hosts and relative links
    for _lst in (urllib.parse.uses_relative, urllib.parse.uses_netloc):
        if _s not in _lst:
            _lst.append(_s)

TIMEOUT = 10            # seconds, connect and each read
PROXY = {"fails": 0, "error": None}     # the SOCKS proxy itself: consecutive failures (a host refusing isn't one)
MAX_BODY = 5 << 20      # bytes
DEFAULT_PORT = {"gemini": 1965, "gopher": 70}


class FetchError(Exception):
    """A fetch that didn't produce a response: `kind` is timeout | unreachable | refused | tls | proxy | toobig."""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind, self.detail = kind, detail


# -- URLs ------------------------------------------------------------------------------------------------------------

def canonical(url):
    """The one spelling of a gemini:// or gopher:// URL: lowercase scheme and host, no default port, no fragment,
    '#' in a gopher selector percent-encoded (Hister drops fragments). Raises ValueError for anything else."""
    u = urlsplit(url.strip())
    scheme = u.scheme.lower()
    if scheme not in DEFAULT_PORT or not u.hostname:
        raise ValueError("not a gemini:// or gopher:// URL")
    host = u.hostname.lower()
    if ":" in host:
        host = "[%s]" % host
    port = u.port or DEFAULT_PORT[scheme]
    netloc = host if port == DEFAULT_PORT[scheme] else "%s:%d" % (host, port)
    if scheme == "gemini":
        path = u.path or "/"
        return urllib.parse.urlunsplit((scheme, netloc, path, u.query, ""))
    # gopher: the selector is opaque, so keep it exactly; '#' is part of it (not a fragment) and a raw tab is the
    # search separator
    rest = url.strip().split(u.netloc, 1)[1] if u.netloc else ""
    return "gopher://%s%s" % (netloc, rest.replace("#", "%23").replace("\t", "%09").replace(" ", "%20") or "/")


def gopher_parts(url):
    """gopher://host[:port]/<type><selector>[%09query] -> (host, port, type, selector, query)."""
    u = urlsplit(url)
    host, port = u.hostname, u.port or 70
    raw = url.split(u.netloc, 1)[1] if u.netloc else "/"
    raw = raw[1:] if raw.startswith("/") else raw
    if not raw:
        return host, port, "1", "", ""
    itype, rest = raw[0], unquote(raw[1:])
    query = ""
    if "\t" in rest:
        rest, query = rest.split("\t", 1)
        query = query.split("\t", 1)[0]
    return host, port, itype, rest, query


def gopher_url(host, port, itype, selector, query=""):
    netloc = host if int(port or 70) == 70 else "%s:%s" % (host, port)
    sel = quote(selector, safe="/:~.,;=!$&'()*+@-_")
    return "gopher://%s/%s%s%s" % (netloc, itype, sel, ("%09" + quote(query)) if query else "")


# -- politeness ------------------------------------------------------------------------------------------------------

class Polite:
    """One request at a time per host, at least `gap` seconds apart for hosts in `gaps`, and none while a host has
    asked us to slow down (Gemini 44)."""

    def __init__(self, gaps=None):
        self.gaps = dict(gaps or {})
        self.locks, self.last, self.until = {}, {}, {}
        self.guard = threading.Lock()

    def lock(self, host):
        with self.guard:
            return self.locks.setdefault(host, threading.Lock())

    def slowed(self, host):
        return max(0.0, self.until.get(host, 0) - time.time())

    def slow_down(self, host, seconds):
        """Back off for `seconds` (at least), doubling if the host asks again while we're still backing off."""
        prev = self.until.get(host, 0) - time.time()
        wait = max(float(seconds or 0), 2 * prev if prev > 0 else 0, 5.0)
        self.until[host] = time.time() + min(wait, 3600)

    def run(self, host, fn):
        with self.lock(host):
            gap = self.gaps.get(host, 0)
            wait = self.last.get(host, 0) + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                return fn()
            finally:
                self.last[host] = time.time()


# -- connections -----------------------------------------------------------------------------------------------------

def socks5h(proxy, host, port, timeout=TIMEOUT):
    """A TCP connection to host:port through a SOCKS5 proxy "host:port", the name resolved by the proxy."""
    phost, _, pport = proxy.rpartition(":")
    try:
        s = socket.create_connection((phost, int(pport)), timeout=timeout)
    except OSError as e:
        PROXY["fails"] += 1
        PROXY["error"] = "SOCKS proxy %s: %s" % (proxy, e)
        raise FetchError("proxy", "the SOCKS proxy %s can't be reached: %s" % (proxy, e))
    PROXY["fails"], PROXY["error"] = 0, None
    try:
        s.sendall(b"\x05\x01\x00")                                   # v5, one method: no auth
        if _recv(s, 2) != b"\x05\x00":
            raise FetchError("proxy", "SOCKS proxy refused the no-auth method")
        name = host.encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        head = _recv(s, 4)
        if head[1] != 0:
            kind = {3: "unreachable", 4: "unreachable", 5: "refused", 6: "timeout"}.get(head[1], "proxy")
            why = {3: "its network is unreachable", 4: "the host is unreachable", 5: "it refused the connection",
                   6: "it timed out"}.get(head[1], "the proxy failed (SOCKS reply %d)" % head[1])
            raise FetchError(kind, "%s:%d can't be reached through the proxy: %s. Some hosts block VPN ranges; "
                                   "try opening it natively." % (host, port, why))
        _recv(s, {1: 4, 4: 16}.get(head[3], None) or _recv(s, 1)[0])   # the bound address, ignored
        _recv(s, 2)
        return s
    except BaseException:
        s.close()
        raise


def _recv(s, n):
    out = b""
    while len(out) < n:
        chunk = s.recv(n - len(out))
        if not chunk:
            raise FetchError("proxy", "the SOCKS proxy closed the connection")
        out += chunk
    return out


def connect(host, port, proxy="", timeout=TIMEOUT):
    try:
        if proxy:
            return socks5h(proxy, host, port, timeout)
        return socket.create_connection((host, port), timeout=timeout)
    except FetchError:
        raise
    except socket.timeout:
        raise FetchError("timeout", "%s:%d did not answer" % (host, port))
    except ConnectionRefusedError:
        raise FetchError("refused", "%s:%d refused the connection" % (host, port))
    except OSError as e:
        raise FetchError("unreachable", "%s:%d: %s" % (host, port, e))


def read_all(s, cap=MAX_BODY):
    parts, size = [], 0
    while True:
        try:
            chunk = s.recv(65536)
        except (ssl.SSLEOFError, ssl.SSLZeroReturnError, ConnectionResetError):
            break                                                    # ragged closes are common in gemini space
        except socket.timeout:
            raise FetchError("timeout", "the server stopped sending")
        if not chunk:
            break
        parts.append(chunk)
        size += len(chunk)
        if size > cap:
            raise FetchError("toobig", "more than %d MB" % (cap >> 20))
    return b"".join(parts)


# -- Gemini ----------------------------------------------------------------------------------------------------------

class GeminiResponse:
    def __init__(self, url, status, meta, body, cert_sha256, not_after):
        self.url, self.status, self.meta, self.body = url, status, meta, body
        self.cert_sha256, self.not_after = cert_sha256, not_after

    @property
    def mime(self):
        return self.meta.split(";", 1)[0].strip().lower() if 20 <= self.status < 30 else ""

    @property
    def charset(self):
        for part in self.meta.split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                return v.strip().strip('"')
        return "utf-8"


def gemini(url, proxy="", timeout=TIMEOUT):
    """One Gemini request (no redirects followed). The certificate is not verified here: the caller applies TOFU
    with cert_sha256 / not_after."""
    u = urlsplit(url)
    host, port = u.hostname, u.port or 1965
    req = (url + "\r\n").encode("utf-8")
    if len(req) > 1026:
        raise FetchError("toobig", "the URL is longer than Gemini's 1024 bytes")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    raw = connect(host, port, proxy, timeout)
    try:
        try:
            t = ctx.wrap_socket(raw, server_hostname=host)
        except (ssl.SSLError, OSError) as e:
            raise FetchError("tls", "TLS with %s failed: %s" % (host, e))
        with t:
            der = t.getpeercert(binary_form=True) or b""
            t.sendall(req)
            data = read_all(t)
    finally:
        raw.close()
    head, _, body = data.partition(b"\r\n")
    if not head[:2].isdigit():
        raise FetchError("proto", "not a Gemini response")
    status = int(head[:2])
    meta = head[3:].decode("utf-8", "replace").strip()[:1024]
    return GeminiResponse(url, status, meta, body, hashlib.sha256(der).hexdigest(), cert_not_after(der))


def cert_not_after(der):
    """notAfter of an X.509 certificate (DER) as a unix time, or 0. A tiny DER walk: Certificate -> tbsCertificate ->
    [0] version? serial, signature, issuer, validity(notBefore, notAfter)."""
    try:
        def tlv(buf, i):
            tag, n = buf[i], buf[i + 1]
            i += 2
            if n & 0x80:
                k = n & 0x7F
                n = int.from_bytes(buf[i:i + k], "big")
                i += k
            return tag, i, i + n                                      # tag, value start, value end

        _, cs, _ = tlv(der, 0)
        _, ts, te = tlv(der, cs)
        i, fields = ts, []
        while i < te and len(fields) < 6:
            tag, vs, ve = tlv(der, i)
            fields.append((tag, vs, ve))
            i = ve
        if fields and fields[0][0] == 0xA0:                           # explicit version
            fields = fields[1:]
        _, vs, _ = fields[3]                                         # validity
        _, a, b = tlv(der, vs)                                       # notBefore
        tag, a, b = tlv(der, b)                                      # notAfter
        text = der[a:b].decode()
        fmt = "%y%m%d%H%M%SZ" if tag == 0x17 else "%Y%m%d%H%M%SZ"
        return int(datetime.datetime.strptime(text, fmt).replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
        return 0


# -- Gopher ----------------------------------------------------------------------------------------------------------

def gopher(host, port, selector, query="", proxy="", timeout=TIMEOUT):
    """One Gopher request: selector[<TAB>query]<CRLF>, read to EOF."""
    line = selector + (("\t" + query) if query else "") + "\r\n"
    s = connect(host, port, proxy, timeout)
    with s:
        s.sendall(line.encode("utf-8"))
        return read_all(s)


def decode(body, charset="utf-8"):
    try:
        return body.decode(charset)
    except (UnicodeDecodeError, LookupError):
        return body.decode("latin-1")
