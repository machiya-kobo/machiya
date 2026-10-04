"""GitHub for code-import. GET only (REST, `X-GitHub-Api-Version: 2022-11-28`), with one fine-grained, read-only token
per resource owner (a fine-grained token has exactly one: `owner` and `machiya-kobo` need two), sent as
`Authorization: Bearer …`. Permissions: Metadata, Contents, Issues and Pull requests, all read. Never a classic token.

| Call | Use |
|---|---|
| `/user` | the token's login |
| `/user/repos?affiliation=owner&visibility=all` (the login's own) / `/orgs/{o}/repos?type=all` | every repo of the owner, 100 a page |
| `/repos/{o}/{r}/git/trees/{branch}?recursive=1` | the default branch's files (409/404: an empty repo) |
| `/repos/{o}/{r}/git/blobs/{sha}` | one file (base64) |
| `/repos/{o}/{r}/issues?state=all&sort=updated&direction=asc&since=` | issues and PRs (a PR has `pull_request`, merged when `merged_at` is set) |
| `/repos/{o}/{r}/releases` | releases (drafts skipped) |

The lists are conditional GETs (the ETag kept in the state): an unchanged answer is a 304, which costs nothing
against the 5,000 calls an hour. `/search/*` is never used (not in the fine-grained tokens' endpoint list).
"""
import base64

from forges import Client, Forge, ForgeError, Item, Repo, quote_path, unix

HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHub(Forge):
    name = "github"

    def __init__(self, api, owner_tokens, gap=0.25, cache=None):
        """owner_tokens: [(owner, token)], one token per owner."""
        if not owner_tokens:
            raise SystemExit("code-import: CODE_IMPORT_GITHUB_TOKEN_FILES names no owner")
        self.api = api.rstrip("/")
        self.clients = {}
        for owner, token in owner_tokens:
            if owner.lower() in self.clients:
                raise SystemExit("code-import: GitHub owner %r is named twice" % owner)
            self.clients[owner.lower()] = (owner, Client(self.api, token, "Bearer", HEADERS, gap=gap,
                                                         name="github(%s)" % owner, cache=cache))

    @property
    def calls(self):
        return sum(c.calls for _, c in self.clients.values())

    def client(self, repo):
        return self.clients[repo.owner.lower()][1]

    def repo(self, r):
        private = bool(r.get("private")) or (r.get("visibility") or "public") != "public"
        return Repo(host=self.name, id=str(r["id"]), owner=(r.get("owner") or {}).get("login") or "", name=r["name"],
                    url=r["html_url"], description=r.get("description") or "",
                    topics=[t.lower() for t in (r.get("topics") or [])], language=r.get("language") or "",
                    homepage=r.get("homepage") or "", branch=r.get("default_branch") or "", private=private,
                    fork=bool(r.get("fork")), archived=bool(r.get("archived")), mirror=bool(r.get("mirror_url")),
                    stamp=r.get("pushed_at") or r.get("updated_at") or "", created=unix(r.get("created_at")),
                    updated=unix(r.get("pushed_at") or r.get("updated_at")))

    def repos(self):
        out = []
        for key, (owner, c) in self.clients.items():
            me, _ = c.get("/user", conditional=True)
            if not isinstance(me, dict) or not me.get("login"):
                raise ForgeError("github GET /user: no login (token for %s)" % owner)
            if me["login"].lower() == key:
                found = c.pages("/user/repos", [("affiliation", "owner"), ("visibility", "all"), ("sort", "full_name")],
                                size=100, size_param="per_page", conditional=True)
            else:
                found = c.pages("/orgs/%s/repos" % owner, [("type", "all"), ("sort", "full_name")], size=100,
                                size_param="per_page", conditional=True)
            out += [self.repo(r) for r in found if (r.get("owner") or {}).get("login", "").lower() == key]
        return out

    def tree(self, repo):
        if not repo.branch:
            return []
        data, _ = self.client(repo).get("/repos/%s/%s/git/trees/%s" % (repo.owner, repo.name, quote_path(repo.branch)),
                                        [("recursive", "1")], missing=(404, 409))
        if data is None:
            return []
        return [(e["path"], e["sha"], int(e.get("size") or 0)) for e in data.get("tree") or [] if e.get("type") == "blob"]

    def blob(self, repo, sha):
        data, _ = self.client(repo).get("/repos/%s/%s/git/blobs/%s" % (repo.owner, repo.name, sha))
        try:
            return base64.b64decode(data.get("content") or "")
        except (ValueError, AttributeError):
            raise ForgeError("github: blob %s of %s is not base64" % (sha, repo.full_name))

    def issue_scopes(self, repos):
        return [("repo:" + r.id, [r]) for r in repos]

    def issues(self, scope, repos, since):
        repo = repos[0]
        params = [("state", "all"), ("sort", "updated"), ("direction", "asc")]
        if since:
            params.append(("since", since))
        for i in self.client(repo).pages("/repos/%s/%s/issues" % (repo.owner, repo.name), params, size=100,
                                         size_param="per_page", conditional=True):
            pr = i.get("pull_request")
            state = i.get("state") or ""
            if pr and pr.get("merged_at"):
                state = "merged"
            yield repo.id, Item(kind="pr" if pr else "issue", key="issue:%d" % i["number"], url=i["html_url"],
                                title=i.get("title") or "", body=i.get("body") or "", updated=unix(i.get("updated_at")),
                                created=unix(i.get("created_at")), state=state, number=int(i["number"]))

    def releases(self, repo):
        out = []
        for r in self.client(repo).pages("/repos/%s/%s/releases" % (repo.owner, repo.name), size=100,
                                         size_param="per_page", conditional=True):
            if r.get("draft"):
                continue
            out.append(Item(kind="release", key="release:%s" % r["id"], url=r["html_url"],
                            title=r.get("name") or r.get("tag_name") or "", body=r.get("body") or "",
                            updated=unix(r.get("published_at") or r.get("created_at")),
                            created=unix(r.get("created_at")), tag=r.get("tag_name") or "",
                            state="prerelease" if r.get("prerelease") else ""))
        return out

    def doc_url(self, repo, path):
        return "%s/blob/%s/%s" % (repo.url, quote_path(repo.branch), quote_path(path))
