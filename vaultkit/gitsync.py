"""GitSync: a read-write clone of the vault that a service commits to in batches and keeps in step with the
remote. Generalized from Konbini's writer (vaultkit 0.2.0); Niwa uses it for its garden fields.

- touch(summary) records a change; the worker commits after `idle` seconds without changes (or `max_wait`
  after the first), as one commit by `author`, then pulls and pushes.
- A pull never stashes: pending writes are committed first. If the rebase onto upstream conflicts, replay()
  rebuilds our unpushed commits file by file: notes by a three-way frontmatter merge (merge_note: the body is
  always upstream's), event logs (`events_dir/*.jsonl`) as line unions, .gitattributes as a line union.
  A note that can't be merged keeps upstream's version, and the loss is logged. Git conflict markers are never
  committed.
- on_pull(head) is called after a pull moved HEAD (reindex there).
"""
import os
import subprocess
import threading
import time

from .front import CONFLICT_RE
from .frontmatter import merge_note
from .notes import safe_path


class GitSync:
    def __init__(self, repo, author, paths, events_dir="", label="sync", idle=120, max_wait=600, pull_every=60,
                 on_pull=None, env=None):
        self.repo, self.author, self.paths = repo, author, list(paths)
        self.events_dir, self.label = events_dir.strip("/"), label
        self.idle, self.max_wait, self.pull_every = idle, max_wait, pull_every
        self.on_pull = on_pull
        self.env = env
        self.lock = threading.RLock()
        self.pending = []
        self.first = self.last = 0.0
        self.last_pull = 0.0
        self.error = ""

    # -- git ------------------------------------------------------------------

    def run(self, *args, timeout=300):
        env = dict(os.environ, **self.env) if self.env else None
        try:
            return subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True, timeout=timeout,
                                  check=True, env=env).stdout
        except (subprocess.SubprocessError, OSError) as e:
            print("git %s failed: %s" % (" ".join(a for a in args if not a.startswith("user.")),
                                         (getattr(e, "stderr", "") or str(e)).strip()), flush=True)
            return ""

    def no_symlinks(self):
        """core.symlinks=false (v0.22, KURA-2): a link committed upstream is checked out as a plain file, so no write
        (ours or replay's) goes through it to a file outside the vault. Set once per process."""
        if getattr(self, "_no_symlinks", False):
            return
        if self.run("config", "--get", "--default", "", "core.symlinks").strip() != "false":
            self.run("config", "core.symlinks", "false")
        for entry in self.run("ls-files", "-s", "-z").split("\0"):
            meta, _, rel = entry.partition("\t")
            if meta.startswith("120000 ") and os.path.islink(os.path.join(self.repo, rel)):
                os.unlink(os.path.join(self.repo, rel))
                self.run("checkout", "-q", "--", rel)
        self._no_symlinks = True

    def git(self, *args):
        return self.run("-c", "user.name=%s" % self.author[0], "-c", "user.email=%s" % self.author[1],
                        "-c", "commit.gpgsign=false", *args)

    def head(self):
        return self.run("rev-parse", "HEAD").strip()

    # -- changes --------------------------------------------------------------

    def touch(self, summary):
        with self.lock:
            now = time.time()
            if not self.pending:
                self.first = now
            self.last = now
            self.pending.append(summary)

    def ensure_gitattributes(self):
        if not self.events_dir:
            return
        path = os.path.join(self.repo, ".gitattributes")
        line = "%s/*.jsonl merge=union" % self.events_dir
        existing = open(path).read() if os.path.exists(path) else ""
        if line not in existing:
            with open(path, "a") as f:
                f.write(("" if existing.endswith("\n") or not existing else "\n") + line + "\n")

    def dirty(self):
        return bool(self.run("status", "--porcelain", "--", *self.paths, ".gitattributes").strip())

    def commit(self, force=False):
        with self.lock:
            if not self.pending and not (force and self.dirty()):
                return
            pending = self.pending or ["sync"]
            seen, parts = set(), []
            for summary in pending:
                if summary not in seen:
                    seen.add(summary)
                    parts.append(summary)
            msg = "%s: %d change%s (%s)" % (self.label, len(pending), "" if len(pending) == 1 else "s",
                                           ", ".join(parts[:8]) + (", ..." if len(parts) > 8 else ""))
            self.ensure_gitattributes()
            self.git("add", "-A", "--", *[p for p in self.paths if os.path.exists(os.path.join(self.repo, p))],
                     *([".gitattributes"] if os.path.exists(os.path.join(self.repo, ".gitattributes")) else []))
            bad = self.conflicted()
            if bad:
                self.git("reset", "-q")
                self.error = "refusing to commit git conflict markers in " + ", ".join(bad[:3])
                print("export: " + self.error, flush=True)
                return
            if self.run("diff", "--cached", "--name-only").strip():
                self.git("commit", "-q", "-m", msg)
            self.pending = []
            print("export: " + msg, flush=True)

    def ahead(self):
        out = self.run("rev-list", "--count", "@{u}..HEAD").strip()
        return int(out) if out.isdigit() else 0

    def conflicted(self):
        """Staged files whose change adds git conflict marker lines."""
        bad, current = [], None
        for line in self.run("diff", "--cached", "-U0", "--no-color", "--no-ext-diff").splitlines():
            if line.startswith("+++ "):
                name = line[4:].rstrip("\t").strip('"')
                current = name[2:] if name.startswith("b/") else None
            elif line.startswith("+") and current and CONFLICT_RE.match(line[1:]) and current not in bad:
                bad.append(current)
        return bad

    def rebasing(self):
        return any(os.path.exists(os.path.join(self.repo, ".git", d)) for d in ("rebase-merge", "rebase-apply"))

    def blob(self, rev, rel):
        r = subprocess.run(["git", "-C", self.repo, "show", "%s:%s" % (rev, rel)], capture_output=True, text=True,
                           timeout=60)
        return r.stdout if r.returncode == 0 else None

    # -- sync -----------------------------------------------------------------

    def pull(self):
        with self.lock:
            self.no_symlinks()
            if self.dirty():
                self.commit(force=True)
            if self.dirty() or self.rebasing():
                self.error = "working tree not clean; not pulling"
                print("sync: " + self.error, flush=True)
                return False
            before = self.head()
            self.git("fetch", "-q", "origin")
            upstream = self.run("rev-parse", "@{u}").strip()
            if not upstream:
                return False
            if self.ahead():
                self.git("rebase", "-q", upstream)
                if self.rebasing():
                    self.git("rebase", "--abort")
                    if not self.replay(upstream):
                        self.error = "rebase conflicted and replay failed; will retry"
                        print("sync: " + self.error, flush=True)
                        return False
            else:
                self.git("merge", "-q", "--ff-only", upstream)
            after = self.head()
            if after != before and self.on_pull:
                self.on_pull(after)
            return True

    def replay(self, upstream):
        """Rebuild our unpushed commits on top of upstream, file by file, instead of committing conflict markers."""
        base = self.run("merge-base", "HEAD", upstream).strip()
        ours = self.head()
        if not base or not ours:
            return False
        names = [n for n in self.run("diff", "--name-only", "-z", base, ours).split("\0") if n]
        subjects = [l for l in self.run("log", "--format=%s", "%s..%s" % (base, ours)).splitlines() if l]
        merged, notes = {}, []
        for rel in names:
            b, o, t = self.blob(base, rel), self.blob(ours, rel), self.blob(upstream, rel)
            if self.events_dir and rel.startswith(self.events_dir + "/") and rel.endswith(".jsonl"):
                seen = set((b or "").splitlines()) | set((t or "").splitlines())
                new = [l for l in (o or "").splitlines() if l not in seen]
                text = t or ""
                merged[rel] = text + ("" if not text or text.endswith("\n") else "\n") + "".join(l + "\n" for l in new)
            elif rel == ".gitattributes":
                lines = (t or "").splitlines()
                merged[rel] = "\n".join(lines + [l for l in (o or "").splitlines() if l not in lines]) + "\n"
            elif o is None:
                notes.append("%s: we deleted it; kept upstream" % rel)
            elif t is None:
                if b is None:
                    merged[rel] = o
                else:
                    notes.append("%s: gone upstream; dropped our edit" % rel)
            elif b is None:
                notes.append("%s: created on both sides; kept upstream" % rel)
            else:
                text = merge_note(b, o, t)
                if text is None:
                    notes.append("%s: couldn't merge frontmatter; kept upstream" % rel)
                else:
                    merged[rel] = text
        self.git("reset", "-q", "--hard", upstream)
        for rel, text in list(merged.items()):
            try:
                path = safe_path(self.repo, rel)
            except ValueError as e:
                notes.append("%s: not written (%s)" % (rel, e))
                del merged[rel]
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        if merged:
            self.git("add", "-A", "--", *merged.keys())
        if self.conflicted():
            self.git("reset", "-q", "--hard", upstream)
            return False
        if self.run("diff", "--cached", "--name-only").strip():
            msg = subjects[0] if len(subjects) == 1 else "%s: %d commits replayed (%s)" % (
                self.label, len(subjects), "; ".join(s.replace(self.label + ": ", "") for s in subjects[:4]))
            self.git("commit", "-q", "-m", msg + "\n\nReplayed onto upstream after a conflicting edit.")
        for n in notes:
            print("sync: replay: " + n, flush=True)
        print("sync: rebase conflicted; replayed %d file(s) onto %s" % (len(merged), upstream[:8]), flush=True)
        return True

    def push(self):
        with self.lock:
            if self.ahead():
                self.git("push", "-q", "origin", "HEAD")
                if self.ahead():
                    self.error = "push failed; will retry after the next pull"
                    print("sync: " + self.error, flush=True)
                else:
                    self.error = ""
                    print("sync: pushed", flush=True)

    def step(self, now=None):
        """One worker tick: commit a finished batch, then (between batches) pull and push."""
        now = now or time.time()
        if self.pending and (now - self.last >= self.idle or now - self.first >= self.max_wait):
            self.commit()
        if not self.pending and (now - self.last_pull >= self.pull_every or self.ahead()):
            self.last_pull = now
            if self.pull():
                self.push()

    def worker(self, tick=10):
        while True:
            time.sleep(tick)
            try:
                self.step()
            except Exception as exc:        # keep syncing after transient failures
                self.error = str(exc)
                print("sync failed: %s" % exc, flush=True)

    def status(self):
        return {"pending": len(self.pending), "ahead": self.ahead(), "error": self.error or None}
