# Shared UI (`ui/` + `vaultkit.shell`)

This is how the web rooms (Kura, Niwa, Konbini) put the [design language](design.md) into practice. It's vendored with vaultkit: `./vendor.sh <service>/app <tag>` copies `vaultkit/*.py` and `ui/machiya.css`, `ui/machiya.js` into `app/vaultkit/` (the ui files land in `app/vaultkit/ui/`). The build's `python3 -m vaultkit.verify` covers them, so never edit them in place.

## Wiring an app

1. **Serve the two files**: `/static/machiya.css` → `app/vaultkit/ui/machiya.css`, `/static/machiya.js` → `app/vaultkit/ui/machiya.js` (ignore the query). `shell.page` links them as `?v=<content hash>`, so they may be cached as immutable; put `shell.UI_VERSION` into your service worker's cache name.
2. **Rooms**: set `MACHIYA_ROOMS` in the stack's compose (deployment config, not app code), for example:
   `MACHIYA_ROOMS=shiori=https://shiori.example.ts.net,konbini=https://konbini.example.ts.net,niwa=https://niwa.example.ts.net,kura=https://kura.example.ts.net,hister=https://hister.example.ts.net,searxng=https://searxng.example.ts.net`
   `shell.rooms()` reads it. Unset, there's no switcher (standalone). Your own `*_NIWA_URL` / `*_KONBINI_URL` settings can stay for in-page links.
   Add `machiya=https://machiya.example.ts.net` when the stack runs the [landing page](services/landing.md) (v0.18): every Rooms menu then ends with a **Machiya · home** row before Settings (v0.19.1; "status" in v0.18–0.19.0), the footer's "Part of Machiya" links there, and Apps gets a Machiya switch. Without the key nothing changes.
3. **Pages**: build every page with `shell.page(ctx, room, title, body, tabs, current, stylesheets=["/static/<app>.css?v=…"], scripts=["/static/<app>.js?v=…"], icons={…})`, with `shell.header(room, nav, current, links, subtitle, tools)` at the top of `body` and `shell.footer(room, status, links)` at the end.
   - `ctx = shell.prefs(self.headers.get("Cookie"))` gives `.theme` (the appearance: system/night/day; `auto` is read as system), `.palette` (the theme, v0.15: a `vaultkit.palettes` key, `tokyo-night` when unset or unknown) and `.text` (text size).
   - `room` is `kura`, `niwa` or `konbini`: it sets the seal, the wordmark and `--room`.
   - Nav labels are Title Case. Up to four phone tabs, plus the automatic Rooms tab, whose menu ends in Settings.
