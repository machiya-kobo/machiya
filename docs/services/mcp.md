# machiya-mcp (the MCP server)

**One MCP endpoint for the whole stack**, so Claude Code (and other MCP clients) can read and update the board, read the vault's notes and the saved pages, and suggest notes for the garden, without knowing four APIs. It is a client of the rooms' HTTP APIs and changes none of them.

- **Code:** [`stack/mcp/`](../../stack/mcp/) in this repo (stdlib Python, no dependencies), Tests: `python3 -m unittest discover -s stack/mcp/tests` (from `stack/mcp/`) (fake rooms, a stateful fake Hister, and real git repos for the notes tools).
- **Vendored vaultkit:** `stack/mcp/vaultkit/` is copied with `./vendor.sh stack/mcp vX.Y.Z` from the repo root, like the other consumers; the image build runs `python3 -m vaultkit.verify` so an edited copy fails. Only the notes write tools use it, and it is imported only when `MCP_NOTES_DIR` is set.
- **Version:** 0.6.0 (see [CHANGELOG.md](../../stack/mcp/CHANGELOG.md); the tool list is below).
- **Endpoint:** `POST /mcp` (legacy Streamable HTTP, plain JSON replies, no session, no SSE; protocol 2025-03-26 to 2025-11-25). `GET /healthz` and `/api/status` need no identity.

## Standalone

Each room is an optional URL (`KONBINI_URL`, `KURA_URL`, `NIWA_URL`, `HISTER_URL`); a room's tools appear only when its URL is set, so the server works with any mix of rooms and no room depends on it.

## Tools

Names are `<room>_<verb>`, in a fixed order. Reads are `readOnlyHint: true`; writes say so, and a write tool is listed only when its rooms are configured.

