"""code-import: the owner's repos on Forgejo and GitHub, searchable in Hister (Machiya, stack/code-import).

Every CODE_IMPORT_INTERVAL seconds:
  1. every forge lists every repo of its owners; forks, archived repos, mirrors, the names in CODE_IMPORT_EXCLUDE (none
     by default) and the losing half of a twin (a repo on both forges, CODE_IMPORT_TWINS) are left out, and a repo that
     was imported before and is now left out, gone, renamed or moved has its documents withdrawn;
  2. per repo: a card (name, description, topics); when the repo changed (its push stamp), on its first run and on a
     full run, the README and the markdown docs of the default branch and the releases;
  3. issues and PRs (title, body, state; no comments) changed since the cursor (one search per owner on Forgejo,
     one conditional list per repo on GitHub);
  4. each becomes one Hister document at its real forge URL, `metadata.source: code` with code_host, code_repo,
     code_kind, code_state, code_private, always with `html`, after our own secret scan (redact or refuse). A URL
     Hister already holds as somebody else's page (the owner browsed it) is left alone; only documents whose
     metadata.source is `code` are ever replaced or deleted.
A full run (the first, then every CODE_IMPORT_FULL_INTERVAL) re-reads everything and withdraws what disappeared.
Each full run of a source (and its first run after a start, when its last check is over a day old) first reconciles:
every document the state says Hister holds is asked for (HEAD /api/document), and the ones Hister lost (a "delete
matching documents" on its Rules page, a reset) are forgotten and sent again in the same run.
No code bodies (phase 2). Nothing is written to a forge: GET only.

  python3 codeimport.py               the service: a run every CODE_IMPORT_INTERVAL seconds, status in status.json
  python3 codeimport.py --once [--full]
                                      one run (a full one with --full), then exit (non-zero if it failed)
  python3 codeimport.py --dry-run [--limit N] [--repo owner/name]
                                      print what would be added (at most N documents per repo, default 5); writes
                                      nothing anywhere (no state, no Hister writes; Hister is only asked which URLs it
                                      has, and only if CODE_IMPORT_HISTER_URL is set)
"""
import argparse
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hister as histermod                                     # noqa: E402
import render                                                  # noqa: E402
import secretscan                                              # noqa: E402
import tokens                                                  # noqa: E402
from forgejo import Forgejo                                    # noqa: E402
from forges import RETRY_DELAYS, ForgeError, Repo, iso          # noqa: E402
from github import GitHub                                      # noqa: E402
from store import Store                                        # noqa: E402

VERSION = "0.1.5"
DOC_V = 1               # the documents' shape: part of every fingerprint, so a bump re-sends everything once
FINAL = ("added", "known", "refused", "rejected", "skipped")
DOC_EXTS = (".md", ".markdown", ".mdown", ".mkd")
README_RE = re.compile(r"^readme(\.(md|markdown|mdown|mkd|txt|rst|org))?$", re.I)
DEFAULT_EXCLUDE = ""                    # none by default (0.1.4): a deployment names its own
# Vendored, sample and test trees (decided 2026-10-05): READMEs, docs/ and the other markdown stay. Any depth.
DEFAULT_DOC_SKIP = tuple(p for d in ("node_modules", "vendor", "third_party", "sample-vault", "tests", "test", "fixtures",
                                     "examples") for p in (d + "/*", "*/" + d + "/*"))
HOSTS = {"forgejo": "Forgejo", "github": "GitHub"}
RECONCILE_EVERY = 86400     # at a start, a source whose last complete reconcile is older than this is checked at once
RECONCILE_LIMIT = 300       # seconds one source's check may take; past it, it stops (the next full run checks again)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WINDOW_RE = re.compile(r"^(%s)[a-z]*\s+([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-3]):([0-5]\d)$" % "|".join(DAYS), re.I)


def repo_key(owner, name):
    """metadata.code_repo: a value Hister v0.20.0 can match. Metadata values are tokenized when indexed and a
    metadata query is one unanalyzed term, so the value must be a single lowercase token: `/`, `-`, `.` and `:` split
    it (tested on a throwaway Hister 2026-10-04), `_` doesn't. `machiya-kobo/kura` -> `machiya_kobo__kura`."""
    def part(s):
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "_"
    return "%s__%s" % (part(owner), part(name))


def fingerprint(*parts):
    return hashlib.sha256(json.dumps([DOC_V] + list(parts), sort_keys=True, default=str).encode()).hexdigest()[:32]


def pause_window(value):
    """`Sun 02:20-02:50` -> (weekday, (h, m), (h, m)); empty -> None. Within one day, in CODE_IMPORT_TZ."""
    value = (value or "").strip()
    if not value:
        return None
    m = WINDOW_RE.match(value)
    if not m:
        raise SystemExit("code-import: CODE_IMPORT_PAUSE is like 'Sun 02:20-02:50', not %r" % value)
    start, end = (int(m.group(2)), int(m.group(3))), (int(m.group(4)), int(m.group(5)))
    if not start < end:
        raise SystemExit("code-import: CODE_IMPORT_PAUSE must end after it starts (%r)" % value)
    return DAYS.index(m.group(1).lower()), start, end


