"""HTTP client for the rooms: a fixed base URL per backend, JSON in and out, stdlib only.

Invariants (tests guard them): no Origin or Referer ever goes to Konbini, Kura or Niwa (a same-origin header is
what makes Konbini and Niwa treat a caller as "web", the owner's powers); Hister calls always carry
`Origin: hister://`; every call names the caller with X-Agent; there's no way to reach a URL the operator didn't
configure (callers pass a path, never a URL)."""
import json
import urllib.error
import urllib.parse
import urllib.request

HISTER_ORIGIN = "hister://"
FORBIDDEN_HEADERS = {"origin", "referer"}


class BackendError(Exception):
    """A backend answered with an error status (or couldn't be reached: status 0)."""

    def __init__(self, status, message, room=""):
        super().__init__(message)
        self.status, self.message, self.room = status, message, room


class Backend:
    def __init__(self, room, base, origin=None, timeout=15, opener=None):
        self.room, self.base, self.origin, self.timeout = room, base.rstrip("/"), origin, timeout
        self.opener = opener or urllib.request.build_opener()

    def headers(self, agent):
        h = {"Accept": "application/json", "X-Agent": agent}
        if self.origin:
            h["Origin"] = self.origin
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
            body = e.read().decode("utf-8", "replace")[:300]
            try:
                body = json.loads(body).get("error", body)
            except (ValueError, AttributeError):
                pass
            raise BackendError(e.code, str(body), self.room)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise BackendError(0, "%s unreachable: %s" % (self.room, getattr(e, "reason", e)), self.room)
