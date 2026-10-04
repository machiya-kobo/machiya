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
| `MACHIYA_ROOMS` | none | the rooms' and engines' public addresses (`shiori=…,konbini=…,niwa=…,kura=…,hister=…,searxng=…`), as in every room: the links, the Rooms menu, and where each is polled |
| `LANDING_APPS` | none | the stack's services, `machiya-mcp=https://…,smallweb=https://…` |
| `LANDING_PROBES` | none | a different address to poll than to link, per app (`hister=http://hister:4433`) |
| `LANDING_MIRROR_STATUS` | none | vault-mirror's `status.json` (its volume, mounted read-only) |
| `LANDING_AUTH` | `tailscale` | `tailscale`: only a `Tailscale-User-Login` in `LANDING_USERS` (`*` = anyone the tailnet lets through; empty = nobody); `open`: no check, localhost only |
| `LANDING_USERS` | none | the Tailscale logins allowed in |
| `LANDING_BIND`, `LANDING_PORT` | `0.0.0.0`, `8080` | the listener; `tailscale` mode refuses a non-loopback bind unless `LANDING_BIND_BEHIND_PROXY=1` (the proxy is the only way in) |
| `LANDING_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | `open` mode answers only these `Host` names |
| `LANDING_POLL`, `LANDING_TIMEOUT` | `60`, `3` | seconds between polls (15 at least), and per request |
| `LANDING_TOKEN_FILE` | none | a token sent as `Authorization: Bearer` to Kura, Niwa and Konbini over https only (for rooms with an identity file); never to the engines or services |
| `LANDING_CHANGELOGS` | none | where each app's `CHANGELOG.md` is, `kura=https://…/CHANGELOG.md,…` (fetched every `LANDING_CHANGELOG_POLL` seconds, 900) |
| `LANDING_CHANGELOG_TOKEN_FILE` | none | a read token for those URLs (`Authorization: token …`, https only), while the repositories are private |
| `LANDING_STATE` | none (`/data/landing.json` in the image) | the deploy history; unset keeps it in memory |
| `LANDING_TZ` | `UTC` | the time zone of the exact times in tooltips |
| `MACHIYA_COOKIE_DOMAIN` | none | shared theme and text size with the rooms, as in every room |
| `MACHIYA_SOURCE_URL` | none | the AGPL source link in the footer and About |

`MACHIYA_IDENTITY_FILE` is refused for now: vaultkit's identity file has no `landing` room to grant yet. Once it has one, the page reads the file like the rooms do (the owner, or a principal with `landing` `read`), and `AUTH=hister` follows with vaultkit.

## Endpoints

- `GET /`: the page (no-store; it refreshes itself every minute while visible).
- `GET /api/status`: the same as JSON (`overall`, `apps`, `sync`, `deploys`); owner-only, since it names every app's version.
- `GET /healthz`: `{"ok": true, "version": …}` for the container's health check and the monitoring probe; no identity, no data.
- `/settings`, `/theme`, `/manifest.webmanifest`, `/static/…`: as in the rooms.

The page sends only GETs, follows no redirect, and reads at most 4 MB from an app.
