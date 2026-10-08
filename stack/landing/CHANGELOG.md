# Changelog: machiya-landing

## 0.6.1

- **Further toward Shiori** (vaultkit 0.27.0, the style guide's round 2): Shiori's result cards, Title Case section headings, and the header's current page as a raised pill with no underline.

## 0.6.0

- **Shiori's look** (vaultkit 0.26.3, the style guide): outlined chips and pills; hovering thickens an outline instead of filling it, so text stays at 4.5:1 in every theme.
- **The browser-tab icon is the small house** (`machiya-small.svg`, plus `machiya.ico` for older browsers), as in every room.
- vaultkit re-vendored (0.23.0 → 0.26.3, which also brings 0.24/0.25's contrast fixes).

## 0.5.1

- Shorter copy: the status page's empty Recent Deploys line is one sentence (the owner's copy-editing pass, 2026-10-05).

## 0.5.0

- **Its own sign-in cookie** (vaultkit 0.22; decided 2026-10-05 after the sweep's LEAD-1): landing keeps a host-only `__Host-machiya_sso_landing`, made from a one-time code hister-login sends back to `/machiya/callback`, instead of reading the shared `machiya_sso` on the tailnet's whole domain (still read while hister-login's `HISTER_LOGIN_LEGACY` allows it). One sign-in still covers everything: the first visit makes one silent trip. Sign Out sets landing's own marker; the helper remembers the ended session, so no room signs the browser straight back in.
- **Room tokens for the owner's reads**: when `LANDING_TOKEN_FILE` holds a room token from hister-login (`mht_…`, scoped to Kura, Niwa and Konbini), it goes to them as `Authorization: Bearer` (over https, or loopback), and Hister's token (`LANDING_HISTER_TOKEN_FILE`) goes to Hister only. Without one, as before until the switch.
- vaultkit re-vendored.

## 0.4.1

- **A chunked body is refused** (the 2026-10 sweep, LEAD-2): `PUT /api/prefs` and `POST /signout` read their body with vaultkit's `signin.read_body`, so a `Transfer-Encoding: chunked` body, a duplicate or odd `Content-Length`, an oversized or a short one answers 413 and closes the connection. Before, a chunked body was read as empty and its bytes were parsed as the next request on a kept-alive connection (behind a proxy other than Tailscale Serve).
- **Today's Saved & Read links only http(s), gemini and gopher addresses** (LEAD-5): a page Hister stored under `javascript:`, `data:`, `file:` or any other scheme is left out, as Niwa's garden links already were. The page's CSP blocked them; this is defence in depth.

## 0.4.0

- **The Shared settings follow you** (decided 2026-10-05; [docs/contracts/prefs.md](../../docs/contracts/prefs.md)): in `LANDING_AUTH=hister` mode with the helper, `/api/prefs` is the account's. It is forwarded to hister-login's `/v1/prefs` with the caller's own credential and never kept here, so a theme picked in Kura shows here on the next load, and on every other device. In the Tailscale fallback it answers 503 (no account while sign-in is down) and the page keeps its cookies. Without the helper, `LANDING_PREFS` as before.
- **Settings starts with Shared**: Theme, Appearance, Text Size and Apps, "Follows you on every Machiya app when signed in." and where they are kept now ("Signed in as … Saved to your account.", or "Sign-in is unavailable…"). Then **This Device** (Use This Device's Size), Account and About.
- **Automatic sign-in** with `MACHIYA_SIGNIN_PROVIDER=oidc`, as the rooms: a page that needs a sign-in goes through tsidp with no taps. Sign Out sets the marker that makes the helper show its page instead until the next sign-in.
- A fresh browser's first page is drawn in the account's theme (the sign-in check carries it).
- vaultkit re-vendored (v0.21 for the settings, untagged).
- **Starts on OpenBSD** (the fleet test): vaultkit no longer computes a scrypt hash at import, which crashed it at start on a Python without `hashlib.scrypt` (LibreSSL).

## 0.3.4

- **Repos you can search** (the owner): Hister's card shows **N repos**, the total of `metadata.source:code metadata.code_kind:repo` (code-import's repo cards; with the owner's token, polled with the page count), hidden when 0 or unavailable.
- **code-import** joins the stack services, from its `status.json` (`LANDING_CODE_STATUS`; a missing file is quiet: "Not in this stack"): its version, the forges and the repos it indexed; Sync gets **Repos indexed**: the last good run, documents added in the last day (the page's own samples), failed runs. Behind after an hour without a good run, broken when it says `ok: false`.

## 0.3.3

- **Code documents stay out of the pages** (docs/contracts/hister.md, the code documents section): Hister's newest-page search, which feeds Saved & Read and Hister's newest page, now ends ` -label:vault -metadata.source:vault -metadata.source:code`, so code-import's repos, issues and PRs never show up as saved or read pages. Hister's page count (`/api/stats`) is Hister's own total and still includes them.
- **vaultkit v0.20.0**: `MACHIYA_SSO_COOKIE` names the sign-in cookie the page reads and clears (default `machiya_sso`), as the helper and the rooms do, so a dev stack's own cookie (`machiya_dev_sso`) and production's no longer collide.

## 0.3.2

- **Fixed (security): a body the page didn't read became the next request.** On a kept-alive connection, a request body landing never read (a refused `PUT /api/prefs`, a `POST` it answers 405, any `GET`'s) was parsed as the next request: one smuggled past Tailscale Serve, with a `Tailscale-User-Login` Serve never saw, so anyone who could reach the page could be served as the owner (and the answer could reach the next person on that connection). Such a request now ends with `Connection: close`. Tests send several people's requests down one kept-alive connection in tailscale mode too.

## 0.3.1

- **Fixed: one request's sign-in answer decided the next.** The page speaks HTTP/1.1 with keep-alive, so one handler serves many requests on a connection, and Tailscale Serve sends different people's requests down the same connection. The Hister sign-in's answer was kept on the handler, so a request inherited the answer of the one before it on that connection: with the helper down, the tailnet owner was still sent to sign in instead of getting the page with the banner, and the owner's Hister token (`X-Access-Token` or `Bearer`) got "401 sign in". Worse, a request without a credential could have inherited a signed-in answer. Every request now starts with no answer. Tests send their requests down one kept-alive connection, as Serve does, and cover the helper's name not resolving.

## 0.3.0

- **Settings and sign-in like the rooms** (the owner): `LANDING_AUTH=hister` through vaultkit's `histerauth`, as Konbini and Niwa have it: Hister's users are the sign-in (`LANDING_AUTH_SIGNIN_URL`, `LANDING_HISTER_USERS`, `LANDING_AUTH_URL`, `LANDING_PUBLIC_URL`), with **the Tailscale fallback** (`LANDING_USERS`, `LANDING_BIND_BEHIND_PROXY=1`), so the status page still opens, with the banner, when Hister or the helper is down. Signed out, a page goes to sign in and an API call gets 401 JSON; `POST /signout` (same-origin) ends the Hister session; the pages carry the sign-in meta (machiya.js adds Sign Out to the Rooms menu). Settings gains **Account** (who, how, Sign Out) and the header the person button; theme and text size follow the person through `GET`/`PUT /api/prefs` (`prefs.sqlite3` beside `LANDING_STATE`, or `LANDING_PREFS`). `/healthz`, `/api/changelog` and the static files stay open. The default stays `tailscale`.
- **Counts read as k** (the owner): from 1,000 one decimal and k, M or B, a trailing .0 dropped, cut to one decimal (1,150 → 1.1k, 12,340 → 12.3k, 999,999 → 999.9k), everywhere on `/` and `/status`.
- **Shiori**: the card shows its version only (no build hash); Recent Deploys notes a new version, and a rebuild of the same version as "0.1.0 (build 1a5633c → 381f496)"; what a version brought comes from the hosted build's `/_shiori/CHANGELOG.md` ("## 0.2.0 (2026-10-04)" headings). Web searches read "Searches 112 today · 3.4k mo · 41.2k yr".
- Konbini's `/api/health` is asked with the owner's token too (the same rule: Konbini's configured address only): Konbini gives the owner its full view (`sync`, `livesync`, `boards`), so the Board synced row keeps its LiveSync and pending writes, and "N in WIP" comes from `boards` without listing `/api/cards`. Refused, it is asked again without the token and the limited view stands.

## 0.2.3

- **The rooms run `AUTH=hister`**, so their owner-only reads (Konbini `/api/cards` for WIP, Working On and Due Soon; Kura `/api/vaults` and `/api/recent`; Niwa's `/feed.xml`) answered 401 and `/status` lost "N in WIP" and the vault totals, and Today went empty. They now carry the owner's token, the existing `LANDING_HISTER_TOKEN_FILE` (re-read when it changes), as `X-Access-Token`: only to Kura, Konbini and Niwa at their configured address (`MACHIYA_ROOMS`, `LANDING_APPS`, `LANDING_PROBES`), only over https (or loopback), never across a redirect (none is followed) and never to another origin. The open probes (`/api/status`, `/api/health`, `/api/changelog`) stay credential-free. A refusal still degrades quietly.
- Kura: when `/api/vaults` is refused, the card shows the open status's `vault_count` (Kura 0.6.13) beside the default vault's notes ("317 notes · 4 vaults").

## 0.2.2

- The image carries `today.py`: 0.2.1's Dockerfile listed the modules by name and missed it, so the container crashed at start. It now copies every module, and a test checks the Dockerfile against the files.

## 0.2.1

- **More stats on `/status`** (the owner, through shiori): Konbini shows **N in WIP** beside its card count (from Konbini's per-board counts in `/api/health` when it has them, else counted from `/api/cards`); Kura shows **N vaults · N notes** across every vault from `GET /api/vaults` (counts only: a private vault's name never leaves the probe), and the launcher's Kura tile counts all of them too; Shiori shows **web searches** today, this month and this year ("312 searches today · 4,210 this month · 51k this year") from a counts file the deployment writes (`LANDING_SEARCH_COUNTS`; every SearXNG search; left out quietly when the file is missing). Shiori's `status.json` `hister` field (the Hister it was built against) isn't shown: Hister's own row shows the running one.

## 0.2.0

- **`/` is a launcher now** (the owner's pick, "Launcher + Today"): the day and a status pill ("All up", "1 needs a look"; it links the status page), a **Search everything** pill that sends the query to Shiori's search page (`LANDING_SEARCH_URL`, another origin: a plain form, no live results; the page's CSP allows that one form target), the four rooms with one count each (pages, cards in WIP, published, notes), and **Today**: Working On (Konbini's WIP cards with their next step), Due Soon (cards due in the next 14 days, or overdue), Notes Changed (Kura's newest), Saved & Read (Hister's newest pages, a feed reader's reads and stars marked), Garden (Niwa's tended notes of the last 30 days). Each section leaves itself out when its room is missing or refuses. `GET /api/today` is the same as JSON.
- **The status page moves to `/status`.** Niwa shows its published count only; Hister its version (from its MCP `initialize`, the one POST this page sends, with the owner's token when set, at most every 15 minutes); vault-mirror its version (0.1.1's `status.json`); Shiori its version, build, AI ("AI on · N left today", "AI down") and feed health from `/_shiori/status.json`, `/shiori/ai/status` and `/shiori/healthz` (the build stamp when the file isn't there yet), with no vaultkit line.
- **feed-import** joins the stack services, from its `status.json` (`LANDING_FEED_STATUS`), and Sync gets **Feeds read**: the last good run, pages added in the last day (from the page's own samples), failed runs. **Pages indexed** is judged now: fine while Hister is up and its newest page is under three days old, behind after that, red only when Hister is down.
- vaultkit after v0.19.0 (the Rooms menu's house row reads "Machiya · home"); re-vendor at the release tag.

## 0.1.2

- Hister shows Up again: its `/health` answers 200 with an empty body, which 0.1.1 read as "not healthy". Any 2xx from `/health` now means up; the test's fake Hister answers the same way.

## 0.1.1

- **Hister with users** (phase 1 of the Hister sign-in): Hister is up or down by its open `GET /health` now, not `/api/stats`, which answers 403 once Hister's user handling is on. The page count and the newest page are best effort: with **`LANDING_HISTER_TOKEN_FILE`** (the owner's token, `X-Access-Token`, to Hister only; re-read when the file changes; a set file that is missing or empty stops the start) they keep showing; without it (or with a refused token) Hister is still up and the card says the count needs the token. Every Hister call, `/health` included, sends `Origin: hister://`; the token is never logged or in the page's JSON.

## 0.1.0

- The stack's front door: a page that links every room (Shiori, Konbini, Niwa, Kura), the engines (Hister, SearXNG) and the stack's services (machiya-mcp, smallweb, vault-mirror), in the rooms' look (vaultkit 0.17.2's shell, tab bar and Rooms menu; both themes, every palette).
- **Status:** each app's state (up, behind, starting, error, down, or "Not in this stack" when it has no address), its version and vendored vaultkit, polled from its own status endpoint in the background (GETs only, 3 s timeout, every 60 s); `/api/status` is the same as JSON.
- **Sync:** the vault's last pull (Kura, the mirror's `status.json`), the board's commit and LiveSync cycle (Konbini), the garden's unpushed writes (Niwa), the notes' last push into Hister (Kura) and Hister's newest page. Behind is a calm yellow dot; only broken is drawn as an alert.
- **Recent deploys:** version changes the page saw itself, kept in `LANDING_STATE`, with what each version brought from the app's own `GET /api/changelog` (its `CHANGELOG.md`; `LANDING_CHANGELOGS` overrides an address). An app that doesn't serve one shows its versions only.
- Owner-only: `LANDING_AUTH=tailscale` with `LANDING_USERS`, or `open` on localhost with a Host allow-list; with `MACHIYA_IDENTITY_FILE`, the `landing` `read` grant.
- `GET /api/changelog`: this page's own changelog. vaultkit after v0.17.2, untagged (the house row and footer link, the `landing` room, `vaultkit.changelog`); re-vendor at the release tag.
