# The dev stack

A standing Machiya on one development machine: every service a full deployment runs, with Hister's users as the sign-in, on **synthetic data only**. Agents and people develop and test against it by default, and touch a real deployment only to release. The same stack, the same seed and the same checks run on the test VMs, in containers or, where there are none (the BSDs, Haiku), as plain processes.

Everything lives in [`compose/dev/`](../compose/dev/): `compose.yml`, the `dev` script that drives it, the `seed/`, and the small stand-ins for the outside world.

## The synthetic-data rule

**Never put a real note, page, feed, login or token into the dev stack.** What it holds is made from the repository, the same on every machine:

| Data | From | Notes |
|---|---|---|
| the vault | [`sample-vault/`](../sample-vault/) plus [`seed/vault/`](../compose/dev/seed/vault/) | one commit with a fixed date, so its hash is the same everywhere (`d09e5c4…` today). Three vaults: `personal` (the default; every board column, a published garden, tags), `team` (shared) and `work` (**private**: never pushed to Hister; the word *quillwort* is only there, so a search proves where it looked) |
| saved pages | [`seed/site/`](../compose/dev/seed/site/) through [`seed/hister.json`](../compose/dev/seed/hister.json) | invented `*.example` sites served by `fixtures`; 8 pages with labels and fixed times, the collections `@pages`, `@notes`, `@code`, `@workshop`, `@travel`, and history entries (one pinned) |
| a feed reader | [`seed/newsblur.json`](../compose/dev/seed/newsblur.json), served by `fake_newsblur.py` | read and starred stories pointing at the fixture sites; feed-import imports them like the real NewsBlur |
| code forges | [`stack/code-import/dev/seed/`](../stack/code-import/dev/seed/), served by `fake_forgejo.py` and `fake_github.py` | invented repos of a user `lantern` and the orgs `workshop`/`workshop-kobo`: issues, PRs, releases, and a fork, a mirror, an archived repo, twins and an excluded name; [code-import](services/code-import.md) puts them into Hister as `metadata.source:code` (the `@code` collection; `@pages` leaves them out) |
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

