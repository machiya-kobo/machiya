# Plan: one identity for every room

Status: **proposal**, with the owner's answers to its questions folded in (see "Decisions"). Nothing here is built yet. When a phase lands, its part moves into the
docs it changes (principles, contracts, service pages) and this file shrinks; when all of it has landed, this file goes.

## Why

Today a room knows only two things about a request: whether `Tailscale-User-Login` is in its `*_USERS` list, and
whether the request *looks* like the owner's browser (no `X-Agent`, a same-origin `Origin`). The second is a header
the caller writes, so any client the gate admits can claim the owner's powers: an agent can publish in Niwa or add a
board area by sending the right `Origin`. Without Tailscale (`*_AUTH=open`) there is no identity at all.

Every agent, the MCP server and the room-to-room calls reach the rooms as the owner's login, so "agents get 403"
isn't enforced, and work notes stay out of AI only because every client filters them.

## Goals

1. **Every request has a principal** (who) and **grants** (what it may do), decided from something the caller can't
   forge: Tailscale's identity, a proxy you trust, a password-backed session or a secret token.
2. **People, agents and services are different principals.** An agent never gets the owner's powers by sending a
   header, and Kura itself refuses agents the private vaults.
3. **Tailscale is optional.** The same identity file and grants work with Tailscale, behind your own auth proxy, or
   with the rooms' built-in sign-in.
4. **One place to manage it:** one read-only identity file, one CLI. Per-user preferences live in each room.
5. **Simple and solid:** standard library only, few moving parts, fail closed, every rule tested.

## Not goals

- Accounts for strangers, sign-up, password reset by email, OAuth/OIDC in the rooms. (Use a proxy for OIDC.)
- Passkeys in the rooms: verifying WebAuthn needs ECDSA and CBOR, which Python's standard library lacks, and vaultkit
  is standard library + `markdown` + `pyyaml`. A proxy that does passkeys (e.g. Authelia) works through the
  proxy-header mode.
- Hister and SearXNG: third-party, unchanged; they stay behind the network and Hister's own token.
- Protecting the owner from software running as the owner on the owner's own laptop. An agent there that uses the
  owner's Tailscale login *is* the owner as far as the network can tell. Give such agents a token instead (below);
  the rooms will then treat them as agents, but nothing can force them to use it.

## Threat model (what this must stop)

| Threat | Today | After |
|---|---|---|
| An admitted agent claims owner powers with `Origin`/no `X-Agent` | works | refused: powers come from the principal |
| An agent reads a private (work) vault through Kura | client-side filters only | Kura refuses it |
| A web page posts to a room while the owner browses (CSRF) | fixed for Konbini's API (0.11.0); Konbini's GET tag removal still open | cookie sessions need same-origin for every state change; no state change on GET |
| DNS rebinding against an open-mode room | fixed (Host allow-lists, 0.5.0/0.3.1/0.11.0) | unchanged |
| A stolen token or session | n/a | tokens revocable one by one; sessions revocable per principal; tokens never in URLs or logs |
| Password guessing on the built-in sign-in | n/a | scrypt, per-account and per-address throttling, generic errors |
| A forged identity header | the room must sit behind Tailscale serve | header modes refuse to start on a non-loopback bind unless told the proxy is the only way in |
| A leaked identity file | n/a | it holds hashes only (scrypt, SHA-256 of tokens); the session key lives in its own file |

## Principals

A principal has a **name** (`[a-z0-9-]+`), a **kind** and **grants**:

- `person`: the owner, a household member, a guest. Signs in with Tailscale, a proxy, or a password.
- `agent`: a Claude session, machiya-mcp, the claude VM, `tools/pm`. Uses a token or a Tailscale tagged node.
- `service`: one room calling another (Niwa → Konbini). Uses a token.

`X-Agent` stays, as a **label** for the history ("which session did this"), never as a permission. The events in
the vault keep `actor` (now the principal name) and `agent` (the label).

## Grants

A grant names a room and what the principal may do there. `owner = true` means everything, everywhere. The list is
deliberately short:

| Room | Grant | Meaning |
|---|---|---|
| any | `read` | pages and read APIs |
| kura | `vaults = [...]` | which vaults it may read: vault names, `"default"`, `"shared"` (every `+shared` vault). Default for agents: `["default", "shared"]`. A private vault is never readable unless named here |
| niwa | `suggest` | `POST /api/suggest` |
| niwa | `publish` | publish, dismiss, garden fields (owner powers today) |
| konbini | `write` | card writes, comments, claims |
| konbini | `areas` | new `area/*` lanes and new tags (owner powers today) |
| mcp | `use` | call machiya-mcp; with `limits` (below) |
| smallweb | `save` | save a page |

A request with no principal, or a principal without the grant, gets 403 (401 with a sign-in link for a browser
page without a session, when built-in sign-in is on). Unknown grant names in the file refuse to start.

## The identity file

One TOML file (Python's `tomllib`, 3.11+; every room's image is 3.13), mounted **read-only** into every room, path
in `MACHIYA_IDENTITY_FILE`. Rooms re-read it when its mtime changes (checked at most once a second). It holds
**no secret in clear**: passwords are scrypt hashes, tokens are SHA-256 hashes; the session-signing key is a separate
file (`session_key_file`, 32 random bytes, 0600).

```toml
# /etc/machiya/identity.toml (example; names are examples)
version = 1
session_key_file = "/etc/machiya/session.key"
session_days = 30                          # renewed on use: expires 30 days after the last visit
# Tailscale tagged nodes: the app capability Serve forwards (tailscale serve --accept-app-caps=...)
tailscale_capability = "github.com/machiya-kobo/cap/identity"

[principals.owner]
id = "k3m9q2x7p4w8r6t1"                      # random, set by the CLI's add; required for a person
kind = "person"
owner = true
tailscale = ["owner@example.com"]           # Tailscale-User-Login values
proxy = ["owner"]                           # values of the trusted proxy header
password = "scrypt$16384$8$1$<salt-b64>$<hash-b64>"
session_epoch = 1                           # bump to sign out every session of this principal

[principals.household]
id = "h5n2c8v4b7z3j9d6"
kind = "person"
tailscale = ["partner@example.com"]
grants = { niwa = ["read"], kura = { read = true, vaults = ["default"] } }

[principals.mcp]
kind = "agent"
tailscale_tag = "mcp"                       # the value in the forwarded capability, for a tagged node
grants = { kura = { read = true, vaults = ["default", "shared"] }, konbini = ["read", "write"], niwa = ["read", "suggest"] }
limits = { reads_per_min = 120, writes_per_min = 30, writes_per_day = 300, suggests_per_day = 10 }

[principals.claude-vm]
kind = "agent"
grants = { kura = { read = true, vaults = ["default"] }, konbini = ["read"] }
[[principals.claude-vm.tokens]]
id = "k7d2"                                 # public part, shown in logs
hash = "sha256:<hex>"                       # of the secret part
label = "claude VM"
expires = 2027-04-01

[principals.niwa]
kind = "service"
grants = { konbini = ["read"] }
[[principals.niwa.tokens]]
id = "q9xa"
hash = "sha256:<hex>"
label = "niwa → konbini"
```

The file is the **only** source of grants. Tailscale, a proxy, a password or a token only prove *which* principal is
calling.

**Compatibility:** without `MACHIYA_IDENTITY_FILE`, a room behaves exactly as today: `*_USERS` logins are owners,
`open` mode is everyone-is-owner with the Host allow-list. Existing deployments keep working until they opt in.

## How a request is identified

One function in vaultkit, `identity.resolve(headers, peer, config) -> Principal | None`, used by every room. It
tries, in order, and the first proof that is present decides (a present but **invalid** proof is a 401, never a
fall-through to the next method):

1. **`Authorization: Bearer mch_<id>_<secret>`** (agents, services, Shiori devices). Look up `<id>`, compare
   SHA-256 of `<secret>` in constant time, check `expires`. Only from the header; a token in a URL or a cookie is
   refused.