4. **Theme**: keep `/theme?set=` working as a no-JavaScript fallback (set the `theme` cookie, accept `system` and `auto`), but link the header to `/settings`, not the old night/day/auto links.
5. **`/settings`** (v0.21: the same order in every app, [Settings](#settings-appearance-per-app-this-device) below):
   `shell.settings_page([shell.shared_section(ctx, room, links, state, who, signin), <your section>, shell.device_section(ctx, [shell.offline_row(), …]), <account>, shell.about_section(room, VERSION, status_text, vaultkit)], room)`.
   `settings_page` puts the sections in the house order (below) whatever order they come in, so this call needs no change.
   - Build your section with `shell.toggle`, `shell.select` and `shell.text_field` rows, plus a footnote written in Shiori's style: name each setting and say what on and off do, and end it "Follows you to your other devices when signed in." for settings in `APP_PREFS`.
   - `state` says where the Shared choices are kept: in hister mode `HISTER.prefs_state(result)` (`account`, or `unavailable` in the fallback); a room's own store (identity file) `room`; no store `standalone`.
   - Each room's settings are kept in localStorage under `<room>Settings`, plus a cookie for anything marked `cookie=True` that the server needs for the first render. The ones that follow the person are declared once in `shell.APP_PREFS`.
   - React to live changes by listening for `machiya:setting` events (also fired when a value arrives from the account).
   - `shell.appearance_section` and `shell.apps_section` (Display and Apps, before v0.21) stay for one release.
6. **CSS**: load machiya.css first, then your own. Delete your copies of the tokens, base, header, tab bar, chips, settings and footer rules, and keep only the room's own layout (Kura's three columns, Niwa's garden, Konbini's lanes). Use the tokens (`--room`, `--house`, `--fs-*`, `--sp-*`, `--r-*`) and the `.is-note` / `.is-card` / `.is-garden` classes with `.thing`, so things wear their room's colour.
7. **Verify**: take screenshots before and after (desktop and phone, both themes). Check no page is wider than the viewport at 390px, and that there are no console errors.

## Settings: Appearance, per-app, This Device

The owner's rule (2026-10-05): settings that affect every app follow the signed-in person to every app and every device. The contract (schema, API, conflict rule, migration) is [contracts/prefs.md](contracts/prefs.md); this is the web rooms' side.

**Every Settings page has the same order** (v0.23, the lead's decision on the owner's "flow logically and not be overwhelming", 2026-10-05): everyday choices first, connections and rare things later; a dependent setting directly under its parent; short Title Case headers. `shell.settings_page` sorts the sections into this order (`shell.ORDER`); the keys and cookies never changed, only placement and names.
1. **Appearance** (`shell.shared_section`, titled "Shared" in v0.21–0.22): Theme, Appearance, Text Size, and **Use This Device's Size** directly under Text Size (with its size row while it is on; this browser's own size, a cookie the rooms of this browser share, `machiya_textSizeDevice`, never sent). Footnote "Follows you on every Machiya app when signed in." (only where an account exists), then a state line of a few words:

   | `state` | The line |
   |---|---|
   | `account` | "Saved to your account." (the Account section names the person) |
   | `signed-out` | "Kept in this browser. Sign In" |
   | `unavailable` | "Kept here until sign-in is back." (machiya.js also switches to it when the account stops answering) |
   | `standalone` | "Kept for every room in this browser." with a shared cookie domain, else "Kept in this browser." |
   | `room` | "Saved for you in <Room>." (a room's own store, identity file) |

2. **The room's own sections**, in the order the room passes them (Kura's Reading, Niwa's Garden, Konbini's Board). A device-only row may sit here (Kura's Obsidian Vault): machiya.js keeps every row in this browser unless the room declared it in `APP_PREFS`.
3. **Rooms**: which apps the Rooms menu shows (`show_<app>`, the account's `apps_hidden`). `shared_section` brings it along; pass `apps=False` and place `shell.rooms_section(ctx, room, links)` yourself if you must.
4. **This Device** (`shell.device_section(ctx, rows, note="")`): device-only rows (Offline Copies). Footnote "Only on this device." No rows, no section; a room with a single device row may fold it into its own section instead.
5. **Account**: who is signed in, Sessions, Sign Out (the room's own section titled "Account").
6. **About**: version, What's New, Source (`shell.about_section`).

**Copy** (the owner, 2026-10-05: "Reduce it to a sentence or two"): a footnote is at most two short sentences saying only what the person needs to decide (no internals, history or cross-references); a tooltip is a few words; an empty state or alert is one sentence plus at most a short hint.

**One choice covers every room in this browser.**
- Set `MACHIYA_COOKIE_DOMAIN` in the stack's compose (for a tailnet: `<tailnet>.ts.net`). `shell.page` puts it on `<body data-cookie-domain>`.
- machiya.js then writes theme, palette, textSize, textSizeDevice and the Apps `show_*` switches as `machiya_<key>` cookies on that domain. `ts.net` is on the Public Suffix List, so `<tailnet>.ts.net` is the site and every room shares them.
- `shell.prefs()` prefers `machiya_<key>` over the room's own cookie, and `textSizeDevice` over `textSize` for the size the page shows (`ctx.text`; the account's is `ctx.text_shared`).
- Unset (standalone on its own host): per-room cookies, as before.
- Shiori's web pages read the same `machiya_*` cookies.

**The account (the settings follow the person).**
- A room passes `prefs_url="/api/prefs"` to `shell.page` on every page whose request has a principal with preferences. The page then carries `<meta name="machiya-prefs" content="/api/prefs">` (`shell.prefs_meta`; a local path only) and, when the room set `shell.APP_PREFS`, `<meta name="machiya-app-prefs">`. Leave it out where nobody is signed in (the sign-in page, a 401), so a stranger's page never asks.
- `/api/prefs` in **hister mode with the helper** forwards to it: `out = HISTER.forward_prefs(result, method, headers, body, origins)`. It returns `None` without a helper, and then (or **without hister mode**) the room's own store answers: `signin.handle_prefs(store, principal, method, headers, body, secure, origins)`. Both give the contract's JSON with its `ETag`.
- **A fresh browser** (no cookies yet): `shell.prefs(cookie_header, account=result.prefs)` draws the first page in the account's theme (the check's answer carries it).
- **machiya.js** follows the contract's client rules: a change PUTs only that key; one it can't send waits in `localStorage["machiyaPrefsPending"]` and goes first next time; on load and on return to the tab (30 s or more, `If-None-Match`) the account's value wins unless this browser changed the key since the account last said it (then this browser's is newer, and is sent: the fix for one room's stale copy reverting another's choice); a value the account lacks is filled in from this browser. Other tabs of the room follow at once (`BroadcastChannel`), other rooms on return to their tab (from the cookies). Every failure is silent.
- **A room's own settings that follow the person:** declare them once, `shell.APP_PREFS = {"previewPane": {"type": "bool", "cookie": True}, "group": {"type": "choice", "values": ["area", "family"], "cookie": True}}`. The account key is `<room>.<snake_case>` (`kura.preview_pane`), and booleans travel as `on`/`off`. machiya.js applies only values that pass (a choice in `values`, on/off), writes the cookie when `cookie`, and fires `machiya:setting`.
- The service worker never touches `/api/prefs` (it is in the core's `bypass`; the rooms' `^/api/` covers it too), and the answer is `no-store`.

