# Konbini API

JSON on the board's host (`https://konbini.example.ts.net`), owner-only. Writes come from the web UI or `pm`.

**Access** (`KANBAN_AUTH`): `tailscale` (the default) admits only requests whose `Tailscale-User-Login` is in `KANBAN_TAILNET_USERS`, anything else gets 403; `open` (localhost or a trusted LAN only) admits every request with a startup warning; the `Tailscale-User-Login` header is ignored there (nothing vouches for it) and every event names `local`. Any other value refuses to start. `/api/health` reports the mode as `auth`. In both modes form posts must be same-origin, and API callers name themselves with `X-Agent`. `KANBAN_BIND` sets the listener's address (a native install behind `tailscale serve` binds `127.0.0.1`).
 Agents may move cards and edit board fields, but get 403 for `publish` and the other garden fields, for new `area/*` tags, and for unknown `topic/*` tags.

**With Machiya's identity file** (`MACHIYA_IDENTITY_FILE`, [identity.md](../identity.md)) the file replaces `KANBAN_TAILNET_USERS` and the `X-Agent` test: a caller proves who it is (`Authorization: Bearer mch_…` or `mcd_…`, a Tailscale login or tagged node, a proxy header with `KANBAN_AUTH=header`, or the `machiya_session` cookie with `KANBAN_SIGNIN=1`), and the `konbini` grants decide: `read` for pages and read APIs, `write` for every change (cards, comments, claims, the board's forms), `areas` for new `area/*` lanes and new tags. No proof or a bad one is **401**, a missing grant **403**; an event's actor is the principal, and `X-Agent` stays a label. With the file the board also answers `POST /api/pair` and `GET`/`PUT /api/prefs` (404 without it). The exact fields are in `app/app.py` and `app/store.py` (`parse_note`) in [machiya-kobo/konbini](https://github.com/machiya-kobo/konbini).

## Read by other services

### `GET /api/cards[?board=&area=&machine=&topic=&tag=]`

→ `{"cards": [card, …], "imported": "<iso time>", "head": "<commit>"}`. A card:

```json
{"slug": "example-project", "title": "Example Project", "path": "Projects/Example Project.md", "board": "wip", "priority": 1,
 "summary": "…", "next": "…", "blocked_by": "", "area": "tools", "areas": ["tools"], "effort": "l",
 "topics": ["search"], "machines": ["server1"], "type": "project", "status": "active", "tags": ["…"],
 "repo": "…", "publish": false, "growth": "", "updated": "2026-01-15", "post": "", "post_url": "",
 "rank": null, "checks_done": 3, "checks_total": 5,
 "waiting": "", "created": "2026-01-02", "started": "2026-01-05", "completedDate": "", "due": "",
 "stream": "Example Stream", "goal": "", "dependsOn": ["Other Project"]}
```

Field names follow the [frontmatter schema](../frontmatter.md). Konbini reads both the legacy and the current names, and the card keeps its legacy keys with the same values: `board` is the column (from `status:` or `board:`), `priority` is 1-3 (from `high`/`normal`/`low` or 1-3), `blocked_by` is the `waiting:` text (or `blocked_by:`), and `status` is the old `status/*` value (derived from the column when a note has no tag). Added beside them: `waiting` (= `blocked_by`), `created`, `started`, `completedDate`, `due` (YYYY-MM-DD or ""), `stream` and `goal` (link targets or text, ""), `dependsOn` (link targets without `[[ ]]`, []).

Readers:
- **Shiori** maps slug ↔ path for notes it meets through Hister at `/p/<slug>` URLs. It keeps this lookup for as long as such URLs remain in Hister.
- A dashboard or any other reader may use it too; it is plain JSON.

### `GET /api/digest?days=30`

The board half of Niwa's stream: `{start, end, days, now: {wip, blocked}, entries, head}`. Entries are per project per day (`kind: project`: slug, title, date, moves, top Log rows, more, next, board, area, `path` of the card's note, done, started) and per host per day (`kind: systems`). Niwa adds its garden half and renders it.

### `GET /api/health` (alias `GET /api/status`, the probe path every room answers)

→ `{"ok", "imported", "head", "cards", "sync", "hister", "livesync", "auth"}`, for monitoring (`auth`: `tailscale` or `open`).

