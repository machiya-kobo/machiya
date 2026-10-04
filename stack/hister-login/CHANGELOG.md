# Changelog: hister-login

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
