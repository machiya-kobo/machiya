"""The forge interface: what code-import needs from a code host (Forgejo/Gitea, GitHub), and nothing else.

A forge answers, for the owners it is configured with:
- owner_names(), repos(owner): every repo of one owner (forks and archived ones too: the importer decides);
- tree(repo):              the default branch's files [(path, blob sha, size)], [] for an empty repo;
- blob(repo, sha):         one file's bytes;
- issue_scopes(repos):     how its issues and PRs are asked for: [(scope, [repos])] (Forgejo: one search per owner;
                           GitHub: one list per repo);
- issues(scope, repos, since): (repo id, Item) for every issue and PR updated at or after `since` (None: all);
- releases(repo):          every published release (no drafts), as Items.
A forge only ever READS (GET). Every failure is a ForgeError, and the run stops without recording anything for it.

The HTTP client sends the token as a header only to the configured API base, never follows a redirect while it holds
one, spaces its calls, and keeps an ETag per URL (in the state) so an unchanged answer is a free 304. A transient
failure (a TLS, connect or read timeout, a reset connection, a 5xx, a 429 or a secondary rate limit) is tried again
after 2, 8 and 30 s (Retry-After honoured, up to MAX_WAIT); 401, 403 and 404 never are.
"""
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

import tokens

USER_AGENT = "machiya-code-import (read-only)"


RETRY_DELAYS = (2, 8, 30)       # seconds before the 2nd, 3rd and 4th try of a call that failed transiently
MAX_WAIT = 120                  # a Retry-After (or rate-limit reset) longer than this fails the call instead


class ForgeError(Exception):
    """The forge couldn't be asked (unreachable, refused the token, rate-limited, answered nonsense)."""


class Transient(Exception):
    """Worth another try: a timeout, a reset, a 5xx, a 429 or a secondary rate limit. `wait`: what the forge asked."""

    def __init__(self, msg, wait=0):
        super().__init__(msg)
        self.wait = wait


@dataclass
class Repo:
    host: str                       # "forgejo" | "github"
    id: str                         # the host's own id: stable across renames and transfers
    owner: str
    name: str
    url: str                        # the repo's web page
    description: str = ""
    topics: list = field(default_factory=list)
    language: str = ""
    homepage: str = ""
    branch: str = ""                # the default branch
    private: bool = False
    fork: bool = False
    archived: bool = False
    mirror: bool = False
    empty: bool = False
    stamp: str = ""                 # changes when the repo's content changes (a push)
    created: int = 0
    updated: int = 0

    @property
    def full_name(self):
        return "%s/%s" % (self.owner, self.name)


@dataclass
class Item:
    """One issue, PR or release (docs are built by the importer from tree + blob)."""
    kind: str                       # issue | pr | release
    key: str                        # unique within its repo: issue:12, release:34
    url: str
    title: str
    body: str = ""
    updated: int = 0
    created: int = 0
    state: str = ""                 # open | closed | merged (issues and PRs)
    number: int = 0
    tag: str = ""


