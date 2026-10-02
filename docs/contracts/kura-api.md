# Kura API

It's JSON over the same host as the reader (`https://kura.example.ts.net`), owner-only (`Tailscale-User-Login` in `KURA_USERS`). Clients: the Shiori apps directly, Shiori's hosted pages through a `/kura/` route on the web server that hosts them. No CORS.

Shiori's hosted pages reach it at `/kura/api/…` on their own host. Serving several [vaults](#vaults) is part of the API.

## Conventions

- **A note's identity is its path** in the vault: `Projects/Example-Project.md`. `slug` is the path without `.md`. With several vaults, the identity is `(vault, path)` (see [Vaults](#vaults)); for the default vault nothing changes.
- **`url`** is always `https://kura…/n/<slug>` (percent-encoded) for the default vault, exactly as the reader serves it, and `https://kura…/v/<vault>/n/<slug>` for any other vault. Shiori's history and marks, and Hister documents, key on it; a note's `url` never changes. `https://kura…` is Kura's public address (`KURA_PUBLIC_URL`, else `https://<Host>`), an **origin with no path**: Kura refuses to start with a path in it (0.5.0), so `/n/` or `/v/` always starts a note's path.
- **Dates** are unix seconds: `changed` is the exact time of the note's latest commit; `created` is UTC midnight of the note's `date:` frontmatter day. `null` when unknown.
- **Snippets** are HTML in which only `<mark>` is markup; everything else is escaped, the same as Hister's snippets.
- **Paging:** `limit` (default 20, max 100) and `offset`. `total` is always exact. `total: 0` means "found nothing", which is what triggers Shiori's respelling retry.
- **Errors:** `{"error": "…"}` with 400 (bad parameters), 403, 404, or 503 (the first clone hasn't finished yet).
- `Templates/` and `CLAUDE.md` files never appear.

## Vaults

Kura can serve several vaults. The first one configured is the **default vault** (in the examples `personal`). The others are **private** vaults, for example work ones (`work`, `client`), unless Kura's config (`KURA_VAULTS`, `name+shared:Title=…`) marks one **shared** (in the examples `team`). A shared vault is treated like the default vault everywhere below except its address (`/v/<vault>/…`) and the Niwa and Konbini links; a vault without the mark is private, so the switch fails closed. Kura's config is the one place it is set, and `/api/vaults`' `private` carries it to clients. Everything here is additive: **a client that never sends `vault` sees exactly the API above**, the default vault only, shared vaults included.

- Every note object has **`"vault": "<name>"`**.
- `url`: `https://kura…/n/<slug>` for the default vault (unchanged, for good), `https://kura…/v/<vault>/n/<slug>` for the others. `/v/<default>/n/X` answers 301 to `/n/X`. **That is the only new note URL shape.** A work note's images in `html` are `…/v/<vault>/a/…`, and the reader's other pages move under `/v/<vault>/` the same way (`f/`, `t/`, `search`, `recent`), but none of them is a note's `url`.
- **Reading a URL for `/v/`.** Kura serves `/v/` however a path reaches it: its HTTP server folds a leading `//` and it decodes `%XX` once, so `//v/work/n/X` and `/%76/work/n/X` are the same work note as `/v/work/n/X`. A client that keeps work notes out by their address (Shiori's `isPrivateNote`, the MCP server's guard) folds and decodes the path the same way before it looks, and counts a vault name it can't decode as another vault's. `/V/` and a double-encoded `%2576` aren't `/v/` to Kura.
- **Kura's reader stays at Kura's own address.** A web server routing to Kura under a path (Shiori's hosted pages' `/kura/`) passes only the API it needs (`api/search`, `api/recent`, `api/note`, `api/vaults`, `feed.xml`), never the reader: under a prefix a work note's page wouldn't start with `/v/`.
- A note in any vault but the default always has `published: false` and `card_url: null`, whatever its frontmatter says: Niwa and Konbini read the default vault only, so other vaults (shared or private) get no Niwa or Konbini links.
- **The `vault` parameter**: one name, a comma list, or `all`; omitted = the default vault; an unknown name is a 400.
  - `/api/search`, `/api/recent`: a list or `all`.
  - `/api/tags`, `/api/folders`, `/api/note` (`path` + `vault`): one name. `/api/note`'s sanitized `html` (no scripts, iframes, styles or event handlers, and absolute links) works for every vault, so a client previews a work note from it. Hister's `api/preview` never has work notes.
  - `/api/notes`: `paths` are in `vault`; `urls` carry their vault in the URL (a `/v/…` URL counts as asking for that vault).
  - `/feed.xml` takes **no** `vault`: it is the default vault only, so Shiori's feed and OPML export can never carry work notes off the device. A shared vault has its own feed at `/v/<vault>/feed.xml`; a private vault has none (404).
- **Query syntax** gains `vault:x`. It only narrows within the vaults the `vault` parameter allows and never widens them: `q=vault:client` without `vault=client` (or `all`) finds nothing. A client that hasn't opted in can't get a work note, even through a query a model wrote.
- **`GET /api/vaults`** → `{"vaults": [{"name": "personal", "title": "Personal", "default": true, "private": false, "obsidian": "personal", "notes": 120, "head": "…", "synced_at": 1767225600, "error": null}, …]}`. `private: true` marks a private (work) vault; `false` is the default vault or a shared one. A client that hasn't fetched this, or can't, treats every non-default vault as private. Titles come from Kura's config (for example `Work`, `Client`). `obsidian` is the Obsidian vault name for `obsidian://open?vault=…`; it's configurable per vault and defaults to the vault's folder (its subdir), else its name. Work vaults are laptop-only in Obsidian, so a phone opens Kura's reader instead.
- **`/api/status`** keeps its top-level fields for the default vault and adds `"vaults": {"<name>": {"head", "synced_at", "notes", "error"}}`. The top-level `error` is non-null, and the probe fails, when **any** vault's last sync errored. `/api/status` needs no identity, so a private vault's entry is only `{"error": …}` (a shared vault's is in full, like the default's); its `head`, `synced_at` and `notes` are in `/api/vaults` (owner-only).

**Rules for clients**

1. Only Kura's reader and Shiori's Notes search ask for a non-default vault.
2. Kura never pushes a private vault into Hister. The push sends the default vault and the shared ones (label `vault`, a shared note under its `/v/<vault>/n/…` url), and withdraws a vault's documents once it is no longer shared.
3. Work notes never reach AI. Nothing that feeds a model (Shiori's AI features, the MCP server, a note search) sends `vault`; it gets the default vault, and drops any note whose `vault` isn't the default or a vault `/api/vaults` reports `private: false`, as defence in depth (when in doubt, drop it). **"AI" includes on-device models: Shiori doesn't offer Summarize, labels or AI Answer for a note in a private vault, even on Apple Intelligence.** Shiori's hosted pages' `/shiori/ai/*` read only from Hister, which never holds private-vault notes (it does hold shared ones).
4. Clients must not send a private vault note's `url` or `title` to Hister (history, add, label, delete). Shiori's Remember What You Open is gated to the default vault and the shared ones.

## Identity

With Machiya's identity file (`MACHIYA_IDENTITY_FILE`, [plans/identity.md](../plans/identity.md)) Kura asks it who is
calling instead of `KURA_USERS`. Without the file nothing here applies and the API is as above.

- **Proofs:** `Authorization: Bearer mch_…` (a stored token: agents, services, scripts) or `Bearer mcd_…` (a paired
  Shiori device), a Tailscale login or tagged node, a trusted proxy's login header, or the built-in sign-in's
  `machiya_session` cookie. A proof that is present but invalid is a **401**, never ignored in favour of another.
- **401** = no or a bad proof; **403** = proven, but the principal has no `kura` `read` grant (or no entry at all).
  The body is a short plain-text reason.
- **Vault scope:** a principal reads only the vaults its grant allows: `"default"`, `"shared"` (every shared vault)
  and vault names. The owner reads all. **An agent's default is the default and shared vaults; a private vault only
  when its grant names it.** Everything in [Vaults](#vaults) is cut to that scope: `/api/vaults` lists only those,
  `vault=all` means all of those, and a vault outside the scope is treated exactly like one that doesn't exist (400
  `no such vault` in the API, the same 404 page in the reader), so its name doesn't leak. A principal whose grant
  leaves out the default vault gets 400 from an API call without `vault`, and 404 from `/n/…` and `/feed.xml`.
- `/api/status` stays open; its full view (repo URL, folder, error texts) is the owner's only.
- `default`, `shared` and `v` are never vault names.

## A note in a list

```json
{
  "path": "Projects/Example-Project.md",
  "slug": "Projects/Example-Project",
  "vault": "personal",
  "folder": "Projects",
  "title": "Example Project",
  "url": "https://kura.example.ts.net/n/Projects/Example-Project",
  "summary": "A small example project that describes itself in one line…",
  "snippet": "…the <mark>search</mark> front door…",
  "tags": ["type/project", "area/tools", "topic/search"],
  "created": 1764547200,
  "changed": 1767225600,
  "published": false,
  "card_url": "https://konbini.example.ts.net/p/example-project"
}
```

`snippet` appears only in search results. `card_url` is `null` when the note isn't a project card or `KURA_KONBINI_URL` is unset. A card is a note with a board `status:` value (the older `board:` field is still read) or the `type/project` tag, and its slug is the `project:` field, else the file name slugified (Konbini's rule).

## Endpoints

### `GET /api/search`

| Param | |
|---|---|
| `q` | the query (below) |
| `sort` | `relevance` (default) or `changed` (newest first) |
| `limit`, `offset` | paging |
| `tag` | only notes with this tag or one nested under it (`topic` matches `topic/docker`) |
| `folder` | only notes in this folder or below it |

Query syntax: words are ANDed. `"a phrase"` matches the phrase, `-word` excludes it, and `word*` matches a prefix. `title:word`, and `title:shio*` for title suggestions, match in the title. `tag:x` and `folder:x` work like the parameters. Ranking is bm25, with title matches weighted most, then tags, then body.

→ `{"total": 15, "took_ms": 3, "results": [note, …]}`

### `GET /api/notes?paths=a.md,b.md` or `?urls=…`

A batch lookup, for Shiori's "You Opened" entries and result marks (pages it knows only by URL). Repeat the parameter or separate values with commas; at most 100. Every value is split on commas, so a path that contains a comma can't be looked up here (use `/api/note?path=`); it comes back in `missing`.

→ `{"notes": [note, …], "missing": ["…"]}`. Notes come back in the order asked; `missing` lists the ones Kura doesn't have.

### `GET /api/note?path=Projects/Example-Project.md`

One note in full: the list fields, plus

- `html`: the rendered note, **sanitized**. No `<script>`, `<iframe>`, `<object>`, `<embed>`, `<style>`, event-handler attributes, or `javascript:`/`data:` URLs. Links and images are absolute (`https://kura…/n/…`, `…/a/…`). Shiori renders it in a WKWebView with JavaScript off, or a sandboxed `srcdoc`, and strips anyway.
- `markdown`: the raw note, frontmatter included
- `backlinks`, `outlinks`: `[{"path", "title", "url"}]`
- `external_links` (for Shiori's "Save This Note's Links"): `[{"url", "text"}]`, the `http(s)`, **`gemini://` and `gopher://`** links in the note's body (markdown links, raw HTML `<a>`, `<scheme://…>` autolinks and **bare URLs in prose**, in lists and tables too, without trailing sentence punctuation or an unbalanced closing bracket, which Obsidian also shows as links; not inline code or code fences; `mailto:` is not one), deduplicated in order (`text` is the link text, the URL itself for a bare URL, `""` for an `<a>` with no text such as an image link), with Kura's own host and the other rooms' hosts (`MACHIYA_ROOMS` and the sister settings; ports ignored) left out. Further filtering (Hister's skip rules, what it already holds) is the client's. **Never for a private vault**: a private-vault note always has `external_links: []`, so no work link can enter a save flow. The default vault and shared vaults have them.

### `GET /api/links?folder=<f>&limit=&offset=`

The external links of every note under a folder of the **default vault** or, with `vault=<name>`, a **shared** one, in one call (Shiori's folder-level "Save Links in These Notes", so a folder isn't N requests). `folder` is required (a path such as `Reading`; `Templates/` never appears). A private vault, `all` or a comma list is a 400 (`vault=<default>` and `vault=<shared>` are fine); a missing or `/` folder is a 400; subfolders are included, notes sorted by path. Paged like the lists: `limit` (default 20, max 100), `offset`.

→ `{"total", "notes": [{"path", "title", "url", "external_links": [{"url", "text"}]}]}`; notes without external links are left out of `notes` (and out of `total`).

### `GET /api/recent?limit=&offset=`

Every note, newest change first (Shiori's Library → Notes). → `{"total", "results": [note, …]}`

### `GET /api/offline`

The notes Kura's service worker keeps on a device for good (Machiya's shared worker core, `pins` in [ui](../ui.md)): every note with `offline: true` in its frontmatter. Owner-only, the default vault and the shared ones (`/v/<vault>/n/…`), never a private vault, never a note under `Archive/` (those pages answer `Cache-Control: no-store` and are never kept on a device).

→ `{"urls": ["/n/Notes/Example", …]}`: site-relative reader URLs, the same paths as each note's `url`.

### `GET /api/tags`, `GET /api/folders`

→ `{"tags": [{"tag": "topic/docker", "count": 12}, …]}`, `{"folders": [{"folder": "Projects", "count": 12}, …]}`

### `GET /api/status`

→ `{"head": "<commit>", "synced_at": <unix>, "notes": 120, "version": "<kura version>", "vaultkit": "v0.1.0", "error": null, "auth": "tailscale"}`, plus `vaults` with several vaults ([Vaults](#vaults)). This one needs no identity and the monitoring probes use it, so it carries no configuration: an error reads `"sync failed"` (or `"push failed"` for the Hister push), and a private vault's entry is only `{"error": …}`. A request that passes the owner gate (every request when `KURA_AUTH=open`; with the identity file, the owner) also gets `"repo": "<url without credentials>"`, `"subdir": "<vault folder>"` and the full error texts. A probe matches `"ready": true` and fails on `"error": "`.

### `GET /feed.xml?q=&tag=&folder=`

RSS 2.0 of the 50 most recently changed matching notes (all notes without a filter). It feeds Shiori's Notes feed and OPML export. Default vault only, with no `vault` parameter; a shared vault's feed is `/v/<vault>/feed.xml`, a private vault has none.
