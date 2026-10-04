# Changelog: machiya-mcp

## 0.8.0

- **Code and notes stay out of AI, enforced** (the owner, 2026-10-05; sweep MACH-M-4, MACH-F-10). `pages_search` and `pages_read` are back, and the machiya plugin stops connecting Hister's own MCP (whose `search` and `get_preview` hand vault notes and code documents to any client). Every page query still ends with the notes, code and cards exclusion, and now every document Hister returns is checked again before any of it reaches the model: a note, a card, a code document, a room's host, a non-page URL, or a document of a shape this server doesn't know (no `metadata` field) is withheld, and `pages_read` refuses anything but a page whose stored URL is the one asked for.

## 0.7.4

- **Code documents stay out** (docs/contracts/hister.md, the code documents section: code-import's repos, docs, issues, PRs and releases, `metadata.source:code`). Every Hister query this server sends now ends ` -label:vault -metadata.source:vault -metadata.source:code -label:konbini`, so the label census, relabel plans and collection audits count pages only. `pages_set_label` refuses a code document. `@code` joins `@notes` and `@pages` as Hister's own aliases: never listed by `collections_list` (nor any alias naming `metadata.source:code`), never created, changed or removed. The server's instructions say `@pages` leaves the code out, and that it stays out of AI context.

## 0.7.3

- **Keep-alive (security):** a request body the server didn't read (a refused caller's 401/403, a browser Origin's
  403, a 404, a GET's) stayed on the connection and was parsed as the next request: one smuggled past Tailscale Serve,
  with a `Tailscale-User-Login` Serve never saw, so anyone who could reach the server could make tool calls as the
  owner. Such a request now ends with `Connection: close`, and every request starts with no state from the one before
  (`handle_one_request`). Tests send several callers' requests down one kept-alive connection, as Serve does.

## 0.7.2

- **`HISTER_TOKEN_FILE`** (phase 1 of the Hister sign-in, docs/contracts/hister.md): the owner's Hister token, sent as `X-Access-Token` on every Hister call and to nothing else. Unset sends none, as before (a Hister without users ignores it). A set file that is missing, empty or not a token stops the server at start; the file is re-read when it changes, so a rotated token needs no restart (a file that vanishes keeps the last good value). Never logged, never in a repr or an error; redirects from Hister are not followed while it is set. `/api/status` says `hister_token: true|false`.
- **Never a page's owner:** a Hister `/api/update` that carries `changes.user_id` is refused inside the server (the token is admin, so Hister itself would accept it). Updates still change a label and nothing else.

## 0.7.1

- **`GET /api/changelog`** serves this server's own `CHANGELOG.md` (`text/markdown`, the first 64 KiB, an `ETag` with 304, 404 without the file; HEAD too), open like `/api/status`, so the landing page can say what a deploy brought. The image carries the file.
- `/api/status` (and `/healthz`) report the vendored vaultkit as `vaultkit` (its tag, as Kura and Niwa do).
- vaultkit v0.18.0.

## 0.7.0

- **Page search and page text come from Hister's own MCP now** (owner, 2026-10-04: no overlap with Hister's MCP). `pages_search` and `pages_read` are removed: Hister's `search` (a query starting `@pages`, which keeps the vault notes out) and `get_preview` replace them, connected by the machiya plugin (0.2.0) beside this server, with `get_history` denied. `machiya_search` searches notes and cards only. `collections_list` and every label and collection tool stay (Hister's MCP has no aliases, labels or writes), and every Hister query this server sends still ends with the notes exclusion. The `capture` prompt searches pages with Hister's MCP when it is connected.
- **Identity** (phase 5 of the identity plan). With `MACHIYA_IDENTITY_FILE` callers are principals: a token, a Tailscale login or tagged node, or a trusted proxy's header (`MCP_AUTH=header`, only with the file); they need the `mcp` `use` grant (401 for no proof or a bad one, 403 without the grant). Buckets and apply tokens are per principal, its `limits` override the defaults, and the audit log records `principal` and `via`. Without the file nothing changes.
- `MCP_TOKEN_FILE`: the `mcp` principal's own token, sent as `Authorization: Bearer` to Kura, Konbini and Niwa (never Hister); redirects are not followed with it.
- vaultkit v0.15.0.

## 0.6.0

- The notes folder defaults to the repository root: `MCP_NOTES_SUBDIR` and `MCP_NOTES_SPARSE` are empty unless set (a vault kept in a folder sets both to it). An empty subdirectory works throughout the notes tools.

## 0.5.1

- Wording and the example `MCP_HISTER_BACKUP_WINDOW` value in the docs and the start-up error.

## 0.5.0

- **Settings.** `MCP_TZ` (default `UTC`) sets the notes' dates and the backup window; `MCP_HISTER_BACKUP_WINDOW` (a weekly slot such as `Sat 03:10-03:40`, local time) pauses Hister writes inside it, unset means no pause, an invalid value exits at start.
- **Notes layout rules** are configuration: `MCP_NOTES_NEVER_WRITE` (default `Templates/,Archive/`), `MCP_NOTES_NEVER_CREATE`, `MCP_NOTES_DEFAULT_FOLDER` (`Inbox/`), `MCP_NOTES_REFUSED_TAGS`, `MCP_NOTES_REQUIRED_TAGS`, `MCP_NOTES_CREATE_TAGS`, `MCP_NOTES_CARDS`, `MCP_NOTES_STATUS_LOCKED`, `MCP_NOTES_MANAGED_MARKERS`; the `notes_create` description spells out the vault's rules from them.
- `MCP_GARDEN_SKIP` (folders `garden_candidates` leaves out; default `Templates/,Archive/`), `MCP_RESERVED_LABELS` and `MCP_RESERVED_COLLECTIONS` (labels and alias names never touched, besides the built-in `vault`, `konbini`, `notes`, `pages`).

## 0.4.0

Page labels and collections: `pages_labels`, `collections_audit`, `pages_set_label`, `pages_relabel`, `collections_set`, `collections_remove`, the `tidy_collections` prompt and skill (dry run, one-use apply token, rollback file, Hister write limits).

## 0.3.0

Notes write tools: `notes_create` and `notes_update` (a write clone of the vault, one commit per call, version checks, path, tag and body rules).

## 0.2.0

Board and garden write tools, prompts, per-caller limits and the audit log.

## 0.1.0

One MCP endpoint over the rooms' APIs (read tools), the owner gate, the untrusted-content envelope.
