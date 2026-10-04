# Using Hister

How Machiya's services use [Hister](../services/hister.md). Hister is upstream code, so these are rules for the callers.

## Every call

- **Send `Origin: hister://`** on POSTs (without it: 403). GETs to `/search` need it unless they ask for JSON (`format=json` or `Accept: application/json`, allowed since v0.20.0); sending it always is simplest.
- **Send the owner's Hister token** as `X-Access-Token` on every call, read from a file (`<NAME>_HISTER_TOKEN_FILE`; machiya-mcp: `HISTER_TOKEN_FILE`), never from the environment, argv, a URL or a log. A Hister without users ignores it, so send it from now on: once user handling is on ([Sign-in](#sign-in)) every call without it is refused. Re-read the file when it changes: regenerating the token (Hister keeps one per user) replaces it for every caller at once. Unset sends none.
- **A 403 means "no or an invalid credential"** once users are on (Hister answers 403 with an empty body, never 401). Don't retry it; say so.
- **Never change ownership.** The owner's token is an admin's: never send `X-Hister-Public`, `X-Hister-Target-User-ID`, or `changes.user_id` in `/api/update`. Every document belongs to the owner.
- **The `hister` CLI** gets the token as `HISTER__APP__ACCESS_TOKEN` in its subprocess's environment only (not `-t`, which puts it in argv).
- **Probes use `GET /health`** (open, no token), never `/api/stats` or a page, which turn 403 when users are on.
- In the reference compose, services reach Hister over a private Docker network (`http://hister:4433`); devices use `https://hister.example.ts.net`. A redirect from Hister is never followed with the token.
- **Never `hister index --force` a URL Hister already has.** It replaces the imported metadata (tags, archive link). Check with `url:"…"` first.
- `POST /api/add` with only a URL does **not** fetch the page. Fetch with the `hister` CLI (`hister -u <server> index --label <l> <url>`), or send the text yourself.
- Avoid your backup window if Hister is stopped for backups: calls hang while it is down.

## Endpoints used

