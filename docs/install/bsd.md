# Installing Kura, Niwa and Konbini on the BSDs

Native installs, packages only, no ports. Covers FreeBSD 14/15, NetBSD 10/11 and OpenBSD 7.7–7.9. The rc.d scripts are in [`contrib/rc.d/`](../../contrib/rc.d/).

**Tested on** FreeBSD 15.1, NetBSD 11.0 and OpenBSD 7.9 (clean amd64 VMs): this guide as written, from the packages and the venv (§2) to Kura, Niwa and Konbini as rc.d services on 127.0.0.1 behind the owner gate. The [dev stack](../dev-stack.md) passes on all three too.

- You need Kura 0.9.0, Niwa 0.6.1 and Konbini 0.13.0 or later (vaultkit v0.22, which needs `markdown` 3.11).
- OpenBSD's Python has no `hashlib.scrypt`. The apps run there, but the identity file's password features refuse.

**The apps** read `<APP>_BIND` and an env file (`<APP>_ENV_FILE` or `--env-file`, from vaultkit's `envfile`):

| App | Settings prefix | Env file variable |
|---|---|---|
| Kura | `KURA_` | `KURA_ENV_FILE` |
| Niwa | `NIWA_` | `NIWA_ENV_FILE` |
| Konbini | `KANBAN_` (the old name stayed) | `KANBAN_ENV_FILE` |

In the examples, `<app>` is `kura`, `niwa` or `konbini`, and `<tailnet>` is your tailnet's name (`<tailnet>.ts.net`).

## 1. Why every app listens on 127.0.0.1

The apps trust the `Tailscale-User-Login` header to know it's you. `tailscale serve` sets that header after Tailscale has checked who is connecting. On a public address, anyone can send `Tailscale-User-Login: you@example.com` and read the whole vault. So:

- set `<APP>_BIND=127.0.0.1`, and put `tailscale serve` in front (§8);
- block the app ports from outside in the firewall as well (§9);
- after installing, check from another machine that `curl -H 'Tailscale-User-Login: x' http://<public-ip>:8080/` gets no answer. On OpenBSD, `netstat -an | grep LISTEN` should show only `127.0.0.1.8080`.

`<APP>_AUTH=open` (no identity check at all) is only for a machine you alone can reach, bound to 127.0.0.1.

## 2. Packages

| | FreeBSD | NetBSD | OpenBSD |
|---|---|---|---|
| install | `pkg install -y python312 py312-sqlite3 py312-pyyaml git-lite curl` | `pkg_add python313 py313-yaml git-base curl daemonize` with `PKG_PATH` set (below) | `pkg_add python%3 py3-yaml git curl` |
| Python | `/usr/local/bin/python3.12` (no `python3` name) | `/usr/pkg/bin/python3.13` (no `python3` name) | `/usr/local/bin/python3` |
| note | **`py312-sqlite3` is separate**: FreeBSD splits `sqlite3` out of Python | sqlite3 is inside `python313` | sqlite3 is inside the Python package |

- **A clean FreeBSD or NetBSD has no `python3` name**, only `python3.12` or `python3.13`. Make one (the Quickstarts do):
  - FreeBSD: `sudo ln -sf /usr/local/bin/python3.12 /usr/local/bin/python3`
  - NetBSD: `sudo ln -sf /usr/pkg/bin/python3.13 /usr/pkg/bin/python3`. `/usr/pkg/bin` must then be on the service's PATH; the rc.d scripts set it.
- **A clean NetBSD has no `pkgin`** (it's a separate package). Install with `pkg_add`, pointed at the binary repository: `sudo env PKG_PATH="https://cdn.NetBSD.org/pub/pkgsrc/packages/NetBSD/$(uname -p)/$(uname -r | cut -d_ -f1)/All" pkg_add python313 py313-yaml git-base curl daemonize`. If you prefer `pkgin`, add it the same way (`pkg_add pkgin`) and configure its repository first.
- A clean FreeBSD or NetBSD also has no `git` or `curl`. They're in the lists above.
- **Run the privileged commands as root** (`su -`), or through `sudo` or `doas` once one is set up.
  - A fresh FreeBSD or NetBSD has no `sudo`. As root: `pkg install sudo`, or `pkg_add sudo` with `PKG_PATH` as above, then allow your user with `visudo`.
  - A fresh OpenBSD has `doas` but no `/etc/doas.conf`. As root: `echo 'permit persist :wheel' > /etc/doas.conf`.
- They need `pyyaml` 6 (the package is fine), and nothing else from pip.
- Niwa and Konbini also call the `openssl` CLI. It's in base everywhere; on OpenBSD it's LibreSSL.

### The venv

The apps need `markdown` **3.11 or later**. vaultkit refuses to start with an older one: 3.7–3.10 on Python 3.13 can be driven out of memory by a single note. Every BSD's package is older (FreeBSD 3.10.2, NetBSD 3.10.3, OpenBSD 3.10.2 as of October 2026), and OpenBSD's pip refuses to install into the system Python (`externally-managed-environment`, PEP 668).

So on all three, make **one venv** for the apps, as root, once the `python3` link above exists. It keeps the packages' PyYAML and adds markdown. It brings its own pip, so you need no pip package:

```sh
python3 -m venv --system-site-packages /usr/local/lib/machiya-venv
/usr/local/lib/machiya-venv/bin/pip install 'markdown>=3.11'
```

- Use `/usr/local/lib/machiya-venv/bin/python3` wherever this guide runs an app or its tools (§4, §10, §11).
- The rc.d scripts (§7) use it by themselves whenever it exists, so make it before the first start.
- FreeBSD's quarterly branch moves the default Python now and then. After an upgrade that changes Python's version, `pkg install` the matching `py3NN-*` packages and make the venv again.

### Check FTS5

Kura's search needs SQLite's FTS5. The packages build it in:

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

`useradd` warns that the home directory doesn't exist and `-m` wasn't given. That's harmless: the `install -d` right after it creates the directory.

**The service user must own its clone.** git refuses a repository owned by someone else ("dubious ownership").

## 4. The code

Clone a release tag of the app, owned by root and read-only for the service:

```sh
git clone --branch vX.Y.Z --depth 1 https://github.com/machiya-kobo/kura.git /usr/local/share/kura-src   # NetBSD: /usr/pkg/share/…
ln -s /usr/local/share/kura-src/app /usr/local/share/kura       # the rc.d scripts run /usr/local/share/<app>/<app>.py
cd /usr/local/share/kura && /usr/local/lib/machiya-venv/bin/python3 -m vaultkit.verify   # the vendored vaultkit is unedited
```

The rc.d scripts expect the app files directly in `/usr/local/share/<app>/` (NetBSD: `/usr/pkg/share/<app>/`), which the link gives. Or point `<app>_code` (FreeBSD and NetBSD) or the flags (OpenBSD) at `.../app`.

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
  - **Konbini** expects an existing clone at `KANBAN_REPO`. Clone it once as the service user, before the first start: `su -m konbini -c 'git clone ssh://git@git.example.net/owner/vault.git /var/db/konbini/repo'` (OpenBSD: `_konbini`).

## 6. The env file

Settings are `KEY=VALUE` lines in `/usr/local/etc/<app>.env` (FreeBSD) or `/etc/<app>.env` (NetBSD, OpenBSD). Mode `0640`, `root:<user>`.

- `#` starts a comment line, and ` # …` after an unquoted value is a comment too.
- Quotes are optional and taken literally. There's no `$` expansion.
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

**Niwa** listens for web, gemini (1965) and gopher (7070), all on `NIWA_BIND`. Its web port defaults to 8080 like Kura's, so give one of them another:
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

**Konbini** has one listener, `KANBAN_BIND:KANBAN_TAILNET_PORT` (default 8081):
```
KANBAN_BIND=127.0.0.1
KANBAN_AUTH=tailscale                       # the default; open only on a LAN without tailscale serve (§1)
KANBAN_TAILNET_USERS=you@example.com
KANBAN_REPO=/var/db/konbini/repo
KANBAN_REPO_SUBDIR=personal                 # the notes' folder in the repo, as Kura's and Niwa's
KANBAN_DB=/var/db/konbini/kanban.sqlite3
GIT_SSH_COMMAND=ssh -i /var/db/konbini/.ssh/deploy_key -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/db/konbini/.ssh/known_hosts
# optional
KANBAN_OBSIDIAN_VAULT=personal              # adds "Edit in Obsidian" links (the vault's name in Obsidian)
TZ=UTC                                      # days on the board and the review; UTC when unset
```

**The other apps are optional URLs.** Leave them out, and the features that need them are off:
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
| python | the venv's python3 when it exists, else `/usr/local/bin/python3.12`; another with `sysrc <app>_python=…` | the venv's python3 when it exists, else `/usr/pkg/bin/python3.13`; another with `<app>_python=…` in `/etc/rc.conf` | the script's `daemon=`: the venv's python3 when it exists, else `/usr/local/bin/python3` (rcctl can't change it; edit the script) |
| settings | `<app>_config`, `_dir`, `_code`, `_python`, `_runas` in rc.conf | the same | `rcctl set <app> flags …` |

All three scripts look for the venv of §2 (`/usr/local/lib/machiya-venv/bin/python3`) each time they run, so nothing needs setting.

Without the venv they fall back to the system Python, whose markdown is missing or too old. The app then exits at once (`No module named 'markdown'`, or vaultkit's "needs Python-Markdown 3.11"), and each BSD reports it differently:
- FreeBSD: daemon(8) restarts it in a loop, and `service <app> status` says it's running. Look in syslog.
- NetBSD: `service <app> status` says it's not running.
- OpenBSD: `rcctl start` says `failed`.

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

- A Service needs its definition, an autoApprover and a grant in the tailnet policy. Give it a name that isn't already served elsewhere.
- `tailscale serve status` doesn't list a Service's serve config: it prints "No serve config" while the Service works. Verify with `curl https://<service>.<tailnet>.ts.net/api/status` from another device.
- **Niwa's gemini and gopher** go through serve as plain TCP. This also maps gopher's port 70 without root (ports below 1024 need root on NetBSD and OpenBSD).
  ```sh
  tailscale serve --bg --tcp=1965 tcp://127.0.0.1:1965
  tailscale serve --bg --tcp=70 tcp://127.0.0.1:7070
  ```
- Allow only yourself in the tailnet grant. The app's `*_USERS` is the second gate.

## 9. Firewall (the second layer)

Block the app ports on the public interface, even though they're bound to loopback:

```
# OpenBSD / FreeBSD pf.conf
block return in quick on egress proto tcp to port { 8080 8081 8082 1965 7070 }
```

- NetBSD's npf: add a `block in final … port { 8080, 8081, 8082, 1965, 7070 }` rule to the external interface's group.
- FreeBSD with ipfw: deny those ports on the external interface.

## 10. Monitoring, upgrades, removal

- **Health:** Kura and Niwa answer `GET /api/status` without the owner gate. Konbini answers `GET /healthz`. Probe them through the tailnet URL. The answer has `ready`, and an `error` that's `null` unless something is broken.
- **Upgrade the app** as root (the checkout is root's, and git refuses another owner's):
  1. `git fetch --tags && git checkout <tag>` in the code directory;
  2. `/usr/local/lib/machiya-venv/bin/python3 -m vaultkit.verify`;
  3. restart the service.
- **Upgrade packages:** `pkg upgrade` (FreeBSD), `pkg_add -u` (NetBSD with `PKG_PATH`, or `pkgin upgrade` if you installed pkgin), `pkg_add -u` (OpenBSD). Make the venv again (§2) when Python's version changed.
- **Remove:**
  - stop and disable the service, and delete its rc.d script;
  - remove the `tailscale serve` entries;
  - delete `/var/db/<app>`, the env file and the user.
  - The vault clone holds your notes, so delete it deliberately.

## 11. Identity (optional)

Skip this if you're the only user, on the tailnet: `*_USERS` (§6) is enough. For other people, agents, sign-in without Tailscale, or Shiori devices, add the identity file ([identity.md](../identity.md)). Every package Python above is 3.11 or newer, which the CLI needs.

```sh
mkdir -p /usr/local/etc/machiya                                   # NetBSD, OpenBSD: /etc/machiya
cd /usr/local/share/kura && /usr/local/lib/machiya-venv/bin/python3 -m vaultkit.identity --file /usr/local/etc/machiya/identity.toml setup --tailscale you@example.com
```

- Each app runs as its own user. So give the file and its `session.key` a group the three users share, mode `0640`: `chgrp machiya` and `chmod 0640` both. Make the group and add `kura`, `niwa` and `konbini` to it (on OpenBSD `_kura` and so on).
- The CLI keeps the mode and owner when it rewrites the file. A new key needs them set again.
- Add the printed lines to each app's env file (`MACHIYA_IDENTITY_FILE=…`, and `<APP>_SIGNIN=1` for the built-in sign-in), then restart it. With the file, `*_USERS` is unused.
- Keep `<APP>_BIND=127.0.0.1` (§1). In `tailscale` or `header` mode the apps refuse any other address unless `<APP>_BIND_BEHIND_PROXY=1`.
- The CLI comes with every app (its vendored `vaultkit`), so `cd` into any app's directory as above, or run it from a checkout of the Machiya repository.

## Niwa and Konbini extras

- **Niwa's gemini certificate** is made once with `openssl req -x509 -newkey ec …`. OpenBSD's LibreSSL makes it fine (7.9). If it fails, create `gemini.crt`/`gemini.key` next to `NIWA_DB` by hand, and Niwa keeps them.
- **Link rot and the `hister` CLI** (Niwa, Konbini) are optional. Without `*_HISTER_URL`, dead links still get Wayback copies. Building `hister` needs Go and cgo.
- **Konbini's blog kit** (`KANBAN_BLOG`) is optional. Without it, the posts pages are empty.