def in_window(window, tz, now=None):
    if window is None:
        return False
    from zoneinfo import ZoneInfo
    day, start, end = window
    t = datetime.fromtimestamp(now or time.time(), ZoneInfo(tz))
    return t.weekday() == day and start <= (t.hour, t.minute) < end


# -- which repos ------------------------------------------------------------------------------------------------------

def parse_twins(value):
    """CODE_IMPORT_TWINS: `github:your-org=forgejo:your-org,forgejo:you=github:you`: a repo of the
    right-hand owner whose name matches a repo of the left-hand owner is the same repo, indexed once, on the left."""
    out = []
    for pair in [p.strip() for p in (value or "").split(",") if p.strip()]:
        try:
            win, lose = pair.split("=")
            (wh, wo), (lh, lo) = win.split(":"), lose.split(":")
        except ValueError:
            raise SystemExit("code-import: CODE_IMPORT_TWINS takes host:owner=host:owner pairs, not %r" % pair)
        if wh not in HOSTS or lh not in HOSTS or wh == lh:
            raise SystemExit("code-import: CODE_IMPORT_TWINS pairs two different hosts (forgejo, github): %r" % pair)
        out.append(((wh, wo.strip().lower()), (lh, lo.strip().lower())))
    return out


class Rules:
    """Forks, archived repos, mirrors, excluded names (`name` or `owner/name`, any case) and twins are left out."""

    def __init__(self, exclude=DEFAULT_EXCLUDE, twins=()):
        self.exclude = {x.strip().lower() for x in (exclude or "").split(",") if x.strip()}
        self.twins = list(twins)

    def decide(self, repos):
        """{(host, id): (included, reason, twin url)}"""
        index = {(r.host, r.owner.lower(), r.name.lower()): r for r in repos}
        out = {}
        for r in repos:
            reason, twin = "", ""
            if r.fork:
                reason = "fork"
            elif r.archived:
                reason = "archived"
            elif r.mirror:                              # a pull mirror (decided 2026-10-05): the original is elsewhere
                reason = "mirror"
            elif r.name.lower() in self.exclude or r.full_name.lower() in self.exclude:
                reason = "excluded"
            else:
                for (wh, wo), (lh, lo) in self.twins:
                    if (r.host, r.owner.lower()) == (lh, lo) and (wh, wo, r.name.lower()) in index:
                        reason = "twin"
                        break
                    if (r.host, r.owner.lower()) == (wh, wo) and (lh, lo, r.name.lower()) in index:
                        twin = index[(lh, lo, r.name.lower())].url
            out[(r.host, r.id)] = (not reason, reason, twin)
        return out


def parse_readme_only(value):
    """CODE_IMPORT_README_ONLY: `github:you/big-fork,…`: repos imported as their card and README only
    (no other docs, issues, PRs or releases). -> {(host, "owner/repo")}, lowercase."""
    out = set()
    for item in [x.strip() for x in (value or "").split(",") if x.strip()]:
        host, _, name = item.partition(":")
        if host not in HOSTS or name.count("/") != 1 or not all(name.split("/")):
            raise SystemExit("code-import: CODE_IMPORT_README_ONLY takes host:owner/repo items (github:owner/repo), "
                             "not %r" % item)
        out.add((host, name.lower()))
    return out


def source_winners(rules, host, owner):
    """The sources whose repos win over this source's same-named ones (its twins' sources)."""
    return ["%s:%s" % w for w, (lh, lo) in rules.twins if (lh, lo) == (host, owner.lower())]


class Ctx:
    """A repo as its documents need it."""

    def __init__(self, forge, repo, twin, secrets):
        self.forge, self.repo, self.twin = forge, repo, twin
        self.fp = fingerprint(repo.full_name, repo.url, repo.private, repo.branch, twin, secrets)
        self.shown = 0
        self.readme_only = False


# -- the importer -------------------------------------------------------------------------------------------------------

