# mac-bridge

Shiori for Classic Macintosh's way into Hister and Kura. A Mac Plus with MacTCP can't speak TLS, and the apps answer only over https, so this small proxy takes plain HTTP from the Mac on the home network and makes the https call for it.

- **Code:** [machiya-kobo/shiori `classic/bridge/`](https://github.com/machiya-kobo/shiori/tree/main/classic/bridge) (stdlib Python, tests, Dockerfile). Image: `mac-bridge`, tags `mac-bridge-vX.Y.Z`.
- **Where:** the LAN address of the machine that runs Hister, never the tailnet or the internet. One port per app: `8070` Hister, `8071` Kura.

## What it allows

| | |
|---|---|
| Who | only the source addresses in `BRIDGE_ALLOW` (the Macs); the host's own address and `100.64.0.0/10` never |
| Methods | `GET` only |
| Paths | exact: Hister `/search`, `/api/preview`, `/api/config`; Kura `/api/search`, `/api/recent`, `/api/note`, `/api/vaults` |
| Vaults | no `vault=all`, and only the vaults in `BRIDGE_VAULTS` (empty: the default vault only) |
| Headers in | `Authorization`, `Accept`, `If-None-Match`, `Origin: hister://`; everything else is dropped, identity headers and cookies included |
| Headers out | a short allow-list; never `Set-Cookie` |
| Size, time | 512 KB (larger answers get `413`), 20 s |
| Logs | path, status and time; never headers, query strings or bodies |

## Credentials

The Mac holds one room token (`mht_`, [identity.md](../identity.md#room-tokens)) with two scopes: `kura`, and `classic-bridge` (a token service in hister-login's `HISTER_LOGIN_TOKEN_SERVICES`).

- **Kura's port** passes the token on; Kura checks its own scope.
- **Hister's port** checks the token with [hister-login](hister-login.md) (`GET /v1/check`) and only then adds Hister's own token, which stays on the server and never crosses the LAN.
- Revoking the token on the sessions page shuts both ports.

**The trade-off:** the Mac's token and its searches cross the LAN in plain HTTP. The LAN is the trust boundary; keep the bridge off any network you don't trust.

## Settings

`BRIDGE_HISTER_URL`, `BRIDGE_KURA_URL` (fixed upstreams; `BRIDGE_ALLOW_HTTP_UPSTREAM=1` for one on the same Docker network), `BRIDGE_ALLOW`, `BRIDGE_DENY`, `BRIDGE_AUTH_URL`, `BRIDGE_PUBLIC_URL` (the origin registered as `classic-bridge`), `BRIDGE_HISTER_USERS`, `BRIDGE_HISTER_TOKEN_FILE`, `BRIDGE_VAULTS`, and optionally `BRIDGE_MAX_BYTES`, `BRIDGE_TIMEOUT`, `BRIDGE_HISTER_PORT`, `BRIDGE_KURA_PORT`. Details: the [bridge's README](https://github.com/machiya-kobo/shiori/tree/main/classic/bridge).
