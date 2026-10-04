# Niwa 庭 (the garden)

**The published notes: a digital garden grown from the vault.** A note is in the garden when its frontmatter has `publish: true`, and only the owner sets that, from Niwa's own buttons. Everything else is derived: growth stage (`growth:`, or from type/status tags), last tended (git), backlinks, "nearby" notes, topic maps, and a pre-publish scan (addresses, keys, tokens, links to machine notes).

- **URLs:** `https://niwa.example.ts.net` (ports 443, 80, gemini 1965, gopher 70)
- **Repo:** [machiya-kobo/niwa](https://github.com/machiya-kobo/niwa)
- **Runs standalone:** its own stack and its own read-write clone (deploy key).

## Pages

`/` (landing: intro from `Garden.md`, pinned, maps, recent), `/n/<path>`, `/t/<tag>`, `/tags`, `/stream` (what changed), `/queue` (suggested and well-linked unpublished notes), `/random`, `/a/<image>`. Modern HTML5.

## Writes

Niwa commits the garden fields only (`publish`, `growth`, `confidence`, `garden_pin`) and its events (`.garden/events/*.jsonl`, union-merged; older events are read from `.board/events`) to its own read-write clone, as `garden`, in batches (vaultkit's GitSync: rebase, three-way frontmatter replay on conflict, push). The owner does this from the web UI; agents get 403 and may only suggest (`POST /api/suggest`).

## Without the others

| Missing | What changes |
|---|---|
| Konbini | no column badges, no "in bloom", the stream shows only the garden half (planted, tended, stages, suggestions, dead links); the gemini and gopher streams always show only that half |
| Kura | no owner links to the full note |
| Hister | no private link copies, no reading line |

## What is served, and what is public

Niwa serves the garden itself: the web pages, plus **gemini** (port 1965) and **gopher** (port 7070) listeners of its own, which are the only "mirrors". It publishes nothing to any other host; what you put on a public site is up to you. The pages show only notes with `publish: true`. The gemini and gopher `/stream` pages are a **public surface**: they list garden events about notes that are published now and never the Konbini board (no cards, no next steps, no blocked-by text, nothing from an unpublished note). The owner's web stream may add the board half and the reading line.

## Settings

`NIWA_REPO_URL` (ssh, read-write; `GIT_SSH_COMMAND` for the key), `NIWA_REPO_SUBDIR`, `NIWA_POLL`, `NIWA_AUTH` (`tailscale`, `open`, `header` or `hister`, as [Kura's](kura.md#settings)), `NIWA_USERS`, `NIWA_BIND_BEHIND_PROXY`, `NIWA_PUBLIC_URL`, `NIWA_HOST`, `NIWA_KONBINI_URL`, `NIWA_KURA_URL`, `NIWA_HISTER_URL`/`NIWA_HISTER_TOKEN_FILE`/`NIWA_HISTER_PUBLIC`/`NIWA_COLD_MAP`, `NIWA_ARCHIVE` (`wayback` | `none`), `NIWA_DB`. Full table: machiya-kobo/niwa README.

## Status and changes

`GET /api/status`: the version, vendored vaultkit, the clone's head and sync (`pending`, `ahead`, `error`), counts, for the probes. `GET /api/changelog`: Niwa's `CHANGELOG.md` as `text/markdown; charset=utf-8` (at most 64 KiB, an `ETag`; 404 without the file), behind the same gate as `/api/status`, for the [landing page](landing.md) (`vaultkit.changelog`).

## Open suggestions

`GET /api/suggestions[?days=60]` `{"suggestions": [{"path", "reason", "agent", "date"}], "days": 60}`, newest first, `days` 1 to 365. `agent` is the suggestion's `X-Agent` (or the login for a web-UI suggestion), `date` a day. The open set is each note's latest suggestion not followed by an unsuggest or a publish. Read by machiya-mcp's `garden_suggest` to avoid repeats; same access as Niwa's other reads.
