# The dev stack

A standing Machiya on one development machine: every service production runs, with Hister's users as the sign-in, on **synthetic data only**. Agents and people develop and test against it by default; production is touched only to release. The same stack, the same seed and the same checks run on the test VMs, in containers or, where there are none (the BSDs, Haiku), as plain processes.

Everything lives in [`compose/dev/`](../compose/dev/): `compose.yml`, the `dev` script that drives it, the `seed/`, and the small stand-ins for the outside world.

## The synthetic-data rule

**Never put a real note, page, feed, login or token into the dev stack.** What it holds is made from the repository, the same on every machine:

| Data | From | Notes |
|---|---|---|
| the vault | [`sample-vault/`](../sample-vault/) plus [`seed/vault/`](../compose/dev/seed/vault/) | one commit with a fixed date, so its hash is the same everywhere (`d09e5c4…` today). Three vaults: `personal` (the default; every board column, a published garden, tags), `team` (shared) and `work` (**private**: never pushed to Hister; the word *quillwort* is only there, so a search proves where it looked) |
| saved pages | [`seed/site/`](../compose/dev/seed/site/) through [`seed/hister.json`](../compose/dev/seed/hister.json) | invented `*.example` sites served by `fixtures`; 8 pages with labels and fixed times, the collections `@pages`, `@notes`, `@workshop`, `@travel`, and history entries (one pinned) |
| a feed reader | [`seed/newsblur.json`](../compose/dev/seed/newsblur.json), served by `fake_newsblur.py` | read and starred stories pointing at the fixture sites; feed-import imports them like the real NewsBlur |
| the owner | made by `dev init` | Hister user `owner` (admin) with a generated password and token, bound to the stub OIDC provider's login |

Secrets are generated per machine under `$DEV_DATA/secrets/` (0600) and never printed; `dev check` fails if one shows up in a service log. To exercise something the seed lacks, extend the seed (in the repository, invented) rather than pasting real data.

## Up and running

You need the four checkouts side by side (`machiya`, `kura`, `niwa`, `konbini`; the rooms are built from them), `git`, `openssl`, Python 3.11+, and rootless Podman 4.9+ with its Docker Compose provider, or Docker with Compose 2.20+.

```sh
cd machiya/compose/dev
./dev init            # the vault, secrets, a throwaway TLS CA, the Hister owner; writes $DEV_DATA/dev.env
./dev up              # builds the rooms and stack services, starts everything, seeds Hister once
./dev check           # the HTTP checks; --browser adds Playwright (Chromium and WebKit) and screenshots
```

| Command | Does |
|---|---|
| `./dev status` | the addresses, the owner's password file, what runs |
| `./dev seed` | the Hister corpus again (idempotent: only what is missing) |
| `./dev logs SERVICE` | a service's log |
| `./dev down` | stops it; the data stays |
| `./dev reset --yes` | down, delete the data, init with the same settings, up: back to the seed |

