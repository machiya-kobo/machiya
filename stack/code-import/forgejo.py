"""Forgejo (and Gitea: the same API v1) for code-import. GET only, with a token of the owner's account (scopes
read:repository, read:issue, read:user, read:organization), sent as `Authorization: token …`.

| Call | Use |
|---|---|
| `/api/v1/user` | the token's login |
| `/api/v1/user/repos` (the login's own) / `/api/v1/orgs/{o}/repos` (else `/api/v1/users/{o}/repos`) | every repo of an owner, 50 a page |
| `/api/v1/repos/{o}/{r}/git/trees/{branch}?recursive=true` | the default branch's files (paged while `truncated`) |
| `/api/v1/repos/{o}/{r}/git/blobs/{sha}` | one file (base64) |
| `/api/v1/repos/issues/search?owner=&type=issues\\|pulls&state=all&since=` | an owner's issues and PRs changed since the cursor: two searches per owner per run, not one call per repo |
| `/api/v1/repos/{o}/{r}/releases` | a repo's releases (drafts skipped) |

Forgejo 16 has no code search API, and the importer needs none.
"""
import base64

from forges import Client, Forge, ForgeError, Item, Repo, quote_path, unix


class Forgejo(Forge):
    name = "forgejo"

    def __init__(self, url, token, owners, gap=0.5, cache=None, **client):
        if not owners:
            raise SystemExit("code-import: CODE_IMPORT_FORGEJO_OWNERS names no owner")
        if len({o.lower() for o in owners}) != len(owners):
            raise SystemExit("code-import: a Forgejo owner is named twice")
        self.web = url.rstrip("/")
        self.owners = owners
        self.c = Client(self.web + "/api/v1", token, "token", gap=gap, name="forgejo", cache=cache, **client)
        self._login = None

    def login(self):
        if self._login is None:
            me, _ = self.c.get("/user")
            if not isinstance(me, dict) or not me.get("login"):
                raise ForgeError("forgejo GET /user: no login")
            self._login = me["login"]
        return self._login

    def repo(self, r):
        owner = r.get("owner") or {}
        private = bool(r.get("private") or r.get("internal") or owner.get("visibility") in ("limited", "private"))
        return Repo(host=self.name, id=str(r["id"]), owner=owner.get("login") or owner.get("username") or "",
                    name=r["name"], url=r["html_url"], description=r.get("description") or "",
                    topics=[t.lower() for t in (r.get("topics") or [])], language=r.get("language") or "",
                    homepage=r.get("website") or "", branch=r.get("default_branch") or "", private=private,
                    fork=bool(r.get("fork")), archived=bool(r.get("archived")), mirror=bool(r.get("mirror")),
                    empty=bool(r.get("empty")), stamp=r.get("updated_at") or "", created=unix(r.get("created_at")),
                    updated=unix(r.get("updated_at")))

    def owner_names(self):
        return list(self.owners)

    def repos(self, owner):
        if owner.lower() == self.login().lower():
            found = self.c.pages("/user/repos")
        else:
            probe, _ = self.c.get("/orgs/%s" % owner, missing=(404,))
            path = ("/orgs/%s/repos" if probe is not None else "/users/%s/repos") % owner
            if probe is None:
                user, _ = self.c.get("/users/%s" % owner, missing=(404,))
                if user is None:
                    raise ForgeError("forgejo: owner %r not found (or the token can't see it)" % owner)
            found = self.c.pages(path)
        return [self.repo(r) for r in found if (r.get("owner") or {}).get("login", "").lower() == owner.lower()]

    def tree(self, repo):
        if repo.empty or not repo.branch:
            return []
        out, page = [], 1
        while True:
            data, _ = self.c.get("/repos/%s/%s/git/trees/%s" % (repo.owner, repo.name, quote_path(repo.branch)),
                                 [("recursive", "true"), ("per_page", 1000), ("page", page)], missing=(404, 409))
            if data is None:
                return out
            out += [(e["path"], e["sha"], int(e.get("size") or 0)) for e in data.get("tree") or [] if e.get("type") == "blob"]
            if not data.get("truncated") or page >= 100:
                return out
            page += 1

    def blob(self, repo, sha):
        data, _ = self.c.get("/repos/%s/%s/git/blobs/%s" % (repo.owner, repo.name, sha))
        try:
            return base64.b64decode(data.get("content") or "")
        except (ValueError, AttributeError):
            raise ForgeError("forgejo: blob %s of %s is not base64" % (sha, repo.full_name))

    def issue_scopes(self, repos):
        by_owner = {}
        for r in repos:
            by_owner.setdefault(r.owner.lower(), []).append(r)
        return [("owner:" + o, rs) for o, rs in sorted(by_owner.items())]

    def issues(self, scope, repos, since):
        owner = scope.split(":", 1)[1]
        for kind in ("issues", "pulls"):
            params = [("owner", owner), ("type", kind), ("state", "all")]
            if since:
                params.append(("since", since))
            for i in self.c.pages("/repos/issues/search", params):
                pr = i.get("pull_request")
                if (kind == "pulls") != bool(pr):
                    continue
                state = i.get("state") or ""
                if pr and (pr.get("merged") or pr.get("merged_at")):
                    state = "merged"
                yield str((i.get("repository") or {}).get("id")), Item(
                    kind="pr" if pr else "issue", key="issue:%d" % i["number"], url=i["html_url"],
                    title=i.get("title") or "", body=i.get("body") or "", updated=unix(i.get("updated_at")),
                    created=unix(i.get("created_at")), state=state, number=int(i["number"]))

    def releases(self, repo):
        out = []
        for r in self.c.pages("/repos/%s/%s/releases" % (repo.owner, repo.name)):
            if r.get("draft"):
                continue
            out.append(Item(kind="release", key="release:%s" % r["id"], url=r["html_url"],
                            title=r.get("name") or r.get("tag_name") or "", body=r.get("body") or "",
                            updated=unix(r.get("published_at") or r.get("created_at")),
                            created=unix(r.get("created_at")), tag=r.get("tag_name") or "",
                            state="prerelease" if r.get("prerelease") else ""))
        return out

    def doc_url(self, repo, path):
        return "%s/src/branch/%s/%s" % (repo.url, quote_path(repo.branch), quote_path(path))

    @property
    def calls(self):
        return self.c.calls

    @property
    def retries(self):
        return self.c.retries
