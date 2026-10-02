# vaultkit

The shared core of Machiya's vault services, in `vaultkit/` of this repo. Each service is standalone (its own repo, stack, copy of the vault and state), and all of them need the same vault code:

| Module | What |
|---|---|
| `front.py` | frontmatter (`note_front`, `tags_of`), git conflict markers, conflict copies written by a sync tool (Obsidian LiveSync), `_str`, `_unlink` |
| `notes.py` | `read_notes(root)`, `Note` (title, tags, published, stage, type, confidence, summary), `relative()` dates |
| `vault.py` | `Vault`: the index (notes, wikilink names, images, links, backlinks, last-changed dates from git) and the Markdown renderer (`garden` and `all`/`kura` link modes; a `retro` flag for HTML 3.2 tables, unused) |
| `frontmatter.py` | `edit_front` (set/remove top-level keys or the tags block, touching nothing else), `merge_note` (three-way frontmatter merge), `version_of`, `EditError` |
| `gitsync.py` | `GitSync`: a read-write clone a service commits to in batches (author, paths, an events log with union merge), pull with rebase and a file-by-file replay on conflict (never commits conflict markers), push |
| `git.py` | `Git` (a runner) and `Mirror` (a read-only clone of an https/ssh/file remote kept up to date; a token travels as a header in git's environment, never in argv or `.git/config`) |
| `identity.py` | who is calling and what they may do ([plans/identity.md](plans/identity.md)): the read-only TOML identity file, `Identity.resolve` (bearer tokens, Tailscale logins and tagged nodes, a trusted proxy header, the built-in sign-in's session cookie), grants, sign-in and device pairing with throttling, and the CLI `python3 -m vaultkit.identity` |
| `signin.py` | the built-in sign-in page and its POST, sign-out, Shiori's device pairing (`POST /api/pair`) and per-user preferences (`Prefs`, `GET/PUT /api/prefs`) over `identity.py`, as plain functions a room wires in a few lines |
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
| `POST /signin` | `handle_post(ident, headers, body, client)` | same-origin only (403); urlencoded, at most `MAX_FORM` (4 KB); 303 to the safe `next` with the session cookie; the page again with 401 (one message for a wrong name or password) or 429 (throttled) |
| `POST /signout` | `handle_signout(ident, headers)` | same-origin only (403); clears the cookie, 303 to `/` |
| `POST /api/pair` | `handle_pair(ident, headers, body, client)` | `{"code", "device"}` (JSON, at most 1 KB) → `{"token": "mcd_…", "principal"}` or `{"error"}` with 401/429; no cookie, so no same-origin rule |
| `GET/PUT /api/prefs` | `handle_prefs(prefs, principal, method, headers, body, secure)` | `{"prefs": {key: value}}`; a PUT merges (`null` removes), all or nothing |

- **Same-origin** (`same_origin(headers, secure)`): `Origin`, or `Referer` when there's no `Origin`, must have the
  request's own `Host` (and https when `secure`). Neither header, `null`, a duplicate or another host is a refusal.
  A prefs PUT proven by a bearer token (`principal.via` `token:…`/`device:…`) needs no `Origin`; one made with a
  session cookie, a Tailscale or proxy login or open mode does (all of those ride along with any request a browser
  makes).
- **`next`** (`safe_next`): one leading `/`, not `//` or `/\`, no control characters or whitespace, at most 2048
  characters, never `/signin` or `/signout`; anything else is `/`. Non-ASCII is percent-encoded.
- **Bodies:** `read_body(headers, rfile, limit)` reads at most `limit` bytes (`MAX_FORM`, `MAX_PAIR`, `MAX_PREFS`) and
  answers None for a larger, chunked or short body: answer 413 and close the connection.
- **Preferences** (`Prefs(path)`): table `prefs(principal, key, value, updated)` keyed by the principal's id in the room's own SQLite file (its
  `*_DB` is fine). Keys `[a-z0-9_.-]{1,64}`, values strings of at most 4 KB, at most 100 keys per principal;
  `get_all(principal)`, `put(principal, changes)` (raises `PrefsError`). Shared ones (theme, text size) keep syncing
  through the `machiya_*` cookies as before; the server copy follows the person to a new device.
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

Konbini subclasses `Vault` to plug in its own cache: `key()` returns the board's HEAD + revision, and `source()` returns its timeline's note list.

## Tests

`python3 -m unittest discover -s tests`, run in any service image (it needs `markdown` and `pyyaml`, which a bare host may lack):

```sh
docker run --rm --user 1000:1000 -e HOME=/tmp -v "$PWD":/v -w /v --entrypoint python3 <registry>/konbini:<tag> -m unittest discover -s tests
```
