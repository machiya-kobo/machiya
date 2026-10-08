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

**Or let Hister's users be the sign-in** (`*_AUTH=hister`, [below](#hister-sign-in-authhister)): one owner, one sign-in for Hister and every room, real sign-out, without the identity file.

## Turn it on

One command writes the file and the session key, adds you as the owner and prints the settings each room needs:

```sh
python3 -m vaultkit.identity --file /srv/machiya/identity/identity.toml setup --tailscale you@example.com
```

- How you come in: `--tailscale LOGIN` (a Tailscale login), `--proxy LOGIN` (your proxy's login header value), `--password` (built-in sign-in; asks for it). Any mix; Tailscale is optional.
- `--owner NAME` names your principal; `--rooms …` picks the rooms to print settings for.
- It runs from a checkout of this repository with Python 3.11+, or inside any room's image (every room vendors vaultkit, `app/vaultkit/`).

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
python3 -m vaultkit.identity add laptop-agent --kind agent
python3 -m vaultkit.identity grant laptop-agent kura read
python3 -m vaultkit.identity grant laptop-agent konbini read
python3 -m vaultkit.identity token mint laptop-agent --label "laptop agent" --days 365
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
- **Paste:** `identity token mint partner --label iPhone` and paste the token into Shiori's settings (the Linux app, scripts).

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
| mode: `tailscale` (default), `header`, `open` (the rooms without the file: also `hister`, [below](#hister-sign-in-authhister)) | `KURA_AUTH` | `NIWA_AUTH` | `KANBAN_AUTH` | `MCP_AUTH` |
| the proxy's login header | `KURA_AUTH_HEADER` | `NIWA_AUTH_HEADER` | `KANBAN_AUTH_HEADER` | `MCP_AUTH_HEADER` |
| a header mode on a non-loopback bind | `KURA_BIND_BEHIND_PROXY=1` | `NIWA_BIND_BEHIND_PROXY=1` | `KANBAN_BIND_BEHIND_PROXY=1` | `MCP_BIND_BEHIND_PROXY=1` |
| Tailscale tagged nodes | `KURA_ACCEPT_APP_CAPS=1` | `NIWA_ACCEPT_APP_CAPS=1` | `KANBAN_ACCEPT_APP_CAPS=1` | `MCP_ACCEPT_APP_CAPS=1` |
| built-in sign-in | `KURA_SIGNIN=1` | `NIWA_SIGNIN=1` | `KANBAN_SIGNIN=1` | none |
| its address (required for sign-in over plain http) | `KURA_PUBLIC_URL` | `NIWA_PUBLIC_URL` | `KANBAN_BOARD_URL` | none |
| its own token for other rooms | none | `NIWA_KONBINI_TOKEN_FILE` | none | `MCP_TOKEN_FILE` |
| one sign-in for every room | `MACHIYA_COOKIE_DOMAIN` | same | same | none |

With the file, `*_USERS` (Konbini: `KANBAN_TAILNET_USERS`) is unused. In the compose `.env`, Konbini's settings are spelled `KONBINI_…`. Every room with the file also answers `POST /api/pair` and `GET`/`PUT /api/prefs` (per-person preferences, in the room's own `prefs.sqlite3`, [contracts/prefs.md](contracts/prefs.md)); without it they are 404.

## One room alone

Every room reads the file on its own; nothing else needs to run. For Kura alone:

```sh
python3 -m vaultkit.identity --file /srv/machiya/identity/identity.toml setup --password --rooms kura
```

then start Kura as [its README](https://github.com/machiya-kobo/kura#quickstart) says, with the directory mounted read-only and the printed lines (`MACHIYA_IDENTITY_FILE`, `KURA_SIGNIN=1`; over plain http also `KURA_PUBLIC_URL=http://<its address>`). The same file can serve more rooms later.

## Hister sign-in (`AUTH=hister`)

A room mode **without the identity file**: the room asks [hister-login](services/hister-login.md) whether the caller is signed in to Hister, so one sign-in (password, or Hister's OIDC provider such as tsidp) covers Hister and every room, and a sign-out anywhere ends it everywhere within 30 seconds. One owner: everyone admitted is the owner, as with the Tailscale gate.

**Ways in**, the first present decides (a present but invalid one is **401**, never passed over):

1. **A room token** (`Authorization: Bearer mht_…`): scripts, pm, machiya-mcp, landing, Niwa→Konbini, an extension ([below](#room-tokens));
2. **A Shiori app's id** (`Authorization: Bearer mhs_…`), the app's own per-device sign-in;
3. **The room's own cookie** (`__Host-machiya_sso_<room>=mhr_…`), a browser's ([below](#one-cookie-per-room));
4. **Legacy**, only while hister-login's `HISTER_LOGIN_LEGACY` allows them: the owner's Hister token (`X-Access-Token`, or any other Bearer) and the old shared-domain cookie `machiya_sso`;
5. **Nothing**: a page goes to the helper's sign-in and comes back with its own cookie; an API call gets `401 {"error": "sign in", "signin": "<address>"}` (vaultkit's `machiya.js` takes the page there when the room opts in). With `MACHIYA_SIGNIN_PROVIDER` (below) the sign-in is automatic.

| | Kura | Niwa | Konbini |
|---|---|---|---|
| the mode | `KURA_AUTH=hister` | `NIWA_AUTH=hister` | `KANBAN_AUTH=hister` |
| the helper, internal (`http://hister-login:8081`) | `KURA_AUTH_URL` | `NIWA_AUTH_URL` | `KANBAN_AUTH_URL` |
| the helper's sign-in page (required) | `KURA_AUTH_SIGNIN_URL` | `NIWA_AUTH_SIGNIN_URL` | `KANBAN_AUTH_SIGNIN_URL` |
| Hister usernames admitted (required; never `*`) | `KURA_HISTER_USERS` | `NIWA_HISTER_USERS` | `KANBAN_HISTER_USERS` |
| when sign-in is unavailable: `tailscale` or `none` | `KURA_AUTH_FALLBACK` | `NIWA_AUTH_FALLBACK` | `KANBAN_AUTH_FALLBACK` |
| Tailscale logins admitted in the fallback only (never `*`) | `KURA_USERS` | `NIWA_USERS` | `KANBAN_TAILNET_USERS` |
| its address, for the way back (required) | `KURA_PUBLIC_URL` | `NIWA_PUBLIC_URL` | `KANBAN_BOARD_URL` |
| the shared preference cookies' domain (and where the legacy `machiya_sso` is cleared) | `MACHIYA_COOKIE_DOMAIN` | same | same |
| the sign-in cookies' base name (default `machiya_sso`; the helper's must match) | `MACHIYA_SSO_COOKIE` | same | same |
| other origins whose room sessions this room accepts (Shiori's hosted pages; optional) | `KURA_AUTH_ACCEPT_ORIGINS` | `NIWA_AUTH_ACCEPT_ORIGINS` | `KANBAN_AUTH_ACCEPT_ORIGINS` |
| sign in automatically through the helper's provider (`oidc`: tsidp; empty: the helper's page) | `MACHIYA_SIGNIN_PROVIDER` | same | same |

- **Refused at start:** no sign-in address or usernames, a `*`, `none` without the helper's address, no public address, or an identity file at the same time (not combined yet). No helper address with the `tailscale` fallback runs on the Tailscale identity alone, with a warning, so a room still stands alone.
- **Signed out never falls back.** Only "nobody answered" does: the helper or Hister unreachable, a 5xx, or Hister's user handling off. Then a room with `tailscale` admits the owner's Tailscale login with a banner ("Signed in through the tailnet: sign-in is unavailable"), caches nothing and counts it (`fallback_total`); a room with `none` answers 503. A Hister account outside `*_HISTER_USERS` is 403, never a fallback (OAuth creates accounts on its own).
- **The cookies** are host-only since vaultkit 0.22 and hister-login 0.3.0 (decided 2026-10-05, after the sweep found the shared `machiya_sso` reaching every host under the tailnet's domain, agents' machines among them): see [One cookie per room](#one-cookie-per-room). Hister's own session never leaves Hister's host.
- **Automatic sign-in** (`MACHIYA_SIGNIN_PROVIDER=oidc`, decided 2026-10-05). An installed web app on an iPhone has its own cookie jar, so each would otherwise ask once. With this setting, a room or landing that needs a sign-in sends the page to the helper with `provider=oidc&auto=1`, and the helper goes straight through Hister's OIDC provider (tsidp knows the device), so there is no page and no tap.
  - **The helper's page shows instead after a deliberate sign-out.** A room's `/signout` sets that room's own marker (`__Host-machiya_sso_<room>_out`), and the helper remembers every helper session ended on purpose for 30 days, so the next automatic trip from any room shows its page and sets the helper's marker (`__Host-machiya_sso_out`). The helper's own sign-out and its sessions page set that marker at once. The next successful sign-in clears them, so "Sign Out" never bounces straight back in.
  - **It also shows after a round trip that failed.** The callback lands on the page with "Sign in with Tailscale didn't work…" and a 10-minute marker, so it never loops.
  - **The room's loop guard links to the plain page.** A tap on "Sign in with Tailscale" (a `provider=` without `auto`) always goes through.
- **Preferences** ([contracts/prefs.md](contracts/prefs.md)): with the helper, a room's `/api/prefs` is the **account's**. The room forwards it to the helper's `/v1/prefs` with the caller's own credential (`histerauth.forward_prefs`). The fallback has no account preferences (503: the page keeps its local values). Without the helper (Tailscale identity only) the room keeps its own store: keyed by the Tailscale login when the room has exactly one fallback login, otherwise by a hash of the Hister username.
- `/api/status` (Konbini: `/api/health`) stays open for the probes.

### One cookie per room

Each room keeps a cookie of its own, set by the room for its host alone. One sign-in still covers everything: a room without its cookie makes one silent trip through the helper.

| Cookie | Host | Holds | Set by |
|---|---|---|---|
| `__Host-machiya_sso` | Hister's (the helper's) | the helper's browser session `mhs_…` | hister-login |
| `__Host-machiya_sso_<room>` (`kura`, `niwa`, `konbini`, `landing`; `shiori` on the hosted pages' hosts) | the room's | a room session `mhr_…`, good in that room only | the room, from a one-time code |
| `__Host-machiya_sso_<room>_state` | the room's | the nonce of a trip to the helper (10 minutes) | the room |
| `__Host-machiya_sso_<room>_try`, `…_out` | the room's | the loop guard (30 s); the deliberate sign-out marker (30 days) | the room |
| `machiya_sso` (legacy) | `Domain=MACHIYA_COOKIE_DOMAIN` | the helper's session, as before | hister-login, only while `HISTER_LOGIN_LEGACY` has `domain-cookie` |

All are `Secure; HttpOnly; SameSite=Lax; Path=/` with **no `Domain`**. The `__Host-` prefix makes the browser refuse any version of them that another host sets or that carries a `Domain`, so no other site on the tailnet (or a link that injects a header) can plant or overwrite one. Over plain http (a stack without TLS) the prefix and `Secure` are dropped.

**The trip.**
1. A page without the room's cookie goes to `<helper>/machiya/signin?return=<page>&state=<SHA-256 of a nonce>`. The nonce stays in the room's own `…_state` cookie.
2. The helper knows the browser (Hister's cookie, its own `__Host-machiya_sso`, the automatic provider, or its page). It sends the browser to `<room>/machiya/callback?code=mhc_…`.
3. The room (vaultkit's `resolve`, no room code) trades the code over the internal network: `POST /v1/redeem` with `X-Machiya-Code`, `X-Machiya-Room: <its origin>` and `X-Machiya-State: <the nonce>`.

**The code's limits:**
- it works **once**, within **60 seconds**;
- only for the room whose origin the trip named;
- only with that browser's nonce;
- only while its helper session lives.

Anything else shows the sign-in page with a link, never a loop. The answer is a room session; the room sets its cookie and goes back to the page.

**Checks.** Every check names the room (`X-Machiya-Room`), so a room session copied into another room is refused (`401 wrong-room`). A room may accept the sessions of other origins it lists in `<P>_AUTH_ACCEPT_ORIGINS`: Shiori's hosted pages, whose nginx passes their own room cookie on to Kura and Konbini.

**Sign-out** in any room ends the helper session and Hister's, and with them every room session and code made from them. The other rooms follow within their 30-second cache.

### Room tokens

**For callers that aren't a browser or a Shiori app,** in place of Hister's raw owner token:
- pm, machiya-mcp, landing, Niwa→Konbini, scripts, the Firefox extension;
- also **machiya-mcp and smallweb themselves**, which take one beside the Tailscale header (`MCP_AUTH_URL`, `SMALLWEB_AUTH_URL`), for an agent on a tagged machine with no Tailscale login.

**What a token is:**
- `mht_` and 43 characters, made by hister-login;
- the helper keeps it only as a hash, under a name you give it;
- it acts as one Hister user;
- it opens **only the rooms it names** (their origins), never Hister. A leak in one room gives no one Hister.

**Make one** on the helper's sessions page (Room Tokens: a name, tick the rooms; it is shown once), or where the helper runs:

```sh
python3 hister_login.py token mint --user <owner> --label "pm on the laptop" --rooms konbini,niwa --out /run/secrets/pm-room-token
python3 hister_login.py token list
python3 hister_login.py token revoke <id>
```

Send it as `Authorization: Bearer mht_…`, from a file, never in a URL or argv. Revoke it on the sessions page, where each token shows when it was last used.

| Caller | Presents to the rooms | Scope |
|---|---|---|
| Shiori's apps (iOS, Mac, Linux) | `Bearer mhs_…`, the app's own sign-in | every room |
| Safari extension | the app's `mhs_…`, passed over native messaging | every room |
| Firefox extension | a pasted room token | kura, konbini |
| pm | `KANBAN_TOKEN_FILE` = a room token; https by default | konbini, niwa |
| machiya-mcp → rooms | `MCP_TOKEN_FILE` = a room token | kura, niwa, konbini |
| Niwa → Konbini | `NIWA_KONBINI_TOKEN_FILE` = a room token | konbini |
| landing's owner reads | `LANDING_TOKEN_FILE` = a room token | kura, niwa, konbini |
| an agent → machiya-mcp, smallweb | a room token (the machiya plugin's `MACHIYA_TOKEN_FILE`) | machiya-mcp, smallweb |
| everything that calls **Hister** (Kura's push, the rooms' own Hister calls, importers, scripts) | Hister's token, unchanged | Hister |

### Moving to it (readers first, writers after)

1. **hister-login 0.3.0**, with `HISTER_LOGIN_LEGACY` at its default (`domain-cookie,hister-token`). Nothing changes for old rooms: the helper still sets the shared `machiya_sso` beside its host-only cookie.
2. **The rooms and landing** re-vendor vaultkit 0.22 (no code change). They prefer their own cookie and still take the shared one and Hister's token, passing both to the helper.
3. **The callers** get room tokens (the table above). Shiori's extensions and apps update. Kura and Konbini list the hosted pages' origins, and the hosted pages' nginx changes ([hister-login](services/hister-login.md#shioris-hosted-pages)).
4. **The switch:** `HISTER_LOGIN_LEGACY=none`. The shared cookie is no longer set, and the rooms refuse it and Hister's raw token (`401 {"reason": "legacy-off"}`). Each browser makes one silent trip per room. Until then, the helper logs (hourly per room) every legacy credential it still accepts. **Rollback:** set the old value again and restart the helper.
5. Afterwards, rotate Hister's token: it sat in the rooms and their clients for weeks.

## Security notes

- **No secret in the file.** Passwords are scrypt hashes, tokens SHA-256 hashes, pairing codes scrypt hashes. The key that signs sessions and device tokens is `session.key` beside it (0600). Back both up; guard the key like a password.
- **A Python without scrypt** (one built against LibreSSL, as OpenBSD's is) has no `hashlib.scrypt`. There the password features refuse with a clear message: a room with `*_SIGNIN=1` doesn't start, `POST /api/pair` answers 503, and the CLI's `passwd`, `pair` and `setup --password` stop. Everything else (open, Tailscale, header and Hister modes, tokens) works. Before vaultkit 0.21 every room crashed at import there.
- **Mount the directory read-only** into each room. Nothing in a room can write identities; the CLI runs on the host that holds the file. A file that turns invalid keeps the last good one (and says so); a bad file at start refuses to start.
- **Header modes trust whoever reaches the port.** In `tailscale` and `header` mode a room refuses a non-loopback bind unless it's told which peer is the proxy: `*_TRUSTED_PROXIES` (Kura, Konbini and Niwa; identity headers from any other peer are dropped), or `*_BIND_BEHIND_PROXY=1` for a room that has no trusted-proxies setting yet. The proxy is a Tailscale sidecar, or the compose with ports on `127.0.0.1`. Your proxy must strip the login header from what clients send. With sign-in only, list no Tailscale logins (or keep `tailscale serve` in front): a client could send that header itself.
- **Niwa's public garden never consults identity.** With `NIWA_PUBLIC_PORT`, Niwa runs a second web listener for anyone. Like its Gemini and Gopher listeners, it serves only published notes. It reads no `Authorization`, `Tailscale-User-Login`, proxy login header or cookie, sets no cookie, and answers only GET and HEAD on a fixed list of read-only routes. The owner's listener keeps its mode and its bind rules unchanged: a header mode still refuses a non-loopback bind without `NIWA_BIND_BEHIND_PROXY=1`. Never point the public proxy (Funnel, Caddy) at the owner's port. Point it at `NIWA_PUBLIC_PORT`.
- **Trade-offs to know:**
  - **Throttling locks out.** Five wrong passwords lock that name for 15 minutes for everyone, and 20 failures lock an address; behind a proxy every client shares the proxy's address. Pairing allows 5 tries per address per 10 minutes.
  - **A pairing code is reusable until it expires.** The rooms can't mark it used (the file is read-only). The short window, the throttle and the one device id in every token it yields (revoked as one) are the guard; cancel it with `pair --cancel` once the device is in.
  - **The shared cookie domain.** With the identity file's built-in sign-in, `MACHIYA_COOKIE_DOMAIN` makes one sign-in cover every room, and sends the session cookie to every site under that domain. Use a domain only Machiya's rooms serve, or leave it unset and sign in per room. (In Hister sign-in mode the rooms' cookies are host-only since vaultkit 0.22; the domain only carries the preference cookies and the legacy `machiya_sso` until the switch.)
- Every state change made with a cookie must be same-origin, and no room changes state on a GET.