| Call | Who |
|---|---|
| `GET /search?q=<query>` (Hister query language: `label:`, `url:"…"`, `added:`; exclude a label with ` -label:x` appended to the query (works server-side with correct totals: `* -label:vault` = everything minus the notes). Hister has no `NOT` operator (`NOT` is a plain word) and no `exclude_label` param. `metadata.source:vault` is a query field too, e.g. `-label:vault -metadata.source:vault`. **Every query except the Code area's ends ` -metadata.source:code`** too ([Code documents](#code-documents-metadatasourcecode))) | Shiori, Konbini (reading line, link rot), landing (newest pages), machiya-mcp (labels and collections: the label census, relabel dry runs; every query ends ` -label:vault -metadata.source:vault -metadata.source:code -label:konbini`) |
| `POST /api/add` `{url, title, text, html?, label, added?, metadata}` | Kura (the note push), code-import (`code`, always with `html`), feed-reader and other importers, Shiori (saves). New documents carry Hister's `metadata.source` convention: `vault` (Kura), `code` (code-import), the importer's own name (with its own `<name>_*` keys), `shiori`. A metadata value a query should match is [one lowercase token](#metadata-values). |
| `POST /api/delete` `{"query": "url:\"…\""}` | Kura (a note deleted, renamed or moved), code-import (a repo, doc, issue or release gone; only its own documents) |
| `GET /api/document?url=<url>` | machiya-mcp (`pages_set_label` checks the page is not a note, card or code document), code-import (whose is this URL?), feed-import (is it known?) |
| `GET /health` | probes (open: 200 `OK` while Hister runs) |
| `GET /api/profile` | hister-login only ([Sign-in](#sign-in)) |
| `POST /api/label` `{url, label}`, `POST /api/update` `{query, changes: {label}}` | machiya-mcp (`pages_set_label`, `pages_relabel`; one exact `url:"…"` match per update) |
| `GET /api/rules`, `POST /api/add_alias`, `POST /api/delete_alias` (form posts) | machiya-mcp (`collections_*`: only `@` aliases whose value is purely labels) |
| `POST /mcp` (Hister's MCP, below) | AI clients: Claude Code through the machiya plugin |
| `GET /preview?id=<url>` | links to Hister's saved copy |
| `hister index` (CLI) | Konbini/Niwa link rot (fetch and store a page) |

## The note push (`label:vault`)

Kura pushes every note in the default vault (`personal/`), and only there; other vaults never go in. Each note becomes one document:

```json
{"url": "https://kura.example.ts.net/n/Projects/Example-Project", "title": "Example Project", "text": "<body, no frontmatter>",
 "html": "<rendered>", "label": "vault", "added": 1764547200,
 "metadata": {"source": "vault", "vault_path": "personal/Projects/Example-Project.md", "tags": ["…"], "vault_published": false,
              "vault_card": "example-project", "ignore_skip_rules": true}}
```

- Only new or changed notes are pushed; a table remembers each URL and content hash. A deleted, renamed or moved note's document is deleted.
- `ignore_skip_rules` is needed because the skip rules refuse `*.example.ts.net` on purpose, so the extension never captures Konbini, Niwa or Kura pages.
- Every note points at its Kura page, project notes included (they do not point at Konbini's `/p/<slug>`); `vault_card` keeps the slug in the metadata.
- Service-owned keys carry the service's prefix (`vault_*`, Hister's convention).
- Notes stay in Hister permanently, so Hister's own UI and the extension find them. Shiori reads notes from Kura and appends ` -label:vault` to every Hister query.

## Aliases

`@pages` = `* -label:vault -metadata.source:vault -metadata.source:code`, `@notes` = `label:vault` and `@code` = `metadata.source:code` (created in Hister's alias settings), so Hister's own UI, extension, TUI and MCP split pages, notes and code the way Shiori does. They sit beside your own topic aliases. **Shiori hides any alias whose expansion names the vault or the code** (`label:vault`, `metadata.source:vault` or `metadata.source:code`) from its collections and reserves the collection names "notes", "pages" and "code", so these aliases never appear as Shiori collections; machiya-mcp never lists, creates, changes or removes them.

## Metadata values

**A metadata value a query should match must be one lowercase token** (tested on v0.20.0, 2026-10-04): letters, digits and `_` only. Hister tokenizes a metadata value when it indexes it (a Unicode word split, then lowercase) but queries `metadata.<key>:<value>` with one unanalyzed term, so `/`, `-`, `.` and `:` split a value into words no query can match whole: `machiya-kobo/kura` never matches, `machiya_kobo__kura` does. A JSON boolean is indexed as a bool and isn't matched by `metadata.x:true`: send `"true"`. Values only for display may be anything. This holds for every service's keys.

## Code documents (`metadata.source:code`)

Approved by the lead, 2026-10-04 (the owner's decisions of 2026-10-05). Background: [services/code-import.md](../services/code-import.md).

[code-import](../services/code-import.md) puts the owner's Forgejo and GitHub repos into Hister: repo cards, READMEs and docs, issues, PRs and releases. Each one is a document at its real forge URL:

```json
{"url": "https://github.com/machiya-kobo/kura/pull/3", "title": "Fix the tag page · machiya-kobo/kura#3",
 "html": "<rendered, escaped>", "text": "…", "added": 1791021600,
 "metadata": {"source": "code", "client": "code-import", "ignore_skip_rules": true, "code_host": "github",
              "code_repo": "machiya_kobo__kura", "code_repo_name": "machiya-kobo/kura", "code_kind": "pr",
              "code_state": "merged", "code_private": "true", "code_updated": 1791021600}}
```

- **The keys:**
  - `code_host`: `forgejo|github`;
  - `code_repo`: `owner__repo`, lowercase, every character other than a-z and 0-9 turned into `_`;
  - `code_kind`: `repo|readme|doc|issue|pr|release`;
  - `code_state`: `open|closed|merged`;
  - `code_private`: `"true"|"false"`;
  - for display only: `code_repo_name`, `code_number`, `code_path`, `code_tag`, `code_twin_url`, `code_redacted`.
  - No label.
- **Every other Hister query adds ` -metadata.source:code`**, next to ` -label:vault -metadata.source:vault`:
  - Shiori's Pages, All, counts and collections;
  - machiya-mcp's suffix and the machiya plugin's skills;
  - landing's newest pages;
  - Konbini's reading line.
  Only the Code area asks for `metadata.source:code`.
- **Aliases:** `@code` and `@pages` as in [Aliases](#aliases).
- **Ownership.** code-import replaces or deletes a document only when Hister says its `metadata.source` is `code`. A URL the owner browsed first stays the owner's page. Other writers keep the existing rule: never re-index a URL Hister already holds.
- **Secrets.** Code documents are always sent with `html`, because Hister's sensitive-content check reads only `html` for a web document. They go through code-import's own scan first. `skip_sensitive_check` is never set.
- **AI: on-device only, like notes** (the owner, 2026-10-05):
  - Shiori's AI treats a code result as a note;
  - `shiori-ai` refuses `metadata.source:code`, as it refuses `vault`;
  - Hister's MCP returns code to any client with the owner's token, so for AI clients it is the model's rule, written in the skills: page queries start with `@pages`, and code results are never pulled into an AI's context.

## Hister's MCP

Hister has its own MCP endpoint, and it is how AI clients search and read saved pages; machiya-mcp has no page reads of its own (since 0.7.0), only the label and collection tools Hister's MCP lacks. Tested with v0.20.0.

- **`POST /mcp`**, Streamable HTTP with plain JSON replies (protocol 2025-06-18; no session, `GET /mcp` is 405). It is not CSRF-guarded, so it needs **no `Origin`**.
- **Tools, all read-only:** `search` (`query` in the query language above, `limit` up to 50, `date_from`/`date_to`, `fields` such as `label`, `domain`, `text`), `get_preview` (one document by exact URL: its whole text, rendered HTML and metadata, **no paging**), `get_history` (visits and opened results). Results are `structuredContent` with the page fields under `untrusted_content`.
- **Page queries start with `@pages`** (`@pages raspberry pi`, `@pages @travel`): Hister's `search` returns vault notes and code documents mixed with pages otherwise. Notes are read from Kura (`notes_search`, `notes_read`). **Code documents stay out of AI context** (on-device only, like notes): a model never queries `@code` or `metadata.source:code`. This is a rule for the model, written in the skills; nothing enforces it.
- **`get_history` is denied** on every client: browsing history stays out of AI context. The machiya plugin's `install.sh` writes a Claude Code deny rule for every name the tool can have (`mcp__plugin_machiya_hister__get_history`, `mcp__hister__get_history`), which hides it from the model. Another client (the desktop app through a local bridge, say) needs its own way to turn it off.
- **Auth.** Without users the tailnet grant on Hister's service is the gate. With Hister's user handling on, every call needs a user's personal token, `X-Access-Token: <token>` (or `Authorization: Bearer <token>`), the owner's for the owner's documents. Hister keeps **one token per user**: regenerating it replaces it for every client at once. Configure clients with the header now; a Hister without users ignores it.
- Hister's `search` also returns up to 20 results the owner opened from Hister's own list for exactly the same query text (`search_history` records); they are not filtered by label.

## Sign-in

With `app.user_handling: true` Hister has users, and [hister-login](../services/hister-login.md) makes a Hister sign-in count in the rooms. What a caller or a room must know:

- **Who am I:** `GET /api/profile` with a session cookie, `X-Access-Token` or `Authorization: Bearer` answers `{"user_id", "username", "is_admin"}`, and 403 without a valid credential. **With user handling off it answers 200 with an empty body to anyone:** a bare 200 is never "signed in". Only hister-login calls it; rooms ask the helper.
- **The helper's answers** (`GET /v1/check` on its internal port, with `X-Machiya-Session: mhs_…` or `X-Access-Token`): `200 {"username", "user_id", "via": "session"|"token", "kind"}`; `401 {"reason": "signed-out"}` (never fall back to another identity on this); `503 {"reason": "hister-unavailable"|"user-handling-off"}` (a room may fall back to its Tailscale identity, or refuse). `POST /v1/signout` ends a session everywhere; `GET /healthz` is 503 unless the helper and Hister are both fine.
- **The cookie:** `machiya_sso=mhs_…` on the shared cookie domain is opaque and means nothing to Hister: `hister.*` never accepts it. Only the helper sets it; a room only clears it (on sign-out, or when the helper says signed out). Hister's own session cookie (`hister`) stays host-only on Hister's host.
- **Behind a proxy:** Hister re-sends `Set-Cookie: hister=<session>` on every signed-in answer (its sliding expiry). A proxy that adds a Hister session for the browser (the hosted pages' nginx with `auth_request`) must drop it: `proxy_hide_header Set-Cookie;`.
- **Rooms in `AUTH=hister`** ([identity.md](../identity.md#hister-sign-in-authhister)) accept the cookie, `Authorization: Bearer mhs_…` (Shiori's apps) and the owner's Hister token; an API call without one gets `401 {"error": "sign in", "signin": "<the helper's sign-in address>"}`, which a page follows by navigating there.

## What callers must never do

- SearXNG never queries Hister.
- No Hister result, copy or `private_url` appears on gemini, gopher or the public garden export. Those read only `archive_url` (Wayback).
- Shiori's AI features never summarize a note: they refuse by host (`kura.`, `konbini.`, `niwa.`) and by label `vault`.
