# Preferences: the settings that follow a person

One set of settings that follows the signed-in person to every Machiya app and every device. The owner asked for it on 2026-10-05 ("If I sign in and then change settings that affect all apps, like theme, I expect them to change in every app") and answered the design's questions the same day. This page is the contract for every reader and writer:

- the rooms (Kura, Niwa, Konbini) and landing, through vaultkit (`vaultkit/prefs.py`, `signin.handle_prefs`, `histerauth.forward_prefs`, `ui/machiya.js`);
- [hister-login](../services/hister-login.md), which keeps the account's copy;
- Shiori's apps, extensions and hosted pages.

Changes to this contract go through the lead, as for the Kura and Konbini APIs. Readers change first (accept old and new), writers after.

## Three kinds of setting

| Kind | Settings | Where they live |
|---|---|---|
| **Shared** | Theme (`palette`), Appearance (`theme`), Text Size (`text_size`), Apps (`apps_hidden`: which rooms the switcher shows), and Shiori's Pills (`pills`) | the account; every app shows them first, in the same order, under **Shared** (Apps only where there is a switcher, Pills only in Shiori) |
| **Per-app** | Kura's Preview Pane, Niwa's Link Previews, Konbini's Group By and Done Cards, Shiori's search and result options, … | the account too, as `<app>.<key>`; the app that reads a key validates its value |
| **This Device** | "Use This Device's Size" (the one Shared setting a device may override), Kura's Obsidian Vault, Offline Copies, Shiori's addresses and Dock icon, every sign-in and token, Shiori's AI settings | the device only; never sent |

**Secrets never go in preferences**: no token, password, key or address with credentials, in any key.

## The schema (v1)