2. **Tailscale** (`*_AUTH=tailscale`): `Tailscale-User-Login` → the principal listing it; for a tagged node (no
   login), `Tailscale-App-Capabilities` → the capability named by `tailscale_capability` → its `principal` value →
   the principal with that `tailscale_tag`. Serve strips both headers from what a client sends.
3. **Trusted proxy header** (`*_AUTH=header`, `*_AUTH_HEADER=Remote-User`): the header's value → the principal
   listing it under `proxy`. For Authelia, oauth2-proxy, Caddy or nginx with basic auth.
4. **Session cookie** (`machiya_session`), from the built-in sign-in (below), in any mode that enables it.
5. **`*_AUTH=open`:** the owner, as today (Host allow-list on); meant for localhost.

**Header modes are only safe behind the proxy.** In `tailscale` and `header` mode a room refuses to start on a
non-loopback bind unless `*_BIND_BEHIND_PROXY=1` says the proxy is the only way in (the Docker sidecar case). This
is today's advice made a check.

## Built-in sign-in (no Tailscale, no proxy)

Enabled with `*_SIGNIN=1` (or always when `*_AUTH=signin`). Kept small:

- `GET /signin` shows a form (vaultkit's shared UI); `POST /signin` takes principal name and password. scrypt
  (`hashlib.scrypt`, N=2^14, r=8, p=1, 16-byte salt), compared in constant time. A wrong name and a wrong password
  get the same answer and take the same time.
- Throttling, in memory per room: 5 failures per principal per 15 minutes, 20 per client address per 15 minutes;
  then 429 until the window passes. Logged without the password.
- On success: `machiya_session` cookie = `base64url(payload).base64url(HMAC-SHA256(key, payload))`, payload
  `{"p": name, "e": session_epoch, "iat": ..., "exp": ...}`. `HttpOnly`, `Secure` (when the room's public URL is
  https), `SameSite=Lax`, `Path=/`, `Domain=MACHIYA_COOKIE_DOMAIN` when set, so one sign-in covers every room.
- A session is valid while the signature checks, `exp` is in the future, the principal exists and its
  `session_epoch` matches. Signing everyone out: rotate the key. Signing one principal out: bump its epoch.
- **Renewed on use:** a valid session whose `iat` is over a day old gets a fresh cookie with `exp` = now + 30 days,
  so a session ends 30 days after its last use. `iat` of the first sign-in is kept as `auth` and never renewed past
  a hard cap of 180 days (then sign in again).
- `POST /signout` clears the cookie. Every state change made with a cookie must be same-origin (the existing rule,
  now for all rooms); no room changes state on GET.
- Only `person` principals may sign in with a password; agents and services use tokens.

## Tokens

Two kinds, both sent only as `Authorization: Bearer …`:

- **Stored tokens**, `mch_<id>_<secret>`: `id` 4–8 characters, `secret` 32 random bytes base64url. Minted by the CLI,
  printed once; the file stores `id`, `sha256(secret)`, label, optional `expires`. Revoking = deleting the entry. For
  agents, services, scripts and a Shiori device set up by pasting.
- **Device tokens**, `mcd_<payload>.<hmac>`: signed with the session key, like a session but without an expiry,
  payload `{"p": name, "d": device id, "e": session_epoch, "iat": ...}`. Issued by a room when a Shiori device pairs
  with a code (below), because a room can't write the file. Valid while the signature, the principal, its epoch hold
  and `d` isn't in the principal's `revoked_devices`. Revoking one device = `identity device revoke <id>` (adds it to
  that list); all of a person's devices = bump the epoch.

A room never logs a token, only its `id` or device id. Rooms may record `last_used` per token or device in their own
DB, for the CLI's `list` to show stale ones (optional).

### Pairing a Shiori device (both ways)

- **Paste:** `identity token mint <principal> --label "iPhone"` prints a stored token; paste it into Shiori's
  settings (Linux, scripts, a Mac).
- **Code:** `identity pair <principal> --label "iPhone"` writes a pairing entry to the file: a random 8-character
  code (shown once, in groups of four, no look-alike letters) stored as an scrypt hash, the label, a device id and
  `expires` = now + 10 minutes. Shiori asks for the code and posts it to `POST /api/pair` on any room
  (`{"code": …, "device": "iPhone"}`); the room checks it against unexpired entries, throttled (5 tries per address
  per 10 minutes, every room), and answers a device token. The CLI removes expired entries; `identity pair --cancel`
  removes one early. A room can't mark a code used (the file is read-only), so a code stays good until it expires;
  the short window, the throttle and the device id in every token it yields (revocable as one) are the guard.

## Tailscale with tagged nodes

- Run agents (the MCP container's sidecar, the claude VM) as tagged devices, e.g. `tag:machiya-agent`.
- In the tailnet policy, grant the rooms' services to that tag with the app capability:
  `{"src": ["tag:machiya-agent"], "dst": ["tag:machiya-room"], "app": {"github.com/machiya-kobo/cap/identity": [{"principal": "mcp"}]}}`.
- Each room's Serve forwards it: `--accept-app-caps=github.com/machiya-kobo/cap/identity` (or `AcceptAppCaps` in the
  room's serve config), and the room opts in with `<ROOM>_ACCEPT_APP_CAPS=1`. Off by default: a Serve older than
  v1.92, or one without the setting, passes a client's own copy of the header straight through. Needs Tailscale **v1.92+**. To verify in phase 1: that the setting works through `serve.json`
  and Tailscale Services as Machiya deploys them.
- The capability says *who*; the identity file says *what*. A capability naming a principal the file doesn't have is
  refused.

## Per-user preferences

Each room keeps its users' preferences in its own SQLite DB: `prefs(principal, key, value, updated)`, read and written
through `GET/PUT /api/prefs` (same-origin or token). Shared ones (theme, text size) keep syncing across rooms through
the `machiya_*` cookies on `MACHIYA_COOKIE_DOMAIN`, as today; the server copy makes them follow the person to a new
device. A room stays standalone: no shared writable store.

## Per-agent policy

An agent principal's `grants` decide what it may touch; its `limits` replace machiya-mcp's per-login buckets (keyed
by principal now).

**Rooms see machiya-mcp only.** It authenticates its own callers (token or tagged node) against the same identity
file, applies each caller's `mcp` grant and `limits` itself, and calls the rooms as the principal `mcp` with its own
token or tagged node, with the caller's name in `X-Agent` as a label. The rooms trust no on-behalf-of header: what
`mcp` may do in a room is the most any caller can do through it. machiya-mcp keeps its route table and `NEVER` fields
as defence in depth.

## What changes in each room

- **vaultkit** (new `identity.py`, `signin` UI pieces): config loading and reload, `resolve`, scrypt, tokens,
  sessions, grants check, throttling, the CLI (`python3 -m vaultkit.identity`: `init`, `add`, `passwd`, `token mint`,
  `token revoke`, `pair`, `device revoke`, `epoch bump`, `check`, `whoami <headers>`). The CLI runs on the host that
  holds the file, and only there: nothing in the rooms can write identities. Tested on its own. Released with a vaultkit tag and
  vendored (this is the one place shared security code belongs).
- **Kura:** refuse `default` and `shared` as vault names in `KURA_VAULTS` (they are keywords in a grant's `vaults`,
  so a private vault called `shared` would otherwise be granted to every agent); replace `allowed()` with
  `resolve` + `read`; the `vault` parameter, `vault:` queries, `/v/` pages,
  `/api/notes` URLs and search rows are cut to the principal's `vaults`; a private vault not granted answers 404 (not
  403, so its name doesn't leak). `DefaultVaultOnlyTest` grows an agent-principal twin.
- **Niwa:** owner powers (`publish`, `dismiss`, `meta`) need `niwa.publish`; `/api/suggest` needs `suggest`; the
  `agent == "web"` checks in `writer.py` become grant checks. Gemini and gopher are unchanged (public, published only).
- **Konbini:** `write` and `areas` grants replace `agent == "web"`; `/p/<slug>/tags?remove=` becomes a POST.
  `KANBAN_TAILNET_USERS` keeps working as the no-file fallback.
- **machiya-mcp:** authenticates callers by token or tagged node; grants and limits per caller principal; calls rooms
  as `mcp` with its own token (no delegation).
  Re-vendor vaultkit (it's on v0.9.4).
- **smallweb:** the same `resolve`; `save` grant.
- **Shiori:**
  - App (macOS/iOS): a per-device token in the Keychain, sent as `Authorization` to Kura, Konbini, Niwa (never to
    Hister or SearXNG). Settings get "Sign in to Machiya": type a pairing code, or paste a token.
  - Extension: Firefox keeps the token in `storage.local` (never sync); Safari asks the app over native messaging.
  - Hosted pages: the browser's `machiya_session` cookie on the shared domain; the web server passes it through on
    `/kura/` and `/konbini/` unchanged. They only read rooms, so the same-origin rule never bites.
  - Linux: a token in `~/.config/shiori/config.json` (0600).
- **Room-to-room:** Niwa → Konbini with Niwa's service token (`NIWA_KONBINI_TOKEN_FILE`). Kura → Hister unchanged.
- **compose:** an `identity` volume with the file and key, mounted read-only into every room; `.env.example` and the
  install guides show the three ways in (Tailscale, proxy, built-in sign-in).

## Phases (one PR each, tests in each)

1. **Design sign-off** (this document) and the Tailscale check (capabilities through `serve.json`/Services).
2. **vaultkit identity core + CLI** (in review: `vaultkit/identity.py`, `tests/test_identity.py`), with a test suite of its own (resolve order, invalid proof never falls through,
   constant-time compares, expiry, epochs, reload, throttling, malformed TOML refuses to start). Owner reviews; tag
   vaultkit.
3. **Kura** (read-only, simplest; proves the API and the vault scoping). In review: machiya-kobo/kura, with the
   contract's new "Identity" section.
4. **Konbini and Niwa** (grants for owner powers; GET write removed; service token for Niwa → Konbini).
5. **machiya-mcp** (callers by principal, its own token, delegation or not).
6. **Built-in sign-in pages and per-user prefs** in all three rooms.
7. **Shiori** (app, extension, hosted pages, Linux).
8. **Docs:** principles (owner gate → identity), contracts (401/403 shapes, `Authorization`, `/api/prefs`), install
   guides, SECURITY.md files. Remove this plan.

Each phase keeps the no-file fallback working, so the stack never has a flag day.

## Testing and review

- Every room: a test per grant (allowed and refused), per proof type, and the "invalid proof never falls through"
  rule; the existing work-vault tests stay green.
- A cross-room test in the machiya repo: one identity file, the three rooms started, an agent token, a household
  person, the owner; a table of who may do what, checked end to end.
- A security review of the vaultkit module before its tag (cookie format, comparisons, throttling, file permissions,
  header-mode bind check).

## Decisions (the owner's answers)

1. **machiya-mcp:** rooms see machiya-mcp only; it enforces each caller's grants and limits itself. No on-behalf-of
   header.
2. **New people:** no grants until given; every room answers 403.
3. **Capability name:** `github.com/machiya-kobo/cap/identity`.
4. **Sessions:** 30 days, renewed on use (hard cap 180 days from sign-in).
5. **Shiori pairing:** both: a one-time code (signed device tokens, `POST /api/pair`) and pasting a stored token.
6. **The CLI** runs on the host with the file; no admin page.
7. Earlier: Tailscale optional (proxy header and built-in sign-in, both); shared code in vaultkit with a tag; Kura
   enforces vault scopes for agents (default and shared vaults only by default); Tailscale tagged nodes for agents;
   one read-only TOML file; per-user prefs in each room's DB; Shiori in scope.