| Room | Reads | Writes |
|---|---|---|
| Board (Konbini) | `board_list_cards` (filters: board, area, machine, topic, tag, stream, text), `board_get_card` (+ events), `board_review`, `board_roundup` | `board_add_backlog`, `board_move`, `board_set_next`, `board_block`, `board_set_priority`, `board_set_stream`, `board_set_goal`, `board_set_due`, `board_set_dependencies`, `board_tag`, `board_log`, `board_claim`, `board_release` |
| Notes (Kura) | `notes_search`, `notes_read` (markdown, paged, backlinks and outlinks, and a `version`), `notes_recent`, `notes_lookup`, `notes_tags`, `notes_folders` | `notes_create`, `notes_update` (through the server's own write clone of the vault, not Kura: Kura stays read-only; listed only when `MCP_NOTES_DIR` is set) |
| Pages (Hister) | `pages_search`, `pages_read` (paged), `collections_list`, `pages_labels`, `collections_audit` | `pages_set_label`, `pages_relabel`, `collections_set`, `collections_remove` |
| Garden (Niwa) | `garden_candidates` (needs Kura and Niwa) | `garden_suggest` |
| Cross-room | `machiya_search` (notes + pages + cards, grouped), `machiya_status` (which rooms answer) | |

- **`board_add_backlog`** checks for a similar card (same slug or title, one inside the other, nearly the same spelling, or a significant word in common) and a similar note (Kura title search per significant word) first, and returns them with `created: false` unless `even_if_similar` is true. The area and topics must already exist; a new one is refused as a question for the owner and nothing is created.
- **`board_move`** to `archived` needs `confirm: true`, which the model should only set after the owner agrees. `log` adds a one-line event in the same call (for `done`: what shipped).
- **`board_tag`** changes only existing `topic/<name>` and `machine/<name>` tags; `area/*`, `status/*`, `type/*` and new topics are the owner's.
- **`notes_create` / `notes_update`** (the rules are in the tool descriptions): default vault only, generated frontmatter and an existing-tags-only rule (a new tag is the owner's), paths and names checked, protected paths refused and managed notes limited by the layout rules (configurable with the `MCP_NOTES_*` rules in the Configuration table: the default protects only `Templates/` and `Archive/`; everything else, such as cards, locked statuses and managed blocks, applies only if you configure it), `expected_version` on every update (from `notes_read`), a published note needs `confirm`, frontmatter edited by line, one commit per call as the bot author `machiya-mcp`, pushed at once (pull first, a rejected push retried once, conflict markers never committed, and the result says when a concurrent edit changed what landed). No delete, move or attachments. They ask in Claude Code.
- **Labels and collections** (rules: [Hister contract](../contracts/hister.md) and this section): labels are flat lowercase topics, one per page; **a new label is the owner's** (refused as `needs_owner`); `vault`, `konbini` and import labels (the labels your importers apply, such as a feed reader's or an archive importer's; list them in `MCP_RESERVED_LABELS`) are never applied and their pages never changed; notes can't match any query (every query ends ` -label:vault -metadata.source:vault -label:konbini`). `pages_relabel` is **two steps**: a dry run (count, sample, current labels, an `apply_token` good for 10 minutes, one use, bound to the caller and to the exact set of pages) then the apply; **at most 200 pages** per apply, a **rollback file** first (`MCP_ROLLBACK_DIR`, a map of URL to old label), one `/api/update` per page that must match exactly one, 5 applies an hour. A **collection** is an `@name` alias whose value is purely `label:a` or `label:(a|b)`; `collections_set` and `collections_remove` (two steps, the old definition saved) touch only those: `notes`, `pages`, the reserved aliases (`MCP_RESERVED_COLLECTIONS`) and every other alias are the owner's. **There is no `pages_history` tool, and there never will be.** Writes pause during Hister's weekly backup window (in the `MCP_TZ` timezone). `pages_labels` pages through Hister 100 at a time (there is no label facet) and is cached five minutes.
- **`board_log`**: one line per card per 10 minutes (milestones, not steps). **`garden_suggest`**: a vault path or a card slug, a reason of at most 300 characters; it lands in Niwa's queue and the owner decides. A note that already has an open suggestion isn't suggested again: the tool reports the existing one (Niwa's `GET /api/suggestions`; with an older Niwa the 10 a day cap is the only brake).
- Resources: `machiya://card/{slug}`, `machiya://note/{path}`, `machiya://review`. Prompts (slash commands in Claude Code, `/mcp__machiya__<name>`): `weekly_review` (asks before any write), `backlog_triage`, `capture` (idea or URL to a de-duplicated card), `garden_candidates`, `tidy_collections`.

## Safety

- **Writes go along a fixed route table, with the fields each route allows, and nothing else** (`ROUTES` in `mcp.py`): Konbini `PATCH /api/cards/<slug>` (board, status, next, waiting, priority, stream, goal, due, dependsOn, tags_add, tags_remove), `POST /api/cards`, `POST …/events`, `POST`/`DELETE …/claim`, and Niwa `POST /api/suggest`. A body with `publish`, `growth`, `confidence`, `garden_pin`, `confirm_new_tags` or `new_label`, or to any other route, raises before a request is made. There is no tool to publish and never will be.
- **Owner only.** `MCP_AUTH=tailscale` (default) admits a request only when `Tailscale-User-Login` is in `MCP_USERS` (empty = nobody); `open` (localhost or a trusted LAN, a startup warning) ignores the header and logs `local`. Requests with an `Origin` header are refused (a browser page can't call it: DNS rebinding). Every call reaches the rooms as the owner's machine, so this gate is the fence.
- **No way out.** No tool takes a URL; each room has one configured base URL and tools pass paths. Every call sends `X-Agent: mcp:<agent>` (the client's `X-Agent` header). **No `Origin` or `Referer` ever goes to Konbini, Kura or Niwa** (a same-origin header is what makes them treat a caller as "web", the owner's powers); Hister calls always carry `Origin: hister://`.
- **Work vaults never pass.** No call sends `vault`; a note whose `vault` isn't Kura's default, or whose URL has `/v/<vault>/`, is dropped from every list and refused by `notes_read` ([Kura contract](../contracts/kura-api.md), client rule 3).
- **The guard fails closed.** It needs Kura's default vault name (`/api/vaults`); when Kura can't give it (error, no default, odd answer) every note tool refuses, and the failure is cached for 15 s only. The `/v/` test reads the URL as Kura serves it (`//` folded, `%XX` decoded) and looks below the path of `KURA_PUBLIC_URL`. Kura refuses a public address with a path since 0.5.0, so that last part is defence in depth.
- **Notes and pages are separate.** Every page search ends ` -label:vault -metadata.source:vault`; results labelled `vault`, or on a room's own host, are dropped; `pages_read` refuses those hosts (the same rule as Shiori's AI features); collections whose expansion names the vault are hidden.
- **Content is data.** Every result is `structuredContent` with `trust: "untrusted"`, a notice, and the text under `untrusted_content` (Hister's own MCP shape), invisible control characters stripped. Reads are paged (`max_chars` 20k default, `offset`).
- **Refusals are questions.** A room's 403, or a 409 about tags, comes back as `isError` with `needs_owner: true` and "This is a question for the owner; don't retry with other flags." Other errors (a 404, a 422 such as a card depending on itself, a repeated suggestion) come back as plain errors. A read-only board (405) says so.
- **Limits and log.** Token buckets per caller: 120 reads a minute; board writes 30 a minute and 300 a day; garden suggestions 10 a day. A call refused by one bucket takes nothing from the others, and a call refused for its arguments or by a room doesn't use up the day's allowance. An audit log (`MCP_LOG`, JSONL): time, login, agent, tool, status, duration, argument keys (values only for slugs, columns and similar); never note or page text.

## Configuration

| Variable | |
|---|---|
| `MCP_AUTH`, `MCP_USERS`, `MCP_BIND`, `MCP_PORT` | Machiya's auth shape (above); defaults `tailscale`, none, `0.0.0.0`, 8080 |
| `KONBINI_URL`, `KURA_URL`, `NIWA_URL`, `HISTER_URL` | the rooms; `<ROOM>_PUBLIC_URL` sets the links in results (default the same) |
| `KURA_URL` must be a URL Kura's owner gate admits | Kura checks `Tailscale-User-Login` (`KURA_USERS`), and this server sends only `X-Agent`: point `KURA_URL` at the tailscale-served host, or at a Kura running `KURA_AUTH=open` on a private network. Konbini and Niwa are the same (`KANBAN_AUTH`, `NIWA_AUTH`) |
| `MCP_NOTES_DIR` (+ `MCP_NOTES_REPO_URL`, `MCP_NOTES_REFERENCE`, `MCP_NOTES_SPARSE`, `MCP_NOTES_SUBDIR`, `MCP_NOTES_SSH_KEY`, `MCP_NOTES_TZ`, `MCP_NOTES_PER_MIN`, `MCP_NOTES_PER_DAY`) | the notes write clone: setting the directory turns the write tools on (the first start clones `MCP_NOTES_REPO_URL`, borrowing `MCP_NOTES_REFERENCE`'s objects); needs git, ssh and vaultkit, which the image carries. Unset: the server stays read-and-board only and needs no vaultkit |
| `MCP_TZ` [UTC], `MCP_HISTER_BACKUP_WINDOW` [none] | the time zone for the notes' dates and the backup window (`MCP_NOTES_TZ` overrides the notes' only); a weekly slot like `Sat 03:10-03:40` (local time, within one day) when Hister is stopped for its backup: Hister writes pause then (an invalid value makes the server exit at start; unset means no pause) |
| `MCP_HISTER_WRITES_PER_MIN`, `MCP_BULK_PER_HOUR`, `MCP_RELABEL_MAX`, `MCP_ROLLBACK_DIR` | Hister write limits (defaults 60 a minute, 5 bulk applies an hour), the apply cap (200), the rollback files (default `/data/rollback`) |
| `MCP_RESERVED_LABELS` [none], `MCP_RESERVED_COLLECTIONS` [none] | page labels never applied or changed, besides the built-in `vault` and `konbini`; alias names never created, changed or removed, besides `notes` and `pages` (a label-only alias among them is checked for drift in `collections_audit`) |
| `MCP_GARDEN_SKIP` [`Templates/,Archive/`] | folders `garden_candidates` leaves out |
| `MCP_NOTES_SUBDIR` [repo root], `MCP_NOTES_SPARSE` [none] | the vault folder inside the write clone, and the sparse-checkout paths (set both to the folder when the vault is not at the repository root) |
| **Notes layout rules** (comma lists; path rules are vault-relative prefixes `Dir/` or `Dir/File.md`, or `*` globs, case-insensitive) | `MCP_NOTES_NEVER_WRITE` [`Templates/,Archive/`]: paths the write tools never create or change. `MCP_NOTES_NEVER_CREATE` [none]: paths where a new note may not be made (existing notes there can still be updated). `MCP_NOTES_DEFAULT_FOLDER` [`Inbox/`]: where `notes_create` puts a note given only a title. `MCP_NOTES_REFUSED_TAGS` [none]: tags never set from here, exact or `prefix*`. `MCP_NOTES_REQUIRED_TAGS` [none]: tag prefixes (`kind/`) a note must have at least one tag of. `MCP_NOTES_CREATE_TAGS` [none]: on a new note, tags in the namespaces named here must be one of these. `MCP_NOTES_CARDS` [none]: folders and tags that mark a note as a board card (its status and tags change with the board tools). `MCP_NOTES_STATUS_LOCKED` [none]: folders and tags whose notes' `status` another tool manages. `MCP_NOTES_MANAGED_MARKERS` [none]: names of `<!-- name:start -->` / `<!-- name:end -->` blocks another tool rewrites; they are never edited |
| `MCP_LOG` | audit log path (default `/data/mcp.log`) |
| `MCP_READS_PER_MIN`, `MCP_WRITES_PER_MIN`, `MCP_WRITES_PER_DAY`, `MCP_SUGGESTS_PER_DAY` | limits per caller (defaults 120, 30, 300, 10) |

## Using it (Claude Code)

**Install** it per machine with the plugin (`plugins/machiya/install.sh`), which adds the permission rules below under the plugin's prefix.

```
claude mcp add --transport http --scope user machiya https://machiya-mcp.example.ts.net/mcp --header "X-Agent: claude@$(hostname -s)"
```

Tools appear as `mcp__machiya__<tool>`. Suggested permissions (small writes run free, everything else asks):

```json
{"permissions": {
  "allow": ["mcp__machiya__board_list_cards", "mcp__machiya__board_get_card", "mcp__machiya__board_review", "mcp__machiya__board_roundup",
            "mcp__machiya__notes_*", "mcp__machiya__pages_search", "mcp__machiya__pages_read", "mcp__machiya__collections_list",
            "mcp__machiya__garden_candidates", "mcp__machiya__pages_labels", "mcp__machiya__collections_audit", "mcp__machiya__machiya_*",
            "mcp__machiya__board_set_next", "mcp__machiya__board_log", "mcp__machiya__board_claim", "mcp__machiya__board_release"],
  "ask": ["mcp__machiya__board_add_backlog", "mcp__machiya__board_move", "mcp__machiya__board_block", "mcp__machiya__board_set_priority",
          "mcp__machiya__board_set_stream", "mcp__machiya__board_set_goal", "mcp__machiya__board_set_due", "mcp__machiya__board_set_dependencies", "mcp__machiya__board_tag", "mcp__machiya__garden_suggest",
          "mcp__machiya__notes_create", "mcp__machiya__notes_update",
          "mcp__machiya__pages_set_label", "mcp__machiya__pages_relabel", "mcp__machiya__collections_set", "mcp__machiya__collections_remove"]}}
```
 A Claude Code plugin ([`plugins/machiya/`](../../plugins/machiya/), installed from this repo as a marketplace) bundles this connection with the cross-room skills `backlog-add`, `weekly-review`, `recall` and `garden-suggest`; its tools are named `mcp__plugin_machiya_machiya__<tool>`, so the permission rules need that prefix instead. Claude.ai, the desktop app's connectors and the phone can't reach it (they dial from Anthropic's cloud).

## Wiring in a stack

Add a `machiya-mcp` service beside your other rooms: build context `stack/mcp/` (or a published image), a non-root user, a small memory limit, a data volume, and the default network only: **not** `network_mode: service:tailscale`, because its calls to the rooms must leave through the host's own tailscaled, like Konbini's calls to Kura, or they would arrive as the tagged sidecar, which has no `Tailscale-User-Login`. Environment: `MCP_USERS=<your Tailscale login>`, `KONBINI_URL`/`KURA_URL`/`NIWA_URL=https://<room>.<tailnet>.ts.net`, `HISTER_URL=http://hister:4433` (join the Hister network); and in `serve.json` the Tailscale Service name → `http://machiya-mcp:8080`. **Order matters:** create the Tailscale Service and its owner-only grant in the policy first; a `serve.json` naming an undefined service can break the sidecar. Verify after: `machiya_status` from Claude Code shows every room `ok` (it proves the identity path), `notes_search` returns notes, and a note from a non-default vault is unreachable. Monitoring: probe `/healthz`.