The schema lives in [`vaultkit/prefs.py`](../../vaultkit/prefs.py) and is exported as [`prefs.schema.json`](prefs.schema.json) (`python3 -m vaultkit.prefs`; a test keeps the file current, and Shiori's tests read it as they read `palettes.json`).

| Key | Values | Label |
|---|---|---|
| `theme` | `system`, `day`, `night` (`auto` is stored as `system`) | Appearance |
| `palette` | the ten keys of `vaultkit/palettes.py` (`tokyo-night` … `ayu`) | Theme |
| `text_size` | `xsmall`, `small`, `standard`, `large`, `xlarge` (the house's five steps; Shiori maps its own through `HOUSE_STEPS`) | Text Size |
| `apps_hidden` | a comma list from `shiori, konbini, niwa, kura, hister, searxng, machiya`, stored in that order without repeats; empty = all shown | Apps |
| `pills` | JSON `{"order": [pill id, …], "hidden": [pill id, …]}`, ids `[a-z0-9_-]{1,32}`, at most 32 each; stored compact. The store checks the shape, Shiori the ids | Pills (Shiori only) |
| `language`, `time_zone` | reserved: refused until an app uses them | |
| `<app>.<key>` | `<app>` one of `kura, niwa, konbini, shiori, machiya` (landing); `<key>` is `[a-z0-9_]{1,48}`; the value a string of at most 1024 bytes with no control characters | the app's |

Anything else is **400**. At most 100 keys per person, and a body of at most 512 KiB.

- **Booleans** travel as `"on"` / `"off"`.
- **The browser's names.** On the web, the shared keys are also cookies: `theme`, `palette` and `textSize`, written as `machiya_<key>` on `MACHIYA_COOKIE_DOMAIN`. Apps are `show_<app>` = `true`/`false`, and `apps_hidden` lists the `false` ones. A room's own key is `<room>.<snake_case>` of its local name (`previewPane` → `kura.preview_pane`).

## The API, both sides

| Endpoint | Who calls it | Credential |
|---|---|---|
| `GET`, `PUT /api/prefs` on each room and landing | `machiya.js` | the room's own gate. In `AUTH=hister` mode with the helper it **forwards** (below); otherwise it is the room's own store |
| `GET`, `PUT /v1/prefs` on hister-login's internal port (8081) | the rooms and landing, forwarding | exactly one of `X-Machiya-Session: mhs_…` or `X-Access-Token: <Hister token>`, as for `/v1/check`; anything else is 400 |
| `GET`, `PUT /machiya/api/prefs` on hister-login's public port (8080, on Hister's host) | Shiori's apps (`Authorization: Bearer mhs_…`), extensions, Linux and scripts (`X-Access-Token`, or `Authorization: Bearer <Hister token>`), the hosted pages (the sign-in cookie, through their nginx) | one credential; a `PUT` carried by the cookie must have an `Origin` among the helper's return hosts. There is no CORS: no other site's page may read or write it |

**GET** answers `200` with `Cache-Control: no-store` and `ETag: "<rev>"`; with a matching `If-None-Match` it answers `304`:

```json
{"v": 1, "rev": 42,
 "prefs":   {"theme": "night", "palette": "nord", "text_size": "large", "apps_hidden": "searxng",
             "kura.preview_pane": "on", "konbini.group": "family"},
 "updated": {"theme": 1791200000, "palette": 1791100000, "text_size": 1791200300, "apps_hidden": 1791000000,
             "kura.preview_pane": 1790900000, "konbini.group": 1790900000}}
```

**PUT** takes `{"prefs": {"text_size": "xlarge"}}` (`Content-Type: application/json`). It **merges** the keys sent; `null` removes a key, which puts it back to its default. It answers like GET. Writing the value a key already has changes nothing: neither `rev` nor that key's `updated`.

- `rev` goes up by one with every write that changes something, and never goes back (not even when an account's settings are deleted), so an ETag a client holds can't match a later state.
- `updated` is the server's clock (Unix seconds) when each key was last written. Client clocks are never used.
- Old clients read `prefs` alone and keep working: this is the old `{"prefs": …}` shape with `v`, `rev` and `updated` added.

**Errors** are JSON `{"error": "…"}`:

| Status | Meaning |
|---|---|
| 400 | not `{"prefs": {…}}`, a key or value the schema refuses, or (on `/v1/prefs`) not exactly one credential |
| 401 | signed out: `{"error": "sign in", "signin": "<the helper's sign-in address>"}` |
| 403 | a PUT carried by a cookie from another site |
| 405 | another method |
| 413 | body too large |
| 415 | not JSON |
| 503 | the store, the helper or Hister can't answer, or the room is in its Tailscale fallback (no account while sign-in is down): `{"error": "preferences unavailable"}` |

## Who owns the data

**One store, in hister-login**, keyed by the Hister user. The key is `hi:<sha256(username)[:32]>` (the rooms' existing form, so Kura's rows carry over), kept in its own file, `prefs.sqlite3`, beside the sessions file. The helper derives the key **only from the credential it resolves itself**: no endpoint takes a user id, so a caller can only read or write the account it proves.

| Caller | Account preferences? |
|---|---|
| a Hister sign-in (browser session, an app's `mhs_` id) or the owner's Hister token | yes |
| the Tailscale fallback, `open` mode | no: 503 or the room's own store |
| a room with no helper (identity file, Tailscale, open, or hister mode without `*_AUTH_URL`) | the room's own store, with the same API and the same client rules |

**Sign-out leaves preferences alone**: they are account data, not session data. `hister_login.py prefs delete --user NAME` removes them, for an account that is deleted (and might be made again under the same name).

## The forwarding rule (rooms and landing in hister mode)

A room keeps its same-origin `/api/prefs`, so `machiya.js`, the page's `<meta name="machiya-prefs">` and the service worker's bypass don't change. In hister mode with the helper (`*_AUTH_URL` set), `vaultkit.histerauth.HisterAuth.forward_prefs(result, method, headers, body, origins)` does this:

1. It passes the request to `/v1/prefs` with the **caller's own credential**: the `machiya_sso` cookie's id as `X-Machiya-Session`, or the caller's Hister token as `X-Access-Token`. It never sends a user id.
2. A `PUT` carried by the cookie must be same-origin with the room (`origins`: its public address), else 403. One with `Authorization` or `X-Access-Token` is a client's.
3. `If-None-Match` goes along, and the helper's answer comes back as it is: 200 with its `ETag`, 304, 400.
4. The helper unreachable or failing gives 503. Signed out at the helper gives 401 with the sign-in address.
5. In the fallback (the owner's Tailscale login, sign-in unavailable) the answer is 503: there is no account then, and the page keeps its local values.
6. It returns `None` when the room has no helper. The room then answers from its own store (`signin.handle_prefs`), as before.

**First render.** The `/v1/check` answer carries the account's Shared values as `prefs`. The room caches them with the check (`Result.prefs`, at most 30 s old) and passes them to `shell.prefs(cookies, account=result.prefs)`. So a fresh browser with no `machiya_*` cookies is drawn in the person's theme from its first page. A cookie always wins over the account here; `machiya.js` brings the cookies up to date right after.

## The client rules (conflicts)

**Last write wins, per key**, in the order writes reach the store. Every client follows these rules (vaultkit's `machiya.js` does for the web rooms, and Shiori's `SharedSettings` must too):

1. **Send only the keys the person changed.** A Text Size change on the phone must not carry the Theme the laptop changed a minute ago.
2. **A change that can't be sent** (offline, 5xx) waits as **pending**: in `localStorage["machiyaPrefsPending"]` on the web, and in App Group `prefsPending` for Shiori. It goes **first** at the next contact, before the server's answer is applied. A 400 drops it (the store will never take it); 401, 403 and 404 keep it for later.
3. **On load and on return** (after 30 s or more away, with `If-None-Match`), the server's value wins, with one exception. If the server still says what it said at the client's last contact (same value, same `updated`) while this client now holds something else, then the person changed it here since, perhaps in another room of the same browser. This client's value is newer, and it is sent. The client keeps the last answer (`localStorage["machiyaPrefsSeen"]`: `rev`, `prefs`, `updated`) to tell the two apart.
   This rule is the fix for the revert of 2026-10-05: one room's stale copy undid a theme picked in another room.
4. **Fill the blanks.** A value this client has that the account doesn't have yet is sent, once. It never overwrites a value the account already has. When the account no longer has a key the client saw before, it was removed: the client goes back to the default.
5. **Unknown values are never applied.** The schema's lists decide for the shared keys, and the app's own spec for its keys.
6. **Failures are silent.** The cookies (web) and the App Group copy (Shiori) stay the first-paint and offline path. The Shared section's state line says "Sign-in is unavailable…" instead.

A change made offline can override a newer one from another device when it lands. For settings, that is the expected "the last thing I did wins".

**When a change shows elsewhere:**

| Where | How |
|---|---|
| the page you're on | at once |
| other tabs of the same room | at once (`BroadcastChannel("machiya-prefs")`) |
| other rooms in the same browser | on return to their tab, from the shared cookies, with no network |
| other devices, Shiori's native apps | on the next load, and on return to the tab or app after 30 s or more (a 304 when nothing changed); the apps on foreground and on opening Settings |

There is no push stream and no polling (the owner, 2026-10-05).

## "Use This Device's Size"

Text Size is the one Shared setting a device may override (the owner, 2026-10-05). The This Device section has a switch, **Use This Device's Size**. When it is on, a second picker sets this device's own size:

- On the web it is the cookie `machiya_textSizeDevice` (shared by the rooms in that browser). The page shows it instead of the account's size, and it is never sent anywhere.
- The Shared Text Size row keeps showing and changing the account's size, which every other device follows.
- Shiori: Dynamic Type or a local size, the same rule.

## Migration (once, at the switch)

1. **Shared keys from the rooms' old stores.** Run `hister_login.py prefs import --user <Hister username> [--tailscale <login>] <file>…` on the helper's host, over Kura's, Niwa's, Konbini's and landing's old `prefs.sqlite3`, which it opens read-only.
   - For each key, the value with the newest `updated` across the files wins. It is kept only when it is newer than what the account already holds, so a second run changes nothing.
   - A file's rows count as the user's when their principal is `hi:<sha256(user)>`, the `tailscale_uid` of a `--tailscale` login, or a `--principal` given. A file with a single principal is taken as the user's, and the command says so.
   - `--dry-run` shows the result without writing. `prefs show --user` shows what the account holds afterwards.
2. **Values only devices hold** (every `show_*`, the per-app settings, all of Shiori's): fill the blanks (client rule 4). The first device to sync seeds a key, and every device then follows.
3. **The rooms' `prefs.sqlite3` files** stay, read-only, for one release (a rollback), then they are deleted.
4. **The Shiori web app** stops calling Kura's `/kura/api/prefs` and uses `/machiya/api/prefs`. Kura's `/api/prefs` keeps answering older clients, forwarding in hister mode.

**Rollout order:** the helper (0.2.0), then the rooms and landing together (a room still on the old `machiya.js` writes its stale store's value into the shared cookie, which the new rule 3 would then send to the account), then Shiori's hosted pages, then the apps.
