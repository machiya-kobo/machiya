"""Settings from an env file, for native installs (docs/install/bsd.md). OpenBSD's rc.d can't set environment
variables for a daemon, so each app reads `<APP>_ENV_FILE` (or `--env-file PATH`) before it reads its settings:

    # /etc/kura.env
    KURA_BIND=127.0.0.1
    KURA_REPO_URL=ssh://git@git.example.net/owner/vault.git
    KURA_USERS="you@example.com"        # quotes are optional; a comment after an unquoted value needs a space before #

Rules: one KEY=VALUE per line; blank lines and lines starting with # are skipped; an optional `export ` prefix is
allowed; a value may be wrapped in matching single or double quotes (taken literally); there is no shell expansion
($VAR, ~, backslashes stay as they are). The real environment wins: a variable that is already set is left alone.
A line that isn't KEY=VALUE is an error (a service should fail at start-up, not run with half its settings). Errors
name the file, the line number and the key, never the line itself: it may hold a secret.
"""
import os
import re
import sys

KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(ValueError):
    pass


def parse(text, name="env file"):
    """KEY=VALUE text -> [(key, value)], in order."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not KEY_RE.match(key):
            raise EnvFileError("%s, line %d: not KEY=VALUE (the key must be letters, digits and _)" % (name, n))
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif value[:1] in ("'", '"'):
            raise EnvFileError("%s, line %d: %s has an unterminated quote" % (name, n, key))
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0]      # KEY=value  # a comment
        out.append((key, value))
    return out


def load(path, environ=None):
    """Read `path` into the environment (os.environ by default) without overriding what is already set.
    -> the keys it set. A missing or unreadable file raises OSError; a bad line raises EnvFileError."""
    environ = os.environ if environ is None else environ
    with open(path, encoding="utf-8") as f:
        pairs = parse(f.read(), path)
    applied = []
    for key, value in pairs:
        if key not in environ:
            environ[key] = value
            applied.append(key)
    return applied


def load_for(app, argv=None, environ=None):
    """The app's env file, if one is named: `--env-file PATH` (or `--env-file=PATH`) on the command line, else
    `<APP>_ENV_FILE` in the environment. Call it first thing, before any setting is read. -> the path, or ""."""
    environ = os.environ if environ is None else environ
    argv = sys.argv[1:] if argv is None else argv
    path = ""
    for i, a in enumerate(argv):
        if a == "--env-file" and i + 1 < len(argv):
            path = argv[i + 1]
        elif a.startswith("--env-file="):
            path = a.split("=", 1)[1]
    path = path or environ.get("%s_ENV_FILE" % app.upper(), "")
    if path:
        load(path, environ)
    return path
