# Shared UI (`ui/` + `vaultkit.shell`)

This is how the web rooms (Kura, Niwa, Konbini) put the [design language](design.md) into practice. It's vendored with vaultkit: `./vendor.sh <service>/app <tag>` copies `vaultkit/*.py` and `ui/machiya.css`, `ui/machiya.js` into `app/vaultkit/` (the ui files land in `app/vaultkit/ui/`). The build's `python3 -m vaultkit.verify` covers them, so never edit them in place.

## Wiring an app

1. **Serve the two files**: `/static/machiya.css` → `app/vaultkit/ui/machiya.css`, `/static/machiya.js` → `app/vaultkit/ui/machiya.js` (ignore the query). `shell.page` links them as `?v=<content hash>`, so they may be cached as immutable; put `shell.UI_VERSION` into your service worker's cache name.
2. **Rooms**: set `MACHIYA_ROOMS` in the stack's compose (deployment config, not app code), for example:
   `MACHIYA_ROOMS=shiori=https://shiori.example.ts.net,konbini=https://konbini.example.ts.net,niwa=https://niwa.example.ts.net,kura=https://kura.example.ts.net,hister=https://hister.example.ts.net,searxng=https://searxng.example.ts.net`
   `shell.rooms()` reads it. Unset, there's no switcher (standalone). Your own `*_NIWA_URL` / `*_KONBINI_URL` settings can stay for in-page links.
3. **Pages**: build every page with `shell.page(ctx, room, title, body, tabs, current, stylesheets=["/static/<app>.css?v=…"], scripts=["/static/<app>.js?v=…"], icons={…})`, with `shell.header(room, nav, current, links, subtitle, tools)` at the top of `body` and `shell.footer(room, status, links)` at the end.
   - `ctx = shell.prefs(self.headers.get("Cookie"))` gives `.theme` (system/night/day; `auto` is read as system) and `.text` (text size).
   - `room` is `kura`, `niwa` or `konbini`: it sets the seal, the wordmark and `--room`.
   - Nav labels are Title Case. Up to four phone tabs, plus the automatic Rooms tab, whose menu ends in Settings.
4. **Theme**: keep `/theme?set=` working as a no-JavaScript fallback (set the `theme` cookie, accept `system` and `auto`), but link the header to `/settings`, not the old night/day/auto links.
5. **`/settings`**: `shell.settings_page([shell.appearance_section(ctx), <your section>, shell.apps_section(room, links, shown), shell.about_section(room, VERSION, status_text, vaultkit)], room)`.
   - Build your section with `shell.toggle`, `shell.select` and `shell.text_field` rows, plus a footnote written in Shiori's style: name each setting and say what on and off do.
   - `shown` = `{k: False}` for rooms switched off (from the `show_*` values; machiya.js hides them client-side too).
   - Settings are per device: localStorage under `<room>Settings`, and a cookie for anything marked `cookie=True` that the server needs for the first render.
   - React to live changes by listening for `machiya:setting` events.
6. **CSS**: load machiya.css first, then your own. Delete your copies of the tokens, base, header, tab bar, chips, settings and footer rules, and keep only the room's own layout (Kura's three columns, Niwa's garden, Konbini's lanes). Use the tokens (`--room`, `--house`, `--fs-*`, `--sp-*`, `--r-*`) and the `.is-note` / `.is-card` / `.is-garden` classes with `.thing`, so things wear their room's colour.
7. **Verify**: take screenshots before and after (desktop and phone, both themes). Check no page is wider than the viewport at 390px, and that there are no console errors.

## Shared settings, room search, update toast

**Shared settings (settings stay per device, and one choice covers every room).**
- Set `MACHIYA_COOKIE_DOMAIN` in the stack's compose (for a tailnet: `<tailnet>.ts.net`). `shell.page` puts it on `<body data-cookie-domain>`.
- machiya.js then writes theme, textSize and the Apps `show_*` switches as `machiya_<key>` cookies on that domain. `ts.net` is on the Public Suffix List, so `<tailnet>.ts.net` is the site and every room shares them.
- `shell.prefs()` prefers `machiya_<key>` over the room's own cookie. Nothing to change in the apps beyond vendoring.
- Unset (standalone on its own host): per-room cookies, as before.
- Shiori's web app reads the same `machiya_theme`, `machiya_textSize` and `machiya_show_*` cookies.

