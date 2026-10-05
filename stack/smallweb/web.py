"""http(s) pages for POST /api/save: one page its owner asked for, fetched once and saved to Hister. Never a proxy (/page
stays gemini/gopher), never following links, no robots.txt (a single save a person asked for, like a browser visit).

The SSRF defence is the point of this module:
- the URL: http(s) only, no userinfo, at most 2048 characters, never a private Kura vault's address (`/v/<name>/`);
- the host is resolved here, and the save is refused if ANY address is private (loopback, RFC 1918, link-local,
  multicast, reserved, unspecified, CGNAT 100.64.0.0/10, fc00::/7, 0.0.0.0/8, broadcast, or an IPv6 form of one of
  those), unless SMALLWEB_FETCH_ALLOW names the host or the address;
- the connection goes to the vetted address itself (through SOCKS5 as an IP literal when a proxy is set), so a second
  DNS answer can't swap it, with the real Host header, SNI and certificate checked against the host name;
- each redirect (at most 5) goes through the same checks.
"""
import codecs
import html
import ipaddress
import re
import socket
import ssl
import time
import http.client
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import smolnet

MAX_URL = 2048
MAX_REDIRECTS = 5
TIMEOUT = 10            # seconds, connect and each read
DEADLINE = 30           # seconds for one response in all, so a trickling server can't hold a save worker
MAX_BODY = 5 << 20      # bytes
GAP = 1.5               # seconds between requests to one host
MIMES = ("text/html", "application/xhtml+xml", "text/plain")
DEFAULT_PORT = {"http": 80, "https": 443}
CAFILE = None           # tests only: trust this CA file instead of the system's
resolve = socket.getaddrinfo        # tests patch this

_EXTRA = [ipaddress.ip_network(n) for n in ("0.0.0.0/8", "100.64.0.0/10", "255.255.255.255/32", "fc00::/7")]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_VAULT = re.compile(r"^/v/[^/]+(/|$)", re.I)


class Refused(ValueError):
    """The URL itself can't be saved (a 400 when it is asked for)."""


class PrivateAddress(Refused):
    """An IP literal in a private range, in the URL asked for."""


class Blocked(Exception):
    """A private address, or a private vault's: never fetched. Logged as `blocked: …`, never counted as a failure."""


class WebError(Exception):
    """The page couldn't be fetched or isn't one smallweb saves."""


# -- addresses -------------------------------------------------------------------------------------------------------

def private(ip):
    """Is this address one smallweb must never reach on its own? IPv6 forms of IPv4 addresses count as the IPv4 one."""
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


def parse_allow(value):
    """SMALLWEB_FETCH_ALLOW: a comma list of host names (exact, lowercase) and CIDRs -> (names, networks)."""
    names, nets = set(), []
    for item in (value or "").split(","):
        item = item.strip().lower()
        if not item:
            continue
        if "/" in item or re.fullmatch(r"[0-9.]+|[0-9a-f:]*:[0-9a-f:.]*", item):
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                raise SystemExit("smallweb: SMALLWEB_FETCH_ALLOW: %r is not a host name or a CIDR" % item)
        elif re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", item):
            names.add(item)
        else:
            raise SystemExit("smallweb: SMALLWEB_FETCH_ALLOW: %r is not a host name or a CIDR" % item)
    return names, nets


def allowed_ip(ip, allow):
    ip = ipaddress.ip_address(ip)
    return any(ip.version == n.version and ip in n for n in allow[1])


