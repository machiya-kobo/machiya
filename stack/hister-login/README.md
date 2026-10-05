# hister-login

Hister's users as the one sign-in for every Machiya room. Hister keeps its session cookie (`hister`) on its own host; this
helper sits on that host too and turns a Hister session into an opaque id, its own host-only cookie
`__Host-machiya_sso=mhs_…` (0.3.0). Each room (`<P>_AUTH=hister`, `vaultkit.histerauth`) keeps a host-only cookie of its own,
made from a one-time code the helper sends back with the browser; rooms ask the helper about their cookie (or an app's id,
or a room token) over the internal network; the helper asks Hister's `GET /api/profile` and caches the answer for 30 s.
Signing out anywhere ends the Hister session, every id on it and every room's cookie made from them. Hister itself is not
patched. The design: [docs/identity.md](../../docs/identity.md#one-cookie-per-room).

```
HISTER_LOGIN_PUBLIC_URL=https://hister.example.ts.net HISTER_LOGIN_HISTER_URL=http://hister:4433 \
  MACHIYA_COOKIE_DOMAIN=example.ts.net HISTER_LOGIN_DB=/tmp/hl.sqlite3 python3 hister_login.py
python3 -m unittest discover -s tests      # from this directory; needs markdown and pyyaml (vaultkit's dependencies)
podman build -t hister-login:dev .         # or docker build
dev/gate0.sh                               # the phase-0 proof: a dev stack, Playwright checks, then down again
```

## Ports and paths

| Port | Reached by | Paths |
|---|---|---|
| `HISTER_LOGIN_PORT` (8080), public | browsers and apps, through the proxy on Hister's host (Tailscale Serve sends it `/machiya/` and the single path `/api/oauth/callback`; everything else goes to Hister) | `GET /machiya/signin?return=…[&state=…][&app=1][&provider=oidc[&auto=1]]`, `GET /api/oauth/callback` (a shim: Hister's own callback, then an id and the way back), `POST /machiya/signout`, `GET`/`POST /machiya/sessions` (sessions, the rooms each browser opened, room tokens), `POST /machiya/api/app-session`, `GET /machiya/healthz`, `GET`/`PUT /machiya/api/prefs` (0.2.0), `/machiya/static/…` |
| the same port, for a host in `HISTER_LOGIN_PROXIED_ORIGINS` (0.3.0) | the hosted pages (`search.*`, `shiori.*`), through their nginx, which passes `Host` on | only `GET /machiya/start`, `GET /machiya/callback`, `GET /machiya/signed-out`, `GET`/`PUT /machiya/api/prefs`, `/machiya/static/…`, `POST /machiya/signout`: the helper is that host's room (`__Host-machiya_sso_shiori`) |
| `HISTER_LOGIN_INTERNAL_PORT` (8081), internal only, never routed by the proxy | the rooms and the hosted pages' nginx | `GET /v1/check`, `POST /v1/redeem` (0.3.0), `POST /v1/signout`, `GET /v1/nginx`, `GET /healthz`, `GET`/`PUT /v1/prefs` (0.2.0) |

`GET /v1/check` takes one credential, `X-Machiya-Session: mhs_…|mhr_…|mht_…` or `X-Access-Token: <Hister token>`, and
(0.3.0) the asking room's origins, `X-Machiya-Room: https://kura.example.ts.net[, <origin it also accepts>…]`:

| Credential | 200 when |
|---|---|
| `mhr_…` (a room session) | it was made for one of those origins, and its helper session is alive (checked with Hister as below) |
| `mht_…` (a room token) | it names the room's own origin (the first), and isn't revoked or expired |
| `mhs_…` of an app | the session is alive (every room) |
| `mhs_…` of a browser (the old shared-domain cookie) | `domain-cookie` is in `HISTER_LOGIN_LEGACY`, and the session is alive |
| Hister's token | `hister-token` is in `HISTER_LOGIN_LEGACY`, and Hister says so |

| Hister said | Answer |
|---|---|
| 200 with a JSON `username` | `200 {"username", "user_id", "via": "session"\|"token", "kind": "room"\|"token"\|"app"\|"browser"\|"hister-token", "room", "prefs"}` (the row's expiry slides; `prefs`: the account's Shared settings, 0.2.0) |
| 401/403, or the id is unknown or expired | `401 {"reason": "signed-out"}`; every id on that Hister session is deleted, with the room sessions made from them |
| (the helper itself) another room's session, or a token for other rooms | `401 {"reason": "wrong-room"}` |
| (the helper itself) a legacy credential after the switch | `401 {"reason": "legacy-off"}` |
| 200 with no JSON user (`user_handling` is off) | `503 {"reason": "user-handling-off"}`, logged loudly |
| timeout, connection error, 5xx | `503 {"reason": "hister-unavailable"}` |

