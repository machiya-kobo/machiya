# machiya-landing

Machiya's front door: `/` is a launcher (Search everything, the rooms, and Today: cards in progress and due, changed notes, saved and read pages, the garden), and `/status` links every room, engine and stack service and shows how each is doing (up or down, version and vendored vaultkit), how fresh the vault's sync is, and what was deployed lately. It looks like the rooms (vaultkit's shell, tab bar and Rooms menu). Full description: [docs/services/landing.md](../../docs/services/landing.md).

```
LANDING_AUTH=open LANDING_BIND=127.0.0.1 LANDING_PORT=8080 LANDING_STATE=/tmp/landing.json \
  MACHIYA_ROOMS=kura=https://kura.example.ts.net,konbini=https://konbini.example.ts.net python3 landing.py
python3 -m unittest discover -s tests      # from this directory; needs markdown and pyyaml (vaultkit's dependencies)
podman build -t machiya-landing:dev .      # or docker build; run it with --init
```

## Standalone

Every app is optional. The page polls only what has an address; anything else shows **Not in this stack**, never an error. With no apps at all it still starts and says so. No app needs the landing page, and it needs no app.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `MACHIYA_ROOMS` | none | the rooms' and engines' public addresses (`shiori=…,konbini=…,niwa=…,kura=…,hister=…,searxng=…,machiya=…`), as in every room: the links, the Rooms menu, and where each is polled; `machiya=` is this page's own address (the rooms' Rooms menu and footer link here) |
| `LANDING_APPS` | none | the stack's services, `machiya-mcp=https://…,smallweb=https://…` |
| `LANDING_PROBES` | none | a different address to poll than to link, per app (`hister=http://hister:4433`) |
| `LANDING_MIRROR_STATUS` | none | vault-mirror's `status.json` (its volume, mounted read-only) |
| `LANDING_CODE_STATUS` | none | code-import's `status.json` (a read-only copy): the code-import row and Sync's Repos indexed; missing: quiet |
| `LANDING_FEED_STATUS` | none | feed-import's `status.json` (its data volume, mounted read-only): the feed-import row and Sync's Feeds read |
| `LANDING_SEARCH_COUNTS` | none | a JSON file of web searches (`{"updated", "today", "yesterday", "month", "year", "by_day"}`, UTC days, counts only) written by the deployment; the Shiori card shows today / this month / this year. Missing: left out |
| `LANDING_SEARCH_URL` | none | Shiori's search page, `https://search.example.ts.net/`: the launcher's Search everything pill GETs `<url>?q=…` (unset: no pill) |
| `LANDING_HISTER_TOKEN_FILE` | none | the owner's Hister token (`X-Access-Token`, re-read on change). Hister: the page count, the newest pages, Saved & Read and its version once Hister has users. The rooms with `AUTH=hister` (0.2.3): their owner-only reads (Konbini's health and cards, Kura's vaults and recent notes, Niwa's feed), sent only to their configured https address, never across a redirect; the open status reads never get it. Set but missing or empty stops the start; without it Hister is still shown up or down (from its open `/health`), just without the counts ([contracts/hister.md](../../docs/contracts/hister.md)) |
| `LANDING_AUTH` | `tailscale` | `tailscale`: only a `Tailscale-User-Login` in `LANDING_USERS` (`*` = anyone the tailnet lets through; empty = nobody); `open`: no check, localhost only; `hister`: Hister's users through the hister-login helper (below); with the identity file also `header` (`LANDING_AUTH_HEADER`) |
| `LANDING_AUTH_SIGNIN_URL` | none | `hister`: the helper's public sign-in (`https://hister.example.ts.net/machiya/signin`); required |
| `LANDING_HISTER_USERS` | none | `hister`: the Hister usernames admitted (the owner's); required, never `*` |
| `LANDING_AUTH_URL` | none | `hister`: the helper's internal address (`http://hister-login:8081`); unset: the Tailscale identity only |
| `LANDING_AUTH_FALLBACK` | `tailscale` | `hister`: when sign-in is unavailable, a `LANDING_USERS` login is let in with a banner (`none` refuses everyone: not advised for a status page) |
| `LANDING_PUBLIC_URL` | none | this page's address (`https://machiya.example.ts.net`): the way back after sign-in, and the origin a sign-out or prefs write must come from; required with `hister` |
| `LANDING_PREFS` | `prefs.sqlite3` beside `LANDING_STATE` | per-person theme and text size (`/api/prefs`) |
| `LANDING_USERS` | none | the Tailscale logins allowed in |
| `LANDING_BIND`, `LANDING_PORT` | `0.0.0.0`, `8080` | the listener; `tailscale` mode refuses a non-loopback bind unless `LANDING_BIND_BEHIND_PROXY=1` (the proxy is the only way in) |
| `LANDING_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | `open` mode answers only these `Host` names |
| `LANDING_POLL`, `LANDING_TIMEOUT` | `60`, `3` | seconds between polls (15 at least), and per request |
| `LANDING_TOKEN_FILE` | none | a token sent as `Authorization: Bearer` to Kura, Niwa and Konbini over https only (for rooms with an identity file), for their status and changelog; never to the engines or services |
| `LANDING_CHANGELOG_POLL` | `900` | seconds between asking each app for its changelog (`GET /api/changelog`); a new version is asked for at once |
| `LANDING_CHANGELOGS` | none | an override per app, `kura=https://…/CHANGELOG.md,…`, for an app that doesn't serve `/api/changelog` |
| `LANDING_CHANGELOG_TOKEN_FILE` | none | a read token for the override URLs only (`Authorization: token …`, https only) |
| `MACHIYA_IDENTITY_FILE` | none | Machiya's identity file: callers need the `landing` `read` grant (the owner has it); `LANDING_ACCEPT_APP_CAPS`, `LANDING_BIND_BEHIND_PROXY` as in the rooms |
| `LANDING_STATE` | none (`/data/landing.json` in the image) | the deploy history; unset keeps it in memory |
| `LANDING_TZ` | `UTC` | the time zone of the exact times in tooltips |
| `MACHIYA_COOKIE_DOMAIN` | none | shared theme and text size with the rooms, as in every room |
| `MACHIYA_SOURCE_URL` | none | the AGPL source link in the footer and About |

`AUTH=hister` comes through vaultkit (`histerauth`), as for the rooms.

## Endpoints

- `GET /`: the launcher (no-store; it refreshes itself every minute while visible, never under a half-typed search).
- `GET /status`: the status page (the same refresh).
- `GET /api/today`: Today as JSON (owner-only).
- `GET /api/status`: the status page as JSON (`overall`, `apps`, `sync`, `deploys`); owner-only, since it names every app's version.
- `GET /api/changelog`: this page's own `CHANGELOG.md` (`vaultkit.changelog`), open like every app's.
- `GET`/`PUT /api/prefs`: the signed-in person's theme and text size (owner-only; a PUT same-origin).
- `POST /signout` (`hister` mode): ends the Hister session (same-origin only), then `/`.
- `GET /healthz`: `{"ok": true, "version": …}` for the container's health check and the monitoring probe; no identity, no data.
- `/settings`, `/theme`, `/manifest.webmanifest`, `/static/…`: as in the rooms.

The page sends GETs, plus one POST: Hister's MCP `initialize` (it changes nothing), at most every 15 minutes, for Hister's version. It follows no redirect and reads at most 4 MB from an app. It asks Kura, Konbini, Niwa, machiya-mcp and smallweb for `GET /api/changelog`; an app that answers 404 (or anything but markdown) shows its versions only.
