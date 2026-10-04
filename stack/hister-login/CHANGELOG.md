# Changelog: hister-login

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
