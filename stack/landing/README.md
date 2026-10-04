# machiya-landing

Machiya's front door and status page: one page that links every room, engine and stack service and shows how each is doing (up or down, version and vendored vaultkit), how fresh the vault's sync is, and what was deployed lately. It looks like the rooms (vaultkit's shell, tab bar and Rooms menu). Full description: [docs/services/landing.md](../../docs/services/landing.md).

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
| `LANDING_AUTH` | `tailscale` | `tailscale`: only a `Tailscale-User-Login` in `LANDING_USERS` (`*` = anyone the tailnet lets through; empty = nobody); `open`: no check, localhost only; with the identity file also `header` (`LANDING_AUTH_HEADER`) |
| `LANDING_USERS` | none | the Tailscale logins allowed in |
| `LANDING_BIND`, `LANDING_PORT` | `0.0.0.0`, `8080` | the listener; `tailscale` mode refuses a non-loopback bind unless `LANDING_BIND_BEHIND_PROXY=1` (the proxy is the only way in) |
| `LANDING_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | `open` mode answers only these `Host` names |
| `LANDING_POLL`, `LANDING_TIMEOUT` | `60`, `3` | seconds between polls (15 at least), and per request |
| `LANDING_TOKEN_FILE` | none | a token sent as `Authorization: Bearer` to Kura, Niwa and Konbini over https only (for rooms with an identity file), for their status and changelog; never to the engines or services |
| `LANDING_HISTER_TOKEN_FILE` | none | the owner's Hister token, sent as `X-Access-Token` to Hister only, for the page count and the newest page once Hister's user handling is on ([contracts/hister.md](../../docs/contracts/hister.md)); re-read when the file changes; set but missing or empty stops the start. Without it Hister is still shown up or down (from its open `/health`), just without the count |
| `LANDING_CHANGELOG_POLL` | `900` | seconds between asking each app for its changelog (`GET /api/changelog`); a new version is asked for at once |
| `LANDING_CHANGELOGS` | none | an override per app, `kura=https://…/CHANGELOG.md,…`, for an app that doesn't serve `/api/changelog` |
| `LANDING_CHANGELOG_TOKEN_FILE` | none | a read token for the override URLs only (`Authorization: token …`, https only) |
| `MACHIYA_IDENTITY_FILE` | none | Machiya's identity file: callers need the `landing` `read` grant (the owner has it); `LANDING_ACCEPT_APP_CAPS`, `LANDING_BIND_BEHIND_PROXY` as in the rooms |
| `LANDING_STATE` | none (`/data/landing.json` in the image) | the deploy history; unset keeps it in memory |
| `LANDING_TZ` | `UTC` | the time zone of the exact times in tooltips |
| `MACHIYA_COOKIE_DOMAIN` | none | shared theme and text size with the rooms, as in every room |
| `MACHIYA_SOURCE_URL` | none | the AGPL source link in the footer and About |

`AUTH=hister` will come through vaultkit, as for the rooms.

## Endpoints

- `GET /`: the page (no-store; it refreshes itself every minute while visible).
- `GET /api/status`: the same as JSON (`overall`, `apps`, `sync`, `deploys`); owner-only, since it names every app's version.
- `GET /api/changelog`: this page's own `CHANGELOG.md` (`vaultkit.changelog`), behind the same gate.
- `GET /healthz`: `{"ok": true, "version": …}` for the container's health check and the monitoring probe; no identity, no data.
- `/settings`, `/theme`, `/manifest.webmanifest`, `/static/…`: as in the rooms.

The page sends only GETs, follows no redirect, and reads at most 4 MB from an app. It asks Kura, Konbini, Niwa, machiya-mcp and smallweb for `GET /api/changelog`; an app that answers 404 (or anything but markdown) shows its versions only.
