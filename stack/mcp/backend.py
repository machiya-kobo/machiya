"""HTTP client for the rooms: a fixed base URL per backend, JSON in and out, stdlib only.

Invariants (tests guard them): no Origin or Referer ever goes to Konbini, Kura or Niwa (a same-origin header is
what makes Konbini and Niwa treat a caller as "web", the owner's powers); Hister calls always carry
`Origin: hister://`; every call names the caller with X-Agent; there's no way to reach a URL the operator didn't
configure (callers pass a path, never a URL). With a token (MCP_TOKEN_FILE, the `mcp` principal's, for Kura, Konbini
and Niwa only) every call sends `Authorization: Bearer`; with Hister's token (HISTER_TOKEN_FILE, the owner's, for Hister
only) every Hister call sends `X-Access-Token`. With either, a redirect is an error, never followed: urllib would carry
the header to wherever it points."""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

HISTER_ORIGIN = "hister://"
FORBIDDEN_HEADERS = {"origin", "referer"}
TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")


class SecretFile:
    """A token kept in a file (its first line), re-read when the file changes (inode, mtime, size), so a rotated
    token is picked up without a restart. A file that vanishes or holds no token keeps the last good value (a rotation
    in progress); the caller checks the file once at start-up. The value is never in repr() or a log line."""

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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BackendError(Exception):
    """A backend answered with an error status (or couldn't be reached: status 0)."""

    def __init__(self, status, message, room=""):
        super().__init__(message)
        self.status, self.message, self.room = status, message, room


class Backend:
    def __init__(self, room, base, origin=None, timeout=15, opener=None, token="", access_token=None):
        if token and origin:
            raise ValueError("the rooms' token never goes to %s" % room)
        if access_token is not None and origin != HISTER_ORIGIN:
            raise ValueError("Hister's token never goes to %s" % room)
        self.room, self.base, self.origin, self.timeout = room, base.rstrip("/"), origin, timeout
        self.token = token
        self.access_token = access_token        # a SecretFile (HISTER_TOKEN_FILE), Hister only; None without one
        guarded = bool(token) or access_token is not None
        self.opener = opener or (urllib.request.build_opener(NoRedirect) if guarded else urllib.request.build_opener())

    def __repr__(self):                 # never the token
        return "Backend(%s, %s%s)" % (self.room, self.base, ", token" if self.token or self.access_token else "")

    def headers(self, agent):
        h = {"Accept": "application/json", "X-Agent": agent}
        if self.origin:
            h["Origin"] = self.origin
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.access_token is not None:
            value = self.access_token.get()
            if value:
                h["X-Access-Token"] = value
        assert self.origin or not (FORBIDDEN_HEADERS & {k.lower() for k in h}), "no Origin/Referer to %s" % self.room
        return h

    def get(self, path, params=None, agent="mcp"):
        return self.request("GET", path, params=params, agent=agent)

    def request(self, method, path, params=None, body=None, agent="mcp", form=False):
        if not path.startswith("/") or "://" in path or path.startswith("//"):
            raise ValueError("backend paths are absolute paths on the configured host")
        query = urllib.parse.urlencode([(k, v) for k, v in (params or {}).items() if v not in (None, "")], doseq=True)
        headers = self.headers(agent)
        data = None
        if body is not None and form:          # Hister's alias calls are form posts
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path + ("?" + query if query else ""), data=data, method=method,
                                     headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            if (self.token or self.access_token is not None) and 300 <= e.code < 400:
                raise BackendError(e.code, "%s answered a redirect (%s); not followed" % (self.room, e.code), self.room)
            body = e.read().decode("utf-8", "replace")[:300]
            try:
                body = json.loads(body).get("error", body)
            except (ValueError, AttributeError):
                pass
            raise BackendError(e.code, str(body), self.room)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise BackendError(0, "%s unreachable: %s" % (self.room, getattr(e, "reason", e)), self.room)