### `GET /healthz`

For the container health check: `200 ok`, plain text, no data, no identity, whatever `KANBAN_AUTH` says. `/api/health` and `/api/status` stay gated on the Tailscale identity, so a check inside the container can't use them.

### `GET /api/changelog`

→ Konbini's `CHANGELOG.md` as `text/markdown; charset=utf-8` (at most 64 KiB, an `ETag`; 404 without the file), gated like `/api/health`. Read by the [landing page](../services/landing.md) for Recent Deploys. Served with `vaultkit.changelog` ([ui.md](../ui.md#the-changelog-endpoint-vaultkitchangelog-v018)).

### `GET /api/review`

The weekly review (the board's `/review` page) as JSON. Read by `pm review`.

→ `{"week_start": "2026-01-12", "counts": {"wip": 4, "blocked": 1, "stale": 1, "nonext": 1, "done": 2, "ideas": 5}, "areas": [{"area": "tools", "wip": 4, "limit": 3, "over": true}, …], "sections": [section, …]}`.

Sections come in a fixed order: `wip` (WIP over the area's limit; 3 per area by default), `blocked` (the Blocked column, a `waiting:` reason or an unfinished dependency, for 7+ days), `stale` (WIP/Ready with no change for 14+ days), `nonext` (WIP/Ready without `next`), `done` (finished since Monday), `ideas` (Backlog, oldest first). A section is `{"key", "title", "hint", "cards": [card, …]}`; each card appears once, in the first section that applies:

```json
{"slug": "example-project", "title": "Example Project", "board": "wip", "area": "tools", "path": "Projects/Example Project.md",
 "priority": null, "next": "", "waiting": "", "updated": "2026-01-01", "waiting_on": [],
 "reasons": [{"key": "wip", "text": "tools over its limit"}, {"key": "stale", "text": "idle 17d"},
             {"key": "nonext", "text": "no next action"}]}
```

`reasons[0]` is the card's section; `waiting_on` lists the titles of unfinished `dependsOn` cards.

## Board-only

| | |
|---|---|
| `GET /api/cards/<slug>/kit` | a card's writing kit |
| `GET /api/events`, `/api/roundup`, `/api/links`, `/api/rev`, `/api/stream` (SSE) | history, roundups, link rot, change feed |
| `PATCH /api/cards/<slug>` | board fields (`board` or `status`, `next`, `blocked_by` or `waiting`, `priority` as 1-3 or high/normal/low, `rank`, `dependsOn`, `stream`, `goal`, `due`); the note is written under the names it already uses. A move sets `started` (first move to WIP) and `completedDate` (move to done; cleared when reopened) |
| `POST /api/cards` | a new stub note (`pm new`) |
| `POST /api/cards/<slug>/events` | a log line |
| `POST`/`DELETE /api/cards/<slug>/claim` | an agent's "working on this" badge (15 min) |
| `POST /api/order` | ranks within a column |
| `POST /api/garden/suggest` | answers 308 to Niwa's `/api/suggest` (`pm suggest` posts to Niwa directly) |

**`dependsOn` in a PATCH:** a list of names, or one string of names separated by commas; each is a card's slug or title, or a note name (`[[X]]` is accepted too). It's stored as a block list of quoted links to the note's file name (`dependsOn:\n  - "[[Kura]]"`, the frontmatter schema), compared by the card each name points to, so re-sending the same dependencies changes nothing. `""` or `[]` removes the field. A card that depends on itself → 422. `pm dep <slug> +x -y` builds on it.

**`stream` in a PATCH:** the card's workstream, a string (a name, or a `[[link]]` kept as written); `""` removes the field. Written as the `stream:` scalar (the frontmatter schema) and logged as a change. `pm stream <slug> <name|->` sets or clears it.

**`goal` and `due` in a PATCH:** `goal` is a string (the goal the card counts toward; `""` removes it); `due` is a date `YYYY-MM-DD` (`""` removes it, anything else → 422). Written as the `goal:` / `due:` scalars (the frontmatter schema) and logged as changes; a goal's target on `/goals` is the latest `due` among its cards. `pm goal <slug> <name|->` and `pm due <slug> <YYYY-MM-DD|->` set or clear them.
