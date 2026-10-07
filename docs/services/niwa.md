# Niwa (the garden)

**The published notes: a digital garden grown from the vault.**

- A note is in the garden when its frontmatter has `publish: true`. Only you set that, with Niwa's own buttons.
- Niwa derives everything else: growth stage (`growth:`, or from type/status tags), last tended (git), backlinks, "nearby" notes, topic maps, and a pre-publish scan (addresses, keys, tokens, links to machine notes).

- **URLs:** `https://niwa.example.ts.net` (ports 443 and 80; gemini 1965; gopher 70, which the app serves on 7070)
- **Repo:** [machiya-kobo/niwa](https://github.com/machiya-kobo/niwa)
- **Runs standalone:** its own stack and its own read-write clone (deploy key).

## Pages

| Path | What |
|---|---|
| `/` | landing: intro from `Garden.md`, pinned notes, maps, recent |
| `/n/<path>`, `/t/<tag>`, `/tags` | a note, a tag, every tag |
| `/stream` | what changed |
| `/queue` | suggested and well-linked unpublished notes |
| `/random`, `/a/<image>` | a random note, a vault image |

Every page is modern HTML5.

## Writes

- Niwa commits only the garden fields (`publish`, `growth`, `confidence`, `garden_pin`) and its events.
- Events go to `.garden/events/*.jsonl`, union-merged. Niwa still reads older events from `.board/events`.
- It commits to its own read-write clone, as `garden`, in batches. vaultkit's GitSync rebases, replays frontmatter three-way on a conflict, and pushes.
- You publish from the web UI. Agents get 403 and may only suggest (`POST /api/suggest`).

## Without the others

| Missing | What changes |
|---|---|
| Konbini | no column badges, no "in bloom", the stream shows only the garden half (planted, tended, stages, suggestions, dead links); the gemini and gopher streams always show only that half |
| Kura | no links for you to the full note |
| Hister | no private link copies, no reading line |

## What is served, and what is public

- Niwa serves the garden itself: the web pages, plus its own **gemini** (port 1965) and **gopher** (port 7070) listeners. Those are the only "mirrors".
- It publishes nothing to any other host. What you put on a public site is up to you.
- The pages show only notes with `publish: true`.
- The gemini and gopher `/stream` pages are **public**. They list garden events about notes that are published now. They never show the Konbini board: no cards, no next steps, no blocked-by text, nothing from an unpublished note.
- Your own web stream may add the board half and the reading line.

## Public garden (optional, off by default)

To turn it on:
1. Set `NIWA_PUBLIC_PORT` (for example `8081`) and `NIWA_GARDEN_URL`, its public origin (`https://garden.example`).
2. Publish that port through a TLS proxy (Caddy, Tailscale Funnel). In a compose stack, map it apart from your own port, for example `127.0.0.1:8081:8081` behind the proxy.
3. Keep your own `NIWA_PORT` on the tailnet.

Options:
- `NIWA_PUBLIC_NOINDEX=1` keeps search engines out.
- `NIWA_PUBLIC_BIND` binds the public port somewhere other than `NIWA_BIND`.

The public site serves the published notes, their tags and images, the stream of garden events, search and `/feed.xml`. It has no queue, settings, sign-in or writes, and nothing from Konbini, Kura or Hister. How it treats identity: [identity.md](../identity.md#security-notes).

Before you turn it on:
- A published note whose scan has errors is held back on every channel (web, public, Gemini, Gopher, feed) until you acknowledge it on the note's page.
- Notes published with "Publish anyway" before Niwa 0.8.0 need acknowledging once more (Queue, Held Back).
- Add `NIWA_SCAN_DENY` with any names that must never appear publicly.
- `NIWA_ARCHIVE` defaults to `wayback`. Every external link in a published note keeps its address and gets an "archive.org" link beside it. Set `NIWA_ARCHIVE=none` to turn that off.

## Settings

- `NIWA_REPO_URL` (ssh, read-write; `GIT_SSH_COMMAND` for the key), `NIWA_REPO_SUBDIR`, `NIWA_POLL`
- `NIWA_AUTH` (`tailscale`, `open`, `header` or `hister`, as [Kura's](kura.md#settings)), `NIWA_USERS`, `NIWA_BIND_BEHIND_PROXY`
- `NIWA_PUBLIC_URL`, `NIWA_HOST`
- `NIWA_KONBINI_URL`, `NIWA_KURA_URL`
- `NIWA_HISTER_URL`, `NIWA_HISTER_TOKEN_FILE`, `NIWA_HISTER_PUBLIC`, `NIWA_COLD_MAP`
- `NIWA_ARCHIVE` (`wayback` | `none`), `NIWA_DB`

[Niwa's settings page](https://github.com/machiya-kobo/niwa/blob/main/docs/settings.md) has the full table.

## Status and changes

- `GET /api/status`: the version, vendored vaultkit, the clone's head and sync (`pending`, `ahead`, `error`) and counts, for the probes.
- `GET /api/changelog`: Niwa's `CHANGELOG.md` as `text/markdown; charset=utf-8`, at most 64 KiB, with an `ETag`. 404 without the file. Same gate as `/api/status`. The [landing page](landing.md) reads it (`vaultkit.changelog`).

## Open suggestions

`GET /api/suggestions[?days=60]` returns `{"suggestions": [{"path", "reason", "agent", "date"}], "days": 60}`, newest first.

- `days` is 1 to 365.
- `agent` is the suggestion's `X-Agent`, or the login for a web-UI suggestion. `date` is a day.
- The open set is each note's latest suggestion that no unsuggest or publish followed.
- machiya-mcp's `garden_suggest` reads it to avoid repeats. Access is the same as Niwa's other reads.
