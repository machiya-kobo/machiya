# hister-login dev stack (gate 0)

A rootless podman stack that proves the Hister sign-in end to end on dummy data, then removes itself: `./gate0.sh`.

| Piece | What it stands for |
|---|---|
| `hister` | Hister v0.20.0 (the pinned upstream image), users on, the OIDC config in `hister-config.yml`, the client secret from an env file |
| `hister-login` | the helper, built from `..` |
| `room-kura`, `room-niwa`, `room-konbini` (`room.py`) | the smallest rooms using `vaultkit.histerauth`: Kura with `KURA_AUTH_FALLBACK=none`, Niwa and Konbini with the Tailscale fallback |
| `stub-idp` (`stub_idp.py`) | tsidp's shape: discovery, PKCE S256, `client_secret_post`, userinfo with `email` = `<login>.<idp host>` and no `preferred_username` |
| `front` (`front.conf`) | Tailscale Serve (TLS for `*.machiya.test` on 127.0.0.1:19043; `/machiya/` and `/api/oauth/callback` on Hister's host go to the helper) and the hosted pages' nginx (`shiori.machiya.test`: `auth_request` to `/v1/nginx`) |

`move.py` does the design's data move (§8) on dummy data; `tty_create_user.py` runs `hister create-user` in a pseudo-TTY
(it refuses piped input); `gate0.py` runs the Playwright checks (Chromium and WebKit, through a CONNECT proxy that maps
`*.machiya.test` to the front). The Tailscale identity is faked by sending `Tailscale-User-Login` from the browser.
Everything it writes goes under `DEV_DATA` and `SHOTS`; secrets are generated there and never printed. Dev only: never
deploy from here.
