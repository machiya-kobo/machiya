# Changelog: machiya-mcp

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
