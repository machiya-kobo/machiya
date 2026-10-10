# machiya-mcp (the MCP server)

**One MCP endpoint for Machiya's apps.** Claude Code and other MCP clients use it to:
- read and update the board;
- read and write notes;
- search and read saved pages, label them and keep their collections;
- suggest notes for the garden.

It calls the apps' HTTP APIs and changes none of them. (Below, the apps it reaches, Konbini, Kura, Niwa and Hister, are its rooms.)

AI clients read saved pages here (`pages_search`, `pages_read`), never through Hister's own MCP, which returns notes and code documents to anyone who asks. Here "code and notes stay out of AI" is enforced in code, not in a prompt.

- **Code:** [`stack/mcp/`](../../stack/mcp/) in this repo (stdlib Python, no dependencies).
- **Tests:** `python3 -m unittest discover -s tests` from `stack/mcp/`, with `markdown` and `pyyaml` installed. They use fake rooms, a stateful fake Hister, and real git repos for the notes tools.
- **Vendored vaultkit:** `stack/mcp/vaultkit/`, copied with `./vendor.sh stack/mcp vX.Y.Z` from the repo root. The image build runs `python3 -m vaultkit.verify`, so an edited copy fails. The server imports it only for the notes write tools (when `MCP_NOTES_DIR` is set), the identity file, and `GET /api/changelog` (`vaultkit.changelog`). `/api/status` reads the vaultkit tag from the manifest.
- **Changes:** [CHANGELOG.md](../../stack/mcp/CHANGELOG.md).
- **Endpoint:** `POST /mcp`: legacy Streamable HTTP, plain JSON replies, no session, no SSE; protocol 2025-03-26 to 2025-11-25.
- `GET /healthz`, `/api/status` and `/api/changelog` (the server's `CHANGELOG.md` as `text/markdown`, for the [landing page](landing.md)) need no identity.

## Standalone

Each room is an optional URL: `KONBINI_URL`, `KURA_URL`, `NIWA_URL`, `HISTER_URL`. A room's tools appear only when its URL is set. The server works with any mix of rooms, and no room depends on it.

## Tools

- Names are `<area>_<verb>`, in a fixed order.
- Reads are `readOnlyHint: true`. Writes say so.
- A write tool is listed only when its rooms are configured.

| Area | Reads | Writes |
|---|---|---|
| Board (Konbini) | `board_list_cards` (filters: board, area, machine, topic, tag, stream, text), `board_get_card` (+ events), `board_review`, `board_roundup` | `board_add_backlog`, `board_move`, `board_set_next`, `board_block`, `board_set_priority`, `board_set_stream`, `board_set_goal`, `board_set_due`, `board_set_dependencies`, `board_tag`, `board_log`, `board_claim`, `board_release` |
| Notes (Kura) | `notes_search`, `notes_read` (markdown, paged, backlinks and outlinks, and a `version`), `notes_recent`, `notes_lookup`, `notes_tags`, `notes_folders` | `notes_create`, `notes_update` (through the server's own write clone of the vault, not Kura: Kura stays read-only; listed only when `MCP_NOTES_DIR` is set) |
| Pages, labels and collections (Hister) | `pages_search` (words, a label, a collection), `pages_read` (text, paged), `collections_list`, `pages_labels`, `collections_audit` | `pages_set_label`, `pages_relabel`, `collections_set`, `collections_remove` |
| Garden (Niwa) | `garden_candidates` (needs Kura and Niwa) | `garden_suggest` |
| Cross-room | `machiya_search` (notes + cards, grouped), `machiya_status` (which rooms answer) | |

### Board

- **`board_add_backlog`** first looks for a similar card and a similar note, and returns them with `created: false` unless `even_if_similar` is true.
  - A similar card: the same slug or title, one inside the other, nearly the same spelling, or a significant word in common.
  - A similar note: a Kura title search for each significant word.
  - The area must already exist, and so must the topics unless you said yes to a new one: the model then passes it in `new_topics` and the topic is created with the card. Otherwise it is refused as a question for you and nothing is created. Needs Konbini 0.22.0.
- **`board_move`** to `archived` needs `confirm: true`. The model should set it only after you agree. `log` adds a one-line event in the same call (for `done`: what shipped).
- **`board_tag`** changes `topic/<name>` and `machine/<name>` tags. A topic that does not exist yet is created only when you said yes and the model lists it in `new_topics`; the board logs a `topic_created` event on the card. `area/*`, `status/*` and `type/*` are yours to set.
- **`board_log`**: one line per card per 10 minutes. Milestones, not steps.

### Notes

The tool descriptions carry these rules too. Claude Code asks before each call.

- Default vault only.
- Generated frontmatter, and existing tags only: a new tag is yours to make.
- Paths and names are checked. Protected paths are refused, and managed notes are limited by the layout rules ([Configuration](#notes-layout-rules)). By default only `Templates/` and `Archive/` are protected. Cards, locked statuses and managed blocks apply only if you configure them.
- Every update needs `expected_version`, from `notes_read`.
- A published note needs `confirm`.
- Frontmatter is edited by line.
- One commit per call, as the bot author `machiya-mcp`, pushed at once:
  - it pulls first, and retries a rejected push once;
  - it never commits conflict markers;
  - the result says when a concurrent edit changed what landed.
- No delete, move or attachments.

### Labels and collections

The rules are here and in the [Hister contract](../contracts/hister.md).

- **Labels** are flat lowercase topics, one per page.
- **A new label is yours to make.** It's refused as `needs_owner`.
- `vault`, `konbini` and import labels are never applied, and their pages are never changed. Import labels are the ones your importers apply, such as a feed reader's or an archive importer's; list them in `MCP_RESERVED_LABELS`.
- Notes and code documents can't match any query (see [Safety](#safety)).
- **`pages_relabel` takes two steps:**
  1. A dry run returns the count, a sample, the current labels and an `apply_token`. The token is good for 10 minutes and one use, and is bound to the caller and to the exact set of pages.
  2. The apply. **At most 200 pages** per apply. It writes a **rollback file** first (`MCP_ROLLBACK_DIR`, a map of URL to old label), then sends one `/api/update` per page, which must match exactly one. 5 applies an hour.
- **A collection** is an `@name` alias whose value is purely `label:a` or `label:(a|b)`.
- `collections_set` and `collections_remove` take two steps too, and save the old definition. They touch only collections. `notes`, `pages`, the reserved aliases (`MCP_RESERVED_COLLECTIONS`) and every other alias are yours.
- **There is no `pages_history` tool, and there never will be.**
- Writes pause during Hister's weekly backup window (in the `MCP_TZ` time zone).
- `pages_labels` pages through Hister 100 at a time (there is no label facet), and is cached for five minutes.

### Garden

**`garden_suggest`** takes a vault path or a card slug, and a reason of at most 300 characters. The suggestion lands in Niwa's queue and you decide.

A note that already has an open suggestion isn't suggested again: the tool reports the existing one (Niwa's `GET /api/suggestions`). With an older Niwa, the cap of 10 a day is the only brake.

### Resources and prompts

- Resources: `machiya://card/{slug}`, `machiya://note/{path}`, `machiya://review`.
- Prompts (slash commands in Claude Code, `/mcp__machiya__<name>`): `weekly_review` (asks before any write), `backlog_triage`, `capture` (an idea or URL to a de-duplicated card), `garden_candidates`, `tidy_collections`.

## Safety

### Writes follow a fixed route table

`ROUTES` in `mcp.py` lists every write route and the fields it allows. Nothing else goes out.

- Konbini: `PATCH /api/cards/<slug>` (board, status, next, waiting, priority, stream, goal, due, dependsOn, tags_add, tags_remove), `POST /api/cards`, `POST …/events`, `POST`/`DELETE …/claim`.
- Niwa: `POST /api/suggest`.
- A body with `publish`, `growth`, `confidence`, `garden_pin`, `confirm_new_tags` or `new_label`, or any other route, raises before a request is made.
- There is no tool to publish, and there never will be.

### Who may call it

Only you. Without the identity file, `MCP_AUTH` decides:

- **`tailscale`** (default): a request needs a `Tailscale-User-Login` listed in `MCP_USERS` (empty = nobody).
  - With `MCP_AUTH_URL` set, a request may instead carry a **room token** that hister-login issued for this server (`Authorization: Bearer mht_…`), acting as one of `MCP_HISTER_USERS` ([identity.md](../identity.md#room-tokens)). This is for an agent on a tagged machine, which has no Tailscale login. It never gets Hister's raw token.
  - A bad, revoked or other service's token gets 401. It never falls back to the header.
- **`open`** (localhost or a trusted LAN; warns at startup) ignores the header and logs the caller as `local`.

**With the identity file** (`MACHIYA_IDENTITY_FILE`, [identity.md](../identity.md), vaultkit's `identity.py`), the file replaces `MCP_USERS`. Each request resolves to a principal, from one of:
- `Authorization: Bearer mch_…`, an agent's token minted with `python3 -m vaultkit.identity token mint <name>`;
- a Tailscale login, or a tagged node (`MCP_ACCEPT_APP_CAPS=1`);
- with `MCP_AUTH=header`, a trusted proxy's header (`MCP_AUTH_HEADER`; only with the file).

No proof, or a bad one, gets **401** and never falls through to a login. A principal without the `mcp` `use` grant gets **403** (`{"error": …}`, like the other refusals). In `open` mode, a request without a token is still you. There are no sign-in pages (`MCP_SIGNIN` is ignored).

**In every mode:**
- A request with an `Origin` header is refused, so a browser page can't call the server (DNS rebinding).
- `tailscale` and `header` refuse a non-loopback `MCP_BIND` unless the proxy is the only way in (`MCP_BIND_BEHIND_PROXY=1` or `MCP_TRUSTED_PROXIES`).
- Without the identity file, every call reaches the rooms as your machine, so this gate is the only fence.

### How it calls the rooms

- The rooms see **only this server**. It calls them as the principal `mcp` with its own token (`MCP_TOKEN_FILE`), never on a caller's behalf. So what `mcp` may do in a room is the most any caller can do through it. The caller's own grants decide only whether it may use the MCP; the route table and forbidden fields stay as defense in depth.
- **No way out.** No tool takes a URL. Each room has one configured base URL, and tools pass paths.
- Every call sends `X-Agent: mcp:<agent>`, from the client's `X-Agent` header. With the identity file it's `mcp:<principal>` or `mcp:<principal>/<client's X-Agent>`. It's a label for the rooms' history, never a permission.
- With `MCP_TOKEN_FILE`, every call to Kura, Konbini and Niwa also sends `Authorization: Bearer <the mcp principal's token>`, and a redirect is an error, never followed. The token never goes to Hister and is never logged.
- With `HISTER_TOKEN_FILE`, every Hister call sends `X-Access-Token` (your Hister token), which goes nowhere else.
- **No `Origin` or `Referer` ever goes to Konbini, Kura or Niwa.** A same-origin header makes them treat a caller as "web", with your full powers.
- Hister calls always carry `Origin: hister://`.

### Work vaults never pass

- No call sends `vault`.
- A note whose `vault` isn't Kura's default, or whose URL has `/v/<vault>/`, is dropped from every list and refused by `notes_read` ([Kura contract](../contracts/kura-api.md), client rule 3).
- **The guard fails closed.** It needs Kura's default vault name (`/api/vaults`). When Kura can't give it (an error, no default, an odd answer), every note tool refuses. The failure is cached for 15 s only.
- The `/v/` test reads the URL as Kura serves it (`//` folded, `%XX` decoded) and looks below the path of `KURA_PUBLIC_URL`. Kura refuses a public address with a path, so that last part is defense in depth.

### Notes and pages stay apart

- Every Hister query this server sends ends ` -label:vault -metadata.source:vault -metadata.source:code -label:konbini`.
- It never changes documents labeled `vault`, code documents (`metadata.source:code`, [code-import](code-import.md)), or documents on a room's own host. `pages_set_label` refuses those hosts, the same rule as Shiori's AI features.
- Collections whose expansion names the vault are hidden.
- **Page reads check every document again** before any of it reaches the model (`rooms/hister.py`, `readable`). It withholds a note, a card (`label:konbini`), a code document, a room's host, a non-page URL, or a document whose shape the server doesn't recognize (no `metadata` field, say, after a Hister upgrade). `pages_search` says how many it withheld.
- `pages_read` looks the URL up and refuses anything but a page.
- The query suffix alone fails closed only because of how Hister v0.20.0 parses an unclosed `"`. The second check doesn't depend on it.

### Content is data

- Every result is `structuredContent` with `trust: "untrusted"`, a notice, and the text under `untrusted_content` (Hister's own MCP shape).
- Invisible control characters are stripped.
- `notes_read` is paged: `max_chars` (20k by default) and `offset`.

### Refusals are questions

- A room's 403, or a 409 about tags, comes back as `isError` with `needs_owner: true` and "This is a question for the owner; don't retry with other flags."
- Other errors come back as plain errors: a 404, a 422 (such as a card depending on itself), a repeated suggestion.
- A read-only board (405) says so.

### Limits and the audit log

- Token buckets per caller: the login, or with the identity file the principal's name (which also owns its apply tokens).
  - 120 reads a minute;
  - board writes: 30 a minute and 300 a day;
  - garden suggestions: 10 a day.
- A call refused by one bucket takes nothing from the others. A call refused for its arguments, or by a room, doesn't use up the day's allowance.
- The audit log (`MCP_LOG`, JSONL) records the time, login, agent, tool, status, duration and argument keys. Values only for slugs, columns and the like. Never note or page text.
- With the identity file it records `principal` and `via`, how the caller was proven (`token:<id>`, `tailscale`, `proxy`, `open`). Never a token.

**Limits from the identity file.** A principal's `limits` replace the defaults for its own buckets.
- The names are the settings' names without `MCP_`, in lower case: `reads_per_min`, `writes_per_min`, `writes_per_day`, `suggests_per_day`, `notes_per_min`, `notes_per_day`, `hister_writes_per_min`, `bulk_per_hour`.
- Other names are ignored, because every room shares the file.
- `0` turns that kind of call off for the principal.
- Example: `limits = { reads_per_min = 60, writes_per_day = 50, suggests_per_day = 0 }`.

## Configuration

### Access

| Variable | Default | |
|---|---|---|
| `MCP_AUTH` | `tailscale` | `tailscale` or `open`; with the identity file also `header` ([Who may call it](#who-may-call-it)) |
| `MCP_USERS` | none (nobody) | allowed Tailscale logins. Unused with the identity file |
| `MCP_BIND`, `MCP_PORT` | `0.0.0.0`, `8080` | the listener |
| `MCP_BIND_BEHIND_PROXY` | off | `1` says the Tailscale sidecar is the only way in (machiya-mcp and its sidecar on a network of their own). Without it, `tailscale` mode **refuses to start** on a non-loopback `MCP_BIND`: a neighboring container could send the login header and pass as you. A server started anyway believes no header. The identity file applies the same rule |
| `MCP_TRUSTED_PROXIES` | none | addresses or CIDRs, such as the sidecar's fixed address (`172.31.250.10/32` in the reference compose). `Tailscale-User-Login` counts only from there, whatever other networks the server is on (Hister's). Satisfies the bind check, and is safer than `MCP_BIND_BEHIND_PROXY` |
| `MCP_AUTH_URL` | none | hister-login's internal address (`http://hister-login:8081`, over a private network to it). Turns on room tokens beside the Tailscale header |
| `MCP_PUBLIC_URL` | none | this server's public address: the origin a room token must name |
| `MCP_HISTER_USERS` | none | the Hister usernames a room token may act as (never `*`) |
| `MACHIYA_IDENTITY_FILE` | none | Machiya's identity file (read-only): callers by principal, the `mcp` `use` grant, limits from the file. `MCP_USERS` is then unused. A bad file or setting refuses to start |
| `MCP_AUTH_HEADER` | none | with the identity file and `MCP_AUTH=header`: the proxy's login header, e.g. `Remote-User` |
| `MCP_ACCEPT_APP_CAPS` | off | with the identity file: `1` reads Tailscale tagged nodes' capability (Serve with `--accept-app-caps`, Tailscale v1.92+) |

### Rooms and tokens

| Variable | Default | |
|---|---|---|
| `KONBINI_URL`, `KURA_URL`, `NIWA_URL`, `HISTER_URL` | none | the rooms. An unset room's tools are hidden |
| `<ROOM>_PUBLIC_URL` | the room's URL | the links in results |
| `MCP_TOKEN_FILE` | none | the `mcp` principal's own token (first line of the file), sent as `Authorization: Bearer` to Kura, Konbini and Niwa, never to Hister. Redirects are then not followed. Set but missing or empty: refuses to start. Unset: no `Authorization` |
| `HISTER_TOKEN_FILE` | none | your Hister token (first line of the file; [contracts/hister.md](../contracts/hister.md)), sent as `X-Access-Token` on every Hister call and to nothing else. Re-read when the file changes. Redirects from Hister are then not followed. Set but missing, empty or not a token: refuses to start. Unset: no token (a Hister without users ignores it). The token is admin, so the server refuses any Hister update carrying `changes.user_id` |

`KURA_URL` must be a URL Kura's gate admits. Kura checks `Tailscale-User-Login` (`KURA_USERS`), and this server sends only `X-Agent`. So point `KURA_URL` at the Tailscale-served host, or at a Kura running `KURA_AUTH=open` on a private network. Konbini and Niwa are the same (`KANBAN_AUTH`, `NIWA_AUTH`). With the identity file in the rooms, `MCP_TOKEN_FILE` is the proof instead, and each room grants the `mcp` principal what it may do there.

### Notes write clone

| Variable | Default | |
|---|---|---|
| `MCP_NOTES_DIR` | none | the notes write clone. Setting it turns the write tools on; the first start clones `MCP_NOTES_REPO_URL`, borrowing `MCP_NOTES_REFERENCE`'s objects. Needs git, ssh and vaultkit, which the image carries. Unset: the server stays read-and-board only and needs no vaultkit |
| `MCP_NOTES_REPO_URL`, `MCP_NOTES_REFERENCE`, `MCP_NOTES_SSH_KEY` | none | where to clone from, a local repo to borrow objects from, the ssh key |
| `MCP_NOTES_KNOWN_HOSTS` | none | a known_hosts file holding the forge's host key (0.8.1). Set, ssh accepts only that key (`StrictHostKeyChecking=yes`). Unset, ssh trusts the first key it sees (`accept-new`), so set it. A `GIT_SSH_COMMAND` in the environment overrides both settings |
| `MCP_NOTES_SUBDIR` | repo root | the vault folder inside the write clone |
| `MCP_NOTES_SPARSE` | none | the sparse-checkout paths. When the vault isn't at the repository root, set this and `MCP_NOTES_SUBDIR` to the folder |
| `MCP_NOTES_TZ` | `MCP_TZ` | the time zone for the notes' dates |
| `MCP_NOTES_PER_MIN`, `MCP_NOTES_PER_DAY` | | note write limits per caller |

#### Notes layout rules

Comma lists. Path rules are vault-relative prefixes (`Dir/` or `Dir/File.md`) or `*` globs, case-insensitive.

| Variable | Default | |
|---|---|---|
| `MCP_NOTES_NEVER_WRITE` | `Templates/,Archive/` | paths the write tools never create or change |
| `MCP_NOTES_NEVER_CREATE` | none | paths where a new note may not be made (existing notes there can still be updated) |
| `MCP_NOTES_DEFAULT_FOLDER` | `Inbox/` | where `notes_create` puts a note given only a title |
| `MCP_NOTES_REFUSED_TAGS` | none | tags never set from here, exact or `prefix*` |
| `MCP_NOTES_REQUIRED_TAGS` | none | tag prefixes (`kind/`); a note must have at least one tag from one of them |
| `MCP_NOTES_CREATE_TAGS` | none | on a new note, tags in the namespaces named here must be one of these |
| `MCP_NOTES_CARDS` | none | folders and tags that mark a note as a board card (its status and tags change with the board tools) |
| `MCP_NOTES_STATUS_LOCKED` | none | folders and tags whose notes' `status` another tool manages |
| `MCP_NOTES_MANAGED_MARKERS` | none | names of `<!-- name:start -->` / `<!-- name:end -->` blocks another tool rewrites. They are never edited |

### Hister writes

| Variable | Default | |
|---|---|---|
| `MCP_TZ` | `UTC` | the time zone for the notes' dates and the backup window |
| `MCP_HISTER_BACKUP_WINDOW` | none | a weekly slot like `Sat 03:10-03:40` (local time, within one day) when Hister is stopped for its backup. Hister writes pause then. An invalid value makes the server exit at start; unset means no pause |
| `MCP_HISTER_WRITES_PER_MIN` | `60` | Hister writes a minute |
| `MCP_BULK_PER_HOUR` | `5` | bulk applies an hour |
| `MCP_RELABEL_MAX` | `200` | pages per apply |
| `MCP_ROLLBACK_DIR` | `/data/rollback` | the rollback files |
| `MCP_RESERVED_LABELS` | none | page labels never applied or changed, besides the built-in `vault` and `konbini` |
| `MCP_RESERVED_COLLECTIONS` | none | alias names never created, changed or removed, besides `notes` and `pages`. `collections_audit` checks a label-only alias among them for drift |

### Other

| Variable | Default | |
|---|---|---|
| `MCP_GARDEN_SKIP` | `Templates/,Archive/` | folders `garden_candidates` leaves out |
| `MCP_LOG` | `/data/mcp.log` | the audit log |
| `MCP_READS_PER_MIN`, `MCP_WRITES_PER_MIN`, `MCP_WRITES_PER_DAY`, `MCP_SUGGESTS_PER_DAY` | `120`, `30`, `300`, `10` | limits per caller. A principal's `limits` in the identity file override them for that principal |

## Using it (Claude Code)

**Install** it on each machine with the plugin (`plugins/machiya/install.sh`), which adds the permission rules below under the plugin's prefix. By hand:

```
claude mcp add --transport http --scope user machiya https://machiya-mcp.example.ts.net/mcp --header "X-Agent: claude@$(hostname -s)"
```

Tools appear as `mcp__machiya__<tool>`. Suggested permissions (small writes run free, everything else asks):

```json
{"permissions": {
  "allow": ["mcp__machiya__board_list_cards", "mcp__machiya__board_get_card", "mcp__machiya__board_review", "mcp__machiya__board_roundup",
            "mcp__machiya__notes_*", "mcp__machiya__collections_list",
            "mcp__machiya__garden_candidates", "mcp__machiya__pages_labels", "mcp__machiya__collections_audit", "mcp__machiya__machiya_*",
            "mcp__machiya__board_set_next", "mcp__machiya__board_log", "mcp__machiya__board_claim", "mcp__machiya__board_release"],
  "ask": ["mcp__machiya__board_add_backlog", "mcp__machiya__board_move", "mcp__machiya__board_block", "mcp__machiya__board_set_priority",
          "mcp__machiya__board_set_stream", "mcp__machiya__board_set_goal", "mcp__machiya__board_set_due", "mcp__machiya__board_set_dependencies", "mcp__machiya__board_tag", "mcp__machiya__garden_suggest",
          "mcp__machiya__notes_create", "mcp__machiya__notes_update",
          "mcp__machiya__pages_set_label", "mcp__machiya__pages_relabel", "mcp__machiya__collections_set", "mcp__machiya__collections_remove"]}}
```

**The plugin** ([`plugins/machiya/`](../../plugins/machiya/), installed from this repo as a marketplace) bundles this connection with the skills `backlog-add`, `weekly-review`, `recall`, `garden-suggest` and `tidy-collections`.
- Its tools are named `mcp__plugin_machiya_machiya__<tool>`, so the permission rules need that prefix instead.
- It doesn't connect Hister's MCP. Its `install.sh` **denies** every Hister MCP tool under every name it can have (`mcp__plugin_machiya_hister__*`, `mcp__hister__*`), so an older install or a hand-added server can't bring notes or code back.

Claude.ai, the desktop app's connectors and the phone can't reach the server: they dial from Anthropic's cloud.

## Wiring in a stack

Add a `machiya-mcp` service beside your other rooms:
- build context `stack/mcp/` (or a published image), a non-root user, a small memory limit, a data volume;
- a network of its own, shared only with its Tailscale sidecar, with the sidecar at a fixed address there. Then set `MCP_TRUSTED_PROXIES=<that address>`, or `MCP_BIND_BEHIND_PROXY=1` if the server is on no other network;
- Hister reached over its network or a second small one, and no other network.

**Not** `network_mode: service:tailscale`. Its calls to the rooms must leave through the host's own tailscaled, like Konbini's calls to Kura. Otherwise they arrive as the tagged sidecar, which has no `Tailscale-User-Login`.

Environment:
- `MCP_USERS=<your Tailscale login>`
- `KONBINI_URL`/`KURA_URL`/`NIWA_URL=https://<room>.<tailnet>.ts.net`
- `HISTER_URL=http://hister:4433` (join the Hister network)

In `serve.json`, point the Tailscale Service name at `http://machiya-mcp:8080`.

**Order matters.** Create the Tailscale Service and its owner-only grant in the policy first. A `serve.json` naming an undefined service can break the sidecar.

Verify after:
- `machiya_status` from Claude Code shows every room `ok` (it proves the identity path);
- `notes_search` returns notes;
- a note from a non-default vault is unreachable.

Monitoring: probe `/healthz`.
