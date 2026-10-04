"""code-import's tokens: every credential lives in a file (its first line), never in the environment, argv, a URL or a
log. The Hister token and the forge tokens are handled the same way:

- set but missing, empty or not a token: refuse to start (token_file raises SystemExit, never echoing the contents);
- the file is re-read when it changes (inode, mtime, size), so a rotated token needs no restart; a file that vanishes
  or holds no token keeps the last good value (a rotation in progress);
- while a token is set, a redirect is never followed (NoRedirect): urllib would carry the header to wherever it points.
"""
import os
import re
import threading
import urllib.request

TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}")


class SecretFile:
    """A token kept in a file (its first line). The value is never in repr() or a log line."""

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


def token_file(path, what, required=False):
    """A *_TOKEN_FILE setting -> a SecretFile; None when unset (and not required)."""
    path = (path or "").strip()
    if not path:
        if required:
            raise SystemExit("code-import: %s is required" % what)
        return None
    if not os.path.isfile(path):
        raise SystemExit("code-import: %s: no token in %s" % (what, path))
    secret = SecretFile(path)
    if not secret.value:
        raise SystemExit("code-import: %s: %s doesn't hold a token on its first line" % (what, path))
    return secret


def value(token):
    """A SecretFile, a plain string (tests) or None -> the token's current value ("" for none)."""
    if token is None:
        return ""
    return token.get() if hasattr(token, "get") else token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                 # a 3xx comes back as an HTTPError: the token never follows it
