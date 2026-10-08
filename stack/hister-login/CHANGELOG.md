# Changelog: hister-login

## 0.5.4

- vaultkit re-vendored (vaultkit 0.27.4 → 0.29.0): identity headers are believed only from the trusted proxies inside vaultkit itself, single-use pairing codes, the indexing speedups, and the shared hover and card styles. Base images pinned by digest.

## 0.5.3

- **Shiori's pill hover** (vaultkit 0.27.4): an unselected pill under the pointer fills with 24% of its colour, its text in a shade that stays at 4.5:1 on that fill in every theme.

## 0.5.2

- **A readable pill hover** (vaultkit 0.27.3): an unselected pill under the pointer lifts onto the raised colour with a small shadow, its text in a shade that stays at 4.5:1 in every theme. Also brings 0.27.2's `.sidehead` and `.rows`.

## 0.5.1

- **Further toward Shiori** (vaultkit 0.27.0, the style guide's round 2): Shiori's result cards, Title Case section headings, and the header's current page as a raised pill with no underline.

## 0.5.0

- **Changed default: `HISTER_LOGIN_LEGACY` is now `none`.** Unset, the helper sets no shared-domain `machiya_sso` and rooms refuse a browser's helper id and Hister's raw token (`401 legacy-off`). If an old room or client still needs one, set it: `domain-cookie`, `hister-token`, or both. Each works as before.
- **`/v1/nginx` hands Hister's session only to the hosted pages.** It answers with `X-Hister-Cookie` only when the origin in `X-Machiya-Room` is in `HISTER_LOGIN_PROXIED_ORIGINS` and the room session was made for that same origin. Before, any caller of the internal port could name its own room and trade a Kura or Konbini session, or an app's id, for the raw Hister session. Everything else now gets a 401, as a signed-out browser does. The hosted pages' nginx needs no change.

## 0.4.3

- **The sign-in and sessions pages have their tab icon again**: they wear the Machiya room, and the page shell's `machiya-small.svg`, `machiya.ico` and `machiya-apple-180.png` are now served under `/machiya/static/icons/` (before, the icon link answered 404).
- vaultkit re-vendored (0.24.0 → 0.26.3: Shiori's outlined chips and pills, the contrast fixes).

## 0.4.2

- Stops on SIGTERM in about a second (it ran as PID 1 with no handler, so every `docker stop` waited 10 s and ended in a SIGKILL).
- Vendors vaultkit v0.24.0 (the shared components' contrast fixes).

## 0.4.1

- The sign-in page has no `<form>`, as Hister's own sign-in page: the button's click or Enter signs in, and nothing on the page can post to the helper. A password manager that submits the form from its extension (0.4.0's `form.submit` catch never sees that) no longer lands on "Press Sign In" or loops; one that only ever submits a form now leaves the fields filled for a click. `dev/signin_check.py` drives typed, Enter, manager-fill, auto-submit and submit-only sign-ins in Chromium, WebKit and Firefox.

## 0.4.0

- The sign-in page names the app you're signing in to ("Sign In to Shiori", "Sign In to Kura"…), from the return address, and its header says Machiya.
- A password manager's auto-submit works: it skipped the page's script, posted the form straight to the helper and ended on "Only an app's sign-in is confirmed here". The script now catches `form.submit()`, and the fields have no names, so a direct submit carries no password; it comes back to the form with "Press Sign In to finish signing in."

## 0.3.2

- Shorter copy (the owner's copy-editing pass, 2026-10-05): the app sign-in's Continue page and the sessions page's footnotes are a sentence each; Sign Out Everywhere is the last row of Sessions instead of its own section. Same forms, same behaviour.

## 0.3.1

- **Haiku:** the store keeps SQLite's rollback journal there instead of WAL. Haiku's SQLite can't share a WAL file between processes, so `hister_login.py token mint` failed with "locking protocol" while the server ran (found by the fleet run's Haiku leg). Everywhere else it stays WAL.

## 0.3.0

- **A cookie per room, host-only** (decided 2026-10-05, after the sweep's LEAD-1/KONB-2). The helper's own session is now `__Host-machiya_sso` on Hister's host only, and each room keeps a `__Host-machiya_sso_<room>` of its own (vaultkit `histerauth`, the rooms only re-vendor):
  - a room sends a browser here with `state=<SHA-256 of a nonce>`; once the helper knows the browser it sends it to the room's `/machiya/callback?code=mhc_…`;
  - the room trades the code at the new internal **`POST /v1/redeem`**: one use, 60 s, only for that room's origin (`X-Machiya-Room`) and that browser's nonce (`X-Machiya-State`), only while its helper session lives, for a room session `mhr_…`;
  - `/v1/check`, `/v1/prefs` and `/v1/signout` take `mhr_…` with `X-Machiya-Room` (another room's session is `401 wrong-room`), and answer `kind` and `room`;
  - a sign-in without a state gets no code: the browser goes back to the address as it is.
- **Sign-out ends every room.** A room's sign-out (with its `mhr_…`), `/machiya/signout` and the sessions page end the Hister session, the browser's helper id, and every room session and unused code made from it. The id is remembered as ended for 30 days (a new `ended` table), so the next automatic trip from any room shows the page; the marker `__Host-machiya_sso_out` is host-only now.
- **Room tokens** (`mht_…`) for headless callers instead of Hister's raw token: scoped to rooms, kept as a hash, made on the sessions page (Room Tokens) or with `hister_login.py token mint|add|list|revoke`. A token opens only the rooms it names, never Hister.
- **`HISTER_LOGIN_LEGACY`** (default `domain-cookie,hister-token`, so this release changes nothing for old rooms): while `domain-cookie` is on, the old `machiya_sso` is still set on `MACHIYA_COOKIE_DOMAIN` and rooms may use a browser's helper id; while `hister-token` is on, rooms may use Hister's raw token. **`none` is the switch** (docs/identity.md, the migration); the helper logs, at most hourly per room, each legacy credential it still accepts.
- **The hosted pages' hosts** (`HISTER_LOGIN_PROXIED_ORIGINS`): their nginx sends `/machiya/start`, `/machiya/callback`, `/machiya/signout`, `/machiya/api/prefs` and `/machiya/static/` here with their `Host`, and the helper is their room (`__Host-machiya_sso_shiori`). `/v1/nginx` takes that room session with `X-Machiya-Room` (or the cookie itself). The pages need no change to sign in.
- The sessions page shows the rooms each browser has opened.
- Tests: the code (once, 60 s, its room, its nonce, its helper session), room sessions bound to their room, sign-out across rooms and the page after it, Hister's own sign-out, room tokens (the page and the command), prefs with a room session, the switch, the proxied origins' whole round trip, and keep-alive and smuggling on `/v1/redeem`, `/machiya/callback`, `/machiya/start` and a proxied sign-out.
- vaultkit re-vendored (room sessions).

## 0.2.1

- **The app sign-in never finishes on another site's link** (the 2026-10 sweep, LEAD-3). Any page could send the owner to `/machiya/signin?app=1&return=shiori://anything`. Hister's Lax cookie went along, and the helper created an app session and sent `#sid=…&hister=…` to whatever app owns the `shiori:` scheme on that device. Now:
  - the app return is exactly `<scheme>://signed-in` (HisterKit's `callbackURL`), no other path;
  - `app=1` finishes, or goes on to a provider, only on the app's own web session (`Sec-Fetch-Site: none`) or this page's own navigation (`same-origin`);
  - anything else, a missing header included, gets a confirmation page: its Continue is a same-origin `POST /machiya/signin`, and no session or return cookie exists before it.

  Shiori's apps change nothing: ASWebAuthenticationSession's first load is `none`, and the password form's return is same-origin.
- **Try Again links to this path only** (the 2026-10 sweep, LEAD-6): the link on the "Sign-In Is Unavailable" page is the request's path and query, never its raw target, which could be `//other.host/…` (routing reads only the path). Python 3.12+'s http.server already folds a leading `//`; this no longer depends on it.

## 0.2.0

- **The settings that follow a person** (decided 2026-10-05; [docs/contracts/prefs.md](../../docs/contracts/prefs.md)): the helper keeps one store of each Hister user's settings, in its own file `prefs.sqlite3` beside the sessions file (`HISTER_LOGIN_PREFS_DB`, 0600), keyed by `hi:<sha256(username)[:32]>`, which is derived only from the credential the helper resolves itself (no endpoint takes a user id).
  - The internal `GET`/`PUT /v1/prefs` is for the rooms and landing, which forward their `/api/prefs` with the caller's own `X-Machiya-Session` or `X-Access-Token` (exactly one, as for `/v1/check`).
  - The public `GET`/`PUT /machiya/api/prefs` is for Shiori's apps (`Bearer mhs_…`), extensions and scripts (a Hister token), and the hosted pages through their nginx (the sign-in cookie; a `PUT` needs an `Origin` among the return hosts; no CORS).
  - The answer is `{"v", "rev", "prefs", "updated"}` with `ETag: "<rev>"` (304 on `If-None-Match`). A `PUT` merges only the keys sent and is checked against vaultkit's schema (400). Values are never logged.
  - `/v1/check` also carries the Shared values as `prefs`, so a room draws a fresh browser's first page in the person's theme.
- **`hister_login.py prefs import|show|delete --user NAME`.** `import` seeds an account from the rooms' old `prefs.sqlite3` files, which it opens read-only: for each key the newest `updated` across the files wins, and only when it is newer than the account's own, so a second run changes nothing. `delete` is for an account removed.
- **Automatic sign-in** (decided 2026-10-05: "Yes, Tailscale automatically"). With `MACHIYA_SIGNIN_PROVIDER=oidc` the rooms and landing send a page that needs a sign-in to `/machiya/signin?…&provider=oidc&auto=1`, and the helper goes straight to Hister's OIDC sign-in: tsidp knows the device, so there are no taps, and each installed web app signs itself in.
  - **A deliberate sign-out** sets the marker `<sign-in cookie>_out` (`machiya_sso_out`) on the shared domain for 30 days: `/machiya/signout`, the sessions page signing this browser out, or a room's `/signout` (vaultkit). With the marker, `auto=1` shows the page instead, so Sign Out never bounces straight back in. The next successful sign-in clears it.
  - **A failed round trip:** a callback that doesn't finish a sign-in this helper started lands on the page with "Sign in with … didn't work. Try again, or sign in with your password." and a 10-minute marker, so it never loops.
  - **A tap** on "Sign In with Tailscale" (`provider=` without `auto`) always goes through.
- Tests: the store and its keys, every credential, the refusals, a cookie `PUT`'s `Origin`, no CORS, keep-alive (different people's prefs requests down one connection) and an unread prefs body that never becomes a request; the import rule; the automatic sign-in, the marker and a failed round trip.
- vaultkit re-vendored (the `prefs` module, `MACHIYA_SIGNIN_PROVIDER`).
- **Starts on OpenBSD** (the fleet test): vaultkit no longer computes a scrypt hash at import, which crashed it at start on a Python without `hashlib.scrypt` (LibreSSL).

## 0.1.3

- `/machiya/signin?provider=<name>` (a provider in `HISTER_LOGIN_PROVIDERS`, e.g. `oidc`) sets the return cookie as the page does and goes straight to Hister's `/api/oauth?provider=<name>`: Shiori's "Sign In with Tailscale" is one tap. An unknown provider shows the page; a browser already signed in still finishes at once.

## 0.1.2

- **Keep-alive (security):** a request body the helper didn't read (a GET's, a refused or unknown POST's) stayed on
  the connection and was parsed as the next request: on `:8080`, one smuggled past Tailscale Serve with headers Serve
  never saw, whose answer could reach the next person on that connection. Such a request now ends with
  `Connection: close`, and every request starts with no state from the one before (`handle_one_request`). Tests send
  several people's requests down one kept-alive connection, as Serve does.
- `MACHIYA_SSO_COOKIE` names the sign-in cookie (default `machiya_sso`), so a second stack under the same cookie domain
  (a dev stack on the same tailnet) never reads the other's. The rooms and landing read the same setting
  (vaultkit `histerauth`, unreleased).

## 0.1.1

- The container's healthcheck reads the reply before closing: closing early left a `ConnectionResetError` traceback in the helper's log every minute.

## 0.1.0

The first version (phase 0 of the Hister sign-in): the opaque `machiya_sso` cookie, the sign-in page (Hister's own
password login and OIDC link), the OAuth callback shim, `/v1/check`, `/v1/signout`, `/v1/nginx`, `/healthz`, the
sessions page, the app flow (`app=1` and `POST /machiya/api/app-session`), and the SQLite state (ids stored as hashes,
sign-outs retried while Hister is unreachable). The dev stack and gate-0 checks are in `dev/`.
- `GET /machiya/healthz` (public, for probes) answers 200 `{"ok": true, "hister": "ok"|"down"|"user-handling-off"}`
  whenever the helper itself works, and 503 only when its own state (the SQLite file) fails, so a Hister outage alerts
  once (Hister's own probe). The internal `GET /healthz` stays 503 unless the helper and Hister are both fine: the
  rooms' health flag needs that.
