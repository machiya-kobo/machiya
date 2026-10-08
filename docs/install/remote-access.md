# Reach Machiya from your other devices

The [Quickstart](../../README.md#quickstart) listens on `127.0.0.1` with no sign-in. To use it from your phone or laptop over [Tailscale](https://tailscale.com), on the machine that runs the stack:

**1. Serve each app on your tailnet.** Tailscale adds HTTPS and the `Tailscale-User-Login` header. Give each app its own port, since a machine name has only one port 443:

```bash
sudo tailscale serve --bg --https=8083 http://127.0.0.1:8083   # Kura
sudo tailscale serve --bg --https=8082 http://127.0.0.1:8082   # Niwa
sudo tailscale serve --bg --https=8081 http://127.0.0.1:8081   # Konbini
```

Use `serve`, never `funnel`: Funnel puts the port on the public internet.

**2. Set the addresses in `.env`,** so the links and the **Rooms** menu point at your tailnet, not at `localhost`:

```
KURA_PUBLIC_URL=https://<machine>.<tailnet>.ts.net:8083
NIWA_PUBLIC_URL=https://<machine>.<tailnet>.ts.net:8082
KONBINI_PUBLIC_URL=https://<machine>.<tailnet>.ts.net:8081
MACHIYA_ROOMS=konbini=https://<machine>.<tailnet>.ts.net:8081,niwa=https://<machine>.<tailnet>.ts.net:8082,kura=https://<machine>.<tailnet>.ts.net:8083
```

Hister and SearXNG have no sign-in of their own. Serve them (4433, 8888) and add them to `MACHIYA_ROOMS` only if everyone on your tailnet may use them, and set `HISTER_PUBLIC_URL` and `SEARXNG_PUBLIC_URL` to match.

**3. Turn the login check on:** `KURA_AUTH`, `NIWA_AUTH` and `KONBINI_AUTH` to `tailscale`, and your Tailscale login in `KURA_USERS`, `NIWA_USERS` and `KONBINI_USERS`.

**4. Name the proxy** in `KURA_TRUSTED_PROXIES`, `NIWA_TRUSTED_PROXIES` and `KONBINI_TRUSTED_PROXIES`. The apps believe the login header only from that address, and Kura and Konbini refuse to start without it. Behind `tailscale serve` it isn't `127.0.0.1`:

- **Docker:** the gateway of the stack's network, like `172.18.0.1/32`. Print it with `docker network inspect machiya_default --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`.
- **Rootless Podman:** each app sees its own address, and that changes every time the container is made again. Name the stack's network instead, like `10.89.1.0/24`: `podman network inspect machiya_default --format '{{range .Subnets}}{{.Subnet}}{{end}}'`. Any container in the stack could then send the header.

The network can get another address after `docker compose down`, so check the value after one.

With the [identity file](../identity.md), also set `NIWA_BIND_BEHIND_PROXY=1` and `KONBINI_BIND_BEHIND_PROXY=1`.

**5. Restart it:** `docker compose up -d`. Then open `https://<machine>.<tailnet>.ts.net:8083/` on your phone.

Other ways in:

- People, agents, passwords or your own auth proxy: [identity.md](../identity.md).
- Hister's users as the one sign-in for every app: [hister-login](../services/hister-login.md). The reference compose doesn't run it yet; the [dev stack](../dev-stack.md) has it wired up.