def unix(value):
    """RFC 3339 (both forges) -> unix seconds; 0 if empty or unreadable."""
    if not value:
        return 0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Client:
    """JSON GETs against one API base with one token. `cache` (the Store) keeps ETags and bodies for 304s."""

    def __init__(self, base, token, auth_scheme, headers=None, gap=0.5, timeout=60, name="forge", cache=None,
                 retry_delays=RETRY_DELAYS, max_wait=MAX_WAIT):
        self.base, self.token, self.scheme = base.rstrip("/"), token, auth_scheme
        self.headers, self.gap, self.timeout, self.name, self.cache = dict(headers or {}), gap, timeout, name, cache
        self.retry_delays, self.max_wait = tuple(retry_delays), max_wait
        self.opener = urllib.request.build_opener(tokens.NoRedirect)
        self.last_call = 0.0
        self.calls = 0
        self.retries = 0

    def __repr__(self):                 # never the token
        return "Client(%s %s)" % (self.name, self.base)

    def get(self, path, params=(), missing=(), conditional=False):
        """(parsed JSON or None for a `missing` status, response headers). A transient failure is tried again after
        each of retry_delays (or the forge's Retry-After, if longer and at most max_wait); then, or for any other
        failure, ForgeError."""
        delays = list(self.retry_delays)
        while True:
            try:
                return self.get_once(path, params, missing, conditional)
            except Transient as e:
                what = "%s GET %s: %s" % (self.name, path, e)
                if e.wait > self.max_wait:
                    raise ForgeError("%s (the forge asks to wait %d s)" % (what, e.wait))
                if not delays:
                    raise ForgeError("%s (%d tries)" % (what, len(self.retry_delays) + 1))
                self.retries += 1
                time.sleep(max(delays.pop(0), e.wait))

    def get_once(self, path, params=(), missing=(), conditional=False):
        wait = self.last_call + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        url = self.base + path + ("?" + urllib.parse.urlencode(list(params)) if params else "")
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **self.headers}
        token = tokens.value(self.token)
        if token:
            headers["Authorization"] = "%s %s" % (self.scheme, token)
        cached = self.cache.http_get(url) if (conditional and self.cache is not None) else None
        if cached:
            headers["If-None-Match"] = cached[0]
        req = urllib.request.Request(url, method="GET", headers=headers)
        what = "%s GET %s" % (self.name, path)
        self.calls += 1
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                raw, rh = r.read(), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return json.loads(cached[1]), e.headers
            if e.code in missing:
                return None, e.headers
            if 300 <= e.code < 400:
                raise ForgeError("%s: a redirect (%d), not followed with the token" % (what, e.code))
            retry_after = e.headers.get("retry-after") if e.headers else None
            limited = e.headers is not None and e.headers.get("x-ratelimit-remaining") == "0"
            if e.code == 429 or (e.code == 403 and (retry_after or limited)):
                wait = 0
                if retry_after and str(retry_after).strip().isdigit():
                    wait = int(retry_after)
                elif limited and str(e.headers.get("x-ratelimit-reset") or "").isdigit():
                    wait = max(0, int(e.headers["x-ratelimit-reset"]) - int(time.time()))
                raise Transient("rate-limited (HTTP %d)" % e.code, wait)
            if e.code >= 500:
                raise Transient("HTTP %d" % e.code)
            if e.code in (401, 403):
                raise ForgeError("%s: HTTP %d, the token was refused or lacks a scope" % (what, e.code))
            raise ForgeError("%s: HTTP %d" % (what, e.code))
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise Transient(str(getattr(e, "reason", None) or e) or e.__class__.__name__)
        finally:
            self.last_call = time.time()
        try:
            data = json.loads(raw)
        except ValueError:
            raise ForgeError("%s: not JSON" % what)
        if conditional and self.cache is not None and rh.get("ETag"):
            self.cache.http_put(url, rh.get("ETag"), raw.decode("utf-8"))
        return data, rh

    def pages(self, path, params=(), size=50, size_param="limit", conditional=False):
        """Every item of a paged list (page=1, 2, … until a short page). Pages are built here, never taken from a
        Link header, so the token only ever goes to self.base."""
        page = 1
        while True:
            data, _ = self.get(path, list(params) + [(size_param, size), ("page", page)], conditional=conditional)
            if not isinstance(data, list):
                raise ForgeError("%s GET %s: not a list" % (self.name, path))
            yield from data
            if len(data) < size:
                return
            page += 1
            if page > 1000:
                raise ForgeError("%s GET %s: more than 1000 pages" % (self.name, path))


class Forge:
    name = ""

    def owner_names(self):
        """The owners this forge is configured with: each one is a source of its own (code-import isolates a failing
        source and goes on with the others)."""
        raise NotImplementedError

    def repos(self, owner):
        raise NotImplementedError

    def tree(self, repo):
        raise NotImplementedError

    def blob(self, repo, sha):
        raise NotImplementedError

    def issue_scopes(self, repos):
        raise NotImplementedError

    def issues(self, scope, repos, since):
        raise NotImplementedError

    def releases(self, repo):
        raise NotImplementedError

    def doc_url(self, repo, path):
        raise NotImplementedError


def quote_path(path):
    return urllib.parse.quote(path, safe="/")
