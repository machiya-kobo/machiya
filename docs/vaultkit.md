# vaultkit

The shared core of Machiya's vault services, in `vaultkit/` of this repo. Each service is standalone (its own repo, stack, copy of the vault and state), and all of them need the same vault code:

| Module | What |
|---|---|
| `front.py` | frontmatter (`note_front`, `tags_of`; v0.22: YAML aliases refused and at most `MAX_FRONT` = 64 KiB, `load_yaml`), git conflict markers, conflict copies written by a sync tool (Obsidian LiveSync), `_str`, `_unlink` |
| `notes.py` | `read_notes(root)` (v0.22: symlinks skipped, files opened with `O_NOFOLLOW`), `read_file(path)`, `safe_path(root, rel)` (the path to write, or `ValueError` for `..`, an absolute path or any symlinked part: every writer uses it), `Note` (title, tags, published, stage, type, confidence, summary), `relative()` dates |
| `vault.py` | `Vault`: the index (notes, wikilink names, images, links, backlinks, last-changed dates from git) and the Markdown renderer (`garden` and `all`/`kura` link modes; a `retro` flag for HTML 3.2 tables, unused). v0.22: needs Python-Markdown 3.11 or later (`MIN_MARKDOWN`; older ones run out of memory on one note under Python 3.13, so the import refuses them); symlinked images are not indexed |
| `frontmatter.py` | `edit_front` (set/remove top-level keys or the tags block, touching nothing else), `merge_note` (three-way frontmatter merge), `version_of`, `EditError` |
| `gitsync.py` | `GitSync`: a read-write clone a service commits to in batches (author, paths, an events log with union merge), pull with rebase and a file-by-file replay on conflict (never commits conflict markers), push. v0.22: `core.symlinks=false` before the first pull, and replay never writes through a link. `lock` is re-entrant: a writer holds it around its own edit so a pull can't wipe it (NIWA-8) |
| `git.py` | `Git` (a runner; v0.22: `.error` keeps the last failure, URL credentials redacted in every log line, `redact()`) and `Mirror` (a read-only clone of an https/ssh/file remote kept up to date; a token travels as a header in git's environment, never in argv or `.git/config`; v0.22: `core.symlinks=false`, and `.failed` says why the last `update()` didn't reach the remote: a caller must not report that as synced) |
| `websafe.py` | v0.22, the sweep's shared rules: `base_headers()` for every non-HTML answer (nosniff, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`), `asset_headers(name)` for a vault file (sandboxed: an SVG never runs script in the room; anything not an image is a download), `header_value()` (refuses CR/LF/control characters), `location(path)` (a safe local redirect target), `private()` / `vet()` (the private-address rule, the same as smallweb's), `public_opener(allow)` (outside URLs: every connection and redirect vetted at connect time; a credential never follows a redirect) and `token_opener()` (no redirects at all) |
| `identity.py` | who is calling and what they may do ([identity.md](identity.md)): the read-only TOML identity file, `Identity.resolve` (bearer tokens, Tailscale logins and tagged nodes, a trusted proxy header, the built-in sign-in's session cookie), grants, sign-in and device pairing with throttling, and the CLI `python3 -m vaultkit.identity` |
| `signin.py` | the built-in sign-in page and its POST, sign-out, Shiori's device pairing (`POST /api/pair`) and per-user preferences (`Prefs`, `GET/PUT /api/prefs`) over `identity.py`, as plain functions a room wires in a few lines |
| `histerauth.py` | `*_AUTH=hister` ([identity.md](identity.md#hister-sign-in-authhister), [hister-login](services/hister-login.md)): v0.22 keeps each room's own host-only cookie (`__Host-<base>_<room>`, a room session from a one-time code: `resolve` answers `/machiya/callback` itself, `respond` redirects it; every check names the room, `X-Machiya-Room`, plus `<P>_AUTH_ACCEPT_ORIGINS`), takes room tokens (`Bearer mht_…`), and `TokenGate`/`token_gate_for` give a service with a gate of its own (machiya-mcp, smallweb) room tokens beside it; `load_for` with the start-up refusals, `HisterAuth.resolve` (the token, the `machiya_sso` id (renamed by `MACHIYA_SSO_COOKIE`, `sso_cookie_name`), the check against the helper with its cache and health flag, the Tailscale fallback or 503, the loop guard; v0.21: the automatic sign-in through `MACHIYA_SIGNIN_PROVIDER` and the account's shared preferences on `Result.prefs`), `signout` (with the signed-out marker), `forward_prefs` and `prefs_state` (a room's `/api/prefs` is the account's, [contracts/prefs.md](contracts/prefs.md)), `safe_return`, `hister_headers(token_file)` (`Origin: hister://` and the owner's token, re-read on change), default refusal pages; standard library only, tested in `tests/test_histerauth.py` |
| `prefs.py` | v0.21, [contracts/prefs.md](contracts/prefs.md): the schema of the settings that follow a person (`SHARED`, `<app>.<key>`, `validate`, `schema()`; `python3 -m vaultkit.prefs` prints it as JSON), `Store` (per principal: values, `updated`, `rev`), `read_rows` and `merge` (the migration rule); tested in `tests/test_prefs.py`, with `ui/machiya.js`'s sync rules run in Node (`tests/js/prefs_sim.mjs`; its menus and pull to refresh in `tests/js/ui_sim.mjs`, from `tests/test_vaultkit.py`) |
| `verify.py` | drift check for a vendored copy |

Consumers: **Konbini** (the board), **Kura** (every note, search, API) and **Niwa** (the published garden). The principles are in [principles.md](principles.md).

## Vendored, not installed

Services copy the package in at a tag, so their builds need no network and no private-repo access:

```sh
./vendor.sh <service>/app v0.1.0     # from this repo's root     # -> <service>/app/vaultkit/ + VENDORED (version + sha256 per file)
python3 -m vaultkit.verify           # in the service: fails if the copy was edited in place
```

Each service's Dockerfile runs `python3 -m vaultkit.verify`, so an edited copy fails the build. To fix something, change it here, add a test, tag it, and re-vendor it into every consumer.

## Using it

```python
from vaultkit import Vault, Mirror, read_secret

m = Mirror("https://forgejo.example/owner/obsidian.git", "/data/repo", token=read_secret("/secrets/token"))
head, changed = m.update()          # clone, or fetch + hard reset
v = Vault("/data/repo", "personal")
v.revision = head                   # the index rebuilds when this changes
v.index()
html = v.render(v.get("Projects/Kura"), "", mode="all")
```

**Indexing** (v0.28). `index()` builds the new index aside and puts it in place at the end, so a reader in another
thread sees the previous index whole until the new one is ready. `read_notes()` re-reads only notes whose stat
(device, inode, size, modification and change times) changed since its last call for that root, and frontmatter goes
through LibYAML when PyYAML has it (aliases still refused; anything LibYAML refuses goes to the Python loader). The
tended dates walk the git history once, then only the commits added since; the same walk gives `Vault.tended_at`, each
note's commit time in seconds. On 3,000 notes and 400 commits, a re-index after a commit takes 0.14 s (1.64 s in 0.27).

**Hardening** (v0.29).
- **Trusted proxies:** `identity.load_for` and `histerauth.load_for` read `<ROOM>_TRUSTED_PROXIES`, and an identity header from any other peer counts for nothing inside vaultkit too (`Identity.resolve(headers, client)`; pass `client=` to `HisterAuth.resolve`). `identity.trusted_proxies()` and `identity.peer_trusted()` replace the rooms' own copies.
- **Note classes:** a note's raw-HTML `class` survives only when every class is vaultkit's own (`task`, `wikilink`, `seed`, `mermaid`, `language-*`).
- **Remote images:** `render(..., remote_images="click")` shows an image from another site as a placeholder with a Load image button (`ui/machiya.js`), or a link on a retro page; the default `"load"` is unchanged, for API answers.
- **Pairing and devices:** a pairing code works once per room; a paired device's token lasts 90 days (older ones until 2027-01-06).
- **Room tokens:** rooms explain hister-login 0.6.0's `token-expired`.

**A note is data, never code** (v0.13). `render()` returns clean HTML (`vaultkit.sanitize.clean`): raw HTML in a note
keeps only known tags and attributes; scripts, styles, frames, forms and event handlers are dropped, and a link or
image keeps only an `http(s)`, `mailto`, `obsidian`, `gemini` or `gopher` URL (or a relative one). `- [ ]` / `- [x]`
items become disabled checkboxes (`li.task`), bare URLs in the text become links (never inside `<a>`, `<code>` or
`<pre>`), and table alignment is an `align` attribute, not a style. An API answer read outside the room uses
`clean(html, base=URL, schemes=API_SCHEMES)` (relative URLs made absolute, gemini/gopher dropped).

Behind the sanitizer, every HTML page a room serves carries `shell.security_headers()`: a Content-Security-Policy
(`script-src 'self'`: no inline script and no `on…=` attributes anywhere in a room's own markup either; images from
anywhere; forms and frames only to and by the room), `X-Content-Type-Options: nosniff` and `Referrer-Policy:
same-origin`.

**Themes** (v0.15): `vaultkit.palettes` holds ten palettes, each with a dark and a light variant, and writes machiya.css's palette section (`css()`). `shell.prefs()` reads the `palette` cookie (`machiya_palette` when shared) into `ctx.palette`, `page()` puts `palette-<key>` on `<body>` and uses the palette's bar colours in the theme-color metas, and `appearance_section` (now titled Display) offers Theme (the palette) and Appearance (System / Light / Dark). Rooms pass `ctx.palette` to `manifest_colors`.

**The installed app's colours** (v0.14): `shell.manifest_colors(theme, headers, palette)` gives the manifest's
`background_color` and `theme_color` (the splash screen and title bar): Night or Day as chosen, and with System the
device's own scheme when the browser sends `Sec-CH-Prefers-Color-Scheme` (`security_headers()` now includes the
`Accept-CH` that asks for it), else Night, plus `user_preferences.color_scheme_dark` for browsers that read per-scheme
manifest colours. Serve the manifest with `Vary: ` + `shell.MANIFEST_VARY` and `Cache-Control: no-cache`. An Android
install keeps the colours it saw when installed; iOS draws its own launch screen.

Shared page pieces (v0.13): `shell.title(room, what)` ("Lantern - Kura"), `shell.message(heading, text, actions)`,
`shell.not_found(room, what)`, `shell.offline(room)` (the precached `/offline`: no status line, no network named),
`signin.needed(room, next, ctx, signin=True)` (the 401 page: the plain header, no Rooms switcher), and `who=` on
`header()` / `page()` (the signed-in name: a person button to `/settings#account`, and a row in the phone's Rooms
sheet). `settings_page` gives each section an id (`#account`); `appearance_section(ctx, synced=True)` says the theme
follows the person when the page has a `prefs_url`.

Turning identity on for the first time is one command:

```sh
python3 -m vaultkit.identity [--file PATH] setup [--owner NAME] [--tailscale LOGIN] [--password] [--proxy LOGIN] \
    [--rooms kura,niwa,konbini,mcp] [--yes]
```

PATH defaults to `$MACHIYA_IDENTITY_FILE`, else `./machiya-identity/identity.toml` (the directory is created 0700).
A new file and its `session.key` are written as `init` writes them (0600), with an owner (`owner` unless `--owner`: a
person, `owner = true`, a random id) and the logins asked for; `--password` asks twice (at least 12 characters). On a
TTY with none of these flags (and no `--yes`) it asks for a Tailscale login and whether to set a password. It never
overwrites: on an existing file it validates it, refuses when another principal is the owner, and only adds the
requested logins to the owner (a login another principal holds, or replacing a password, is refused). It also refuses
to leave the owner with no way to sign in. Then it prints, per room in `--rooms`, the lines to paste
(`MACHIYA_IDENTITY_FILE=<absolute path>`; with a password, `<PREFIX>_SIGNIN=1` and the room's public-URL setting
(`KURA_PUBLIC_URL` / `NIWA_PUBLIC_URL` / `KANBAN_BOARD_URL`) to fill in; with a Tailscale or proxy login,
`<PREFIX>_BIND_BEHIND_PROXY=1` for a Docker or sidecar setup; prefixes `KURA`, `NIWA`, `KANBAN`, `MCP`) and the next
steps for the rooms asked for: mount the directory read-only, then (with `mcp`) `add`, `grant` and `token mint`, and
(with `kura`) `pair` for Shiori.
It never prints a password, hash or key. Exit codes: 0 done, 2 usage, 1 refused (nothing written).

A room's owner gate, once it reads an identity file (`MACHIYA_IDENTITY_FILE`):

```python
from vaultkit import identity

ident = identity.load_for("konbini", os.environ, bind=BIND)   # None without the file: keep the old *_USERS gate
who = ident.resolve(self.headers, client=self.client_address[0])
for c in who.cookies:                                         # a renewed or cleared session
    self.send_header("Set-Cookie", c)
if not who:                                                   # 401: no or a bad proof; 403: nobody in the file
    return self.send(who.status, who.error)
if not who.principal.can("konbini", "write"):
    return self.send(403, "not allowed")
```

`principal.vaults()` is Kura's scope: `"*"` for the owner, else vault names plus `"default"`/`"shared"`. Mount the
identity file's **directory** read-only into the room (the CLI replaces the file atomically, and a file mount would
keep the old one).

`load_for(..., secure=True)`: the session cookie gets `Secure`, and `signin` counts only an https page as
same-origin. A room passes `secure=False` only when it is really served over plain http (no https public URL: a
localhost or LAN setup), since a browser drops a `Secure` cookie set over http. Leave it on whenever a proxy in front
speaks https. (Niwa will take it from `NIWA_PUBLIC_URL`'s scheme or a `NIWA_SECURE_COOKIES` setting.)

### Sign-in, pairing and preferences (`vaultkit.signin`)

Every function takes the request's headers (http.server's message, or any mapping with `.get`), its body as bytes and
the client address, and returns `(status, [(header, value), ...], body bytes)`:

| Route | Call | What it does |
|---|---|---|
| `GET /signin` | `handle_get(ident, headers, query)` | the form (name, password, a hidden `next`) in the shared shell; 404 when sign-in is off |
| `POST /signin` | `handle_post(ident, headers, body, client, origins)` | same-origin only (403); urlencoded, at most `MAX_FORM` (4 KB); 303 to the safe `next` with the session cookie; the page again with 401 (one message for a wrong name or password) or 429 (throttled) |
| `POST /signout` | `handle_signout(ident, headers)` | same-origin only (403); clears the cookie and (v0.13) the browser's HTTP cache (`Clear-Site-Data`), 303 to `/`; machiya.js empties the offline copies before posting |
| `POST /api/pair` | `handle_pair(ident, headers, body, client)` | `{"code", "device"}` (JSON, at most 1 KB) → `{"token": "mcd_…", "principal"}` or `{"error"}` with 401/429; no cookie, so no same-origin rule |
| `GET/PUT /api/prefs` | `handle_prefs(prefs, principal, method, headers, body, secure, origins)` | the room's own store ([contracts/prefs.md](contracts/prefs.md)): `{"v", "rev", "prefs", "updated"}` with `ETag: "<rev>"` (`If-None-Match`: 304); a PUT merges only the keys sent (`null` removes), checked against the schema (400), all or nothing. In hister mode with the helper the room forwards instead (`histerauth.forward_prefs`) |

- **Same-origin** (`same_origin(headers, secure, origins)`): `Origin`, or `Referer` when there's no `Origin`, must
  be one of the room's own `origins` (its public address(es); every handler takes `origins=`), or without those
  have the request's own `Host`, over https only. **Over plain http a room must pass `origins`**: there `Host` and
  `Origin` both come from whatever page pointed its name at the room (DNS rebinding), so without them every
  same-origin check refuses. Neither header, `null`, a duplicate or another host is a refusal.
  A prefs PUT proven by a bearer token (`principal.via` `token:…`/`device:…`) needs no `Origin`; one made with a
  session cookie, a Tailscale or proxy login or open mode does (all of those ride along with any request a browser
  makes).
- **`next`** (`safe_next`): one leading `/`, not `//` or `/\`, no control characters or whitespace, at most 2048
  characters, never `/signin` or `/signout`; anything else is `/`. Non-ASCII is percent-encoded.
- **Bodies:** `read_body(headers, rfile, limit)` reads at most `limit` bytes (`MAX_FORM`, `MAX_PAIR`, `MAX_PREFS`) and
  answers None for a larger, chunked or short body: answer 413 and close the connection.
- **Preferences** (`Prefs(path)`, a `prefs.Store`): table `prefs(principal, key, value, updated)` keyed by the
  principal's id in the room's own SQLite file, plus `prefs_rev(principal, rev)` (v0.21; an older file opens as it is).
  `snapshot(principal)` → `(rev, values, updated)`, `write(principal, changes)`; the older `get_all(principal)` and
  `put(principal, changes)` (keys `[a-z0-9_.-]{1,64}`, values of at most 4 KB, at most 100 keys; `PrefsError`) stay.
  `handle_prefs` checks every PUT against the schema in `vaultkit.prefs`. The shared ones also travel as the
  `machiya_*` cookies, the first-paint and offline path.
- Sign-in pages answer `Cache-Control: no-store` and refuse framing (`X-Frame-Options: DENY`,
  `frame-ancestors 'none'`). Every JSON answer is `no-store`.

Wiring, in a room's `http.server` handler (Kura shown; `ident` from `load_for`, `prefs = signin.Prefs(DB)`):

```python
from vaultkit import signin

def reply(self, status, headers, body):
    self.send_response(status)
    for k, v in headers:
        self.send_header(k, v)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

# do_GET
if path == "/signin":
    return self.reply(*signin.handle_get(ident, self.headers, url.query))
# do_POST: these three before the room's own gate (they are how you get past it)
limits = {"/signin": signin.MAX_FORM, "/signout": signin.MAX_FORM, "/api/pair": signin.MAX_PAIR}
if path in limits:
    body = signin.read_body(self.headers, self.rfile, limits[path])
    if body is None:
        self.close_connection = True
        return self.reply(413, [("Content-Type", "text/plain")], b"request body too large\n")
    if path == "/signin":
        return self.reply(*signin.handle_post(ident, self.headers, body, self.client_address[0]))
    if path == "/signout":
        return self.reply(*signin.handle_signout(ident, self.headers))
    return self.reply(*signin.handle_pair(ident, self.headers, body, self.client_address[0]))
# /api/prefs (GET and PUT): after the room's gate (and its read grant), as the resolved principal
if path == "/api/prefs":
    body = signin.read_body(self.headers, self.rfile, signin.MAX_PREFS) if self.command == "PUT" else b""
    status, headers, out = signin.handle_prefs(prefs, who.principal, self.command, self.headers, body, ident.secure)
    return self.reply(status, headers + [("Set-Cookie", c) for c in who.cookies], out)   # a renewed session
```

Behind a proxy, `client` is the proxy's address for everyone, so the per-address throttles (20 sign-in failures, 5
pairing tries per window) are shared; the per-name one (5) still holds. A room with sign-in on links to `/signin` from
its 401 page and offers a sign-out button (a same-origin form post) in its settings.

**Preferences without an identity file.** A room with no `MACHIYA_IDENTITY_FILE` (`load_for` gave None) keeps its
old gate (`*_USERS`, or open mode's Host allow-list), and that alone decides who gets in. `identity.ambient(auth,
headers)` only names whose preferences these are, so the room calls it only after the old gate admitted the request:

- `"tailscale"`: the one `Tailscale-User-Login` as `Principal(login, "person", owner=True, via="tailscale",
  uid=tailscale_uid(login), tailscale=(login,))`; None when the header is missing, empty, sent twice or holds a
  control character. `tailscale_uid(login)` is always `"ts:"` + the first 32 hex digits of the SHA-256 of the
  trimmed, lowercased login: 35 characters however long the login, and no email address in the room's database.
- `"open"`: `OPEN_OWNER` (uid `":open"`).
- anything else: None (no preferences: `handle_prefs` answers 401).

```python
# /api/prefs without an identity file: after the room's *_USERS gate admitted the request
who = identity.ambient(AUTH, self.headers)                  # AUTH: the room's own tailscale | open setting
status, headers, out = signin.handle_prefs(prefs, who, self.command, self.headers, body, SECURE, ORIGINS)
```

These principals are not bearer, so a PUT still needs same-origin with the room's `origins`.

Konbini subclasses `Vault` to plug in its own cache: `key()` returns the board's HEAD + revision, and `source()` returns its timeline's note list.

### Response and fetch safety (`vaultkit.websafe`, v0.22)

What every room does with it (the sweep of 2026-10; the per-room findings are KURA-1, NIWA-1, KONB-2, NIWA-4..6, KONB-7, KONB-10):

```python
from vaultkit import websafe, shell

# every answer: HTML pages already send shell.security_headers(); everything else (JSON, text, CSS, images, 3xx):
for k, v in websafe.base_headers(): self.send_header(k, v)

# /a/<file> from the vault: never the room's own headers
for k, v in websafe.asset_headers(rel): self.send_header(k, v)        # Content-Type included

# a redirect built from the request: percent-encoded, never //host, never a CR/LF
self.send_header("Location", websafe.location(path))                  # or websafe.header_value(url) for a full URL

# link checkers (outside URLs): public addresses only, on every hop
with websafe.public_opener().open(req, timeout=10) as r: ...            # raises websafe.Blocked for a private one

# a call that carries a token (Hister, another room): no redirect is ever followed
with websafe.token_opener().open(req, timeout=10) as r: ...

# a writer: the path to write, or ValueError (a symlink, .., an absolute path)
path = vaultkit.safe_path(vault_root, rel)
```

A Mirror's caller checks `m.failed` after `m.update()` and reports it instead of a fresh "synced" (MACH-F-4).

## Tests

`python3 -m unittest discover -s tests`, run in any service image (it needs `markdown` 3.11 or later and `pyyaml`, which a bare host may lack; since v0.22 vaultkit refuses to import with an older markdown, which can run out of memory on one note):

```sh
docker run --rm --user 1000:1000 -e HOME=/tmp -v "$PWD":/v -w /v --entrypoint python3 <registry>/konbini:<tag> -m unittest discover -s tests
```