`$DEV_DATA` is `compose/dev/data` unless `--data DIR` or `MACHIYA_DEV_DATA` says otherwise; the first stack made outside the default is remembered in `compose/dev/.data-dir`. Use a disk-backed folder (`/var/tmp/<you>/…`), never `/tmp`. The compose project is `machiya-dev` unless `--project` says otherwise (below). A podman API socket is used for compose (`DOCKER_HOST`, the user's `podman.socket`, or one started under `$DEV_DATA`). After a reboot, `./dev up` again.

**A second stack beside a standing one** (an agent testing a branch while the shared stack keeps running): give it its own project, another loopback address and that address as its base, and its own checkouts:

```sh
./dev --data /var/tmp/<you>/dev-data init --project machiya-dev-<you> --bind 127.0.0.2 --url https://127.0.0.2 \
      --siblings /var/tmp/<you>
./dev --data /var/tmp/<you>/dev-data up && ./dev --data /var/tmp/<you>/dev-data check --browser
```

The ports are the same numbers on 127.0.0.2, and the checks and `status` use that address. The containers, images and network are the project's own, so the standing stack is never touched.

## Ports

Everything is on `127.0.0.1` (`--bind` changes it). The public ports go through `front`, which does what Tailscale Serve does in a deployment: TLS (a throwaway CA, `$DEV_DATA/certs/ca.crt`, because the sign-in helper only sends a browser back to an `https` address), and on Hister's port it sends `/machiya/` and `/api/oauth/callback` to the hister-login helper.

| Port | Service | Notes |
|---|---|---|
| 19200 | landing | `/` the launcher, `/status`; `AUTH=hister`, fallback `tailscale` |
| 19201 | Kura | `AUTH=hister`, fallback **none** |
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
| 19213, 19214 | fake Forgejo, fake GitHub | plain http; code-import reads them with the dummy tokens in `$DEV_DATA/secrets/` (`forgejo-token`, `github-*-token`) |
| 19224 | Hister, plain http | for agents and scripts on this machine: its MCP is `http://127.0.0.1:19224/mcp` |
| 19226 | machiya-mcp, plain http | `http://127.0.0.1:19226/mcp` |

Sign in as `owner` with the password in `$DEV_DATA/secrets/owner-password`. One sign-in covers every room; sign-out on the helper's sessions page ends it everywhere within 30 s.

**Automatic sign-in.** A stack made since hister-login 0.2.0 sets `MACHIYA_SIGNIN_PROVIDER=oidc` (`--signin-provider oidc`, the default; `--signin-provider ''` for the page), as production does. A room or landing that needs a sign-in then goes through the stub OIDC provider, which plays tsidp and signs the owner in at once: no page and no click. After a Sign Out (a room's, or the helper's) the helper's page shows until the next sign-in. An older `dev.env` has no `DEV_SIGNIN_PROVIDER` and keeps the page.

**One cookie per room** (hister-login 0.3.0, vaultkit 0.22; [identity.md](identity.md#one-cookie-per-room)). Each room and landing keeps its own host-only `__Host-machiya_sso_<room>`, made from a one-time code; the helper keeps `__Host-machiya_sso`. On the dev stack every room is the same host on different ports, and cookies ignore ports, which is why the names carry the room. `--auth-legacy none` at init makes the stack run **after the switch** (`HISTER_LOGIN_LEGACY=none`): the rooms then take only their own cookie, the apps' ids and room tokens, never the shared `machiya_sso` or Hister's raw token. Without it (an older `dev.env`, or a stack whose rooms vendor an older vaultkit) the helper keeps the old ways working.

**How it differs from a deployment.** All rooms share one host name, so cookies (Hister's own `hister` cookie too) are shared by port rather than by a cookie domain, and `MACHIYA_COOKIE_DOMAIN` is empty. landing signs in like the rooms (`AUTH=hister`, fallback `tailscale`); machiya-mcp and smallweb run `AUTH=open`, so the network decides who reaches them: the ports bind to `127.0.0.1`, and on the tailnet only owner-only grants should reach them. machiya-mcp, Niwa (to Konbini) and landing call the rooms with the stack's **room token** (`$DEV_DATA/secrets/room-token`, made at init and registered with the helper at `up`, scoped to the rooms, machiya-mcp and smallweb); every service's Hister calls use the owner's Hister token, which goes to Hister only.

**The room-session checks** (`check`, at the HTTP level):
- one trip: the helper sends the code to Kura's `/machiya/callback`, and Kura sets its own cookie, host-only, with no `Domain`;
- the code works once;
- the code is bound: refused at another room, with another browser's nonce, and without a state cookie;
- Kura's session opens no other room;
- with `--auth-legacy none`, Hister's raw token opens no room (`401 legacy-off`) while the room token opens every one;
- Sign Out in Kura ends the browser's Niwa session within 30 s.

With `--browser`, it also checks that each room's cookie in a real browser is `__Host-…_<room>` on the host alone, Secure, HttpOnly and Lax, and that no shared `machiya_sso` exists after the switch.

**The settings checks** (`check --browser`, [contracts/prefs.md](contracts/prefs.md)):
- screenshots of landing's Shared section (desktop and phone, light and dark);
- Theme and Appearance changed in Kura's Settings send one key each, and show in Konbini, Niwa and landing on the next load, and in a second browser (another device);
- Use This Device's Size stays in its browser;
- with hister-login stopped (the checks stop and start the `<project>-hister-login-1` container), Niwa, Konbini and landing keep working on their cookies through the Tailscale fallback, and a change made meanwhile waits and lands in the account when the helper is back.

They need rooms that vendor vaultkit 0.21 and forward `/api/prefs` (`histerauth.forward_prefs`). Older rooms fail the cross-device checks.

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

`--tailnet-users` is what Niwa and Konbini admit in their Tailscale fallback. The tailnet policy must grant those ports to the owner's devices only. The ports for agents (19224, 19226) and the stand-ins (19209–19212) stay on `127.0.0.1`.

**The sign-in cookie's name.** Until production's switch (`HISTER_LOGIN_LEGACY=none`), its sign-in cookie is set on the tailnet's whole domain (`MACHIYA_COOKIE_DOMAIN=<tailnet>.ts.net`) and so also reaches the dev host, where a room with legacy reading still on would pick it up: a browser signed in to that stack would bounce between the dev rooms and the helper. (After the switch production sets no shared cookie, and the rooms read only their own.) So the dev stack on a tailnet names its own cookie, `--sso-cookie machiya_dev_sso` (`MACHIYA_SSO_COOKIE` in the helper, the rooms and landing). That needs rooms and a landing that vendor a vaultkit reading `MACHIYA_SSO_COOKIE`: with older ones, leave it unset (the default `machiya_sso`) and use a separate browser profile for the dev stack. To switch an existing stack, set `DEV_SSO_COOKIE=machiya_dev_sso` in `$DEV_DATA/dev.env` and `./dev up`. Tokens (agents, the MCP) are not affected either way.

## Agents on the dev stack

The [machiya plugin](../plugins/machiya/) reads one setting, saved in Claude Code's settings by its installer, and (plugin 0.4.0) the path of a room token file for a machine with no Tailscale login:

```sh
MACHIYA_MCP_URL=http://127.0.0.1:19226/mcp MACHIYA_TOKEN_FILE=$DEV_DATA/secrets/room-token \
  plugins/machiya/install.sh            # `install.sh check` tests the connection without changing anything
```

From another machine on the tailnet, use `https://<machine>.<tailnet>.ts.net:19206/mcp` instead. Saved pages come from machiya-mcp's `pages_search` and `pages_read`; since plugin 0.3.0 the plugin connects no Hister MCP and its installer denies Hister's MCP tools, so agents need no Hister token (vault notes and code documents stay out of AI context, on the dev stack too). To work against a real deployment (a release check), run the installer again with its URL.

## On a test VM

[`tools/dev-test`](../tools/dev-test) copies the four working trees to another machine over ssh and runs `dev init`, `up` and `check` there, then `down`:

```sh
tools/dev-test tv-debian --packages                     # Debian: Docker from Debian's packages
tools/dev-test tv-freebsd --packages --native --hister-bin /path/to/hister-freebsd-amd64
tools/dev-test tv-debian --keep && \
  tools/dev-test tv-haiku --packages --native --hister-via tv-debian      # no Hister there: borrow tv-debian's
```

`--packages` installs what the stack needs with the machine's package manager (Debian's Docker; on the BSDs the packages of [install/bsd.md](install/bsd.md) and a venv, `DIR/venv` with `--system-site-packages` and pip's `markdown>=3.11`, which every run puts first on `PATH` when it exists: every BSD's markdown package is 3.10 and OpenBSD's pip refuses the system Python; on Haiku the line below). The checks are the HTTP ones. The vault's commit hash comes out the same on every machine.

Proven (2026-10-04/05, the counts have since grown to 17 Docker and 16 native checks): `tv-debian` with Docker 17/17; `tv-arm64` (Debian 13 aarch64) with Docker 17/17 and natively with upstream's linux_arm64 Hister binary 16/16; `tv-freebsd` natively with `--hister-via tv-debian` 15/15 (every Python service on FreeBSD 15.1, Hister on Debian through the tunnels). Again on 2026-10-05 with vaultkit 0.22 (markdown 3.11): `tv-debian` with Docker 23/23; natively `tv-freebsd` 22/22 with `--hister-bin`, and `tv-netbsd` and `tv-openbsd` 22/22 with `--hister-via tv-debian` (FreeBSD and NetBSD with markdown from the system pip, OpenBSD with it preinstalled; the venv step above replaced the system pip after that run). SearXNG is left out natively.

### A Hister borrowed from another machine (`--hister-via`)

For an OS with no Hister: Haiku, and NetBSD and OpenBSD until a binary is built there (FreeBSD's builds, below). The layout, all ssh from the machine that runs `tools/dev-test`:

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

These are upstream's Dockerfile steps without its Linux-only static linking (`-linkmode external -extldflags -static`, `-tags netgo,osusergo`); proven on FreeBSD 15.1 (2026-10-05, below). If `npm ci` fails on FreeBSD, build the UI on Linux (the same two npm commands, or `podman run --rm -v $PWD:/app -w /app node:24 sh -c '…'`) and copy `webui/app/build/` over: it is plain HTML, CSS and JavaScript. Then `tools/dev-test tv-freebsd --packages --native --hister-bin ./hister`. OpenBSD and NetBSD: the same steps with their Go 1.26 and Node packages, unchecked.

## Without containers (`--native`)

`./dev init --native` runs every service as a process from the checkouts: the same environment as `compose.yml`, with each service name rewritten to `127.0.0.1:<port>` and each container path to `$DEV_DATA` (the `x-native` blocks there say how). The front listens on 19200–19208 as before, the services behind it on 19230–19240, Hister on 19224 and machiya-mcp on 19226, Niwa's gemini and gopher on 1965 and 7070. `./dev up` starts them in the background (pid files in `$DEV_DATA/run/`, logs in `$DEV_DATA/logs/`); `./dev down` stops them.

| Service | Natively |
|---|---|
| the rooms, landing, hister-login, machiya-mcp, smallweb, feed-import, code-import, vault-mirror, the stub OIDC provider, the fixtures, the fake NewsBlur, Forgejo and GitHub, the front | Python 3.11+ with `markdown` 3.11+ and `pyyaml`, plus `git` and `openssl` |
| Hister | a `hister` binary for the OS (`HISTER_BIN=…` at init, or on `PATH`). It is Go with cgo (SQLite, sqlite-vec): build it with Go 1.26 and a C compiler. Upstream ships Linux (amd64, arm64), macOS and Windows binaries; the Linux ones work for `--hister-bin` (tested on arm64) |
| SearXNG | left out; run it yourself from a checkout with `SEARXNG_SETTINGS_PATH=compose/dev/config/searxng.yml` on `127.0.0.1:19237` if you need it |

Natively the fixture sites' `*.example` names don't resolve, so feed-import stores the reader's copy of a story instead of fetching the original (`FEED_IMPORT_MAX_TRIES=1`). Everything else, including the sign-in in a browser, behaves as in containers (tested on Linux).

### Haiku

Haiku has no Docker or Podman, so the dev stack runs there with `--native`. What works today (R1/beta6, x86_64):

- **Python services: yes.** Haiku packages Python 3.10–3.14 with `sqlite3` and `ssl`; only 3.14 is `python3`. `markdown` and `pyyaml` are packaged for 3.10 only (too old: the services need 3.11+), so use pip:

  ```sh
  pkgman install -y python3.14 git openssl3
  python3 -m ensurepip --altinstall && python3 -m pip install 'markdown>=3.11' pyyaml tzdata
  ```

  **Proven 2026-10-05** on `tv-haiku` (R1/beta6 x86_64), and again on a fresh VM in the docs review (hrev59866): `tools/dev-test tv-haiku --packages --native --hister-via tv-debian` = 16/16 (SearXNG skipped). `pkgman install` is a no-op on a fresh R1/beta6 (all preinstalled); pip warns about running as root, which is harmless (Haiku's `user` is uid 0), and a plain `python3 -m venv` works too. Haiku quirks:
  - **no tz database**: Niwa and Konbini stop with `ZoneInfoNotFoundError: … UTC` unless `tzdata` is installed (it is, above);
  - sshd reads `AuthorizedKeysFile config/settings/ssh/authorized_keys` (relative to /boot/home), not `~/.ssh`; `ssh-keygen -A` + a reboot makes the host keys; the login user is `user` (uid 0);
  - python3.14, git, curl, openssl3 and pkgman are preinstalled;
  - **revert the VM between runs**: a second run on the same VM can hit `Address already in use` (stale sockets);

  Python's own test suite skips its threading, socket and http.server tests on Haiku (they hang on some VMs), so the threaded stdlib servers are the thing to watch.
- **FreeBSD: a native Hister builds** (proven 2026-10-05: v0.20.0 with go126 1.26.7 + node24, the recipe above, 16/16 with `--hister-bin`).
- **Hister on Haiku: not natively, so borrow one.** HaikuPorts has a Go 1.26 fork with cgo, but Hister's search index (bleve) needs `mmap-go` and `golang.org/x/sys/unix`, which have no Haiku support upstream. Run the Haiku stack with `--hister-via` (above): another test VM keeps a dev stack up, and Haiku's 127.0.0.1:19224 is a tunnel to its Hister. The same layout passed on FreeBSD:

  ```sh
  tools/dev-test tv-debian --keep                                  # the Hister host (Docker)
  tools/dev-test tv-haiku --packages --native --hister-via tv-debian
  ssh tv-debian /var/tmp/machiya-dev-test/machiya/compose/dev/dev --data /var/tmp/machiya-dev-test/data down
  ```

  The machine running `tools/dev-test` needs ssh to both VMs; Haiku's `sshd` must allow the reverse forward (`AllowTcpForwarding`, on by default in OpenSSH).
- **SearXNG: no.** Its current releases need `curl_cffi`, which has no Haiku build. It is optional here anyway.
- `sshd` runs by default (set a password with `passwd` first), so `tools/dev-test` can reach it once the machine exists. Haiku is single-user: everything runs as `user`, home is `/boot/home`.
