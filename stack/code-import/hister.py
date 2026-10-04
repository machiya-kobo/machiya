"""code-import's Hister calls (docs/contracts/hister.md): `Origin: hister://` on every call, and the owner's token as
`X-Access-Token` from CODE_IMPORT_HISTER_TOKEN_FILE (tokens.py: checked at start, re-read when it changes; while it is
set a redirect from Hister is never followed).

Three calls: GET /api/document (is this URL known, and whose is it?), POST /api/add (a code document, always with
`html`: Hister's sensitive-content check reads only `html` for a web document), POST /api/delete (one exact
`url:"…"`, only for a document whose metadata.source is `code`).
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import tokens


class HisterDown(Exception):
    """Hister is unreachable or answered 5xx: the run stops and nothing is recorded against a document."""


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
            kept.sort(key=lambda kv: kv[0])
            query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, query, ""))


def is_ours(doc):
    """A Hister document code-import owns: metadata.source is `code`. Anything else (a page the owner browsed, a
    note, a feed-import page) is never overwritten or deleted."""
    return isinstance(doc, dict) and (doc.get("metadata") or {}).get("source") == "code"


class Hister:
    def __init__(self, base, token=None, timeout=60):
        """token: a tokens.SecretFile, a plain string (tests), or None for none."""
        self.base, self.timeout = base.rstrip("/"), timeout
        self.token = token if token else None
        self.opener = urllib.request.build_opener(tokens.NoRedirect) if self.token else urllib.request.build_opener()

    def __repr__(self):                 # never the token
        return "Hister(%s%s)" % (self.base, ", token" if self.token else "")

    def call(self, method, path, body=None):
        """(status, parsed JSON or text). 4xx come back; 5xx and network errors raise HisterDown."""
        headers = {"Origin": "hister://", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        token = tokens.value(self.token)
        if token:
            headers["X-Access-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        what = "Hister %s %s" % (method, path.split("?")[0])
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            if self.token is not None and 300 <= e.code < 400:
                raise HisterDown("%s: a redirect (%d), not followed with the token" % (what, e.code))
            if e.code >= 500:
                raise HisterDown("%s: HTTP %d" % (what, e.code))
            if e.code == 403:
                raise HisterDown("%s: 403, no or an invalid token (CODE_IMPORT_HISTER_TOKEN_FILE)" % what)
            status, raw = e.code, e.read()[:500]
        except (urllib.error.URLError, OSError) as e:
            raise HisterDown("%s: %s" % (what, e))
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
        if not doc.get("html"):
            raise ValueError("a code document is always sent with html (Hister checks only html for secrets)")
        if "skip_sensitive_check" in doc:
            raise ValueError("code-import never sets skip_sensitive_check")
        return self.call("POST", "/api/add", doc)

    def delete(self, url):
        status, _ = self.call("POST", "/api/delete", {"query": 'url:"%s"' % url.replace('"', "%22")})
        return status