## Room search, update toast

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

## Menus, pull to refresh (2026-10-05)

**Menus are closed whenever a page is shown again.** The owner's report: in an installed app, Rooms → Settings → back showed the Rooms menu still open. The cause: tapping a link inside the `<details class="rooms">` followed it with the menu open, and the back/forward cache (always on in an installed app on iOS) restored the page exactly as it was left. machiya.js now:
- closes the menu when a link or a form inside it is followed (so even the snapshot iOS shows during the back swipe is closed). A link that leaves the page where it is (`target="_blank"`, a modifier key, `download`, or one the room handled with `preventDefault`) keeps it open;
- closes every menu on `pageshow`, `popstate` and `pagehide`; open `<dialog>`s and popovers close too, except on a fresh load (a room may open one then on purpose). A dialog is closed with `close()`, so the room hears its `close` event;
- covers the Rooms menu in the header and the phone's Rooms sheet. **A room's own menus** (card menus and the like) join by being `<details data-menu>`; a room's `<dialog>` sheets are covered as they are. An ordinary `<details>` in the page is content and is left alone.

**Pull to refresh in the installed app.** An installed app has no reload button, so in `display-mode: standalone` (or iOS's `navigator.standalone`) machiya.js adds one gesture, made to feel like an iOS app's: at the top of the page, drag down and the page's content comes down after the finger, while the header and the tab bar stay put (like an iOS app's bars; the header also holds the status bar's band, so moving it would open a hole there). A round reload mark sits in the gap that opens under the header, centred in it, growing and turning with the pull; it turns house blue once letting go will reload. Let go short of that and the content springs back (200 ms ease-out); let go past it and the content eases to rest 56 px down with the mark spinning, and 200 ms later the page reloads (`location.reload()`; the old page keeps spinning until the new one shows). Browser tabs keep their own pull to refresh, untouched.
- The travel: the content moves 0.6 of the finger's travel past a 10 px slop, up to ready at 70 px (about 125 px of finger); past ready it gets stiffer and stiffer and never goes past 130 px. Drawn once a frame (`requestAnimationFrame`), as `transform: translate3d(0, --pull-y, 0)` on `.pull-move`.
- What moves: the body's children in the page's flow (`.pull-move`), not the header, the tab bar, a toast, a dialog, a popover, or anything `position: fixed`. The class comes off once the content is back, so nothing on the page is left transformed (a transform would change how `position: fixed` inside it behaves).
- `prefers-reduced-motion`: the content still follows the finger (direct manipulation), but a release snaps (no spring, no spin) and the reload goes at once.
- It never starts with a menu, sheet, dialog or popover open, in a field, on the tab bar, inside a pane that scrolls on its own, with text selected, with two fingers, on a sideways or upward swipe, or once the page has scrolled; a gesture another script takes over (`preventDefault` on `touchmove`, like Konbini's card drag) cancels it.
- A room opts an area out with `data-no-pull`.
- The listeners are passive. In the installed app the page doesn't rubber-band (`overscroll-behavior-y: none` on `html.pull-refresh`), so iOS's bounce never competes with the pull (and that is why machiya.js moves the content itself: the first version, 2026-10-05, moved only the mark, and the owner saw the page stand still), and one pull reloads once.
- An app that scrolls a pane of its own instead of the window (Shiori's web app scrolls `#list`) gets nothing from this; it would do the same on its pane: start only at the pane's `scrollTop` 0, move the pane's content, keep its bars still.
- Tested in Node (`tests/js/ui_sim.mjs`, run by `tests/test_vaultkit.py`: the travel, once-a-frame drawing, what moves, the spring back, the hold and the reload's timing, reduced motion) and in WebKit and Chromium at 390×844 with touch, both themes (and Chromium with real touch input: the window never scrolls during a pull, scrolls normally after).

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

| Room | Section | Settings (account key) | This Device |
|---|---|---|---|
| Kura | Reading | Preview Pane (toggle, cookie; `kura.preview_pane`) | Obsidian Vault (text: adds "Edit in Obsidian"), Offline Copies |
| Niwa | Garden | Link Previews (toggle; `niwa.link_previews`) | Offline Copies |
| Konbini | Board | Group By (Area / Family; `konbini.group`), Done Cards (5 / 10 / All; `konbini.done_cards`) | Offline Copies |
| landing | none | | |

All rooms get Appearance (Theme: the ten palettes; Appearance: System / Light / Dark; Text Size; Use This Device's Size), Rooms, This Device (when they have device rows), Account and About. The page's `<body>` carries `theme-<appearance>` and, for any palette but Tokyo Night, `palette-<key>`; machiya.css's palette section is generated from `vaultkit/palettes.py` (`python3 -m vaultkit.palettes`), and machiya.js sets the browser bar to the new palette's `--dark` when the theme changes on the page.

## The source link (AGPL section 13)

The rooms are AGPL software, and people who use one over a network must be offered its source. vaultkit's shell does it: set **`MACHIYA_SOURCE_URL`** to the address of the room's source repository (a plain `http://` or `https://` URL), and the page footer gets a **Source code** link and Settings, About gets **Source code** and **Licence** rows. Unset (the default), nothing is shown and every page is byte-identical to one built without the setting. Anything that isn't a plain http(s) address is ignored. Each room sets its own repository's URL in its compose or env file; a fork must point it at its own source.

## The changelog endpoint (`vaultkit.changelog`, v0.18)

Every app serves its own `CHANGELOG.md` at **`GET /api/changelog`**, for the [landing page](services/landing.md)'s Recent Deploys:

```python
from vaultkit import changelog
status, body, headers = changelog.handle(os.path.join(APP_DIR, "CHANGELOG.md"), self.headers)
```

- Put it behind the same gate as your `/api/status` (open where that is open). Answer HEAD like GET without the body.
- `COPY CHANGELOG.md` into the image next to the app.
- The answer: the file's first 64 KiB (cut at a whole line), `text/markdown; charset=utf-8`, an `ETag` (`If-None-Match` gets a 304), `Cache-Control: no-cache`. No file: 404.
- Keep the file's shape: `## X.Y.Z` per version, newest first, bullets under it. The landing page shows each version's first bullet.