class Importer:
    def __init__(self, forges, store, hister=None, *, rules=None, dry_run=False, secrets="redact",
                 full_interval=21600, max_docs=200, max_doc_bytes=262144, max_text=100000, doc_skip=(), limit=None,
                 only_repo=None, readme_only=(), reconcile_limit=RECONCILE_LIMIT, out=print, clock=time.time):
        self.forges, self.store, self.hister, self.rules = forges, store, hister, rules or Rules()
        self.dry_run, self.secrets, self.full_interval = dry_run, secrets, full_interval
        self.max_docs, self.max_doc_bytes, self.max_text = max_docs, max_doc_bytes, max_text
        self.doc_skip = tuple(DEFAULT_DOC_SKIP) + tuple(doc_skip)
        self.limit, self.only_repo = limit, (only_repo or "").lower() or None
        self.readme_only = set(readme_only)
        self.out, self.clock = out, clock
        self.stats, self.fresh, self.ctx, self.sources = {}, set(), {}, {}
        self.later, self.touched = None, set()
        self.reconcile_limit = reconcile_limit
        self.checked = set()            # the sources whose start-up reconcile is settled (in this process)
        self.reconciled = {}            # this run's reconciles: source -> (checked, missing, complete)

    def log(self, msg):
        self.out("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))

    def note(self, host, key, n=1):
        self.stats.setdefault(host, {}).setdefault(key, 0)
        self.stats[host][key] += n

    def lookup(self, url):
        return self.hister.document(url) if self.hister else None

    # -- withdrawing -----------------------------------------------------------------------------------------------------

    def defer(self, fn):
        """Within a source's pass, writes that withdraw or move on wait until the whole source has completed: a
        source that fails part way withdraws nothing (fail closed per source)."""
        if self.later is None:
            fn()
        else:
            self.later.append(fn)

    def withdraw(self, row):
        """A document whose source is gone: deleted from Hister only if it is ours there; the row is forgotten.
        Deferred to the end of the source's pass; a URL that was written again meanwhile (touched) is left alone."""
        row = dict(row)
        self.defer(lambda: None if row["url"] in self.touched else self.withdraw_now(row))

    def withdraw_now(self, row):
        url = row["url"]
        if row["status"] == "added":
            if self.dry_run:
                self.out(json.dumps({"would_delete": url}))
                self.note(row["host"], "would delete")
                return
            doc = self.lookup(url)
            if histermod.is_ours(doc):
                status = self.hister.delete(url)
                if status >= 300:
                    raise histermod.HisterDown("Hister POST /api/delete: HTTP %d" % status)
                self.note(row["host"], "deleted")
            elif doc is not None:
                self.note(row["host"], "left alone (a page now)")
        current = self.store.doc(url)
        if not self.dry_run and current and (current["status"], current["src"]) == (row["status"], row["src"]):
            self.store.drop_doc(url)                    # (a row rewritten since, e.g. now refused, stays)

    def withdraw_repo(self, host, repo_id, kinds=None):
        for row in self.store.docs(host, repo_id, kinds):
            self.withdraw(row)

    # -- one document ----------------------------------------------------------------------------------------------------

    def put(self, ctx, kind, key, url, src, build, updated=0):
        """build() -> (title, line, body markdown, extra metadata); called only when the source changed."""
        host, repo = ctx.forge.name, ctx.repo
        url = histermod.norm(url)
        self.touched.add(url)
        row = self.store.doc(url)
        if row and row["src"] == src and row["status"] in FINAL:
            self.note(host, "unchanged")
            return url
        if self.dry_run and self.limit is not None and ctx.shown >= self.limit:
            self.note(host, "not shown (--limit)")
            return url
        built = build()
        if built is None:                                   # not a document after all (binary, …)
            self.store.put_doc(url, host=host, repo_id=repo.id, kind=kind, key=key, src=src, status="skipped")
            self.note(host, "skipped")
            return url
        title, line, body, extra = built
        title, k1 = secretscan.redact(title)
        body, k2 = secretscan.redact(body)
        found = sorted(set(k1) | set(k2))
        if found and self.secrets == "refuse":
            self.store.put_doc(url, host=host, repo_id=repo.id, kind=kind, key=key, src=src, status="refused",
                               error="likely secret: " + ",".join(found))
            self.log("  refused (likely secret: %s) %s" % (",".join(found), url))
            self.note(host, "refused (secret)")
            ctx.shown += self.dry_run
            return url
        if len(body) > self.max_text:
            body = body[:self.max_text] + "\n\n[…]"
        meta = {"source": "code", "client": "code-import", "ignore_skip_rules": True, "code_host": host,
                "code_repo": repo_key(repo.owner, repo.name), "code_repo_name": repo.full_name, "code_kind": kind,
                "code_private": "true" if repo.private else "false"}
        if updated:
            meta["code_updated"] = int(updated)
        if ctx.twin:
            meta["code_twin_url"] = ctx.twin
        if found:
            meta["code_redacted"] = "true"
        meta.update(extra)
        doc = {"url": url, "title": title, "text": "%s\n%s\n\n%s" % (title, line, body),
               "html": render.page(title, url, line, body), "added": int(updated or self.clock()), "metadata": meta}

        existing = self.lookup(url)
        if existing is not None and not histermod.is_ours(existing):
            self.store.put_doc(url, host=host, repo_id=repo.id, kind=kind, key=key, src=src, status="known", error=None)
            self.note(host, "already a page")
            if self.dry_run:
                self.out(json.dumps({"already_in_hister": url}))
            return url
        if self.dry_run:
            self.out(json.dumps({"would_add": {k: v for k, v in doc.items() if k not in ("html", "text")},
                                 "html_chars": len(doc["html"]), **({"redacted": found} if found else {})},
                                ensure_ascii=False))
            self.note(host, "would add")
            ctx.shown += 1
            return url
        status, reply = self.hister.add(doc)
        if status in (200, 201):
            self.store.put_doc(url, host=host, repo_id=repo.id, kind=kind, key=key, src=src, status="added", error=None)
            self.note(host, "added " + kind + (" (redacted)" if found else ""))
        else:
            self.store.put_doc(url, host=host, repo_id=repo.id, kind=kind, key=key, src=src, status="rejected",
                               error=("HTTP %d %s" % (status, reply))[:300])
            self.log("  rejected by Hister (%d): %s" % (status, url))
            self.note(host, "rejected")
        return url

    # -- per repo --------------------------------------------------------------------------------------------------------

    def card(self, ctx):
        r = ctx.repo
        facts = ["Repository", HOSTS[r.host], "private" if r.private else "public"] + ([r.language] if r.language else [])
        body = [r.description or ""]
        if r.topics:
            body.append("Topics: " + ", ".join(r.topics))
        if r.homepage:
            body.append("Homepage: " + r.homepage)
        if ctx.twin:
            body.append("Also at " + ctx.twin)
        extra = {"code_topics": list(r.topics)}
        if r.language:
            extra["code_language"] = r.language.lower()
        src = fingerprint(ctx.fp, "repo", r.description, r.topics, r.language, r.homepage)
        return self.put(ctx, "repo", "repo", r.url, src,
                        lambda: (r.full_name, " · ".join(facts), "\n\n".join(body), extra), updated=r.updated)

    def doc_candidates(self, blobs):
        """README first, then markdown docs by path; hidden folders and CODE_IMPORT_DOC_SKIP left out."""
        out = []
        for path, sha, size in blobs:
            base = posixpath.basename(path)
            readme = "/" not in path and README_RE.match(base)
            if not readme and not path.lower().endswith(DOC_EXTS):
                continue
            if any(seg.startswith(".") for seg in path.split("/")[:-1]):
                continue
            if any(fnmatch.fnmatchcase(path.lower(), pat.lower()) for pat in self.doc_skip):
                continue
            out.append(("readme" if readme else "doc", path, sha, size))
        out.sort(key=lambda d: (d[0] != "readme", d[1].lower()))
        return out

    def docs(self, ctx):
        f, r = ctx.forge, ctx.repo
        seen = set()
        cands = self.doc_candidates(f.tree(r))
        cap = "cap:%s:%s" % (f.name, r.id)
        if ctx.readme_only:                                 # CODE_IMPORT_README_ONLY: the README, never capped
            cands = [c for c in cands if c[0] == "readme"]
        if len(cands) > self.max_docs:
            self.note(f.name, "docs over CODE_IMPORT_MAX_DOCS", len(cands) - self.max_docs)
            self.log("  %s:%s has %d markdown docs, over CODE_IMPORT_MAX_DOCS=%d: the first %d are imported"
                     % (f.name, r.full_name, len(cands), self.max_docs, self.max_docs))
            if not self.dry_run:
                self.store.meta(cap, json.dumps({"repo": "%s:%s" % (f.name, r.full_name), "docs": len(cands),
                                                 "max": self.max_docs}))
            cands = cands[:self.max_docs]
        elif not self.dry_run:
            self.store.drop_meta(cap)
        for kind, path, sha, size in cands:
            url = histermod.norm(f.doc_url(r, path))
            seen.add(url)
            src = fingerprint(ctx.fp, kind, path, sha)
            if secretscan.secret_name(path) or size > self.max_doc_bytes:
                row = self.store.doc(url)
                if not (row and row["src"] == src):
                    why = "a secret's name" if secretscan.secret_name(path) else "over CODE_IMPORT_MAX_DOC_BYTES"
                    if row and row["status"] == "added":
                        self.withdraw(row)
                    if not self.dry_run:
                        self.defer(lambda url=url, kind=kind, path=path, src=src, why=why: self.store.put_doc(
                            url, host=f.name, repo_id=r.id, kind=kind, key="doc:" + path, src=src,
                            status="refused" if "secret" in why else "skipped", error=why))
                    self.note(f.name, "refused (secret name)" if "secret" in why else "skipped (too big)")
                continue

            def build(path=path, sha=sha, kind=kind):
                data = f.blob(r, sha)
                if b"\0" in data[:8192]:
                    return None
                label = "README" if kind == "readme" else "Doc"
                line = " · ".join([label, r.full_name, HOSTS[r.host], r.branch])
                return ("%s · %s" % (path, r.full_name), line, data.decode("utf-8", "replace"), {"code_path": path})
            self.put(ctx, kind, "doc:" + path, url, src, build, updated=r.updated)
        for row in self.store.docs(f.name, r.id, ("readme", "doc")):
            if row["url"] not in seen:
                self.withdraw(row)

    def releases(self, ctx):
        f, r = ctx.forge, ctx.repo
        seen = set()
        for it in f.releases(r):
            src = fingerprint(ctx.fp, "release", it.key, it.updated, it.title, it.tag, it.state,
                              hashlib.sha256(it.body.encode()).hexdigest())
            extra = {"code_tag": it.tag}
            if it.state == "prerelease":
                extra["code_prerelease"] = "true"
            line = " · ".join(["Release " + (it.tag or it.title), r.full_name, HOSTS[r.host]])
            seen.add(self.put(ctx, "release", it.key, it.url, src,
                              lambda it=it, line=line, extra=extra: ("%s · %s" % (it.title or it.tag, r.full_name),
                                                                     line, it.body, extra), updated=it.updated))
        for row in self.store.docs(f.name, r.id, ("release",)):
            if row["url"] not in seen:
                self.withdraw(row)

    def repo_pass(self, f, r, twin, full):
        prev = self.store.repo(f.name, r.id)
        moved = bool(prev and prev["included"] and (prev["full_name"] != r.full_name or prev["url"] != r.url or
                                                     prev["branch"] != r.branch))
        if moved:                                       # every URL changes: withdraw them all, import it afresh
            self.withdraw_repo(f.name, r.id)
            self.note(f.name, "repo renamed or moved")
        new = prev is None or not prev["included"] or moved
        if new:
            self.fresh.add((f.name, r.id))
        ctx = self.ctx[(f.name, r.id)] = Ctx(f, r, twin, self.secrets)
        ctx.readme_only = (f.name, r.full_name.lower()) in self.readme_only
        mode = "readme-only" if ctx.readme_only else ""
        if not new and (prev["reason"] or "") != mode:      # CODE_IMPORT_README_ONLY changed for it: read it all again
            new = True
            self.fresh.add((f.name, r.id))
            self.note(f.name, "repo mode changed")
        self.card(ctx)
        changed = new or full or prev["stamp"] != r.stamp
        if changed:
            self.docs(ctx)
            if ctx.readme_only:                             # no releases, issues or PRs: withdraw any imported
                for row in self.store.docs(f.name, r.id, ("release", "issue", "pr")):
                    self.withdraw(row)
            else:
                self.releases(ctx)
            self.note(f.name, "repos read")
        stamp = r.stamp if changed else prev["stamp"]
        self.defer(lambda: self.store.put_repo(f.name, r.id, full_name=r.full_name, url=r.url, branch=r.branch,
                                               included=1, reason=mode, stamp=stamp))

    def issue_pass(self, f, repos, full):
        for scope, srepos in f.issue_scopes(repos):
            key = "issues:%s:%s" % (f.name, scope)
            fresh = any((f.name, r.id) in self.fresh for r in srepos)
            cursor = None if (full or fresh) else self.store.meta(key)
            since = iso(int(cursor)) if cursor else None
            newest, seen = int(cursor or 0), set()
            ids = {r.id for r in srepos}
            for repo_id, it in f.issues(scope, srepos, since):
                if repo_id not in ids:                  # a repo that isn't imported (a fork, archived, excluded)
                    continue
                ctx = self.ctx[(f.name, repo_id)]
                newest = max(newest, it.updated)
                src = fingerprint(ctx.fp, it.kind, it.key, it.updated, it.state, it.title,
                                  hashlib.sha256(it.body.encode()).hexdigest())
                r = ctx.repo
                label = "Pull request" if it.kind == "pr" else "Issue"
                line = " · ".join(["%s #%d" % (label, it.number), it.state, r.full_name, HOSTS[r.host]])
                extra = {"code_state": it.state, "code_number": str(it.number)}
                seen.add(self.put(ctx, it.kind, it.key, it.url, src,
                                  lambda it=it, r=r, line=line, extra=extra: (
                                      "%s · %s#%d" % (it.title, r.full_name, it.number), line, it.body, extra),
                                  updated=it.updated))
            if since is None:                           # a complete list: what isn't in it is gone
                for r in srepos:
                    for row in self.store.docs(f.name, r.id, ("issue", "pr")):
                        if row["url"] not in seen:
                            self.withdraw(row)
            if newest and not self.dry_run:
                self.store.meta(key, newest)

    # -- a run -----------------------------------------------------------------------------------------------------------

    def stored(self, host, owner):
        """The state's repos of one source (an owner on a host)."""
        return [row for row in self.store.repos(host) if (row["full_name"] or "").split("/")[0].lower() == owner.lower()]

    def run(self, full=False):
        """One pass over every source (an owner on a forge), each on its own: a source that fails (after each call's
        retries) is recorded in self.sources and withdraws nothing, and the others go on. Raises HisterDown (Hister
        is every source's: the run stops; the state is written per document, so the next run resumes)."""
        self.stats, self.fresh, self.ctx, self.sources, self.reconciled = {}, set(), {}, {}, {}
        now = self.clock()
        srcs = [(f, owner, "%s:%s" % (f.name, owner.lower())) for f in self.forges for owner in f.owner_names()]
        listing = {}
        for f, owner, key in srcs:
            try:
                repos = f.repos(owner)
                known = self.stored(f.name, owner)
                if known and not repos:
                    raise ForgeError("%s listed no repos, though %d were seen before: nothing is withdrawn (a token "
                                     "that lost its access?)" % (key, len(known)))
                listing[key] = repos
            except ForgeError as e:
                self.fail(key, e)
        # a failed source's repos as the state last saw them, so its twins are still recognised as twins
        phantoms = [Repo(host=f.name, id=row["id"], owner=row["full_name"].split("/")[0],
                         name=row["full_name"].split("/", 1)[1], url=row["url"])
                    for f, owner, key in srcs if key not in listing for row in self.stored(f.name, owner)]
        decisions = self.rules.decide(phantoms + [r for rs in listing.values() for r in rs])
        for f, owner, key in srcs:
            if key not in listing:
                continue
            blind = [w for w in source_winners(self.rules, f.name, owner)
                     if w in self.sources and not self.sources[w]["ok"]
                     and not self.stored(w.split(":", 1)[0], w.split(":", 1)[1])]
            if blind:                       # its twins' source failed and was never seen: can't tell twins apart
                self.sources[key] = {"ok": False, "error": "waiting for %s (its twins' source)" % ", ".join(blind)}
                self.log("source %s: skipped, %s" % (key, self.sources[key]["error"]))
                continue
            self.later, self.touched = [], set()
            try:
                n = self.source_pass(f, owner, key, listing[key], decisions, full, now)
            except ForgeError as e:
                held, self.later = len(self.later), None
                self.fail(key, e, held)
                self.log_reconcile(key, False)
                continue
            later, self.later = self.later, None
            for fn in later:
                fn()
            self.sources[key] = {"ok": True, "repos": n}
            self.log_reconcile(key, True)
            if not self.dry_run:
                self.store.meta("last_ok:" + key, int(now))
        if not self.dry_run:
            self.store.http_prune(now - 7 * 86400)
        return self.stats

    def fail(self, key, error, held=0):
        self.sources[key] = {"ok": False, "error": str(error)[:500]}
        self.note(key.split(":", 1)[0], "sources failed")
        self.log("source %s failed: %s%s" % (key, error, "; %d withdrawal(s) held back" % held if held else ""))

    def source_pass(self, f, owner, key, repos, decisions, full, now):
        last_full = self.store.meta("last_full:" + key)
        full = full or last_full is None or now - float(last_full) >= self.full_interval
        if not self.dry_run and self.hister is not None:
            last = self.last_reconcile(key)
            stale = last is None or now - last.get("at", 0) >= RECONCILE_EVERY
            if full or (key not in self.checked and stale):
                checked, missing, complete = self.reconciled[key] = self.reconcile(f, owner, key, now)
                if missing and not full:            # an incremental run doesn't read them all: make it a full one
                    full = True
            self.checked.add(key)
        current = {r.id for r in repos}
        for row in self.stored(f.name, owner):
            if row["id"] not in current:
                self.withdraw_repo(f.name, row["id"])
                if not self.dry_run:
                    self.defer(lambda rid=row["id"]: (self.store.drop_repo(f.name, rid),
                                                      self.store.drop_meta("issues:%s:repo:%s" % (f.name, rid)),
                                                      self.store.drop_meta("cap:%s:%s" % (f.name, rid))))
                self.note(f.name, "repo gone")
        included = []
        for r in repos:
            if self.only_repo and r.full_name.lower() != self.only_repo:
                continue
            ok, reason, twin = decisions[(f.name, r.id)]
            if not ok:
                self.note(f.name, "repo left out: " + reason)
                prev = self.store.repo(f.name, r.id)
                if prev and prev["included"]:
                    self.withdraw_repo(f.name, r.id)
                self.defer(lambda r=r, reason=reason: self.store.put_repo(
                    f.name, r.id, full_name=r.full_name, url=r.url, branch=r.branch, included=0, reason=reason,
                    stamp=""))
                continue
            included.append(r)
            self.note(f.name, "repos")
            self.repo_pass(f, r, twin, full)
        self.issue_pass(f, [r for r in included if not self.ctx[(f.name, r.id)].readme_only], full)
        if full and not self.dry_run and self.limit is None and not self.only_repo:
            self.defer(lambda: self.store.meta("last_full:" + key, int(now)))
        return len(included)

    # -- the reconcile ---------------------------------------------------------------------------------------------------

    def reconcile(self, f, owner, key, now):
        """Does Hister still hold every document the state says this source added? A document it lost is skipped by
        its fingerprint forever (unchanged), so a lost one is forgotten here and the same run sends it again.
        Sequential HEADs (~1,000 documents is cheap). Hister failing (401, 403, 429, 5xx, unreachable) raises
        HisterDown before anything is forgotten: the run stops and the next one (a full one again) checks again. Past
        reconcile_limit seconds it stops: what it found missing so far is forgotten, but the check isn't recorded as
        done. -> (checked, missing, complete)"""
        rows = sorted((d for r in self.stored(f.name, owner) for d in self.store.docs(f.name, r["id"])
                       if d["status"] == "added"), key=lambda d: d["url"])
        started, checked, missing, complete = time.monotonic(), 0, [], True
        for row in rows:
            if time.monotonic() - started > self.reconcile_limit:
                complete = False
                break
            if not self.hister.exists(row["url"]):
                missing.append(row["url"])
            checked += 1
        for url in missing:
            self.store.forget(url)
        self.note(f.name, "reconcile: checked", checked)
        if missing:
            self.note(f.name, "reconcile: missing", len(missing))
        if complete:
            self.store.meta("reconcile:" + key, json.dumps({"checked": checked, "missing": len(missing), "at": int(now)}))
        else:
            self.log("source %s: reconcile stopped after %d s (%d of %d checked): the next full run checks again"
                     % (key, self.reconcile_limit, checked, len(rows)))
        return checked, len(missing), complete

    def last_reconcile(self, key):
        try:
            return json.loads(self.store.meta("reconcile:" + key) or "null")
        except ValueError:
            return None

    def log_reconcile(self, key, ok):
        if key not in self.reconciled:
            return
        checked, missing, complete = self.reconciled[key]
        what = "" if not missing else ", re-added" if ok else ", re-added on the source's next run"
        self.log("source %s: reconcile: %d missing%s (%d checked%s)"
                 % (key, missing, what, checked, "" if complete else ", stopped early"))

    def caps(self):
        """The repos over CODE_IMPORT_MAX_DOCS, as the last walk of each saw them (for the owner to decide)."""
        out = []
        for key, value in self.store.meta_prefix("cap:"):
            try:
                out.append(json.loads(value))
            except ValueError:
                pass
        return sorted(out, key=lambda c: c.get("repo", ""))

    def source_status(self):
        out = {}
        for key, st in self.sources.items():
            out[key] = dict(st, last_success=int(self.store.meta("last_ok:" + key) or 0) or None,
                            last_full=int(self.store.meta("last_full:" + key) or 0) or None,
                            reconcile=self.last_reconcile(key))
        return out


# -- the service ------------------------------------------------------------------------------------------------------

def owner_list(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def build(env, dry_run=False, limit=None, only_repo=None):
    data = env.get("CODE_IMPORT_DATA", "/data")
    store = Store(":memory:" if dry_run else os.path.join(data, "code-import.sqlite3"))
    gap = env.get("CODE_IMPORT_GAP")
    try:
        delays = tuple(float(x) for x in owner_list(env.get("CODE_IMPORT_RETRY_DELAYS", ",".join(map(str, RETRY_DELAYS)))))
        client = {"retry_delays": delays, "timeout": float(env.get("CODE_IMPORT_TIMEOUT", "60"))}
    except ValueError:
        raise SystemExit("code-import: CODE_IMPORT_RETRY_DELAYS is seconds, comma-separated (2,8,30); "
                         "CODE_IMPORT_TIMEOUT is seconds")
    forges = []
    if env.get("CODE_IMPORT_FORGEJO_URL"):
        token = tokens.token_file(env.get("CODE_IMPORT_FORGEJO_TOKEN_FILE"), "CODE_IMPORT_FORGEJO_TOKEN_FILE",
                                  required=True)
        forges.append(Forgejo(env["CODE_IMPORT_FORGEJO_URL"], token, owner_list(env.get("CODE_IMPORT_FORGEJO_OWNERS")),
                              gap=float(gap) if gap else 0.5, cache=store, **client))
    if env.get("CODE_IMPORT_GITHUB_TOKEN_FILES"):
        pairs = []
        for item in owner_list(env["CODE_IMPORT_GITHUB_TOKEN_FILES"]):
            owner, _, path = item.partition("=")
            if not owner.strip() or not path.strip():
                raise SystemExit("code-import: CODE_IMPORT_GITHUB_TOKEN_FILES takes owner=/path/to/token pairs")
            pairs.append((owner.strip(), tokens.token_file(path, "CODE_IMPORT_GITHUB_TOKEN_FILES (%s)" % owner.strip(),
                                                           required=True)))
        forges.append(GitHub(env.get("CODE_IMPORT_GITHUB_API", "https://api.github.com"), pairs,
                             gap=float(gap) if gap else 0.25, cache=store, **client))
    if not forges:
        raise SystemExit("code-import: no forge: set CODE_IMPORT_FORGEJO_URL and/or CODE_IMPORT_GITHUB_TOKEN_FILES")
    hister_url = env.get("CODE_IMPORT_HISTER_URL", "")
    if not hister_url and not dry_run:
        raise SystemExit("code-import: CODE_IMPORT_HISTER_URL is required (unset is allowed only with --dry-run)")
    htoken = tokens.token_file(env.get("CODE_IMPORT_HISTER_TOKEN_FILE"), "CODE_IMPORT_HISTER_TOKEN_FILE")
    hister = histermod.Hister(hister_url, htoken) if hister_url else None
    secrets = env.get("CODE_IMPORT_SECRETS", "redact").strip().lower()
    if secrets not in ("redact", "refuse"):
        raise SystemExit("code-import: CODE_IMPORT_SECRETS is redact or refuse, not %r" % secrets)
    rules = Rules(env.get("CODE_IMPORT_EXCLUDE", DEFAULT_EXCLUDE), parse_twins(env.get("CODE_IMPORT_TWINS")))
    imp = Importer(forges, store, hister, rules=rules, dry_run=dry_run, secrets=secrets,
                   full_interval=max(3600, int(env.get("CODE_IMPORT_FULL_INTERVAL", "21600"))),
                   max_docs=int(env.get("CODE_IMPORT_MAX_DOCS", "200")),
                   max_doc_bytes=int(env.get("CODE_IMPORT_MAX_DOC_BYTES", "262144")),
                   doc_skip=owner_list(env.get("CODE_IMPORT_DOC_SKIP")), limit=limit, only_repo=only_repo,
                   readme_only=parse_readme_only(env.get("CODE_IMPORT_README_ONLY")))
    return imp, data


def write_status(data, **kw):
    path = os.path.join(data, "status.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(kw, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def serve(env, once=False, full=False):
    imp, data = build(env)
    interval = max(60, int(env.get("CODE_IMPORT_INTERVAL", "900")))
    window, tz = pause_window(env.get("CODE_IMPORT_PAUSE")), env.get("CODE_IMPORT_TZ", "UTC")
    imp.log("code-import %s: forges %s, Hister %s, every %d s, a full run every %d s%s" % (
        VERSION, ",".join(f.name for f in imp.forges), env.get("CODE_IMPORT_HISTER_URL"), interval, imp.full_interval,
        (", paused " + env.get("CODE_IMPORT_PAUSE")) if window else ""))
    last_ok, fails, stats = None, 0, {}

    def healthy():
        """ok=false only when broken: after a failed run (Hister down, or any source failed) once no fully good run
        has happened for three intervals. A source that keeps failing turns it false; the others still import."""
        if last_ok is None:
            return fails == 0
        return fails == 0 or time.time() - last_ok < 3 * interval + 300

    while True:
        started = int(time.time())
        error = None
        common = dict(version=VERSION, started=started, last_success=last_ok, failures_in_a_row=fails,
                      counts=imp.store.counts(), sources=imp.source_status(), caps=imp.caps())
        write_status(data, ok=healthy(), running=True, error=None, last_run=stats, **common)
        if in_window(window, tz):
            imp.log("in the pause window: no run")
        else:
            calls = {f.name: (f.calls, f.retries) for f in imp.forges}
            try:
                stats = imp.run(full=full)
                full = False
                for f in imp.forges:
                    imp.note(f.name, "forge calls", f.calls - calls[f.name][0])
                    if f.retries > calls[f.name][1]:
                        imp.note(f.name, "forge retries", f.retries - calls[f.name][1])
                imp.log("run: %s" % json.dumps(stats, sort_keys=True))
                bad = {k: v["error"] for k, v in imp.sources.items() if not v["ok"]}
                if bad:
                    fails += 1
                    error = "; ".join("%s: %s" % kv for kv in sorted(bad.items()))
                    imp.log("run partly failed (%d in a row): %d source(s) of %d" % (fails, len(bad), len(imp.sources)))
                else:
                    last_ok, fails = int(time.time()), 0
            except histermod.HisterDown as e:
                fails += 1
                error = str(e)
                imp.log("run failed (%d in a row): %s" % (fails, e))
        common.update(last_success=last_ok, failures_in_a_row=fails, counts=imp.store.counts(),
                      sources=imp.source_status(), caps=imp.caps())
        write_status(data, ok=healthy(), running=False, error=error, last_run=stats, **common)
        if once:
            return 0 if error is None else 1
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one run, then exit")
    ap.add_argument("--full", action="store_true", help="with --once: a full run (re-read everything, withdraw what is gone)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added; write nothing")
    ap.add_argument("--limit", type=int, default=None, help="dry run: at most N documents per repo (default 5)")
    ap.add_argument("--repo", help="dry run: only this repo (owner/name)")
    args = ap.parse_args(argv)
    env = dict(os.environ)
    if args.dry_run:
        imp, _ = build(env, dry_run=True, limit=args.limit if args.limit is not None else 5, only_repo=args.repo)
        try:
            stats = imp.run(full=True)
        except histermod.HisterDown as e:
            print("dry run failed: %s" % e, file=sys.stderr)
            return 1
        print("dry run: %s (nothing was written)" % json.dumps(stats, sort_keys=True))
        bad = {k: v["error"] for k, v in imp.sources.items() if not v["ok"]}
        for k, e in sorted(bad.items()):
            print("dry run failed for %s: %s" % (k, e), file=sys.stderr)
        return 1 if bad else 0
    return serve(env, once=args.once, full=args.full)


if __name__ == "__main__":
    sys.exit(main())
