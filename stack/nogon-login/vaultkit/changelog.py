"""GET /api/changelog: an app serves its own CHANGELOG.md (docs/principles.md, principle 7), so the landing page can say
what a deploy brought without a forge, a registry or a token.

    status, body, headers = changelog.handle(path, request_headers)

The app puts it behind the same gate as its /api/status (open where that is open, the owner's where that is gated),
answers HEAD like GET without the body, and copies CHANGELOG.md into its image. The body is the file's first LIMIT
bytes (cut at the last whole line when longer), as text/markdown; an ETag lets a poller ask again cheaply (304).
A missing or unreadable file is a 404, never a 500.
"""
import hashlib

LIMIT = 64 * 1024
CONTENT_TYPE = "text/markdown; charset=utf-8"


def load(path, limit=LIMIT):
    """The changelog's first `limit` bytes, cut at the last newline when the file is longer; None when unreadable."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        data = data[:limit]
        cut = data.rfind(b"\n")
        data = data[:cut + 1] if cut > 0 else data
    return data.decode("utf-8", "replace").encode("utf-8")


def etag(body):
    return '"%s"' % hashlib.sha256(body).hexdigest()[:20]


def handle(path, headers=None, limit=LIMIT):
    """(status, body bytes, [(header, value)]) for GET /api/changelog. headers: the request's (anything with .get)."""
    body = load(path, limit)
    if body is None:
        return 404, b"no changelog\n", [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store")]
    tag = etag(body)
    out = [("Content-Type", CONTENT_TYPE), ("ETag", tag), ("Cache-Control", "no-cache"), ("X-Content-Type-Options", "nosniff")]
    asked = ((headers.get("If-None-Match") if headers is not None else "") or "").strip()
    if asked and tag in [t.strip() for t in asked.split(",")] or asked == "*":
        return 304, b"", out
    return 200, body, out
