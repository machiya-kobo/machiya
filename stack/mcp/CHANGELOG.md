# Changelog: machiya-mcp

## 0.8.3

- Base image re-pinned to the current digest (security fixes in the base layers). No other changes.

## 0.8.2

- vaultkit re-vendored (vaultkit 0.23.0 → 0.29.0): identity headers are believed only from the trusted proxies inside vaultkit itself, single-use pairing codes, the indexing speedups, and the shared hover and card styles. Base images pinned by digest.

## 0.8.1

- **`MCP_NOTES_KNOWN_HOSTS`**: a known_hosts file with the forge's host key. Set, the write clone's ssh accepts only that key (`StrictHostKeyChecking=yes`). Unset, it still trusts the first key it sees (`accept-new`), so set it. A `GIT_SSH_COMMAND` of your own still wins. The key and file paths are quoted now, so a space can't split them.
- **The notes write clone never checks out a symlink.** The first clone writes `core.symlinks=false` before its checkout (before, only the first sync set it, after the clone had checked links out), and every git call the server makes carries it too. A link committed upstream is a plain file holding its target's path.

## 0.8.0

- **Room tokens beside the Tailscale header** (decided 2026-10-05: rooms and tools stop taking Hister's raw token; the agents' VM is a tagged node with no Tailscale login). With `MCP_AUTH_URL` (hister-login's internal address), `MCP_PUBLIC_URL` and `MCP_HISTER_USERS`, a caller may send `Authorization: Bearer mht_…`, a room token hister-login issued for this server; it acts as that Hister user. A bad, revoked or other service's token is 401 and never falls back to the header; without a token the header decides as before. `MCP_TOKEN_FILE` (this server's own credential for the rooms) should then hold a room token for Kura, Konbini and Niwa too. vaultkit re-vendored (`histerauth.TokenGate`).
- **Code and notes stay out of AI, enforced** (decided 2026-10-05; sweep MACH-M-4, MACH-F-10). `pages_search` and `pages_read` are back, and the machiya plugin stops connecting Hister's own MCP (whose `search` and `get_preview` hand vault notes and code documents to any client). Every page query still ends with the notes, code and cards exclusion, and now every document Hister returns is checked again before any of it reaches the model: a note, a card, a code document, a room's host, a non-page URL, or a document of a shape this server doesn't know (no `metadata` field) is withheld, and `pages_read` refuses anything but a page whose stored URL is the one asked for.
- **Note writes (security):** a symlinked note is refused (every path component is checked, the note too, and the protected paths against the resolved one); a commit that fails puts the file back; and uncommitted leftovers in the write clone are discarded before every write, never pushed with the next one (sweep MACH-M-3). An update can't add a frontmatter block to a note that has none, and changes no frontmatter field but summary, status and tags (MACH-M-2: a block saying `publish: true` would have published the note).
- **The login header is believed only behind the proxy (security, sweep MACH-M-5):** with `MCP_AUTH=tailscale` and no identity file the server refuses to start on a non-loopback `MCP_BIND` unless `MCP_BIND_BEHIND_PROXY=1` (set it only when the Tailscale sidecar is the only way in: machiya-mcp on a network of its own with it). A deployment on `0.0.0.0` needs the setting, with the network change, before this version starts.
- `MCP_TRUSTED_PROXIES` (addresses or CIDRs, the Tailscale sidecar's): the login header counts only from there, so the server can share a network with others (Hister's) without a neighbour forging the owner; it also satisfies the bind check. The reference compose gives machiya-mcp a network of its own with the sidecar at a fixed address.
- Each connection times out after 30 s of silence, so half-sent requests can't hold the server's threads (MACH-M-6).
- The label census pages to the end (it stopped at 8000 pages, so counts were wrong above that and a real label could be refused as new); `pages_labels` and `collections_audit` say `truncated: true` if Hister's paging stops early, and then a label missing from the census is looked up before it is called new (MACH-M-7).
- Rollback files get unique names (two writes in one second kept one), and `collections_set` saves its rollback before the change; a rollback that can't be saved stops the write (MACH-M-8).
- Apply tokens are minted and redeemed under a lock (MACH-M-9); `garden_suggest` checks and quotes a card slug before asking the board (MACH-M-10).

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

- **Page search and page text come from Hister's own MCP now** (decided 2026-10-04: no overlap with Hister's MCP). `pages_search` and `pages_read` are removed: Hister's `search` (a query starting `@pages`, which keeps the vault notes out) and `get_preview` replace them, connected by the machiya plugin (0.2.0) beside this server, with `get_history` denied. `machiya_search` searches notes and cards only. `collections_list` and every label and collection tool stay (Hister's MCP has no aliases, labels or writes), and every Hister query this server sends still ends with the notes exclusion. The `capture` prompt searches pages with Hister's MCP when it is connected.
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