Two health answers: the internal `GET /healthz` is the rooms' (503 unless the helper **and** Hister are fine; a room uses it
to know sign-in is unavailable before anyone signs in); the public `GET /machiya/healthz` is the probe's (200 while the
helper and its state work, with Hister's state as `"hister": "ok"|"down"|"user-handling-off"`; 503 only when the
helper's own state fails), so a Hister outage alerts once, from Hister's own `/health` probe.

**Preferences** (0.2.0, [docs/contracts/prefs.md](../../docs/contracts/prefs.md)):
- `GET`/`PUT /v1/prefs` takes the same one credential as `/v1/check`: the rooms forward their `/api/prefs` with the caller's own.
- `GET`/`PUT /machiya/api/prefs` takes `Authorization: Bearer mhs_…` or a Hister token (`X-Access-Token`, `Bearer`), or the sign-in cookie. A cookie `PUT` needs an `Origin` among the return hosts: the hosted pages, through their nginx. There is no CORS.
- Both answer `{"v": 1, "rev", "prefs", "updated"}` with `ETag: "<rev>"` (304 on `If-None-Match`), and a `PUT` merges only the keys sent.
- Errors: 400 (the schema), 401 `{"error": "sign in", "signin"}`, 403, 413, 415, 503.

**A room's cookie** (0.3.0). A room sends a browser here with `return=<its page>&state=<SHA-256 of a nonce>`; the nonce
stays in the room's own host-only cookie. Once the helper knows the browser (Hister's cookie, its own `__Host-machiya_sso`,
the automatic provider or the page), it sends the browser to `<the room's origin>/machiya/callback?code=mhc_…`. The room
trades the code at `POST /v1/redeem` (`X-Machiya-Code`, `X-Machiya-Room: <its origin>`, `X-Machiya-State: <the nonce>`):
the code works once, within 60 s, only for that origin and that nonce, and only while its helper session lives. The answer
is `200 {"session": "mhr_…", "return", "username", "user_id", "max_age", "prefs"}` or `401 {"reason": "bad-code"}`. A
`return` without a state gets no code: the browser goes back to it as it is (the room then makes its own trip; a proxied
origin goes through its `/machiya/start`).

**Sign-out** (`/v1/signout` from a room with its `mhr_…`, `/machiya/signout`, the sessions page) ends the Hister session, the
browser's helper id, every room session and unused code made from it, and remembers the id as ended for 30 days, so
whichever room the browser opens next shows the page instead of an automatic sign-in.

**Room tokens** (0.3.0): `mht_…`, for headless callers (pm, machiya-mcp, landing, Niwa→Konbini, scripts, an extension).
Each acts as one Hister user, opens only the rooms (origins) it names, and is kept as a SHA-256 only. Make one on the
sessions page (Room Tokens: a name and the rooms; shown once) or here: `token mint --user NAME --label L --rooms
kura,konbini [--days N] [--out FILE]`; `token add … --from-file FILE` registers a value made elsewhere; `token list`;
`token revoke ID`. Revoke one on the sessions page.

