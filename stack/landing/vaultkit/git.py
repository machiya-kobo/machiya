"""git for vault services: a plain runner, and Mirror, a read-only copy of a remote kept up to date.

A credential for an https remote comes from a file (a token) and travels as an HTTP header through git's
environment config (GIT_CONFIG_COUNT/KEY/VALUE), so it is never in argv, never in .git/config, and never in a log.
"""
import base64
import os
import subprocess


class Git:
    def __init__(self, repo):
        self.repo = repo
        self.env = None             # extra environment for every call (Mirror's credential)

    def run(self, *args, timeout=300):
        """stdout, or "" when git fails (the failure is printed, minus any `-c user.*` values)."""
        try:
            env = dict(os.environ, **self.env) if self.env else None
            return subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True,
                                  timeout=timeout, check=True, env=env).stdout
        except (subprocess.SubprocessError, OSError) as e:
            print("git %s failed: %s" % (" ".join(a for a in args if not a.startswith("user.")),
                                         (getattr(e, "stderr", "") or str(e)).strip()), flush=True)
            return ""

    def head(self):
        return self.run("rev-parse", "HEAD").strip()


def auth_env(token, user="token"):
    """git environment that sends `token` as HTTP basic auth (Forgejo, Gitea and GitHub accept a token as the
    password). Empty when there is no token."""
    if not token:
        return {}
    basic = base64.b64encode(("%s:%s" % (user, token)).encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic " + basic, "GIT_TERMINAL_PROMPT": "0"}


def read_secret(path):
    """The first line of a secret file ('' when path is empty or unreadable)."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


class Mirror(Git):
    """A read-only clone of `url` at `dest`: clone once, then fetch and hard-reset to the remote branch.
    Local changes are thrown away by design (nothing writes here)."""

    def __init__(self, url, dest, branch="", token="", user="token"):
        super().__init__(dest)
        self.url, self.branch = url, branch
        self.env = auth_env(token, user)

    def update(self):
        """Clone or fetch; returns (HEAD, changed?)."""
        before = self.head() if os.path.isdir(os.path.join(self.repo, ".git")) else ""
        if not before:
            os.makedirs(self.repo, exist_ok=True)
            args = ["clone", "-q"] + (["-b", self.branch] if self.branch else []) + ["--", self.url, "."]
            self.run(*args, timeout=1800)
        else:
            self.run("fetch", "-q", "--prune", "origin")
            self.run("reset", "-q", "--hard", "origin/" + self.branch if self.branch else "@{upstream}")
        after = self.head()
        return after, bool(after) and after != before


def borrow(repo, reference, sparse=(), timeout=1800):
    """Make the clone at `repo` share objects with `reference` (the Machiya stack's vault-mirror) and, with `sparse`,
    check out only those top-level paths (cone mode, e.g. ("personal", ".board")). Idempotent: run it at start-up.

    A fresh clone would use `git clone --reference`; this does the same for a clone that already exists: it writes
    .git/objects/info/alternates, then `git repack -a -d -l` drops every object the reference already has (so the
    history is on disk once). New commits the app makes stay in its own object store. The reference must be
    mounted at the same absolute path wherever this repo is opened, and must never prune (vault-mirror sets
    gc.pruneExpire=never). Returns True when the alternates were already in place, False after adopting them."""
    g = Git(repo)
    objects = os.path.join(reference.rstrip("/"), ".git", "objects")
    if not os.path.isdir(objects):
        raise FileNotFoundError("no git objects at %s (is the mirror mounted?)" % objects)
    alt = os.path.join(repo, ".git", "objects", "info", "alternates")
    try:
        with open(alt, encoding="utf-8") as f:
            present = objects in f.read().split()
    except OSError:
        present = False
    if not present:
        os.makedirs(os.path.dirname(alt), exist_ok=True)
        with open(alt, "a", encoding="utf-8") as f:
            f.write(objects + "\n")
        g.run("repack", "-a", "-d", "-l", "-q", timeout=timeout)
    if sparse:
        want = sorted(p.strip("/") for p in sparse)
        enabled = g.run("config", "--get", "--default", "false", "core.sparseCheckout").strip() == "true"
        have = sorted(g.run("sparse-checkout", "list").split()) if enabled else []
        if have != want:
            g.run("sparse-checkout", "set", "--cone", *want, timeout=timeout)
    return present
