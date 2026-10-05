# Installing Kura, Niwa and Konbini on the BSDs

Native installs, packages only, no ports. Covers FreeBSD 14/15, NetBSD 10 and OpenBSD 7.7–7.9. The rc.d scripts are in [`contrib/rc.d/`](../../contrib/rc.d/).

**Tested on** (2026-10-04/05, clean test VMs, amd64): FreeBSD 15.1 (Kura as an rc.d service; all three rooms natively), NetBSD 11.0 and OpenBSD 7.9 (Kura and Niwa as rc.d services), with this guide. OpenBSD's Python has no `hashlib.scrypt`: the apps start there from vaultkit v0.21.0 (Kura 0.7.0, Niwa 0.5.0, Konbini 0.12.0), and only the identity file's password features refuse. The rooms' own BSD Quickstarts and the [dev stack](../dev-stack.md) ran on all three.

**Needs** apps that read `<APP>_BIND` and an env file (`<APP>_ENV_FILE` or `--env-file`, from vaultkit's `envfile`):

| App | Settings prefix | Env file variable |
|---|---|---|
| Kura | `KURA_` | `KURA_ENV_FILE` |
| Niwa | `NIWA_` | `NIWA_ENV_FILE` |
| Konbini | `KANBAN_` (the old name stayed) | `KANBAN_ENV_FILE` |

In the examples, `<app>` is `kura`, `niwa` or `konbini`, and `<tailnet>` is your tailnet's name (`<tailnet>.ts.net`).

## 1. Why every app listens on 127.0.0.1

The apps trust the `Tailscale-User-Login` header to know the owner. `tailscale serve` sets that header after Tailscale has checked who is connecting. On a public address, anyone can send `Tailscale-User-Login: you@example.com` and read the whole vault. So:

- set `<APP>_BIND=127.0.0.1`, and put `tailscale serve` in front (§8);
- block the app ports from outside in the firewall as well (§9);
- after installing, check from another machine that `curl -H 'Tailscale-User-Login: x' http://<public-ip>:8080/` gets no answer (on OpenBSD: `netstat -an | grep LISTEN` shows only `127.0.0.1.8080`).

`<APP>_AUTH=open` (no identity check at all) is only for a machine you alone can reach, bound to 127.0.0.1.

## 2. Packages

| | FreeBSD | NetBSD | OpenBSD |
|---|---|---|---|
| install | `pkg install -y python312 py312-sqlite3 py312-pip py312-pyyaml git-lite curl` | `pkg_add python313 py313-pip py313-yaml git-base curl daemonize` with `PKG_PATH` set (below) | `pkg_add python%3 py3-pip py3-yaml git curl` |
| Python | `/usr/local/bin/python3.12` (no `python3` name) | `/usr/pkg/bin/python3.13` (no `python3` name) | `/usr/local/bin/python3` |
| note | **`py312-sqlite3` is separate**: FreeBSD splits `sqlite3` out of Python | sqlite3 is inside `python313` | sqlite3 is inside the Python package |

- **A clean FreeBSD or NetBSD has no `python3` name**, only `python3.12` or `python3.13`. Make one (the Quickstarts do): `sudo ln -sf /usr/local/bin/python3.12 /usr/local/bin/python3` on FreeBSD, `sudo ln -sf /usr/pkg/bin/python3.13 /usr/pkg/bin/python3` on NetBSD (then `/usr/pkg/bin` must be on the service's PATH; the rc.d scripts set it).
- **A clean NetBSD has no `pkgin`** (it is a separate package), so install with `pkg_add` and point it at the binary repository first: `sudo env PKG_PATH="https://cdn.NetBSD.org/pub/pkgsrc/packages/NetBSD/$(uname -p)/$(uname -r | cut -d_ -f1)/All" pkg_add python313 py313-pip py313-yaml git-base curl daemonize`. If you prefer `pkgin`, add it the same way (`pkg_add pkgin`) and configure its repository first.
- A clean FreeBSD, NetBSD or Debian also has no `git` or `curl`: they are in the lists above. Run the privileged commands as root (`su -`), or through `sudo` or `doas` once one is set up: a fresh FreeBSD or NetBSD has no `sudo` (install it as root: `pkg install sudo`, or `pkg_add sudo` with `PKG_PATH` as above, then allow your user with `visudo`), and a fresh OpenBSD has `doas` but no `/etc/doas.conf` (as root: `echo 'permit persist :wheel' > /etc/doas.conf`).
- The apps need `markdown` **3.11 or later** (vaultkit refuses to start with an older one: 3.7–3.10 on Python 3.13 can be driven out of memory by a single note). The BSDs' packages ship 3.7–3.10, so install it with pip as root after the packages, once the `python3` link below exists: `python3 -m pip install 'markdown>=3.11'`. If pip refuses to touch the system Python, make a venv (`python3 -m venv --system-site-packages /usr/local/lib/machiya-venv`, then its `bin/pip install 'markdown>=3.11'`) and use its `bin/python3` wherever this guide says `python3`.
- They need `pyyaml` 6 (the package is fine), and nothing else from pip.
- Niwa and Konbini also call the `openssl` CLI. It's in base everywhere; on OpenBSD it's LibreSSL.
- FreeBSD's quarterly branch moves the default Python now and then. After an upgrade, `pkg install` the matching `py3NN-*` packages again.

Check that SQLite has FTS5 (Kura's search needs it). The packages build it in:

```sh
python3 -c 'import sqlite3; sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)"); print("fts5 ok")'
```

Without the `python3` link above, use `python3.12` on FreeBSD and `python3.13` on NetBSD.

## 3. A user and its directories

Each app runs as its own unprivileged user. Its home is its data directory: the clone, the sqlite file, `~/.ssh`.

```sh
# FreeBSD
pw useradd kura -d /var/db/kura -s /usr/sbin/nologin -c "Machiya Kura"
# NetBSD
useradd -g =uid -d /var/db/kura -s /sbin/nologin -c "Machiya Kura" kura    # -g =uid: a kura group, not "users"
# OpenBSD (underscore names for daemons; the daemon login class)
useradd -d /var/db/kura -s /sbin/nologin -L daemon -c "Machiya Kura" _kura

install -d -o kura -g kura -m 0700 /var/db/kura        # OpenBSD: -o _kura -g _kura
```

`useradd` prints a harmless warning that the home directory doesn't exist and `-m` wasn't given: the `install -d` right after it creates the directory.

**The service user must own its clone.** git refuses a repository owned by someone else ("dubious ownership").

## 4. The code

Clone a release tag of the app, owned by root and read-only for the service:

```sh
git clone --branch vX.Y.Z --depth 1 https://github.com/machiya-kobo/kura.git /usr/local/share/kura-src   # NetBSD: /usr/pkg/share/…
ln -s /usr/local/share/kura-src/app /usr/local/share/kura       # the rc.d scripts run /usr/local/share/<app>/<app>.py
cd /usr/local/share/kura && python3 -m vaultkit.verify          # the vendored vaultkit is unedited
```

The rc.d scripts expect the app files directly in `/usr/local/share/<app>/` (NetBSD: `/usr/pkg/share/<app>/`), which is what the link gives. Or set `<app>_code` (FreeBSD and NetBSD) or the flags (OpenBSD) to point at `.../app`.

## 5. The vault

Every app keeps its own clone under its data directory.

- **Kura** only reads. Give it a read-only credential:
  - **https:** a read-only forge user with a token:
    ```
    KURA_REPO_URL=https://git.example.net/owner/vault.git
    KURA_REPO_USER=vault-reader
    KURA_REPO_TOKEN_FILE=/var/db/kura/vault-token     # 0600, owned by kura
    ```
  - **ssh:** a read-only deploy key: `KURA_REPO_URL=ssh://git@git.example.net/owner/vault.git` and `GIT_SSH_COMMAND` as below.
- **Niwa and Konbini** write (garden fields, board frontmatter), so each gets a **deploy key with write access**:

  ```sh
  su -m niwa -c 'ssh-keygen -t ed25519 -N "" -f /var/db/niwa/.ssh/deploy_key'     # add the .pub as a deploy key
  su -m niwa -c 'ssh-keyscan git.example.net >> /var/db/niwa/.ssh/known_hosts'
  ```

  Then in the env file:
  ```
  GIT_SSH_COMMAND=ssh -i /var/db/niwa/.ssh/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/db/niwa/.ssh/known_hosts
  ```

  - **Niwa** clones by itself at first start (`NIWA_REPO_URL`).
  - **Konbini** expects an existing clone at `KANBAN_REPO`. Clone it once as the service user: `su -m konbini -c 'git clone ssh://git@git.example.net/owner/vault.git /var/db/konbini/repo'`.

## 6. The env file

Settings are `KEY=VALUE` lines in `/usr/local/etc/<app>.env` (FreeBSD) or `/etc/<app>.env` (NetBSD, OpenBSD). Mode `0640`, `root:<user>`.

- `#` starts a comment line, and ` # …` after an unquoted value is a comment too.
- Quotes are optional and taken literally, and there's no `$` expansion.
- A variable already in the environment wins.

**Kura:**
```
KURA_BIND=127.0.0.1
KURA_PORT=8080
KURA_USERS=you@example.com                  # the Tailscale login(s) allowed in
KURA_REPO_URL=https://git.example.net/owner/vault.git
KURA_REPO_USER=vault-reader
KURA_REPO_TOKEN_FILE=/var/db/kura/vault-token
KURA_REPO_DIR=/var/db/kura/repo
KURA_REPO_SUBDIR=personal
KURA_DB=/var/db/kura/kura.sqlite3
KURA_PUBLIC_URL=https://kura.<tailnet>.ts.net
TZ=UTC
```

**Niwa** (web, gemini 1965, gopher 7070, all on `NIWA_BIND`; its web port defaults to 8080 like Kura's, so give one of them another):
```
NIWA_BIND=127.0.0.1
NIWA_PORT=8082
NIWA_REPO_SUBDIR=personal
NIWA_USERS=you@example.com
NIWA_REPO_URL=ssh://git@git.example.net/owner/vault.git
NIWA_REPO_DIR=/var/db/niwa/repo
NIWA_DB=/var/db/niwa/niwa.sqlite3             # the gemini certificate is kept next to it
NIWA_HOST=niwa.<tailnet>.ts.net               # the name in the gemini certificate and gopher menus
GIT_SSH_COMMAND=ssh -i /var/db/niwa/.ssh/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/db/niwa/.ssh/known_hosts
```

**Konbini** (one listener, `KANBAN_BIND:KANBAN_TAILNET_PORT`, default 8081):
```
KANBAN_BIND=127.0.0.1
KANBAN_AUTH=tailscale                       # the default; open only on a LAN without tailscale serve (§1)
KANBAN_TAILNET_USERS=you@example.com
KANBAN_REPO=/var/db/konbini/repo
KANBAN_DB=/var/db/konbini/kanban.sqlite3
GIT_SSH_COMMAND=ssh -i /var/db/konbini/.ssh/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/db/konbini/.ssh/known_hosts
# optional
KANBAN_OBSIDIAN_VAULT=personal              # adds "Edit in Obsidian" links (the vault's name in Obsidian)
TZ=UTC                                      # days on the board and the review; UTC when unset
```

**Sister apps are optional URLs**; leave them out and the features that need them are off:
- `*_KONBINI_URL`, `*_KURA_URL`, `*_NIWA_URL`;
- `MACHIYA_ROOMS` (the Rooms switcher);
- the Hister settings. A BSD install normally runs without Hister, because its `hister` CLI has no BSD build.

## 7. The service (rc.d)

Copy the script for your BSD from `contrib/rc.d/<bsd>/<app>`, mode `0555`.

| | FreeBSD | NetBSD | OpenBSD |
|---|---|---|---|
| install to | `/usr/local/etc/rc.d/<app>` | `/etc/rc.d/<app>` | `/etc/rc.d/<app>` |
| enable | `sysrc <app>_enable=YES` | `<app>=YES` in `/etc/rc.conf` | `rcctl enable <app>` |
| start | `service <app> start` | `service <app> start` | `rcctl start <app>` |
| runs as | `daemon(8) -u <app>`, restarted if it dies | `daemonize -u <app>` | `daemon_user=_<app>` |
| log | syslog, tag `<app>` | `/var/log/<app>/<app>.log` (not rotated: daemonize keeps it open; restart the service after rotating) | syslog `daemon.info` → `/var/log/daemon` |
| settings | `<app>_config`, `_dir`, `_code`, `_python`, `_runas` in rc.conf | the same | `rcctl set <app> flags …` |

The scripts pass `--env-file` to the app. They don't use rc.subr's own environment support: FreeBSD's `<name>_env_file` sources the file as shell, which expands `$` and breaks on values with spaces such as `GIT_SSH_COMMAND`.

NetBSD and OpenBSD have no supervisor in base. Restart from cron (`crontab -e` as root):
```
*/5 * * * * rcctl check kura >/dev/null || rcctl start kura          # OpenBSD
*/5 * * * * service kura status >/dev/null || service kura start      # NetBSD
```

## 8. Tailscale in front

Serve each app from loopback with HTTPS on the tailnet:

```sh
tailscale serve --bg --https=443 http://127.0.0.1:8080                              # Kura (Niwa: 8082): node name
tailscale serve --bg --service=svc:<service> --https=443 http://127.0.0.1:8080       # or as a Tailscale Service
```

- A Service needs its definition, an autoApprover and a grant in the tailnet policy. Give it a name that isn't already served elsewhere. `tailscale serve status` doesn't list a Service's serve config (it prints "No serve config" while the Service works): verify with `curl https://<service>.<tailnet>.ts.net/api/status` from another device.
- **Niwa's gemini and gopher** go through serve as plain TCP. This also maps gopher's port 70 without root: ports below 1024 need root on NetBSD and OpenBSD.
  ```sh
  tailscale serve --bg --tcp=1965 tcp://127.0.0.1:1965
  tailscale serve --bg --tcp=70 tcp://127.0.0.1:7070
  ```
- Allow only the owner in the tailnet grant. The app's `*_USERS` is the second gate.

## 9. Firewall (the second layer)

Block the app ports on the public interface even though they're bound to loopback:

```
# OpenBSD / FreeBSD pf.conf
block return in quick on egress proto tcp to port { 8080 8081 8082 1965 7070 }
```

For NetBSD's npf, add a `block in final … port { 8080, 8081, 8082, 1965, 7070 }` rule to the external interface's group. On FreeBSD with ipfw, deny those ports on the external interface.

## 10. Monitoring, upgrades, removal

- **Health:** Kura and Niwa answer `GET /api/status` without the owner gate, and Konbini answers `GET /healthz`. Probe them through the tailnet URL. It reports `ready` and an `error` that's `null` unless something is broken.
- **Upgrade:** as root (the checkout is root's; git refuses another owner's), `git fetch --tags && git checkout <tag>` in the code directory, then `python3 -m vaultkit.verify`, then restart the service. Package upgrades: `pkg upgrade` (FreeBSD), `pkg_add -u` (NetBSD with `PKG_PATH`, or `pkgin upgrade` if you installed pkgin), `pkg_add -u` (OpenBSD).
- **Remove:**
  - stop and disable the service, and delete its rc.d script;
  - remove the `tailscale serve` entries;
  - delete `/var/db/<app>`, the env file and the user.
  - The vault clone holds your notes, so delete it deliberately.

## 11. Identity (optional)

Skip this for one owner on the tailnet: `*_USERS` (§6) is enough. For other people, agents, sign-in without Tailscale or Shiori devices, add the identity file ([identity.md](../identity.md)). Every package Python above is 3.11 or newer, which the CLI needs.

```sh
mkdir -p /usr/local/etc/machiya                                   # NetBSD, OpenBSD: /etc/machiya
cd /usr/local/share/kura/app && python3 -m vaultkit.identity --file /usr/local/etc/machiya/identity.toml setup --tailscale you@example.com
```

- Each app runs as its own user, so give the file and its `session.key` a group the three users share, mode `0640`: `chgrp machiya` and `chmod 0640` both (make the group and add `kura`, `niwa` and `konbini` to it; on OpenBSD `_kura` and so on). The CLI keeps the mode and owner when it rewrites the file; a new key needs them again.
- Add the printed lines to each app's env file (`MACHIYA_IDENTITY_FILE=…`, and `<APP>_SIGNIN=1` for the built-in sign-in), then restart it. With the file, `*_USERS` is unused.
- Keep `<APP>_BIND=127.0.0.1` (§1): in `tailscale` or `header` mode the apps refuse any other address unless `<APP>_BIND_BEHIND_PROXY=1`.
- The CLI comes with every app (its vendored `vaultkit`), so `cd` into any app's directory as above, or run it from a checkout of the Machiya repository.

## Niwa and Konbini extras

- **Niwa's gemini certificate** is made once with `openssl req -x509 -newkey ec …`. On OpenBSD that's LibreSSL, which makes it fine (7.9). If it fails, create `gemini.crt`/`gemini.key` next to `NIWA_DB` by hand and Niwa keeps them.
- **Link rot and the `hister` CLI** (Niwa, Konbini) are optional. Without `*_HISTER_URL`, dead links still get Wayback copies. Building `hister` needs Go and cgo.
- **Konbini's blog kit** (`KANBAN_BLOG`) is optional. Without it, the posts pages are empty.
