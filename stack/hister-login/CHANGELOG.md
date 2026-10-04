# Changelog: hister-login

## Unreleased (0.1.0)

The first version (phase 0 of the Hister sign-in): the opaque `machiya_sso` cookie, the sign-in page (Hister's own
password login and OIDC link), the OAuth callback shim, `/v1/check`, `/v1/signout`, `/v1/nginx`, `/healthz`, the
sessions page, the app flow (`app=1` and `POST /machiya/api/app-session`), and the SQLite state (ids stored as hashes,
sign-outs retried while Hister is unreachable). The dev stack and gate-0 checks are in `dev/`.
