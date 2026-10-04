# code-import

Puts the owner's repos on **Forgejo** and **GitHub** into [Hister](../../docs/services/hister.md), so Shiori can search them as a **Code** area: the repos themselves (name, description, topics), their READMEs and markdown docs, their issues and pull requests (title, body, state), and their releases. Each one is a Hister document at its **real forge URL**, so a result opens the forge's page.

It is stdlib Python with one sqlite file, it listens on nothing, and it only ever reads from the forges (GET). The design is the vault's research note "Code search in Shiori (Forgejo + GitHub)" (architecture (b), the start of (d)); the service page is [docs/services/code-import.md](../../docs/services/code-import.md).

**Status: phase 1 (0.1.0, unreleased).** No code bodies yet: those are phase 2.

## What a run does

Runs happen every `CODE_IMPORT_INTERVAL` seconds (15 minutes by default).

1. **Every forge lists every repo of its owners.** These are left out:
   - forks, archived repos and mirrors (Forgejo's `mirror`, GitHub's `mirror_url`);
   - the names in `CODE_IMPORT_EXCLUDE` (`obsidian`, `pass-store`, `backup` by default; `name` or `owner/name`);
   - **twins**: a repo that is on both forges is indexed once (`CODE_IMPORT_TWINS`). For the owner that is GitHub for `machiya-kobo` (GitHub's `main` is the source of truth) and Forgejo for `owner`. The indexed one carries the other's link (`code_twin_url`).
2. **Per repo:**
   - a **card**: name, description, topics, language, homepage;
   - when the repo has changed (its push stamp), on its first run, and on every full run: the **README** and the **markdown docs** of the default branch, and the **releases** (drafts skipped).
   - Markdown in hidden folders (`.github/…`) and in `node_modules/`, `vendor/`, `third_party/`, `sample-vault/`, `tests/`, `test/`, `fixtures/` and `examples/` (at any depth) is skipped, as are files over `CODE_IMPORT_MAX_DOC_BYTES`: the README, `docs/` and the other markdown stay. A repo has at most `CODE_IMPORT_MAX_DOCS` docs (README first).
   - **Files named like secrets** (`.env*`, `*.pem`, `*.key`, `id_*`, `*.gpg`, `*.age`, `secrets*`, `*.p12`, `*.pfx`, `*.kdbx`) are never read.
3. **Issues and pull requests** changed since the cursor: title, body and state (open, closed, merged); no comments.
   - On Forgejo, one search per owner (`/repos/issues/search?owner=…&since=…`), not one call per repo.
   - On GitHub, one conditional list per repo: an unchanged one is a free `304`.
4. **The secret scan** ([`secretscan.py`](secretscan.py)) runs on every title and body. It looks for private keys, AWS, GitHub, GitLab, Slack, Stripe, Google, Anthropic, OpenAI, Tailscale, npm and age keys, JWTs, passwords in URLs, and `password = …`-style assignments with a random-looking value.
   - By default it **redacts**: the match becomes `[redacted]`, and the document says `code_redacted: "true"`.
   - With `CODE_IMPORT_SECRETS=refuse`, the whole document is refused instead.
   - It never logs what it found, only the kinds.
5. **Hister is asked first** (`GET /api/document`):
   - **A URL Hister already holds as somebody else's page** (the owner browsed that GitHub issue, say) **is left alone**: nothing is sent, and it is never replaced or deleted. It is counted as "already a page".
   - A document whose `metadata.source` is `code` is ours and is replaced when its source changed.
6. **The document is sent** (`POST /api/add`), **always with `html`**: Hister's own sensitive-content check (422) reads only `html` for a web document. `skip_sensitive_check` is never set (the client refuses to send it).
7. **What disappeared is withdrawn** (`POST /api/delete` by exact `url:"…"`, and only when Hister says the document is ours):
   - a repo that is deleted, renamed, transferred, archived, newly excluded or became a twin: all its documents;
   - a doc or release that is gone, when the repo is read again;
   - an issue that is gone, on a full run.

**A full run** happens first, then every `CODE_IMPORT_FULL_INTERVAL` (6 hours). It reads every repo and every issue list completely. Only a source that changed is rebuilt: a fingerprint of what each document was built from is kept, so an unchanged one costs no call and no write.

**Fail closed:**
- A forge that can't be listed (down, token refused, rate-limited) stops the run before anything is withdrawn.
- A forge that suddenly lists **no** repos, after listing some before, stops the run too: a token that lost its access must not wipe the index.
- Hister down (5xx, unreachable, 403) stops the run. The state is written per document, so the next run resumes.

### What Hister gets

```json
{"url": "https://github.com/machiya-kobo/kura/pull/3",
 "title": "Fix the tag page · machiya-kobo/kura#3",
 "html": "<!DOCTYPE html>…", "text": "…", "added": 1791021600,
 "metadata": {"source": "code", "client": "code-import", "ignore_skip_rules": true,
              "code_host": "github", "code_repo": "machiya_kobo__kura", "code_repo_name": "machiya-kobo/kura",
              "code_kind": "pr", "code_state": "merged", "code_private": "true", "code_number": "3",
              "code_updated": 1791021600, "code_twin_url": "https://forgejo.example.ts.net/machiya/kura"}}
```

| Key | Values | Query |
|---|---|---|
| `source` | `code` | `metadata.source:code` (the Code area); every other Hister query adds ` -metadata.source:code` |
| `code_host` | `forgejo`, `github` | `metadata.code_host:github` |
| `code_repo` | `owner__repo`, lowercase, with every other character `_`: `machiya-kobo/kura` → `machiya_kobo__kura` | `metadata.code_repo:machiya_kobo__kura` |
| `code_repo_name` | `machiya-kobo/kura` | for display only (not matchable, see below) |
| `code_kind` | `repo`, `readme`, `doc`, `issue`, `pr`, `release` | `metadata.code_kind:(issue\|pr)` |
| `code_state` | `open`, `closed`, `merged` (issues and PRs) | `metadata.code_state:open` |
| `code_private` | `"true"`, `"false"` (strings: a JSON boolean isn't matchable) | `metadata.code_private:true` |
| `code_updated` | unix seconds (also the document's `added`) | `added:` / date filters |
| `code_number`, `code_path`, `code_tag`, `code_prerelease`, `code_topics`, `code_language`, `code_twin_url`, `code_redacted` | | display |

- No label: Code is its own area, not a topic.
- `ignore_skip_rules: true`, as Kura's notes have: server's skip rules refuse every `*.example.ts.net` URL, which would refuse every Forgejo document.

### The repo filter: why `owner__repo` (tested 2026-10-04)

This was tested on a throwaway Hister v0.20.0 (rootless podman, dummy data). Hister **tokenizes metadata values** when it indexes them (bleve's default analyzer: a Unicode word split, then lowercase). But it turns a metadata query into **one term query that isn't analyzed** (`querybuilder`: `bleve.NewTermQuery(v)` on `metadata.<key>`). So a value matches only if it is a single lowercase token.

| `code_repo` value | `metadata.code_repo:<same value>` | why |
|---|---|---|
| `machiya-kobo/kura` | no match (quoted, escaped: no match either) | `/` and `-` split it into `machiya`, `kobo`, `kura`. `metadata.code_repo:kura` then matches it, along with every other repo with a `kura` word, so it is useless as a filter |
| `machiya-kobo--kura`, `machiya-kobo.kura`, `machiya-kobo:kura` | no match | split the same way |
| **`machiya_kobo__kura`** | **match** | `_` joins words in Unicode word segmentation, so it stays one token |
| `owner__owner_com`, `owner__2048_game` | match | |
| `Machiya_Kobo__Kura` stored, queried lowercase | match | lowercased when indexed (a query must be lowercase) |
| `code_private: true` (JSON boolean) | `metadata.code_private:true` doesn't match | a boolean is indexed as a bool field, so it is sent as the string `"true"` |
| `code_kind:issue`, `code_state:merged`, `code_host:forgejo` | match | single words; nothing is stemmed |

Also verified on that Hister:
- `@code` = `metadata.source:code` works as an alias.
- `@pages` = `* -label:vault -metadata.source:vault -metadata.source:code` keeps code out.
- `<words> -metadata.source:code` leaves only the browsed pages.
- `metadata.code_kind:(issue|pr)` alternation works.
- Hister's 422 fires on `html` holding a `ghp_…` token, but not on the same token sent in `text` only.
- `/api/add` on a URL replaces the document, and the replaced document loses our `source`, which is how a later browse is recognised.
- A page "browsed" over a code document was left alone by a fresh run ("already a page").

## Settings

| Env | Default | |
|---|---|---|
| `CODE_IMPORT_FORGEJO_URL` | — | the Forgejo (or Gitea) server, e.g. `https://forgejo.example.ts.net`; unset: no Forgejo |
| `CODE_IMPORT_FORGEJO_TOKEN_FILE` | — | a file holding a Forgejo token of the **owner's** account (only it sees every private repo), scopes `read:repository`, `read:issue`, `read:user`, `read:organization` (required with the URL) |
| `CODE_IMPORT_FORGEJO_OWNERS` | — | comma list: the token's own login and/or orgs, e.g. `owner,machiya` (required with the URL) |
| `CODE_IMPORT_GITHUB_TOKEN_FILES` | — | `owner=/path/to/token,…`: one **fine-grained, read-only** token per resource owner (Metadata, Contents, Issues, Pull requests: read), e.g. `owner=/secrets/github-owner-token,machiya-kobo=/secrets/github-machiya-kobo-token`; unset: no GitHub. Never a classic token: its `repo` scope can write |
| `CODE_IMPORT_GITHUB_API` | `https://api.github.com` | |
| `CODE_IMPORT_TWINS` | — | `github:machiya-kobo=forgejo:machiya,forgejo:owner=github:owner`: a right-hand repo whose name matches a left-hand one is the same repo, indexed once, on the left |
| `CODE_IMPORT_EXCLUDE` | `obsidian,pass-store,backup` | repo names (`name` or `owner/name`) never imported |
| `CODE_IMPORT_HISTER_URL` | — | `http://hister:4433` (required, except for `--dry-run`) |
| `CODE_IMPORT_HISTER_TOKEN_FILE` | — | the owner's Hister token, sent as `X-Access-Token` on every Hister call ([contracts/hister.md](../../docs/contracts/hister.md)) |
| `CODE_IMPORT_SECRETS` | `redact` | `refuse`: a document with a likely secret isn't sent at all |
| `CODE_IMPORT_INTERVAL` | `900` | seconds between runs (at least 60) |
| `CODE_IMPORT_FULL_INTERVAL` | `21600` | seconds between full runs (at least 3600) |
| `CODE_IMPORT_PAUSE` | — | `Sun 02:20-02:50`: no runs in that weekly slot (Hister's backup), in `CODE_IMPORT_TZ` (default `UTC`) |
| `CODE_IMPORT_MAX_DOCS` | `200` | markdown docs per repo |
| `CODE_IMPORT_MAX_DOC_BYTES` | `262144` | a bigger doc is skipped |
| `CODE_IMPORT_DOC_SKIP` | — | more path globs to skip, comma-separated (`drafts/*`), on top of the defaults above |
| `CODE_IMPORT_GAP` | `0.5` Forgejo, `0.25` GitHub | seconds between two calls to a forge (Forgejo runs on a small Pi VM) |
| `CODE_IMPORT_DATA` | `/data` | `code-import.sqlite3` (the state, cursors and ETags) and `status.json` |

**Tokens.** Every token comes from a file (its first line), never from the environment, argv, a URL or a log.
- A file that is set but missing, empty or not a token stops the start, without echoing the file's contents.
- Each file is re-read when it changes (a rotation needs no restart).
- While a token is set, **a redirect is never followed**: the run stops instead. The token goes only to the configured API base; pages are built from that base, never taken from a `Link` header.

**`status.json`** holds:
- `ok`, `running`, `last_success`, `last_full`, `failures_in_a_row` and `error`;
- `last_run`: the run's counts, and its calls per forge;
- `counts`: documents per host, kind and status.

`ok` turns false after a failed run once no run has succeeded for three intervals. The image's healthcheck reads it. Alert on `ok` only: the counts are information.

## Running

```sh
python3 codeimport.py --dry-run --limit 3                   # print what would be added: at most 3 documents per repo;
                                                             # no state, no Hister writes
python3 codeimport.py --dry-run --repo machiya-kobo/kura --limit 100
python3 codeimport.py --once                                 # one run (exit 1 if it failed)
python3 codeimport.py --once --full                          # one full run now
python3 codeimport.py                                        # the service
podman build -f stack/code-import/Dockerfile -t code-import:dev .        # from the repo root
```

- A dry run asks Hister only which URLs it has, and only if `CODE_IMPORT_HISTER_URL` is set.
- A dry run reads the forges as a first (full) run would, so with `--limit` it reads fewer blobs, not fewer lists.

## Tests

```sh
cd stack/code-import && python3 -m unittest discover -s tests
```

The tests need no network. They run against the fake Forgejo and fake GitHub below, and a fake Hister. The fakes record every request, and a test fails if the importer sends a forge anything but GET.

The synthetic forges hold:
- a fork, an archived repo, a mirror on each host, an excluded name, a private repo, an empty repo, and twins on both hosts;
- a README with an invented token, a file named like a secret, and hidden, vendored, test, example and sample-vault markdown;
- issues, PRs (one merged, one open), and releases (one a draft, one a prerelease).

The command-line tests run `codeimport.py` as the image does and check its exit codes, `status.json`, and that no token or text reaches its output.

## The fake forges (`dev/`)

[`dev/fake_forgejo.py`](dev/fake_forgejo.py) and [`dev/fake_github.py`](dev/fake_github.py) are stdlib servers answering the calls code-import makes, shaped like Forgejo 16's and GitHub's answers. They serve the invented seeds in [`dev/seed/`](dev/seed/): a user `lantern`, a Forgejo org `workshop` and a GitHub org `workshop-kobo`. The tests use them in-process, and the dev stack runs them ([compose/dev](../../compose/dev/compose.yml): `fake-forgejo` on 19213, `fake-github` on 19214, and `code-import` reading them with the dummy tokens `dev init` generates). `./dev check` asks Hister for the Code area's query and its filters, and checks that `@pages` leaves code out ([docs/dev-stack.md](../../docs/dev-stack.md)).

## Layout

| File | |
|---|---|
| `codeimport.py` | the rules (exclusions, twins), the pipeline, the dry run, the service loop and `status.json` |
| `forges.py` | the forge interface (`Repo`, `Item`, `Forge`) and the shared read-only HTTP client (tokens, no redirects, ETags) |
| `forgejo.py`, `github.py` | the two forges |
| `secretscan.py` | the secret scan and the secret-name rule |
| `render.py` | a document's HTML (escaped; headings, code blocks, paragraphs) |
| `hister.py` | the three Hister calls and Hister's URL normalisation |
| `tokens.py` | token files |
| `store.py` | the state |
| `dev/` | the fake forges and their seeds (tests and the dev stack; not in the image) |