def vet(host, port, allow):
    """Resolve host and check every address -> the addresses to connect to. Raises Blocked or WebError."""
    try:
        infos = resolve(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as e:
        raise WebError("%s can't be resolved: %s" % (host, e))
    ips = list(dict.fromkeys(info[4][0].split("%", 1)[0] for info in infos))
    if not ips:
        raise WebError("%s has no address" % host)
    if host not in allow[0]:
        for ip in ips:
            if private(ip) and not allowed_ip(ip, allow):
                raise Blocked("private address")
    return ips


# -- URLs ------------------------------------------------------------------------------------------------------------

def canonical(url):
    """The one spelling of an http(s) URL: lowercase scheme and host (IDNA), no default port, no fragment, non-ASCII
    percent-encoded. Raises Refused for anything smallweb won't fetch."""
    url = (url or "").strip()
    if len(url) > MAX_URL:
        raise Refused("the URL is longer than %d characters" % MAX_URL)
    if re.search(r"[\x00-\x20\x7f]", url):
        raise Refused("the URL has spaces or control characters")
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        raise Refused("not a valid URL")
    scheme = u.scheme.lower()
    if scheme not in DEFAULT_PORT:
        raise Refused("not an http:// or https:// URL")
    if "@" in u.netloc:
        raise Refused("URLs with a user name or password are refused")
    host = (u.hostname or "").rstrip(".")
    if not host:
        raise Refused("the URL has no host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise Refused("the host name is not valid")
        if not re.fullmatch(r"[a-z0-9_.-]+", host):
            raise Refused("the host name is not valid")
    netloc = ("[%s]" % ip.compressed if ip and ip.version == 6 else str(ip)) if ip else host
    if port and port != DEFAULT_PORT[scheme]:
        netloc += ":%d" % port
    safe = "/%:@!$&'()*+,;=-._~"
    out = urlunsplit((scheme, netloc, quote(u.path or "/", safe=safe), quote(u.query, safe=safe + "?"), ""))
    if len(out) > MAX_URL:
        raise Refused("the URL is longer than %d characters" % MAX_URL)
    return out


def vault_path(path):
    """Is this path a private Kura vault's address (`/v/<name>/…`)? Percent-decoded, a leading `//` folded."""
    for _ in range(5):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    return bool(_VAULT.match("/" + path.lstrip("/")))


def check(url, allow):
    """Canonical URL, checked before anything is fetched. Raises Refused (bad URL, IP literal in a private range) or
    Blocked (a private vault's address)."""
    url = canonical(url)
    u = urlsplit(url)
    if vault_path(u.path):
        raise Blocked("private vault address")
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        return url
    if private(ip) and not allowed_ip(ip, allow) and u.hostname not in allow[0]:
        raise PrivateAddress("a private address is never fetched")
    return url


# -- fetching --------------------------------------------------------------------------------------------------------

class _HTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, proxy, timeout):
        super().__init__(host, port, timeout=timeout)
        self.ip, self.proxy = ip, proxy

    def connect(self):
        self.sock = smolnet.connect(self.ip, self.port, self.proxy, self.timeout)


class _HTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, proxy, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self.ip, self.proxy = ip, proxy

    def connect(self):
        raw = smolnet.connect(self.ip, self.port, self.proxy, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except (ssl.SSLError, ssl.CertificateError, OSError) as e:
            raise smolnet.FetchError("tls", "TLS with %s failed: %s" % (self.host, e))
        finally:
            raw.close()                                  # detached by wrap_socket on success; closed on failure


def tls_context():
    ctx = ssl.create_default_context(cafile=CAFILE) if CAFILE else ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status, self.headers, self.body = url, status, headers, body

    @property
    def mime(self):
        return (self.headers.get("content-type") or "").split(";", 1)[0].strip().lower()

    @property
    def charset(self):
        for part in (self.headers.get("content-type") or "").split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.strip().lower() == "charset" and v.strip():
                return v.strip().strip("\"'")
        return ""


def request(url, ip, proxy, user_agent):
    """One GET to the vetted address (no redirects followed). The body is read only for a 200 with a savable type."""
    u = urlsplit(url)
    port = u.port or DEFAULT_PORT[u.scheme]
    conn = _HTTPS(u.hostname, port, ip, proxy, TIMEOUT, tls_context()) if u.scheme == "https" else \
        _HTTP(u.hostname, port, ip, proxy, TIMEOUT)
    target = u.path + ("?" + u.query if u.query else "")
    r = None
    try:
        conn.request("GET", target, headers={"User-Agent": user_agent, "Accept-Encoding": "identity",
                                             "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"})
        r = conn.getresponse()
        headers = {k.lower(): v for k, v in r.getheaders()}
        res = Response(url, r.status, headers, b"")
        if r.status != 200 or res.mime not in MIMES:
            return res
        length = headers.get("content-length") or ""
        if length.isdigit() and int(length) > MAX_BODY:
            raise WebError("the page is larger than %d MB" % (MAX_BODY >> 20))
        parts, size, end = [], 0, time.time() + DEADLINE
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            parts.append(chunk)
            size += len(chunk)
            if size > MAX_BODY:
                raise WebError("the page is larger than %d MB" % (MAX_BODY >> 20))
            if time.time() > end:
                raise WebError("the page took longer than %d s" % DEADLINE)
        res.body = b"".join(parts)
        return res
    except smolnet.FetchError as e:
        raise WebError(e.detail or e.kind)
    except socket.timeout:
        raise WebError("%s did not answer in time" % u.hostname)
    except (http.client.HTTPException, OSError) as e:
        raise WebError("%s: %s" % (u.hostname, e))
    finally:
        if r is not None:
            r.close()                                    # the response owns the socket once the server says close
        conn.close()


def fetch(url, allow, proxy, polite, user_agent):
    """GET a checked URL, following at most 5 redirects, each one checked again -> Response (status 200, a savable
    type). Raises Blocked or WebError."""
    for _ in range(MAX_REDIRECTS + 1):
        u = urlsplit(url)
        ips = vet(u.hostname, u.port or DEFAULT_PORT[u.scheme], allow)
        r, last = None, None
        for ip in ips:                                   # every address was vetted; try them in order
            try:
                r = polite.run(u.hostname, lambda: request(url, ip, proxy, user_agent))
                break
            except WebError as e:
                last = e
        if r is None:
            raise last
        if r.status in (301, 302, 303, 307, 308):
            loc = r.headers.get("location")
            if not loc:
                raise WebError("HTTP %d without a Location" % r.status)
            nxt = urljoin(url, loc.strip())
            if urlsplit(nxt).scheme.lower() not in DEFAULT_PORT:
                raise WebError("redirected to a non-http(s) address")
            try:
                url = check(nxt, allow)
            except PrivateAddress:
                raise Blocked("private address")
            except Refused as e:
                raise WebError("redirected to a URL smallweb won't fetch: %s" % e)
            continue
        if r.status != 200:
            raise WebError("HTTP %d" % r.status)
        if r.mime not in MIMES:
            raise WebError("not saved: %s" % (r.mime or "no content type"))
        return r
    raise WebError("more than %d redirects" % MAX_REDIRECTS)


# -- reading ---------------------------------------------------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.I)


def decode(body, header_charset="", mime="text/html"):
    """By the header's charset, else (HTML) the <meta charset>, else UTF-8 with replacement. Only a text encoding is
    used: a page naming base64, hex, zlib, rot13 or another bytes codec as its charset gets UTF-8 (str.decode would
    raise LookupError for those, and that stopped feed-import for good: the 2026-10 sweep, MACH-F-3)."""
    names = [header_charset]
    if mime != "text/plain":
        m = _META_CHARSET.search(body[:4096])
        names.append(m.group(1).decode("ascii") if m else "")
    for name in names:
        if not name:
            continue
        try:
            info = codecs.lookup(name)
        except LookupError:
            continue
        if not getattr(info, "_is_text_encoding", True):
            continue
        try:
            return body.decode(info.name, "replace")
        except LookupError:
            continue
    return body.decode("utf-8", "replace")


_SPACE = re.compile(r"[ \t\r\f\v ]+")


def collapse(text):
    return "\n".join(line for line in (_SPACE.sub(" ", x).strip() for x in text.splitlines()) if line)


class _Reader(HTMLParser):
    """The page's title, text and a cleaned copy of its HTML. The copy is an allowlist (smallweb 0.3.1, MACH-F-7): only
    the document and text tags below, only the attributes below, and links and images only to http(s), mailto, gemini,
    gopher or a relative address. Script, style, frames, plugins, SVG, MathML, templates and forms' controls go with
    their content; any other tag goes and its text stays."""
    DROP_HTML = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript", "frameset",
                 "frame", "noframes", "applet", "canvas", "audio", "video", "select", "textarea", "button", "head",
                 "xmp", "plaintext", "noembed", "dialog"}                 # dropped with their content
    DROP_TEXT = {"script", "style", "noscript", "template", "svg", "title", "iframe", "object", "math", "select",
                 "textarea", "button", "noframes", "noembed"}
    ALLOWED = {"html", "body", "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "blockquote", "br",
               "caption", "cite", "code", "col", "colgroup", "data", "dd", "del", "details", "dfn", "div", "dl", "dt",
               "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "img",
               "ins", "kbd", "li", "main", "mark", "nav", "ol", "p", "pre", "q", "rp", "rt", "ruby", "s", "samp",
               "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th",
               "thead", "time", "tr", "u", "ul", "var", "wbr"}
    ATTRS = {"href", "src", "alt", "title", "width", "height", "colspan", "rowspan", "cite", "datetime", "lang", "dir",
             "start", "reversed", "scope", "headers", "abbr", "open", "value"}
    URL_ATTRS = {"href", "src", "cite"}
    SCHEME = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*):")
    SCHEMES = {"http", "https", "mailto", "gemini", "gopher"}                     # or a relative address
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "table", "pre",
             "blockquote", "section", "article", "header", "footer", "nav", "aside", "main", "hr", "dt", "dd",
             "figcaption", "form", "address", "details", "summary"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.text = [], []
        self.hide_html, self.hide_text = {}, {}
        self.title = self.og = self.h1 = None
        self.capture, self.buf = None, []                           # "title" | "h1" while inside one

    def _attrs(self, attrs):
        keep = []
        for k, v in attrs:
            if k not in self.ATTRS:
                continue
            if k in self.URL_ATTRS:
                m = self.SCHEME.match(re.sub(r"[\x00-\x20]", "", v or ""))
                if v is None or (m and m.group(1).lower() not in self.SCHEMES):
                    continue
            keep.append(" %s" % k if v is None else ' %s="%s"' % (k, html.escape(v, quote=True)))
        return "".join(keep)

    def _visible_html(self):
        return not any(self.hide_html.values())

    def _visible_text(self):
        return not any(self.hide_text.values())

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def _start(self, tag, attrs, closed):
        if tag == "meta" and self.og is None:
            a = dict(attrs)
            if (a.get("property") or a.get("name") or "").lower() == "og:title" and a.get("content"):
                self.og = a["content"]
        if tag in self.BLOCK:
            self.text.append("\n")
        if tag in self.DROP_HTML:
            if not closed and tag != "embed":
                self.hide_html[tag] = self.hide_html.get(tag, 0) + 1
        elif self._visible_html() and tag in self.ALLOWED:
            self.out.append("<%s%s%s>" % (tag, self._attrs(attrs), " /" if closed else ""))
        if closed:
            return
        if tag in self.DROP_TEXT:
            self.hide_text[tag] = self.hide_text.get(tag, 0) + 1
        if tag == "title" and self.title is None and self.capture is None:
            self.capture, self.buf = "title", []
        elif tag == "h1" and self.h1 is None and self.capture is None:
            self.capture, self.buf = "h1", []

    def handle_endtag(self, tag):
        if tag == self.capture:
            setattr(self, tag, " ".join("".join(self.buf).split()))
            self.capture = None
        if tag in self.BLOCK:
            self.text.append("\n")
        if self.hide_text.get(tag):
            self.hide_text[tag] -= 1
        if tag in self.DROP_HTML:
            if self.hide_html.get(tag):
                self.hide_html[tag] -= 1
            return
        if self._visible_html() and tag in self.ALLOWED:
            self.out.append("</%s>" % tag)

    def handle_data(self, data):
        if self.capture:
            self.buf.append(data)
        if self._visible_html():
            self.out.append(html.escape(data, quote=False))
        if self._visible_text():
            self.text.append(data)

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.out.append("<!%s>" % decl)

    def handle_comment(self, data):                     # comments can hide markup (conditional comments): dropped
        pass


def read_page(body, mime, charset, url, fallback_title=""):
    """-> (title, text, html) of a fetched page: html without script, style, iframe, object, embed or on… attributes."""
    page = decode(body, charset, mime)
    if mime == "text/plain":
        title = (fallback_title or "").strip() or url
        doc = ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>%s</title></head><body><pre>%s</pre></body>"
               "</html>" % (html.escape(title), html.escape(page)))
        return title, collapse(page), doc
    p = _Reader()
    p.feed(page)
    p.close()
    title = next((t for t in (p.title, " ".join((p.og or "").split()), p.h1, " ".join((fallback_title or "").split()))
                  if t), url)
    return title[:500], collapse("".join(p.text)), "".join(p.out)
