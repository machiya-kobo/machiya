# Changelog: hister-login

## 0.2.0

- **The settings that follow a person** (the owner, 2026-10-05; [docs/contracts/prefs.md](../../docs/contracts/prefs.md)): the helper keeps one store of each Hister user's settings, in its own file `prefs.sqlite3` beside the sessions file (`HISTER_LOGIN_PREFS_DB`, 0600), keyed by `hi:<sha256(username)[:32]>`, which is derived only from the credential the helper resolves itself (no endpoint takes a user id).
  - The internal `GET`/`PUT /v1/prefs` is for the rooms and landing, which forward their `/api/prefs` with the caller's own `X-Machiya-Session` or `X-Access-Token` (exactly one, as for `/v1/check`).
  - The public `GET`/`PUT /machiya/api/prefs` is for Shiori's apps (`Bearer mhs_…`), extensions and scripts (a Hister token), and the hosted pages through their nginx (the sign-in cookie; a `PUT` needs an `Origin` among the return hosts; no CORS).
  - The answer is `{"v", "rev", "prefs", "updated"}` with `ETag: "<rev>"` (304 on `If-None-Match`). A `PUT` merges only the keys sent and is checked against vaultkit's schema (400). Values are never logged.
  - `/v1/check` also carries the Shared values as `prefs`, so a room draws a fresh browser's first page in the person's theme.
- **`hister_login.py prefs import|show|delete --user NAME`.** `import` seeds an account from the rooms' old `prefs.sqlite3` files, which it opens read-only: for each key the newest `updated` across the files wins, and only when it is newer than the account's own, so a second run changes nothing. `delete` is for an account removed.
- **Automatic sign-in** (the owner, 2026-10-05: "Yes, Tailscale automatically"). With `MACHIYA_SIGNIN_PROVIDER=oidc` the rooms and landing send a page that needs a sign-in to `/machiya/signin?…&provider=oidc&auto=1`, and the helper goes straight to Hister's OIDC sign-in: tsidp knows the device, so there are no taps, and each installed web app signs itself in.
  - **A deliberate sign-out** sets the marker `<sign-in cookie>_out` (`machiya_sso_out`) on the shared domain for 30 days: `/machiya/signout`, the sessions page signing this browser out, or a room's `/signout` (vaultkit). With the marker, `auto=1` shows the page instead, so Sign Out never bounces straight back in. The next successful sign-in clears it.
  - **A failed round trip:** a callback that doesn't finish a sign-in this helper started lands on the page with "Sign in with … didn't work. Try again, or sign in with your password." and a 10-minute marker, so it never loops.
  - **A tap** on "Sign In with Tailscale" (`provider=` without `auto`) always goes through.
- Tests: the store and its keys, every credential, the refusals, a cookie `PUT`'s `Origin`, no CORS, keep-alive (different people's prefs requests down one connection) and an unread prefs body that never becomes a request; the import rule; the automatic sign-in, the marker and a failed round trip.
- vaultkit re-vendored (the `prefs` module, `MACHIYA_SIGNIN_PROVIDER`).

## 0.1.3

- `/machiya/signin?provider=<name>` (a provider in `HISTER_LOGIN_PROVIDERS`, e.g. `oidc`) sets the return cookie as the page does and goes straight to Hister's `/api/oauth?provider=<name>`: Shiori's "Sign In with Tailscale" is one tap. An unknown provider shows the page; a browser already signed in still finishes at once.

## 0.1.2

- **Keep-alive (security):** a request body the helper didn't read (a GET's, a refused or unknown POST's) stayed on
  the connection and was parsed as the next request: on `:8080`, one smuggled past Tailscale Serve with headers Serve
  never saw, whose answer could reach the next person on that connection. Such a request now ends with
  `Connection: close`, and every request starts with no state from the one before (`handle_one_request`). Tests send
  several people's requests down one kept-alive connection, as Serve does.
- `MACHIYA_SSO_COOKIE` names the sign-in cookie (default `machiya_sso`), so a second stack under the same cookie domain
  (a dev stack on the same tailnet) never reads the other's. The rooms and landing read the same setting
  (vaultkit `histerauth`, unreleased).

## 0.1.1

- The container's healthcheck reads the reply before closing: closing early left a `ConnectionResetError` traceback in the helper's log every minute.

## 0.1.0

The first version (phase 0 of the Hister sign-in): the opaque `machiya_sso` cookie, the sign-in page (Hister's own
password login and OIDC link), the OAuth callback shim, `/v1/check`, `/v1/signout`, `/v1/nginx`, `/healthz`, the
sessions page, the app flow (`app=1` and `POST /machiya/api/app-session`), and the SQLite state (ids stored as hashes,
sign-outs retried while Hister is unreachable). The dev stack and gate-0 checks are in `dev/`.
- `GET /machiya/healthz` (public, for probes) answers 200 `{"ok": true, "hister": "ok"|"down"|"user-handling-off"}`
  whenever the helper itself works, and 503 only when its own state (the SQLite file) fails, so a Hister outage alerts
  once (Hister's own probe). The internal `GET /healthz` stays 503 unless the helper and Hister are both fine: the
  rooms' health flag needs that.
