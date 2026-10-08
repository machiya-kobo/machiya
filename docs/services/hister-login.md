# hister-login: Hister's users as the one sign-in

[Hister](hister.md) has users of its own (`app.user_handling: true`, v0.20.0+), with a password sign-in and OIDC. hister-login makes a Hister sign-in count in every Machiya app (these docs call them rooms: Kura, Niwa, Konbini and the landing page).

- Sign in once, in any room, in Shiori's hosted pages or in Hister. Every room then knows you, each through a host-only cookie of its own.
- Sign out anywhere, and every room forgets you within 30 seconds.
- Hister is not patched.

Code: [stack/hister-login/](../../stack/hister-login/) (settings in its README). The rooms' side is vaultkit's `histerauth.py` (`<ROOM>_AUTH=hister`, [identity.md](../identity.md#hister-sign-in-authhister)).

It's optional. Without it, a room keeps its Tailscale gate or the identity file. A room in `hister` mode without the helper runs on its Tailscale identity alone.

## How it works

```mermaid
flowchart LR
  B[Browser / Shiori] -->|"__Host-machiya_sso_&lt;room&gt;=mhr_… (that room only)"| R[Kura · Niwa · Konbini · landing]
  B -->|"hister.*: /machiya/, /api/oauth/callback (__Host-machiya_sso=mhs_…)"| L[hister-login<br/>SQLite: id → Hister session, room sessions, codes, room tokens;<br/>prefs.sqlite3: each user's settings]
  B -->|"hister.*: everything else"| H[(Hister)]
  R -->|"POST /v1/redeem (a code); GET /v1/check + X-Machiya-Room, cached 30 s; GET/PUT /v1/prefs"| L
  A[pm · machiya-mcp · scripts] -->|"Bearer mht_… (a room token)"| R
  L -->|"GET /api/profile, POST /api/logout"| H
  P[hosted pages' nginx] -->|"auth_request /v1/nginx"| L
  P -->|"Cookie: hister=… (internal hop only)"| H
  H -->|OIDC| I[an OIDC provider, e.g. tsidp]
```

### Where it runs

- Beside Hister, on Hister's own host name.
- The proxy in front (Tailscale Serve, say) sends `/machiya/…` and the single path `/api/oauth/callback` to the helper's public port. Everything else goes to Hister.
- The internal port is reachable only from the rooms' network. The proxy never routes it.
- A Tailscale Serve Proxy handler **strips its mount path** before forwarding, so the targets must add it back:
  - `/machiya/` → `http://hister-login:8080/machiya/`
  - `/api/oauth/callback` → `http://hister-login:8080/api/oauth/callback`

  Without that, the helper gets `/healthz` instead of `/machiya/healthz` and answers 404.

### The cookies

See also [identity.md](../identity.md#one-cookie-per-room).

- The helper's own session: `__Host-machiya_sso=mhs_<32 random bytes>`, on Hister's host only.
- Each room's session: `__Host-machiya_sso_<room>=mhr_…`, on that room's own host.
- All are `Secure; HttpOnly; SameSite=Lax; Path=/`, with no `Domain`.
- They are opaque. The helper keeps the mapping to the browser's Hister session.
- Hister's own `hister` cookie stays on Hister's host.
- `MACHIYA_SSO_COOKIE` renames the base (letters, digits, `_`, `-`). The rooms and the landing page read the same setting.
- While `HISTER_LOGIN_LEGACY` includes `domain-cookie`, the helper also sets the old `machiya_sso` cookie on `MACHIYA_COOKIE_DOMAIN`. It's off unless you set it (0.5.0); so is `hister-token`, which lets rooms take Hister's raw token.

### Sign-in

1. A room without its cookie sends the browser to `https://hister.example.ts.net/machiya/signin?return=<the page>&state=<SHA-256 of a nonce>`.
2. The helper finds out who the browser is, from one of:
   - Hister's cookie, or its own;
   - a sign-in on its page: Hister's password login, which the page posts to Hister's own `/api/login` so the helper never sees the password;
   - "Sign in with …" for Hister's OIDC provider.
3. The helper sends the browser to `<room>/machiya/callback?code=mhc_…`.
4. The room trades the code at the internal `POST /v1/redeem` for its room session. A code is good for one use and 60 s, and is bound to the room's origin, the browser's nonce and the helper session.

Hister has no "return to", so the helper's callback shim passes Hister's OAuth callback through unchanged, then returns to where the sign-in started.

### Checks

A room asks `GET /v1/check` about a room session, an app's id or a room token, naming itself in `X-Machiya-Room`. (Legacy: a browser's helper id or a Hister token.) The helper asks Hister's `GET /api/profile`, at most once per credential every 30 s, and answers:

| Answer | Meaning |
|---|---|
| `200 {username, user_id, via, kind, room}` | signed in |
| `401` | signed out. Every id on that Hister session is dropped, with its room sessions |
| `401 wrong-room` | another room's credential |
| `401 legacy-off` | a legacy credential after the switch |
| `503 {"reason": "hister-unavailable" \| "user-handling-off"}` | unavailable |

### Sign-out

Signing out ends the session everywhere.

- A room's `/signout`, the helper's `/machiya/signout` and its sessions page call Hister's logout. They drop every id on that Hister session, with every room session and unused code made from them.
- The ids are remembered as ended for 30 days, so the next room the browser opens shows the helper's page instead of signing it straight back in.
- A sign-out in Hister's own UI is noticed at the next check, within a minute.
- While Hister is down, the ids are dropped at once and Hister's logout is retried.

### Room tokens

Room tokens (`mht_…`, [identity.md](../identity.md#room-tokens)) are for pm, machiya-mcp, landing, Niwa→Konbini, scripts and extensions, in place of Hister's raw token.

- Each acts as one Hister user.
- Each opens only the rooms it names, plus machiya-mcp and smallweb (named in `HISTER_LOGIN_TOKEN_SERVICES`). Never Hister.
- Make them on the sessions page, or with `hister_login.py token mint`.

### Sessions page

`/machiya/sessions` lists:
- every browser and app signed in through the helper, and the rooms each browser has opened, with "Sign Out" for one and "Sign Out Everywhere";
- the room tokens (made, last used, Revoke; New Token).

### Apps

Shiori's apps get an id in one of two ways:
- they sign in to Hister themselves and trade their own Hister session for an id (`POST /machiya/api/app-session`);
- or they open `/machiya/signin?app=1&return=shiori://signed-in` in an ephemeral web session and get `#sid=…&hister=…` back.

Rules for the second way:
- The return is exactly `<scheme>://signed-in` (`HISTER_LOGIN_APP_SCHEMES`, default `shiori`).
- The flow finishes only on the app's own web session (`Sec-Fetch-Site: none`) or this page's own navigation (`same-origin`).
- Any other navigation, a link from another site say, gets a confirmation page. Its Continue is a same-origin `POST /machiya/signin`, and nothing is created until then.

The apps send `Authorization: Bearer mhs_…` to the rooms.

Hosted pages behind nginx: see [Shiori's hosted pages](#shioris-hosted-pages).

### Automatic sign-in

A room or the landing page with `MACHIYA_SIGNIN_PROVIDER=oidc` sends a page with no sign-in to `/machiya/signin?return=…&provider=oidc&auto=1`. The helper goes straight to Hister's `/api/oauth?provider=oidc`. tsidp knows the device, so there is no page and no tap. Each installed web app, with its own cookie jar, signs itself in.

The sign-in page shows instead:
- **After a deliberate sign-out.** The browser holds the helper's marker `__Host-machiya_sso_out` (set for 30 days by `/machiya/signout` and the sessions page). Or its cookie names a helper session that was signed out on purpose (a room's `/signout`; the helper sets its marker on that visit). The room that signed out also sends no `auto=1` (its own marker).
- **When the last round trip failed.** A callback that doesn't finish a sign-in this helper started lands on the page with "Sign in with Tailscale didn't work. Try again, or sign in with your password." It sets the marker for 10 minutes, so it never loops.

The next successful sign-in clears the marker. `provider=` without `auto=1` (a tap on Shiori's "Sign In with Tailscale") always goes through.

### Settings that follow you

The helper keeps each person's shared settings ([contracts/prefs.md](../contracts/prefs.md)) in their own file, `prefs.sqlite3`, beside the sessions file (`HISTER_LOGIN_PREFS_DB`). Each Hister user's settings are keyed by `hi:<sha256(username)[:32]>`, derived only from the credential the helper resolved itself.

- **The rooms and the landing page** keep their same-origin `/api/prefs`. They forward it to the internal `GET`/`PUT /v1/prefs` with the caller's own credential: exactly one of `X-Machiya-Session` or `X-Access-Token`, as for `/v1/check`, with `X-Machiya-Room`.
- **Shiori's apps, extensions and scripts** call the public `GET`/`PUT /machiya/api/prefs` (on Hister's own host) with `Authorization: Bearer mhs_…`, a room token or a Hister token.
- **The hosted pages** reach that same path through their own nginx with the sign-in cookie. A `PUT` then needs an `Origin` among the return hosts. There is no CORS.
- **First render:** `/v1/check` carries the Shared values as `prefs`, so a room draws a fresh browser's first page in your theme.
- **Values are never logged.**

## Shiori's hosted pages

The hosted pages (`search.*` and `shiori.*`) are static pages in front of Hister, served by `shiori-web`'s nginx. They can't trade a code themselves, so the helper does it for them.

nginx sends a few `/machiya/` paths on those hosts to the helper's public port, with the original `Host`. The helper answers them only for a host in `HISTER_LOGIN_PROXIED_ORIGINS`, as that host's room (`__Host-machiya_sso_shiori`).

```nginx
# search.* and shiori.* (shiori-web): the helper's side of the hosts' sign-in (hister-login 0.3.0)
location ~ ^/machiya/(start|callback|signed-out|signout|api/prefs)$ {
    proxy_pass http://hister-login:8080;
    proxy_set_header Host $host;
}
location /machiya/static/ { proxy_pass http://hister-login:8080; proxy_set_header Host $host; }
map $http_cookie $machiya_room_sid {                  # nginx's $cookie_ can't name a cookie with a "-"
    "~(?:^|;\s*)__Host-machiya_sso_shiori=(?<v>mhr_[A-Za-z0-9_-]{43})" $v;
    default "";
}
location = /_machiya_auth {
    internal;
    proxy_pass http://hister-login:8081/v1/nginx;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Machiya-Session $machiya_room_sid;
    proxy_set_header X-Machiya-Room https://$host;
}
location / {                                          # the Hister route (as before)
    auth_request /_machiya_auth;
    auth_request_set $hister_cookie $upstream_http_x_hister_cookie;
    proxy_set_header Cookie $hister_cookie;           # replaces the browser's whole Cookie header
    proxy_hide_header Set-Cookie;                     # Hister re-sends its session cookie on every answer
    # … the existing proxy_pass and websocket lines
}
# /kura/ and /konbini/: forward the hosted session under THAT ROOM's own cookie name (vaultkit reads only its own
# room cookie): "Cookie: __Host-machiya_sso_kura=$machiya_room_sid" on /kura/ and
# "Cookie: __Host-machiya_sso_konbini=$machiya_room_sid" on /konbini/ (the dev stack: its own cookie prefix).
# Kura and Konbini list both hosts in KURA_AUTH_ACCEPT_ORIGINS / KANBAN_AUTH_ACCEPT_ORIGINS, so either host's session
# is accepted by both rooms.
```

- **The trip:** a page's 401 goes to `…hister…/machiya/signin?return=<page>` as usual. The helper sees no state for that host, so it sends the browser through `<host>/machiya/start`. That sets the state cookie on the host and comes back. Then a code goes to `<host>/machiya/callback`, and the room cookie is set there. The pages need no change for this.
- **Sign Out** must post to `/machiya/signout` on the pages' own origin.
- `GET /v1/nginx` answers 200 with `X-Hister-Cookie: hister=<session>` for nginx's own hop to Hister, else 401 or 503.
- It answers 200 only for an origin in `HISTER_LOGIN_PROXIED_ORIGINS`, with a room session made for that same origin (0.5.0). Another room's session, an app's or a browser's id, or any other origin gets the 401 of a signed-out browser. The internal port can't tell who is asking, so `X-Machiya-Room` alone proves nothing.
- That location must replace the browser's `Cookie` header **and** have `proxy_hide_header Set-Cookie;`. Hister re-sends its session cookie on every signed-in answer. Without it, the raw Hister session would reach the browser on the pages' host.

## When something is down

| | A room with `<ROOM>_AUTH_FALLBACK=tailscale` | A room with `<ROOM>_AUTH_FALLBACK=none` |
|---|---|---|
| signed in, everything up | admitted | admitted |
| signed out (the helper or Hister said so) | sent to sign in; an API call gets 401 `{"error", "signin"}`; **never** the fallback | the same |
| the helper or Hister unreachable, a 5xx, or Hister's user handling off | your Tailscale login (in `<ROOM>_USERS`) is admitted with a banner, "Signed in through the tailnet: sign-in is unavailable"; nothing cached; counted as `fallback_total` | **503** on pages and API; there is no grace period |

Two health checks:
- The internal `/healthz` reports the helper **and** Hister: 503 unless both are fine. A room uses it to know, before anyone has signed in, that sign-in is unavailable.
- The public `/machiya/healthz` is for probes. It's 200 while the helper itself works (Hister's state is in `"hister"`), and 503 only when the helper's own state fails. So a Hister outage alerts once, from Hister's `/health` probe.

## Run it

```sh
HISTER_LOGIN_PUBLIC_URL=https://hister.example.ts.net HISTER_LOGIN_HISTER_URL=http://hister:4433 \
  MACHIYA_COOKIE_DOMAIN=example.ts.net python3 stack/hister-login/hister_login.py
```

The image (`stack/hister-login/Dockerfile`) runs as uid 1000 with its state in `/data`:
- `hister-login.sqlite3` (0600, WAL): an id stored only as its SHA-256, the Hister session it maps to, the user, the times;
- `prefs.sqlite3` (0600): each user's settings.

Back both up together, with Hister's own database.

**Preferences on the command line.** Run these where the helper runs, e.g. `docker exec hister-login python3 /app/hister_login.py prefs …`:
- `prefs import --user NAME [--tailscale LOGIN] [--principal KEY] [--dry-run] FILE…` seeds an account from the rooms' old `prefs.sqlite3` files, which it opens read-only. For each key, the newest `updated` across the files wins, and only when it's newer than the account's own. So a second run changes nothing.
- `prefs show --user NAME` lists what the account holds.
- `prefs delete --user NAME` forgets it (an account removed).

**Test stack.** `stack/hister-login/dev/gate0.sh` brings up a throwaway stack (Hister with users and OIDC, the helper, three stand-in rooms, a stub OIDC provider, a TLS front for `*.machiya.test`), runs the Playwright checks in Chromium and WebKit, and removes it.

### Hister's side

- `app.user_handling: true`, `app.public: false`.
- `server.base_url` = the public address.
- The OIDC provider under `server.oauth.oidc`, with its client secret only in the environment (`HISTER__SERVER__OAUTH__OIDC__CLIENT_SECRET`). Every one-off Hister CLI container needs that secret too once OIDC is configured.
- `HISTER_CONFIG` set, so Hister reads the file at all.
- Bind your OIDC identity (`users.o_auth_id = 'oidc-<email>'`) **before** the first OIDC sign-in. Hister has no account linking, and an unknown OIDC identity becomes a new, empty, non-admin account.