**Automatic sign-in** (0.2.0): `/machiya/signin?…&provider=oidc&auto=1` (a room's, with `MACHIYA_SIGNIN_PROVIDER=oidc`) goes straight to Hister's `/api/oauth?provider=oidc`, unless the browser holds the marker `__Host-machiya_sso_out` (0.3.0: host-only; the rooms keep their own), or its cookie names an id that was signed out on purpose. A deliberate sign-out sets the marker for 30 days, a round trip that failed for 10 minutes; with it the page shows. The next sign-in clears it.

`GET /v1/nginx` (for `auth_request`) takes the hosted pages' room session (`X-Machiya-Session`, or their
`__Host-machiya_sso_shiori` cookie in `Cookie`) with `X-Machiya-Room: https://$host`, and answers 200 with
`X-Hister-Cookie: hister=<session>` for nginx's hop to Hister, 401, or 503. Hister re-sends its cookie on every signed-in
answer, so that nginx location must also have `proxy_hide_header Set-Cookie;`, or the raw Hister session reaches the
browser on the hosted pages' host. The nginx side: [docs/services/hister-login.md](../../docs/services/hister-login.md).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `HISTER_LOGIN_PUBLIC_URL` | required | Hister's public address, `https://hister.example.ts.net` (no path): cookies get `Secure` when it is https; same-origin checks use it |
| `HISTER_LOGIN_HISTER_URL` | `http://hister:4433` | Hister on the internal network |
| `HISTER_LOGIN_LEGACY` | `domain-cookie,hister-token` | what the old world may still use (0.3.0): `domain-cookie` sets the old `machiya_sso` on `MACHIYA_COOKIE_DOMAIN` beside the host-only cookie and lets rooms take a browser's helper id; `hister-token` lets rooms take Hister's raw token. `none` is the switch, once every room and client is new; putting it back is the rollback |
| `HISTER_LOGIN_PROXIED_ORIGINS` | none | the hosted pages' origins (`https://search.…,https://shiori.…`), whose nginx sends `/machiya/start`, `/machiya/callback`, `/machiya/signout`, `/machiya/api/prefs` and `/machiya/static/` here with their `Host`; added to the return hosts |
| `HISTER_LOGIN_TOKEN_SERVICES` | none | services with a gate of their own that also take room tokens, `name=url` comma-separated (`machiya-mcp=https://mcp.example.ts.net,smallweb=https://smallweb.example.ts.net`): offered beside the rooms of `MACHIYA_ROOMS` on the sessions page and to `token mint --rooms` |
| `MACHIYA_SIGNIN_PROVIDER` | none | for a proxied origin's `/machiya/start`: sign in automatically through that provider, as the rooms do |
| `MACHIYA_COOKIE_DOMAIN` | none | the shared domain of the legacy `machiya_sso` (set and cleared only while `domain-cookie` is on; always cleared at sign-out) |
| `MACHIYA_SSO_COOKIE` | `machiya_sso` | the sign-in cookie's base name (letters, digits, `_`, `-`): the helper's own is `__Host-<it>`, a room's `__Host-<it>_<room>`, the hosted pages' `__Host-<it>_shiori`; the rooms and landing must use the same (vaultkit's `histerauth` reads the same setting) |
| `HISTER_LOGIN_RETURN_HOSTS` | the https hosts in `MACHIYA_ROOMS`, minus searxng | where a browser may be sent back to (`host` or `host:port`, exact); the helper's own host is always allowed |
| `HISTER_LOGIN_APP_SCHEMES` | `shiori` | URL schemes the app flow (`app=1`) may return to |
| `HISTER_LOGIN_PROVIDERS` | none | `oidc` shows "Sign in with …" (Hister's `/api/oauth?provider=oidc`) |
| `HISTER_LOGIN_OIDC_LABEL` | `Tailscale` | its label |
| `HISTER_LOGIN_DB` | `/data/hister-login.sqlite3` | the state (0600, WAL) |
| `HISTER_LOGIN_PREFS_DB` | `prefs.sqlite3` beside `HISTER_LOGIN_DB` | each user's settings (0600; 0.2.0). Back it up with the state |
| `HISTER_LOGIN_BIND`, `HISTER_LOGIN_PORT`, `HISTER_LOGIN_INTERNAL_PORT` | `0.0.0.0`, 8080, 8081 | |

## State

One SQLite file. `sessions` holds, per id, its SHA-256 (the id itself is never stored), the raw Hister session it maps to
(the helper must present it to Hister), that session's hash (so every id on one Hister session goes at once), the user,
`browser` or `app`, a short label, and the times. Expiry slides 30 days with each successful check, never past 180 days
from sign-in. `pending_logout` holds Hister sign-outs that couldn't be made while Hister was down; they are retried every
minute. Expired rows go hourly. Lose the file and browsers sign in again silently (one bounce, while Hister's own cookie
holds); apps sign in again, and room tokens must be made again.

0.3.0 adds to the same file: `room_sessions` (a room session's SHA-256, its helper id's hash, the room's origin, the times;
it goes with its helper id), `codes` (a code's SHA-256, its helper id, origin, the state, the return address; 60 s),
`tokens` (a room token's SHA-256, the user, a label, the origins, made, last used, an optional expiry) and `ended` (ids
signed out on purpose, 30 days).

The settings are a second file, `prefs.sqlite3` (vaultkit's `prefs.Store`): per user key `hi:<sha256(username)[:32]>`, each setting's value and `updated` time, and a revision. Losing it means each device fills the account in again from what it has (the contract's "fill the blanks"). On the command line, where the helper runs:

```
python3 hister_login.py prefs import --user owner [--tailscale me@example.com] [--dry-run] kura-prefs.sqlite3 niwa-prefs.sqlite3 …
python3 hister_login.py prefs show --user owner
python3 hister_login.py prefs delete --user owner
```

Layout: `hister_login.py` (everything), `static/` (the sign-in page's script, the icon), `vaultkit/` (vendored, never
edited here), `tests/` (a fake Hister on a local port), `dev/` (the phase-0 dev stack and its Playwright checks).