**Server preferences (theme and text size follow the person to a new device).**
- A room that serves `/api/prefs` (`vaultkit.signin.handle_prefs`, with an identity file or `identity.ambient`) passes `prefs_url="/api/prefs"` to `shell.page` on every page whose request has a principal. The page then carries `<meta name="machiya-prefs" content="/api/prefs">` (`shell.prefs_meta`; a local path only). Leave it out where nobody is signed in (the sign-in page, a 401), so a stranger's page never asks.
- machiya.js then GETs it on load (`credentials: "same-origin"`): when the server's `theme` / `text_size` differ from the cookies, it writes the cookies as the settings page does (`machiya_<key>` on `MACHIYA_COOKIE_DOMAIN`, else the room's own) and applies them without a reload. A change of theme or text size on `/settings` is also PUT there as `{"prefs": {"theme": …, "text_size": …}}` (JSON, same-origin, so the room's `origins` rule passes). Only known values are applied: theme `system`/`night`/`day`, text size `xsmall`/`small`/`standard`/`large`/`xlarge`; anything else from the server is ignored. Every failure (offline, 401, 404, bad JSON) is silent: the cookies stay the fast path for the first paint and offline. A choice made on the page wins over a server answer that arrives later.
- The service worker never touches `/api/prefs` (it is in the core's `bypass`; the rooms' `^/api/` covers it too), and the answer is `no-store`.

**Room search (a room searches its own things, then hands off to Shiori).**
- `shell.search_box(q, action="/search", placeholder="Search Cards")` goes in the header's `tools` (or at the top of your search page). machiya.js focuses it on `/`.
- End your results with `shell.handoff(q, links)`, "Search everything in Shiori ›", which links to `<shiori>/#/search?q=<q>`. It's empty without a Shiori address or a query.
- What each room searches: Konbini its cards, Niwa its published notes, Kura every note (it already has search; move it onto the shared field).

**Update toast (PWA card).**
- machiya.js shows "New Version · Reload" when a new service worker is waiting. Reload posts `{type: "SKIP_WAITING"}`, and the page reloads once the new worker controls it. machiya.js also checks for an update when the app comes back to the foreground (at most once a minute), since iOS rarely closes installed apps.
- Your `sw.js` must:
  1. **not** call `self.skipWaiting()` in `install` (the new worker waits for the user);
  2. handle the message: `self.addEventListener("message", (e) => { if (e.data && e.data.type === "SKIP_WAITING") self.skipWaiting(); });`
  3. keep `clients.claim()` in `activate`, and keep the version (with `shell.UI_VERSION`) in its cache names.
- Test it with a v1 → v2 worker: the toast appears after a reload, Reload swaps in about 1 s, and no worker is left waiting.

## Service worker (`ui/machiya-sw.js`)

One worker core for every room, vendored in `ui/` like `machiya.css`. A room's whole `/sw.js` is what `shell.service_worker()` renders:

```js
importScripts("/static/machiya-sw.js?v=<UI_VERSION>");
machiyaSW({"version": "<room build>-<core hash>", "precache": [...], "notes": {"match": "^/n/", "limit": 200}, ...});
```

**Wiring (each room):**
- Serve `/static/machiya-sw.js` from `app/vaultkit/ui/` next to `machiya.css`/`machiya.js`.
- Serve `/sw.js` as `shell.service_worker(BUILD, precache, **options)` with `Cache-Control: no-cache`. Delete the room's own `sw.js`. Keep registering `/sw.js` from the room's script.
  - `BUILD` is the room's build hash, the same one as its versioned static URLs.
  - `precache` is the shell: the versioned CSS/JS, the icons and `/offline`.
- Options (all optional):

  | Option | Default | |
  |---|---|---|
  | `offline` | `/offline` | the page shown when a navigation has neither network nor a stored copy (precache it) |
  | `timeout` | `2500` | ms a navigation waits for the network before falling back |
  | `bypass` | `/sw.js`, the manifest, `/api/prefs` | path regexps the worker never touches (the APIs: `^/api/`) |
  | `network` | – | navigations never stored (`^/search$`, `^/settings$`): the network, else `/offline` |
  | `notes` | – | `{"match": "^/n/", "limit": 200}`: the notes kept for offline reading |
  | `pages` | `30` | other pages kept (home, lists) |
  | `assetMatch`, `assets` | –, `100` | attachments (`^/a/`), stale-while-revalidate, the 100 most recent |
  | `pins` | – | a URL answering `{"urls": ["/n/…"]}`: notes kept for good and fetched ahead (at most hourly) |

**Caches** (per origin, so per room):
- `static-<version>`: precached, cache-first, replaced on each deploy.
- `notes-v1`, `pages-v1` and `assets-v1`: these survive deploys. Only old `static-*` and `pages-<hash>` caches are deleted.
- `notes-v1` keeps the `limit` most recently read notes (a read moves a note to the end). Pinned notes are never evicted.

**Rules:**
- **Updates:** a new worker waits for the "New Version · Reload" toast (`SKIP_WAITING`), then `clients.claim()`.
- **What gets stored:** only `ok`, same-origin, not redirected responses. A `Cache-Control: no-store` answer is never stored, and it drops any stored copy of that URL.
- **Navigations are network-first for at most `timeout`.** An edit shows up at once online. On a timeout, a network error or a 5xx, the stored copy is shown, marked `<body data-offline="<time>">` for the room's offline banner, else `/offline`. An answer that arrives late still refreshes the stored copy.

**Pinning** (frontmatter `offline: true`):
- The note page carries `shell.OFFLINE_PIN` (`<meta name="machiya-offline" content="pin">`), passed as `page(head=…)`. The worker keeps that copy for good.
- The room's `pins` URL (e.g. `GET /api/offline` → `{"urls": [...]}`, owner-only) lets the worker fetch pinned notes before they're ever read. A note dropped from the list is unpinned.

**Never on a device:** notes under `Archive/`. The room answers those pages with `Cache-Control: no-store`, and leaves them out of `pins`.

**Settings:** `shell.offline_row()` in the room's section: "Offline Copies", with the counts from the worker ("200 notes (1 pinned), 3 pages") and a **Clear Offline Copies** button. `machiya.js` sends `OFFLINE_STATS` / `CLEAR_OFFLINE` over a `MessageChannel`. Name it in the section's footnote.

**Signing out** (v0.13): machiya.js catches every `form[action="/signout"]`, sends the worker `CLEAR_OFFLINE` (waiting
at most a second) and then posts the form, so the next person on the device can't read what was kept; the server's
answer also carries `Clear-Site-Data: "cache"`. A room's network-only list should also cover pages that show
unpublished or owner-only things (Niwa's `/queue` and `/stream`).

**Messages:** `SKIP_WAITING`, `CLEAR_OFFLINE` → `{cleared}`, `OFFLINE_STATS` → `{notes, pinned, pages, assets}`, `SYNC_PINS` → `{synced}` (forces a pins fetch).

**Expected behaviour** (what to check in a browser with a test vault):
- a pinned note is stored before it's ever read, and a note under `Archive/` never is;
- the notes cache caps at its `limit`, dropping the oldest and keeping the pinned notes;
- with the server paused, a stored note shows with the banner, an unread pinned note shows, and an evicted note shows `/offline`, each after the navigation `timeout`;
- a deploy renames the static cache, keeps the notes, and the toast swaps workers;
- Clear Offline Copies empties the caches.

## Per-room settings

| Room | Section | Settings |
|---|---|---|
| Kura | Reading | Preview Pane (toggle, cookie), Obsidian Vault (text: adds "Edit in Obsidian") |
| Niwa | Garden | Link Previews (toggle) |
| Konbini | Board | Group By (Area / Family), Done Cards (5 / 10 / All) |

All rooms get Appearance (Theme: System / Tokyo Night / Tokyo Night Day, and Text Size), Apps and About.

## The source link (AGPL section 13)

The rooms are AGPL software, and people who use one over a network must be offered its source. vaultkit's shell does it: set **`MACHIYA_SOURCE_URL`** to the address of the room's source repository (a plain `http://` or `https://` URL), and the page footer gets a **Source code** link and Settings, About gets **Source code** and **Licence** rows. Unset (the default), nothing is shown and every page is byte-identical to one built without the setting. Anything that isn't a plain http(s) address is ignored. Each room sets its own repository's URL in its compose or env file; a fork must point it at its own source.