`$DEV_DATA` is `compose/dev/data` unless `--data DIR` or `MACHIYA_DEV_DATA` says otherwise; the first stack made outside the default is remembered in `compose/dev/.data-dir`. Use a disk-backed folder (`/var/tmp/<you>/…`), never `/tmp`. The project is always `machiya-dev`. A podman API socket is used for compose (`DOCKER_HOST`, the user's `podman.socket`, or one started under `$DEV_DATA`). After a reboot, `./dev up` again.

## Ports

Everything is on `127.0.0.1` (`--bind` changes it). The public ports go through `front`, which does what Tailscale Serve does in production: TLS (a throwaway CA, `$DEV_DATA/certs/ca.crt`, because the sign-in helper only sends a browser back to an `https` address), and on Hister's port it sends `/machiya/` and `/api/oauth/callback` to the hister-login helper.

| Port | Service | Notes |
|---|---|---|
| 19200 | landing | `/` the launcher, `/status`; `AUTH=hister`, fallback `tailscale` |
| 19201 | Kura | `AUTH=hister`, fallback **none** (as production) |
| 19202 | Niwa | `AUTH=hister`, fallback `tailscale` |
| 19203 | Konbini | `AUTH=hister`, fallback `tailscale` |
| 19204 | Hister + hister-login | users on; the helper's `/machiya/signin`, `/machiya/sessions` |
| 19205 | SearXNG | wikipedia, wiktionary and duckduckgo only: web results are sparse on purpose |
| 19206 | machiya-mcp | `/mcp` |
| 19207 | smallweb | |
| 19208 | stub OIDC provider | tsidp's shape; "Sign in with Tailscale (dev stub)" signs in as the owner |
| 19209 | fixture sites | plain http; `http://127.0.0.1:19209/kyoto-guide.example/` (inside the stack: `http://kyoto-guide.example/`) |
| 19210 | fake NewsBlur | plain http |
| 19211, 19212 | Niwa's gemini and gopher | (natively 1965 and 7070) |
| 19224 | Hister, plain http | for agents and scripts on this machine: its MCP is `http://127.0.0.1:19224/mcp` |
| 19226 | machiya-mcp, plain http | `http://127.0.0.1:19226/mcp` |

Sign in as `owner` with the password in `$DEV_DATA/secrets/owner-password`. One sign-in covers every room; sign-out on the helper's sessions page ends it everywhere within 30 s.

**How it differs from production.** All rooms share one host name, so cookies (Hister's own `hister` cookie too) are shared by port rather than by a cookie domain, and `MACHIYA_COOKIE_DOMAIN` is empty. landing signs in like the rooms (`AUTH=hister`, fallback `tailscale`); machiya-mcp and smallweb run `AUTH=open`, so the network decides who reaches them: the ports bind to `127.0.0.1`, and on the tailnet only owner-only grants should reach them. Niwa and Konbini call each other and Kura with the owner's Hister token; Hister's MCP and every service's Hister calls use it too.

## On the tailnet

To reach it from other devices, put Tailscale Serve on the dev machine's own node, one HTTPS port per public port, and make the stack with that name. Serve then terminates TLS with the node's real certificate, so the front speaks plain http:

```sh
./dev down && rm -rf "$DEV_DATA"       # an existing stack: reset keeps the settings it was made with
./dev init --url https://<machine>.<tailnet>.ts.net --behind-serve --tailnet-users <your tailnet login> \
           --sso-cookie machiya_dev_sso
./dev up
for p in $(seq 19200 19208); do sudo tailscale serve --bg --https=$p http://127.0.0.1:$p; done
sudo tailscale serve status; sudo tailscale funnel status          # every line must say "(tailnet only)"
```

**Serve only, never Funnel**: `tailscale serve --bg --https=…` keeps a port on the tailnet. `tailscale funnel` (or `serve --funnel`) would put the dev stack on the public internet, and a tailnet policy may well allow Funnel for every node. Undo a port with `sudo tailscale serve --https=<port> off`.

`--tailnet-users` is what Niwa and Konbini admit in their Tailscale fallback, as in production. The tailnet policy must grant those ports to the owner's devices only. The ports for agents (19224, 19226) and the stand-ins (19209–19212) stay on `127.0.0.1`.

**The sign-in cookie's name.** A production stack whose sign-in cookie is set on the tailnet's whole domain (`MACHIYA_COOKIE_DOMAIN=<tailnet>.ts.net`) also sends that cookie to the dev host, and a room reads the first `machiya_sso` it finds: a browser signed in to production would bounce between the dev rooms and the helper. So the dev stack on a tailnet names its own cookie, `--sso-cookie machiya_dev_sso` (`MACHIYA_SSO_COOKIE` in the helper, the rooms and landing). That needs rooms and a landing that vendor a vaultkit reading `MACHIYA_SSO_COOKIE`: with older ones, leave it unset (the default `machiya_sso`) and use a separate browser profile for the dev stack. To switch an existing stack, set `DEV_SSO_COOKIE=machiya_dev_sso` in `$DEV_DATA/dev.env` and `./dev up`. Tokens (agents, the MCP) are not affected either way.

## Agents on the dev stack

The [machiya plugin](../plugins/machiya/) reads three settings, saved in Claude Code's settings by its installer:

```sh
MACHIYA_MCP_URL=http://127.0.0.1:19226/mcp \
HISTER_MCP_URL=http://127.0.0.1:19224/mcp \
HISTER_TOKEN_FILE=$DEV_DATA/secrets/owner-token \
  plugins/machiya/install.sh            # `install.sh check` tests the connections without changing anything
```

From another machine on the tailnet, use `https://<machine>.<tailnet>.ts.net:19206/mcp` and `…:19204/mcp` instead, with a copy of the dummy token. The token file's path is saved, never the token; the plugin's `bin/hister-headers` reads it when Claude Code connects. To work against production (a release check), run the installer again with production's URLs and token file.

## On a test VM

[`tools/dev-test`](../tools/dev-test) copies the four working trees to another machine over ssh and runs `dev init`, `up` and `check` there, then `down`:

```sh
tools/dev-test tv-debian --packages                     # Debian: Docker from Debian's packages
tools/dev-test tv-freebsd --packages --native --hister-bin /path/to/hister-freebsd-amd64
tools/dev-test tv-debian --keep && \
  tools/dev-test tv-haiku --packages --native --hister-via tv-debian      # no Hister there: borrow tv-debian's
```

`--packages` installs what the stack needs with the machine's package manager (Debian's Docker; on the BSDs the packages of [install/bsd.md](install/bsd.md); on Haiku the line below). The checks are the HTTP ones. The vault's commit hash comes out the same on every machine.

Proven (2026-10-04): `tv-debian` with Docker 16/16 and natively (a Linux Hister binary) 15/15; `tv-freebsd` natively with `--hister-via tv-debian` 15/15 (every Python service on FreeBSD 15.1, Hister on Debian through the tunnels). SearXNG is left out natively.

### A Hister borrowed from another machine (`--hister-via`)

For an OS with no Hister (Haiku today, any BSD until a binary is built). The layout, all ssh from the machine that runs `tools/dev-test`:

```
 HOST2 (a dev stack, e.g. tools/dev-test HOST2 --keep)       this machine                 HOST (--native)
 Hister on 127.0.0.1:19224  <-- ssh -L 19324:127.0.0.1:19224 --  127.0.0.1:19324  -- ssh -R 19224:127.0.0.1:19324 -->  127.0.0.1:19224
```

1. HOST2's dummy owner (`owner-password`, `owner-token`, `oidc-client-secret`, `hister.env` from its `$DEV_DATA/secrets`) is copied to `HOST:DIR/hister-secrets`.
2. On HOST, `dev init --native --remote-hister DIR/hister-secrets` uses them instead of making an owner, and `dev up` starts everything except Hister: its 127.0.0.1:19224 is the tunnel.
3. Both stacks must keep the default public base (`https://localhost`): Hister's address and the notes' URLs then match, and HOST's Kura and feed-import write the same documents HOST2's already hold.
4. The tunnels close when the run ends. Take HOST2's stack down afterwards: `ssh HOST2 /var/tmp/machiya-dev-test/machiya/compose/dev/dev --data /var/tmp/machiya-dev-test/data down`.

By hand on such a host: `./dev init --native --remote-hister <copied secrets>` and keep an `ssh -R 19224:…` tunnel to a machine running a dev stack open while it runs.

### Building Hister for a BSD

Upstream ships no BSD binaries, and Hister needs cgo (SQLite and sqlite-vec are C), so build it on the BSD itself. Hister v0.20.0 needs **Go 1.26** and its web UI is built with **Node and npm** before `go build` (the UI is embedded in the binary). On FreeBSD 15.1 (packages checked 2026-10-04: `go126` 1.26.7, `node24`, `npm-node24`, base clang 19 as the C compiler):

```sh
sudo pkg install -y go126 node24 npm-node24 git-lite
git clone --depth 1 --branch v0.20.0 https://github.com/asciimoo/hister.git && cd hister
npm ci --workspace=@hister/app --workspace=@hister/components
npm run build --workspace=@hister/app
mkdir -p server/static/app && cp -R webui/app/build/. server/static/app/
CGO_ENABLED=1 go126 build -trimpath -ldflags "-s -w" -o hister .
./hister --version                     # hister version v0.20.0
```

These are upstream's Dockerfile steps without its Linux-only static linking (`-linkmode external -extldflags -static`, `-tags netgo,osusergo`), not yet run on FreeBSD. If `npm ci` fails on FreeBSD, build the UI on Linux (the same two npm commands, or `podman run --rm -v $PWD:/app -w /app node:24 sh -c '…'`) and copy `webui/app/build/` over: it is plain HTML, CSS and JavaScript. Then `tools/dev-test tv-freebsd --packages --native --hister-bin ./hister`. OpenBSD and NetBSD: the same steps with their Go 1.26 and Node packages, unchecked.

## Without containers (`--native`)

`./dev init --native` runs every service as a process from the checkouts: the same environment as `compose.yml`, with each service name rewritten to `127.0.0.1:<port>` and each container path to `$DEV_DATA` (the `x-native` blocks there say how). The front listens on 19200–19208 as before, the services behind it on 19230–19240, Hister on 19224 and machiya-mcp on 19226, Niwa's gemini and gopher on 1965 and 7070. `./dev up` starts them in the background (pid files in `$DEV_DATA/run/`, logs in `$DEV_DATA/logs/`); `./dev down` stops them.

| Service | Natively |
|---|---|
| the rooms, landing, hister-login, machiya-mcp, smallweb, feed-import, vault-mirror, the stub OIDC provider, the fixtures, the fake NewsBlur, the front | Python 3.11+ with `markdown` and `pyyaml`, plus `git` and `openssl` |
| Hister | a `hister` binary for the OS (`HISTER_BIN=…` at init, or on `PATH`). It is Go with cgo (SQLite, sqlite-vec): build it with Go 1.26 and a C compiler. Upstream ships Linux, macOS and Windows binaries only |
| SearXNG | left out; run it yourself from a checkout with `SEARXNG_SETTINGS_PATH=compose/dev/config/searxng.yml` on `127.0.0.1:19237` if you need it |

Natively the fixture sites' `*.example` names don't resolve, so feed-import stores the reader's copy of a story instead of fetching the original (`FEED_IMPORT_MAX_TRIES=1`). Everything else, including the sign-in in a browser, behaves as in containers (tested on Linux).

### Haiku

Haiku has no Docker or Podman, so the dev stack runs there with `--native`. What is realistic today (R1/beta6, x86_64; HaikuPorts, checked 2026-10-04, not yet run on a Haiku machine):

- **Python services: yes.** Haiku packages Python 3.10–3.14 with `sqlite3` and `ssl`; only 3.14 is `python3`. `markdown` and `pyyaml` are packaged for 3.10 only (too old: the services need 3.11+), so use pip:

  ```sh
  pkgman install -y python3.14 git openssl3
  python3 -m ensurepip --altinstall && python3 -m pip install markdown pyyaml
  ```

  Python's own test suite skips its threading, socket and http.server tests on Haiku (they hang on some VMs), so the threaded stdlib servers are the thing to watch.
- **Hister: not natively, so borrow one.** HaikuPorts has a Go 1.26 fork with cgo, but Hister's search index (bleve) needs `mmap-go` and `golang.org/x/sys/unix`, which have no Haiku support upstream. Run the Haiku stack with `--hister-via` (above): another test VM keeps a dev stack up, and Haiku's 127.0.0.1:19224 is a tunnel to its Hister. The same layout passed on FreeBSD:

  ```sh
  tools/dev-test tv-debian --keep                                  # the Hister host (Docker)
  tools/dev-test tv-haiku --packages --native --hister-via tv-debian
  ssh tv-debian /var/tmp/machiya-dev-test/machiya/compose/dev/dev --data /var/tmp/machiya-dev-test/data down
  ```

  The machine running `tools/dev-test` needs ssh to both VMs; Haiku's `sshd` must allow the reverse forward (`AllowTcpForwarding`, on by default in OpenSSH).
- **SearXNG: no.** Its current releases need `curl_cffi`, which has no Haiku build. It is optional here anyway.
- `sshd` runs by default (set a password with `passwd` first), so `tools/dev-test` can reach it once the machine exists. Haiku is single-user: everything runs as `user`, home is `/boot/home`.
