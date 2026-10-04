"""feed-import's Hister calls (docs/contracts/hister.md): `Origin: hister://` on every call, and the owner's token as
`X-Access-Token` from FEED_IMPORT_HISTER_TOKEN_FILE (ignored by a Hister without users). The file is checked at start
(token_file), re-read when it changes, and while a token is set a redirect from Hister is never followed: urllib would
carry the header to wherever it points.

Only four calls: GET /api/document (is this URL known?), POST /api/add (a new page), POST /api/label (label a page
that has none), GET /api/rules (the topic labels, from the aliases). Never a re-index of a known URL.
"""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request


TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")


class SecretFile:
    """A token kept in a file (its first line), re-read when the file changes (inode, mtime, size), so a rotated token
    is picked up without a restart. A file that vanishes or holds no token keeps the last good value (a rotation in
    progress). The value is never in repr() or a log line."""

    def __init__(self, path):
        self.path, self.stamp, self.value, self.lock = path, None, "", threading.Lock()
        self.get()

    def get(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return self.value
        with self.lock:
            if stamp != self.stamp:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        value = f.readline().strip()
                except (OSError, UnicodeError):
                    value = ""
                if TOKEN_RE.fullmatch(value):
                    self.value = value
                self.stamp = stamp
            return self.value

    def __repr__(self):
        return "SecretFile(%s)" % self.path


def token_file(path):
    """FEED_IMPORT_HISTER_TOKEN_FILE -> a SecretFile, or None when unset (no token, as before). Set but missing, empty
    or not a token: refuse to start (never echoing the file's contents)."""
    path = (path or "").strip()
    if not path:
        return None
    if not os.path.isfile(path):
        raise SystemExit("feed-import: FEED_IMPORT_HISTER_TOKEN_FILE: no token in %s" % path)
    secret = SecretFile(path)
    if not secret.value:
        raise SystemExit("feed-import: FEED_IMPORT_HISTER_TOKEN_FILE: %s doesn't hold a token on its first line" % path)
    return secret


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HisterDown(Exception):
    """Hister is unreachable or answered 5xx: the run stops and nothing is counted against an entry."""


def norm(url):
    """The URL as Hister stores it (document.normalizeWebURL in v0.20.0): no fragment, and no utm / utm_* query
    parameters; when one is dropped the query is re-encoded the way Go's url.Values.Encode does (sorted by key)."""
    u = urllib.parse.urlsplit(url)
    if u.scheme.lower() not in ("http", "https"):
        return url
    query = u.query
    if query:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if not (k == "utm" or k.startswith("utm_"))]
        if len(kept) != len(pairs):
            kept.sort(key=lambda kv: kv[0])                  # stable: values keep their order within a key
            query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, query, ""))


class Hister:
    def __init__(self, base, token=None, timeout=60):
        """token: a SecretFile (FEED_IMPORT_HISTER_TOKEN_FILE), a plain string (tests), or None/"" for none."""
        self.base, self.timeout = base.rstrip("/"), timeout
        self.token = token if token else None
        self.opener = urllib.request.build_opener(NoRedirect) if self.token else urllib.request.build_opener()

    def __repr__(self):                 # never the token
        return "Hister(%s%s)" % (self.base, ", token" if self.token else "")

    def token_value(self):
        if self.token is None:
            return ""
        return self.token.get() if hasattr(self.token, "get") else self.token

    def call(self, method, path, body=None):
        """(status, parsed JSON or text). 4xx come back; 5xx and network errors raise HisterDown."""
        headers = {"Origin": "hister://", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        token = self.token_value()
        if token:
            headers["X-Access-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            if self.token is not None and 300 <= e.code < 400:
                raise HisterDown("Hister %s %s: a redirect (%d), not followed with the token"
                                 % (method, path.split("?")[0], e.code))
            if e.code >= 500:
                raise HisterDown("Hister %s %s: HTTP %d" % (method, path.split("?")[0], e.code))
            status, raw = e.code, e.read()[:500]
        except (urllib.error.URLError, OSError) as e:
            raise HisterDown("Hister %s %s: %s" % (method, path.split("?")[0], e))
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def document(self, url):
        """The stored document for this exact URL, or None."""
        status, doc = self.call("GET", "/api/document?url=" + urllib.parse.quote(url, safe=""))
        if status == 404:
            return None
        if status != 200 or not isinstance(doc, dict):
            raise HisterDown("Hister GET /api/document: HTTP %d" % status)
        return doc

    def add(self, doc):
        """201 added, 406 refused by a skip rule, 422 sensitive content; any other 4xx is a final refusal."""
        return self.call("POST", "/api/add", doc)

    def set_label(self, url, label):
        status, _ = self.call("POST", "/api/label", {"url": url, "label": label})
        return status

    def labels(self):
        """The topic labels named in Hister's aliases (label:x and label:(a|b)), as the server importer reads them."""
        status, rules = self.call("GET", "/api/rules")
        out = set()
        if status == 200 and isinstance(rules, dict):
            for value in (rules.get("aliases") or {}).values():
                for group in re.findall(r"label:\(([^)]*)\)", str(value)):
                    out.update(x.strip() for x in group.split("|") if x.strip())
                out.update(re.findall(r"label:([^\s()|\"]+)", str(value)))
        out.discard("vault")
        out.discard("konbini")
        return out
