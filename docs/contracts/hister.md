# Using Hister

How Machiya's services use [Hister](../services/hister.md). Hister is upstream code, so these are rules for the callers.

## Every call

- **Send `Origin: hister://`** on POSTs (without it: 403). GETs to `/search` need it unless they ask for JSON (`format=json` or `Accept: application/json`, allowed since v0.20.0); sending it always is simplest.
- Hister has no token here. In the reference compose, services reach it over a private Docker network (`http://hister:4433`); devices use `https://hister.example.ts.net`.
- **Never `hister index --force` a URL Hister already has.** It replaces the imported metadata (tags, archive link). Check with `url:"…"` first.
- `POST /api/add` with only a URL does **not** fetch the page. Fetch with the `hister` CLI (`hister -u <server> index --label <l> <url>`), or send the text yourself.
- Avoid your backup window if Hister is stopped for backups: calls hang while it is down.

## Endpoints used

| Call | Who |
|---|---|
| `GET /search?q=<query>` (Hister query language: `label:`, `url:"…"`, `added:`; exclude a label with ` -label:x` appended to the query (works server-side with correct totals: `* -label:vault` = everything minus the notes). Hister has no `NOT` operator (`NOT` is a plain word) and no `exclude_label` param. `metadata.source:vault` is a query field too, e.g. `-label:vault -metadata.source:vault`) | Shiori, Konbini (reading line, link rot) |
| `POST /api/add` `{url, title, text, html?, label, added?, metadata}` | Kura (the note push), feed-reader and other importers, Shiori (saves). New documents carry Hister's `metadata.source` convention: `vault` (Kura), the importer's own name (with its own `<name>_*` keys), `shiori`. |
| `POST /api/delete` `{"query": "url:\"…\""}` | Kura (a note deleted, renamed or moved) |
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

`@pages` = `* -label:vault -metadata.source:vault` and `@notes` = `label:vault` (created in Hister's alias settings), so Hister's own UI, extension, TUI and MCP split pages and notes the way Shiori does. They sit beside your own topic aliases. **Shiori hides any alias whose expansion names the vault** (`label:vault` or `metadata.source:vault`) from its collections and reserves the collection names "notes" and "pages", so vault aliases never appear as Shiori collections.

## What callers must never do

- SearXNG never queries Hister.
- No Hister result, copy or `private_url` appears on gemini, gopher or the public garden export. Those read only `archive_url` (Wayback).
- Shiori's AI features never summarize a note: they refuse by host (`kura.`, `konbini.`, `niwa.`) and by label `vault`.
