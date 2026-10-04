# hister-login

Hister's users as the one sign-in for every Machiya room. Hister keeps its session cookie (`hister`) on its own host; this
helper sits on that host too and turns a Hister session into an opaque id, `machiya_sso=mhs_…`, on the tailnet's shared
domain. The rooms (`<P>_AUTH=hister`, `vaultkit.histerauth`) ask the helper about that id over the internal network; the
helper asks Hister's `GET /api/profile` and caches the answer for 30 s. Signing out anywhere ends the Hister session and
every id on it. Hister itself is not patched.

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
| `HISTER_LOGIN_PORT` (8080), public | browsers and apps, through the proxy on Hister's host (Tailscale Serve sends it `/machiya/` and the single path `/api/oauth/callback`; everything else goes to Hister) | `GET /machiya/signin?return=…[&app=1]`, `GET /api/oauth/callback` (a shim: Hister's own callback, then an id and the way back), `POST /machiya/signout`, `GET`/`POST /machiya/sessions`, `POST /machiya/api/app-session`, `GET /machiya/healthz`, `/machiya/static/…` |
| `HISTER_LOGIN_INTERNAL_PORT` (8081), internal only, never routed by the proxy | the rooms and the hosted pages' nginx | `GET /v1/check`, `POST /v1/signout`, `GET /v1/nginx`, `GET /healthz` |

`GET /v1/check` takes `X-Machiya-Session: mhs_…` or `X-Access-Token: <Hister token>` (exactly one) and answers:

| Hister said | Answer |
|---|---|
| 200 with a JSON `username` | `200 {"username", "user_id", "via": "session"\|"token", "kind"}` (the row's expiry slides) |
| 401/403, or the id is unknown or expired | `401 {"reason": "signed-out"}`; every id on that Hister session is deleted |
| 200 with no JSON user (`user_handling` is off) | `503 {"reason": "user-handling-off"}`, logged loudly |
| timeout, connection error, 5xx | `503 {"reason": "hister-unavailable"}` |

Two health answers: the internal `GET /healthz` is the rooms' (503 unless the helper **and** Hister are fine; a room uses it
to know sign-in is unavailable before anyone signs in); the public `GET /machiya/healthz` is the probe's (200 while the
helper and its state work, with Hister's state as `"hister": "ok"|"down"|"user-handling-off"`; 503 only when the
helper's own state fails), so a Hister outage alerts once, from Hister's own `/health` probe.

`GET /v1/nginx` (for `auth_request`, with `X-Machiya-Session: $cookie_machiya_sso`) answers 200 with
`X-Hister-Cookie: hister=<session>` for nginx's hop to Hister, 401, or 503. Hister re-sends its cookie on every signed-in
answer, so that nginx location must also have `proxy_hide_header Set-Cookie;`, or the raw Hister session reaches the
browser on the hosted pages' host.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `HISTER_LOGIN_PUBLIC_URL` | required | Hister's public address, `https://hister.example.ts.net` (no path): cookies get `Secure` when it is https; same-origin checks use it |
| `HISTER_LOGIN_HISTER_URL` | `http://hister:4433` | Hister on the internal network |
| `MACHIYA_COOKIE_DOMAIN` | none (warned) | the shared domain of `machiya_sso`; without it no room sees the cookie |
| `MACHIYA_SSO_COOKIE` | `machiya_sso` | the sign-in cookie's name (letters, digits, `_`, `-`); the rooms and landing must use the same (vaultkit's `histerauth` reads the same setting). A second stack under the same cookie domain, such as a dev stack on the same tailnet, sets its own (`machiya_dev_sso`) so neither reads the other's cookie |
| `HISTER_LOGIN_RETURN_HOSTS` | the https hosts in `MACHIYA_ROOMS`, minus searxng | where a browser may be sent back to (`host` or `host:port`, exact); the helper's own host is always allowed |
| `HISTER_LOGIN_APP_SCHEMES` | `shiori` | URL schemes the app flow (`app=1`) may return to |
| `HISTER_LOGIN_PROVIDERS` | none | `oidc` shows "Sign in with …" (Hister's `/api/oauth?provider=oidc`) |
| `HISTER_LOGIN_OIDC_LABEL` | `Tailscale` | its label |
| `HISTER_LOGIN_DB` | `/data/hister-login.sqlite3` | the state (0600, WAL) |
| `HISTER_LOGIN_BIND`, `HISTER_LOGIN_PORT`, `HISTER_LOGIN_INTERNAL_PORT` | `0.0.0.0`, 8080, 8081 | |

## State

One SQLite file. `sessions` holds, per id, its SHA-256 (the id itself is never stored), the raw Hister session it maps to
(the helper must present it to Hister), that session's hash (so every id on one Hister session goes at once), the user,
`browser` or `app`, a short label, and the times. Expiry slides 30 days with each successful check, never past 180 days
from sign-in. `pending_logout` holds Hister sign-outs that couldn't be made while Hister was down; they are retried every
minute. Expired rows go hourly. Lose the file and browsers sign in again silently (one bounce, while Hister's own cookie
holds); apps sign in again.

Layout: `hister_login.py` (everything), `static/` (the sign-in page's script, the icon), `vaultkit/` (vendored, never
edited here), `tests/` (a fake Hister on a local port), `dev/` (the phase-0 dev stack and its Playwright checks).
