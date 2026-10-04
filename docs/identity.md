# Identity: people, agents and sign-in

Machiya's rooms (Kura, Niwa, Konbini) and machiya-mcp can share one **identity file**: who may come in, and what each one may do. It is **optional and off by default.** This page is the one place that explains it; each room's README links here for the shared part and lists its own settings.

## Do you need it?

**No, for a first run or for just yourself.** Without an identity file every room has one owner and a simple gate:

- `*_AUTH=open`, bound to `127.0.0.1`: no check at all; only you, on this machine (the compose's default).
- `*_AUTH=tailscale` behind `tailscale serve`, with your login in `*_USERS`: only you, from your tailnet.

**Yes, when you want any of these:**

| You want | Without identity | With identity |
|---|---|---|
| a second person (household, guest) with less than everything | everyone in `*_USERS` is the owner | each person has their own grants |
| agents (Claude, scripts, machiya-mcp) that can't do what you can | an agent on your login *is* you | an agent has a token and only its grants; Kura keeps private vaults from it |
| sign-in without Tailscale | not possible (only `open`) | a password, or your own auth proxy |
| Shiori on a phone signed in to the rooms | only through the tailnet | a paired device |

Turning it on changes nothing until you set `MACHIYA_IDENTITY_FILE` in a room, room by room. Without the file a room behaves exactly as before.

## Turn it on

One command writes the file and the session key, adds you as the owner and prints the settings each room needs:

```sh
python3 -m vaultkit.identity --file /srv/machiya/identity/identity.toml setup --tailscale you@example.com
```

- How you come in: `--tailscale LOGIN` (a Tailscale login), `--proxy LOGIN` (your proxy's login header value), `--password` (built-in sign-in; asks for it). Any mix; Tailscale is optional.
- `--owner NAME` names your principal; `--rooms …` picks the rooms to print settings for.
- It runs from a checkout of this repository with Python 3.11+, or inside any room's image once that image vendors vaultkit 0.12.0.

Then, in each room:

1. **Mount the directory read-only, not the file** (the CLI replaces the file; a file mount keeps the old one). The compose: `MACHIYA_IDENTITY_DIR` and `MACHIYA_IDENTITY_FILE` in `compose/.env` (see [.env.example](../compose/.env.example)).
2. **Set the printed variables** (`MACHIYA_IDENTITY_FILE` and the room's own, below) and restart the room.
3. Check: `python3 -m vaultkit.identity --file … check` lists the principals and their grants.

The rooms run as their own user (uid 1000 in the images and the compose): the file and `session.key` must be readable by it. Rooms re-read the file within a second of a change; no restart for new people, tokens or grants.

## The four ways in

A request proves *who* it is; the file alone says *what* it may do. The first proof present decides; a present but invalid one is **401** and never falls through to the next.

| Proof | For | Room setting |
|---|---|---|
| `Authorization: Bearer mch_…` (stored token) or `mcd_…` (paired device) | agents, services, scripts, Shiori | any mode |
| **Tailscale login** (`Tailscale-User-Login`), the principal's `tailscale` list | people on the tailnet | `*_AUTH=tailscale` (the default) |
| **Tailscale tagged node**: the app capability `github.com/machiya-kobo/cap/identity` names a principal's `tailscale_tag` | agents on their own tagged machine | `*_AUTH=tailscale` and `*_ACCEPT_APP_CAPS=1` |
| **Proxy header** (`Remote-User` from Authelia, oauth2-proxy, Caddy, nginx), the principal's `proxy` list | people behind your own auth proxy | `*_AUTH=header`, `*_AUTH_HEADER=Remote-User` |
| **Built-in sign-in**: a person's name and password, then the `machiya_session` cookie | people without Tailscale or a proxy | `*_SIGNIN=1`, alongside any mode |

`*_AUTH=open` with a file: a request without a token is the owner, as before (localhost only). A token still names its holder, so an agent with one is that agent even here.

**Tagged nodes** need Tailscale **v1.92+**. In the tailnet policy, grant the rooms to the agents' tag with the capability:

```json
{"src": ["tag:machiya-agent"], "dst": ["tag:machiya-room"],
 "app": {"github.com/machiya-kobo/cap/identity": [{"principal": "mcp"}]}}
```

Each room's Serve forwards it (`tailscale serve --accept-app-caps=github.com/machiya-kobo/cap/identity`, or `AcceptAppCaps` in `serve.json`), and the room opts in with `*_ACCEPT_APP_CAPS=1`. Leave it off with an older Serve: that passes a client's own copy of the header through. Then `python3 -m vaultkit.identity tag mcp mcp` ties the capability's `principal` value to the principal.

**Verified** (Tailscale 1.102.5, a throwaway tagged node and a Tailscale Service, 2026-10-04):

- In `serve.json`, `"AcceptAppCaps": ["github.com/machiya-kobo/cap/identity"]` goes in the HTTP handler next to `"Proxy"`
  (`"Handlers": {"/": {"Proxy": "…", "AcceptAppCaps": […]}}`). It works for the node's own name and for a Service; for a
  Service the `Web` key is the Service's DNS name (`<service>.<tailnet>.ts.net:443`). The CLI flag wasn't tried.
- The grant needs network access too: give the tag `"ip": ["tcp:443"]` on the room (in the same grant or another), and use
  `"dst": ["svc:<name>"]` when the room is a Tailscale Service.
- From a tagged node the room gets **no** `Tailscale-User-Login` and `Tailscale-App-Capabilities:
  {"github.com/machiya-kobo/cap/identity":[{"principal":"mcp"}]}`, as plain JSON (not RFC 2047 encoded). From a user's own
  device it gets `Tailscale-User-Login` (and `-Name`) **and** the same capability header.
- A client's own `Tailscale-User-Login` or `Tailscale-App-Capabilities` never reaches the room: Serve drops or replaces
  both, on the node name and through the Service alike.
- The capability header is present only when a grant gives that source the capability on that destination.

## Grants

A principal is a `person`, an `agent` or a `service`. `owner = true` (a person) may do everything everywhere. Everyone else gets nothing until granted: every room answers **403**.

| Room | Action | Allows |
|---|---|---|
| `kura` | `read` | pages and the API, in the vaults its scope allows (below) |
| `niwa` | `read` | pages and read APIs |
| `niwa` | `suggest` | `POST /api/suggest` |
| `niwa` | `publish` | publish, dismiss, the garden fields |
| `konbini` | `read` | the board and read APIs |
| `konbini` | `write` | card edits, comments, claims, the board's forms |
| `konbini` | `areas` | new `area/*` lanes and new tags |
| `mcp` | `use` | call machiya-mcp, with the principal's `limits` ([mcp.md](services/mcp.md)) |
| `smallweb` | `read`, `save` | reserved: smallweb doesn't read the file yet |
| `landing` | `read` | the [landing page](services/landing.md) and its `/api/status` |

Grant each action you mean: `write` doesn't include `read`.

**Kura's vault scope.** `--vaults` lists vault names, `default` (the default vault) and `shared` (every `+shared` vault). Without `--vaults`, a non-owner reads **the default and shared vaults**. A private (work) vault is readable only when named, and one outside the scope answers exactly like a vault that doesn't exist. This is what keeps work notes from agents ([Kura contract](contracts/kura-api.md#identity)).

```sh
python3 -m vaultkit.identity grant mcp kura read --vaults default shared
python3 -m vaultkit.identity grant mcp konbini read write
```

(Every command takes `--file PATH` first, or reads `MACHIYA_IDENTITY_FILE`; it is left out below.)

## Day to day

**An agent** (Claude, a script, machiya-mcp):

```sh
python3 -m vaultkit.identity add claude-vm --kind agent
python3 -m vaultkit.identity grant claude-vm kura read
python3 -m vaultkit.identity grant claude-vm konbini read
python3 -m vaultkit.identity token mint claude-vm --label "claude VM" --days 365
```

The token (`mch_<id>_<secret>`) is printed once; the file keeps only its hash. The agent sends it as `Authorization: Bearer …`, never in a URL. A **service** is the same with `--kind service`: Niwa's token for Konbini goes in `NIWA_KONBINI_TOKEN_FILE`, machiya-mcp's own in `MCP_TOKEN_FILE`.

**A person:**

```sh
python3 -m vaultkit.identity add partner --kind person
python3 -m vaultkit.identity login partner tailscale partner@example.com   # or: login partner proxy partner
python3 -m vaultkit.identity passwd partner                                # for the built-in sign-in (12+ characters)
python3 -m vaultkit.identity grant partner kura read --vaults default
python3 -m vaultkit.identity grant partner niwa read
```

**A Shiori device**, either way:

- **Code:** `identity pair partner --label iPhone` prints a one-time code (8 characters, good for 10 minutes; `--minutes` up to 60) and the device id. Type it in Shiori's Sign in to Machiya; Shiori trades it at any room's `POST /api/pair` for its own device token.
- **Paste:** `identity token mint partner --label iPhone` and paste the token into Shiori's settings (Linux, Firefox, scripts).

Details per app: Shiori's [docs/signing-in.md](https://github.com/machiya-kobo/shiori/blob/main/docs/signing-in.md).

**Revoking:**

| To end | Command |
|---|---|
| one stored token | `identity token revoke <id>` (the id is the part after `mch_`) |
| one paired device | `identity device revoke <name> <device>` |
| a pairing code before it expires | `identity pair --cancel <device>` |
| every session and paired device of one person | `identity epoch bump <name>` |
| every session and device token of everyone | rotate the key: `umask 077; openssl rand -base64 32 > session.key.new && mv session.key.new session.key` (keep its owner) |

Stored tokens survive a key rotation; revoke them one by one. Sessions last 30 days from the last use (`session_days`), and never more than 180 days from the sign-in.

## Settings

| | Kura | Niwa | Konbini | machiya-mcp |
|---|---|---|---|---|
| the file (all rooms) | `MACHIYA_IDENTITY_FILE` | same | same | same |
| mode: `tailscale` (default), `header`, `open` | `KURA_AUTH` | `NIWA_AUTH` | `KANBAN_AUTH` | `MCP_AUTH` |
| the proxy's login header | `KURA_AUTH_HEADER` | `NIWA_AUTH_HEADER` | `KANBAN_AUTH_HEADER` | `MCP_AUTH_HEADER` |
| a header mode on a non-loopback bind | `KURA_BIND_BEHIND_PROXY=1` | `NIWA_BIND_BEHIND_PROXY=1` | `KANBAN_BIND_BEHIND_PROXY=1` | `MCP_BIND_BEHIND_PROXY=1` |
| Tailscale tagged nodes | `KURA_ACCEPT_APP_CAPS=1` | `NIWA_ACCEPT_APP_CAPS=1` | `KANBAN_ACCEPT_APP_CAPS=1` | `MCP_ACCEPT_APP_CAPS=1` |
| built-in sign-in | `KURA_SIGNIN=1` | `NIWA_SIGNIN=1` | `KANBAN_SIGNIN=1` | none |
| its address (required for sign-in over plain http) | `KURA_PUBLIC_URL` | `NIWA_PUBLIC_URL` | `KANBAN_BOARD_URL` | none |
| its own token for other rooms | none | `NIWA_KONBINI_TOKEN_FILE` | none | `MCP_TOKEN_FILE` |
| one sign-in for every room | `MACHIYA_COOKIE_DOMAIN` | same | same | none |

With the file, `*_USERS` (Konbini: `KANBAN_TAILNET_USERS`) is unused. In the compose `.env`, Konbini's settings are spelled `KONBINI_…`. Every room with the file also answers `POST /api/pair` and `GET`/`PUT /api/prefs` (per-person preferences, in the room's own `prefs.sqlite3`); without it they are 404.

## One room alone

Every room reads the file on its own; nothing else needs to run. For Kura alone:

```sh
python3 -m vaultkit.identity --file /srv/machiya/identity/identity.toml setup --password --rooms kura
```

then start Kura as [its README](https://github.com/machiya-kobo/kura#quickstart) says, with the directory mounted read-only and the printed lines (`MACHIYA_IDENTITY_FILE`, `KURA_SIGNIN=1`; over plain http also `KURA_PUBLIC_URL=http://<its address>`). The same file can serve more rooms later.

## Security notes

- **No secret in the file.** Passwords are scrypt hashes, tokens SHA-256 hashes, pairing codes scrypt hashes. The key that signs sessions and device tokens is `session.key` beside it (0600). Back both up; guard the key like a password.
- **Mount the directory read-only** into each room. Nothing in a room can write identities; the CLI runs on the host that holds the file. A file that turns invalid keeps the last good one (and says so); a bad file at start refuses to start.
- **Header modes trust whoever reaches the port.** In `tailscale` and `header` mode a room refuses a non-loopback bind unless `*_BIND_BEHIND_PROXY=1` says the proxy is the only way in (a Tailscale sidecar, or the compose with ports on `127.0.0.1`). Your proxy must strip the login header from what clients send. With sign-in only, list no Tailscale logins (or keep `tailscale serve` in front): a client could send that header itself.
- **Trade-offs to know:**
  - **Throttling locks out.** Five wrong passwords lock that name for 15 minutes for everyone, and 20 failures lock an address; behind a proxy every client shares the proxy's address. Pairing allows 5 tries per address per 10 minutes.
  - **A pairing code is reusable until it expires.** The rooms can't mark it used (the file is read-only). The short window, the throttle and the one device id in every token it yields (revoked as one) are the guard; cancel it with `pair --cancel` once the device is in.
  - **The shared cookie domain.** `MACHIYA_COOKIE_DOMAIN` makes one sign-in cover every room, and sends the session cookie to every site under that domain. Use a domain only Machiya's rooms serve, or leave it unset and sign in per room.
- Every state change made with a cookie must be same-origin, and no room changes state on a GET.
