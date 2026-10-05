// machiya.js: shared behaviour for Machiya's web rooms (docs/ui.md). Loaded as a module on every page, before the
// app's own script. It does these things:
//  1. settings: every [data-set] control on /settings saves to localStorage under "<room>Settings"; controls marked
//     data-cookie also set a cookie of the same name, which the server reads for the first render (theme, textSize,
//     …). Theme and text size apply at once.
//     Shared settings (v0.6): with <body data-cookie-domain> (MACHIYA_COOKIE_DOMAIN), theme, palette, textSize, the
//     device's own textSizeDevice and the Apps show_* switches are written as machiya_<key> cookies on that domain, so
//     one choice covers every room on this device (ts.net is on the Public Suffix List: <tailnet>.ts.net is the site,
//     every room shares its cookies). Without it they are the room's own cookies.
//  2. the Apps setting: rooms and neighbours switched off are hidden from the switcher.
//  3. menus (2026-10-05): the Rooms menu (<details class="rooms">, the header's and the phone's Rooms sheet), a room's own
//     <details data-menu>, open <dialog>s and popovers close on Escape, a click outside (the menus), a link or form
//     inside them, and whenever the page is shown again (pageshow, popstate, pagehide), so the back/forward cache never
//     brings a page back with its menu open.
//  4. "/" focuses the search page's field, or opens the room's search page (form.search's action), unless you're typing.
//  5. updates (v0.6): when a new service worker is waiting, a "New Version · Reload" toast; Reload tells it to take
//     over (postMessage {type: "SKIP_WAITING"}) and reloads once it has. Checks for updates on return to the app.
//  6. the settings that follow the person (v0.21, docs/contracts/prefs.md): with <meta name="machiya-prefs"
//     content="/api/prefs"> (shell.page(prefs_url=)), the Shared settings (theme, palette, text_size, apps_hidden)
//     and the room's own (<meta name="machiya-app-prefs">, shell.APP_PREFS) are kept in the account. The rules:
//       - a change PUTs only the key that changed; one that can't be sent (offline, 5xx) waits in
//         localStorage["machiyaPrefsPending"] and goes first at the next contact;
//       - on load, and on return to the tab after 30 s or more (If-None-Match: 304 when nothing changed), the
//         account's value wins, unless this browser changed the key since the account last said it (another room
//         set the shared cookie): then this browser's value is newer and is sent. That is the fix for the revert
//         (a room's stale copy undoing a theme picked in another room, 2026-10-05);
//       - a value the account doesn't have yet is filled in from this browser (never overwriting one it has);
//       - cookies stay the fast path for the first paint and offline; every failure is silent (the Shared section's
//         state line says "Sign-in is unavailable" instead). Only known values are ever applied.
//     Other tabs of the same room follow at once (BroadcastChannel "machiya-prefs"); other rooms in this browser on
//     return to their tab (the shared cookies, re-read without the network). "Use This Device's Size"
//     (textSizeDevice) is this device's own text size and never leaves it.
//  7. sign-out (v0.13): a form posting to /signout first empties the service worker's offline copies (CLEAR_OFFLINE),
//     so whoever uses this device next can't read what was kept; the server's answer also clears the HTTP cache.
//  8. Hister sign-in (v0.18; inert unless the room opts in with <meta name="machiya-signin" content="/signout">,
//     histerauth's signin_meta()): a same-origin fetch answered 401 with a JSON "signin" address sends the whole page
//     there, with return= this page (API calls are never redirected by the server, so the page has to go itself); and
//     every Rooms menu gains a "Sign Out" row, a form posting to that path (so 7. applies to it too).
//  9. pull to refresh (2026-10-05), only in an installed app (display-mode: standalone, or iOS's navigator.standalone),
//     which has no reload button: at the top of the page, a drag down brings the content down after the finger, with a
//     reload mark in the gap under the header; letting go past PULL.threshold holds it open, spinning, and reloads, and
//     short of that it springs back (the second pass, 2026-10-05: at first only the mark moved). Not while a menu,
//     sheet or dialog is open, from inside a scrolling pane, a field or the tab bar, with text selected, with a second finger, or on a sideways swipe; a gesture another script
//     takes over (preventDefault on touchmove, e.g. Konbini's card drag) or [data-no-pull] opts out. Browser tabs keep
//     their own pull to refresh.
// Apps can listen for `machiya:setting` events ({detail: {key, value}}) to react to their own settings.

const room = document.body.dataset.room || "app";
const storeKey = room + "Settings";
const domain = document.body.dataset.cookieDomain || "";
const shared = (key) => key === "theme" || key === "palette" || key === "textSize" || key === "textSizeDevice"
  || key.startsWith("show_");
// the themes (vaultkit/palettes.py PALETTES; a test checks the two lists match): body.palette-<key>, none = Tokyo Night
const PALETTES = ["tokyo-night", "solarized", "nord", "dracula", "catppuccin", "gruvbox", "rose-pine", "kanagawa",
                  "everforest", "ayu"];
const TEXT_SIZES = ["xsmall", "small", "standard", "large", "xlarge"];
// the switcher's rows (vaultkit/prefs.py APPS; a test checks they match): apps_hidden <-> show_<app>
const APPS = ["shiori", "konbini", "niwa", "kura", "hister", "searxng", "machiya"];
const rawFetch = window.fetch ? window.fetch.bind(window) : null;   // 6. never takes the page to sign in (8.)

function load() {
  try { return JSON.parse(localStorage.getItem(storeKey) || "{}"); } catch { return {}; }
}
function save(all) {
  try { localStorage.setItem(storeKey, JSON.stringify(all)); } catch { /* private mode: cookies still work */ }
}
function readCookie(name) {
  for (const part of document.cookie.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return decodeURIComponent(part.slice(i + 1).trim());
  }
  return null;
}
function setCookie(key, value) {
  const year = "path=/; max-age=31536000; samesite=lax";
  if (domain && shared(key)) {
    document.cookie = `machiya_${key}=${encodeURIComponent(value)}; domain=${domain}; ${year}`;
    document.cookie = `${key}=; path=/; max-age=0`;          // the room's own copy would shadow nothing, but tidy up
  } else {
    document.cookie = `${key}=${encodeURIComponent(value)}; ${year}`;
  }
}
function dropCookie(key) {
  if (domain && shared(key)) document.cookie = `machiya_${key}=; domain=${domain}; path=/; max-age=0`;
  document.cookie = `${key}=; path=/; max-age=0`;
}
// a shared key's value in this browser, as the server reads it (shell.prefs): the shared cookie first
function cookieOf(key) {
  return readCookie("machiya_" + key) ?? readCookie(key);
}
function deviceSize() {
  const v = cookieOf("textSizeDevice");
  return TEXT_SIZES.includes(v) ? v : "";
}
function apply(key, value) {
  const b = document.body;
  if (key === "theme") {
    b.classList.remove("theme-system", "theme-night", "theme-day", "theme-auto");
    b.classList.add("theme-" + (value === "auto" ? "system" : value));
  } else if (key === "palette") {
    for (const c of [...b.classList]) if (c.startsWith("palette-")) b.classList.remove(c);
    if (PALETTES.includes(value) && value !== PALETTES[0]) b.classList.add("palette-" + value);
  } else if (key === "textSize") {
    b.dataset.text = deviceSize() || value;                  // the device's own size, when it has one, wins here
  } else if (key === "textSizeDevice") {
    b.dataset.text = value || current("textSize");
  } else if (key.startsWith("show_")) {
    const which = key.slice(5);
    for (const a of document.querySelectorAll(`.rooms .menu [data-room="${which}"]`)) a.hidden = value === false;
  }
  if (key === "theme" || key === "palette") barColour();
  document.dispatchEvent(new CustomEvent("machiya:setting", { detail: { key, value } }));
}
// the browser's bar follows a theme changed on the page: the new palette's --dark, as the server would have sent it
function barColour() {
  const colour = getComputedStyle(document.body).getPropertyValue("--dark").trim();
  if (!colour) return;
  const metas = [...document.querySelectorAll('meta[name="theme-color"]')];
  metas.slice(1).forEach((m) => m.remove());
  if (metas[0]) { metas[0].removeAttribute("media"); metas[0].content = colour; }
}
function showControl(key, value) {
  for (const el of document.querySelectorAll(`[data-set="${key}"]`)) {
    if (el.type === "checkbox") el.checked = !!value; else el.value = value;
  }
}

const settings = load();
const appliedNow = {};                                // what this page shows now, so a re-read applies only changes
// the Apps switches: the show_* cookies (another room may have changed them) win over this room's localStorage copy
for (const a of APPS) {
  const c = cookieOf("show_" + a);
  if (c !== null) settings["show_" + a] = c !== "false";
}
// hide rooms switched off on this device (every page)
for (const [k, v] of Object.entries(settings)) if (k.startsWith("show_") && v === false) apply(k, v);

// -- 6. the settings that follow the person -------------------------------------------------------------------------
const prefsUrl = (document.querySelector('meta[name="machiya-prefs"]') || {}).content || "";
const KNOWN = { theme: ["system", "night", "day"], palette: PALETTES, textSize: TEXT_SIZES };
const DEFAULTS = { theme: "system", palette: PALETTES[0], textSize: "standard" };
const SHARED_KEYS = { theme: "theme", palette: "palette", text_size: "textSize" };   // account key -> local key
function current(key) {                               // as shell.prefs() reads it: the shared cookie first
  let v = cookieOf(key);
  if (v === "auto") v = "system";
  return KNOWN[key].includes(v) ? v : DEFAULTS[key];
}
let appSpec = {};                                     // the room's own synced settings: {localKey: {key, type, …}}
try {
  const m = document.querySelector('meta[name="machiya-app-prefs"]');
  if (m && prefsUrl) appSpec = JSON.parse(m.content) || {};
} catch { appSpec = {}; }
const APP_LOCAL = {};                                 // account key -> local key
for (const [local, spec] of Object.entries(appSpec)) {
  if (spec && typeof spec.key === "string" && spec.key.startsWith(room + ".")) APP_LOCAL[spec.key] = local;
}
const accountKeys = () => [...Object.keys(SHARED_KEYS), "apps_hidden", ...Object.keys(APP_LOCAL)];
const accountKeyOf = (local) => {
  if (local === "textSize") return "text_size";
  if (local === "theme" || local === "palette") return local;
  if (local.startsWith("show_")) return "apps_hidden";
  return Object.keys(APP_LOCAL).find((k) => APP_LOCAL[k] === local) || "";
};

function appsHidden() {                               // this browser's apps_hidden, or undefined when it never chose
  let any = false;
  const hidden = [];
  for (const a of APPS) {
    let v = cookieOf("show_" + a);
    if (v === null && Object.hasOwn(settings, "show_" + a)) v = String(settings["show_" + a]);
    if (v !== null) any = true;
    if (v === "false") hidden.push(a);
  }
  return any ? hidden.join(",") : undefined;
}
// this browser's value of an account key, as the account would hold it; undefined when this browser has none
function localOf(key) {
  if (Object.hasOwn(SHARED_KEYS, key)) {
    let v = cookieOf(SHARED_KEYS[key]);
    if (v === "auto") v = "system";
    return KNOWN[SHARED_KEYS[key]].includes(v) ? v : undefined;
  }
  if (key === "apps_hidden") return appsHidden();
  const local = APP_LOCAL[key];
  if (!local) return undefined;
  const spec = appSpec[local];
  let v = spec.cookie ? readCookie(local) : null;
  if (v === null) {
    const all = load();
    if (!Object.hasOwn(all, local)) return undefined;
    v = all[local];
  }
  if (spec.type === "bool") return v === true || v === "true" || v === "on" ? "on" : "off";
  return String(v);
}
// a value the account holds that this page may apply (the room validates its own, the schema the shared ones)
function valid(key, v) {
  if (typeof v !== "string") return false;
  if (Object.hasOwn(SHARED_KEYS, key)) return KNOWN[SHARED_KEYS[key]].includes(v === "auto" ? "system" : v);
  if (key === "apps_hidden") return v.split(",").every((a) => a === "" || APPS.includes(a));
  const spec = appSpec[APP_LOCAL[key]];
  if (!spec) return false;
  if (spec.type === "bool") return v === "on" || v === "off";
  if (spec.type === "choice") return (spec.values || []).includes(v);
  return v.length <= 1024;
}
// set this browser to the account's value (null: the default), as the settings page would
function setLocal(key, v) {
  const all = load();
  if (Object.hasOwn(SHARED_KEYS, key)) {
    const local = SHARED_KEYS[key];
    const value = v === null ? DEFAULTS[local] : (v === "auto" ? "system" : v);
    setCookie(local, value);
    all[local] = value;
    save(all);
    appliedNow[local] = value;
    showControl(local, value);
    apply(local, value);
  } else if (key === "apps_hidden") {
    const hidden = new Set((v || "").split(",").filter(Boolean));
    for (const a of APPS) {
      const on = !hidden.has(a);
      if (!on || cookieOf("show_" + a) !== null) setCookie("show_" + a, on);
      all["show_" + a] = on;
      settings["show_" + a] = on;
      appliedNow["show_" + a] = on;
      showControl("show_" + a, on);
      apply("show_" + a, on);
    }
    save(all);
  } else if (APP_LOCAL[key]) {
    const local = APP_LOCAL[key];
    const spec = appSpec[local];
    if (v === null) {
      delete all[local];
      save(all);
      if (spec.cookie) dropCookie(local);
      document.dispatchEvent(new CustomEvent("machiya:setting", { detail: { key: local, value: null } }));
      return;
    }
    const value = spec.type === "bool" ? v === "on" : v;
    all[local] = value;
    save(all);
    if (spec.cookie) setCookie(local, value);
    showControl(local, value);
    apply(local, value);
  }
}

function stored(name) {
  try { return JSON.parse(localStorage.getItem(name) || "{}") || {}; } catch { return {}; }
}
function store(name, value) {
  try { localStorage.setItem(name, JSON.stringify(value)); } catch { /* private mode: nothing kept */ }
}
const PENDING = "machiyaPrefsPending";                // changes not yet sent: {key: value or null}
const SEEN = "machiyaPrefsSeen";                      // the account's last answer here: {rev, prefs, updated}

// the Shared section's state line: "unavailable" while the account doesn't answer, back when it does
const stateLine = document.querySelector(".prefs-state");
const stateWas = stateLine ? [stateLine.dataset.prefsState, stateLine.innerHTML] : null;
function reachable(ok) {
  if (!stateLine || !stateWas || stateWas[0] !== "account") return;
  stateLine.dataset.prefsState = ok ? stateWas[0] : "unavailable";
  if (ok) stateLine.innerHTML = stateWas[1]; else stateLine.textContent = stateLine.dataset.unavailable || "";
}

async function call(method, body, etag) {
  const headers = { Accept: "application/json" };
  if (body) headers["Content-Type"] = "application/json";
  if (etag) headers["If-None-Match"] = etag;
  const r = await rawFetch(prefsUrl, { method, credentials: "same-origin", cache: "no-store", headers,
                                       body: body ? JSON.stringify(body) : undefined });
  if (r.status === 304) return { status: 304, data: null };
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  return { status: r.status, data };
}

let running = null, again = false, lastSync = 0;
function sync() {
  if (!prefsUrl || !rawFetch) return Promise.resolve();
  if (running) { again = true; return running; }
  running = (async () => {
    try {
      for (let round = 0; round < 3; round++) {        // a fill-in or a newer local value may need one more PUT
        again = false;
        const pending = stored(PENDING);
        let answer;
        if (Object.keys(pending).length) {
          const { status, data } = await call("PUT", { prefs: pending });
          if (status >= 500 || status === 0) { reachable(false); return; }
          const left = stored(PENDING);                  // drop what went (unless it changed again meanwhile)
          for (const [k, v] of Object.entries(pending)) if (left[k] === v) delete left[k];
          if (status === 400 || status === 413 || status === 415) {
            store(PENDING, left);                        // the account refuses it: never sent again
            continue;
          }
          if (status !== 200 || !data) return;           // 401, 403, 404: kept for later
          store(PENDING, left);
          answer = data;
        } else {
          const seen = stored(SEEN);
          const { status, data } = await call("GET", null, Number.isInteger(seen.rev) ? `"${seen.rev}"` : "");
          if (status >= 500) { reachable(false); return; }
          if (status === 304) answer = seen;
          else if (status === 200 && data) answer = data;
          else return;                                   // 401, 403, 404: nothing here follows the person
        }
        lastSync = Date.now();
        reachable(true);
        if (!reconcile(answer) && !again) return;
      }
    } catch {
      reachable(false);                                  // offline: the cookies carry on; pending waits
    } finally {
      running = null;
      if (again) { again = false; sync(); }             // a change made while the last call was out
    }
  })();
  return running;
}
// the account's answer against this browser: -> true when something must be sent (now pending)
function reconcile(data) {
  if (!data || typeof data.prefs !== "object" || !data.prefs) return false;
  const seen = stored(SEEN);
  const was = (seen.prefs && typeof seen.prefs === "object") ? seen.prefs : {};
  const wasAt = (seen.updated && typeof seen.updated === "object") ? seen.updated : {};
  const prefs = data.prefs, updated = (data.updated && typeof data.updated === "object") ? data.updated : {};
  const pending = stored(PENDING), push = {};
  for (const key of accountKeys()) {
    if (Object.hasOwn(pending, key)) continue;            // a change waiting to go wins
    const local = localOf(key);
    if (!Object.hasOwn(prefs, key)) {
      if (Object.hasOwn(was, key)) setLocal(key, null);   // the account removed it since: the default again
      else if (local !== undefined) push[key] = local;    // fill the blank from this browser
      continue;
    }
    const value = prefs[key];
    if (!valid(key, value) || value === local) continue;  // unknown values are never applied
    if (key === "apps_hidden" && local === undefined && value === "") continue;       // all shown either way
    // the account still says what it said last time, and this browser now says something else: another room (or
    // this one, offline) changed it here since, so this browser's value is the newer one
    if (local !== undefined && was[key] === value && wasAt[key] === updated[key]) push[key] = local;
    else setLocal(key, value);
  }
  store(SEEN, { rev: data.rev, prefs, updated });
  if (!Object.keys(push).length) return false;
  store(PENDING, Object.assign(stored(PENDING), push));
  return true;
}
function changed(local) {                             // a choice made on this page: send that key only
  const key = accountKeyOf(local);
  if (!key || !prefsUrl) return;
  const v = localOf(key);
  if (v === undefined) return;
  store(PENDING, Object.assign(stored(PENDING), { [key]: v }));
  sync();
}

// the same browser: other tabs of this room at once, other rooms on return (the cookies, no network)
const channel = "BroadcastChannel" in window ? new BroadcastChannel("machiya-prefs") : null;
function refresh() {
  for (const key of ["theme", "palette", "textSize"]) {
    const v = current(key);
    if (appliedNow[key] !== v) { appliedNow[key] = v; showControl(key, v); apply(key, v); }
  }
  const d = deviceSize();
  if (appliedNow.textSizeDevice !== d) { appliedNow.textSizeDevice = d; apply("textSizeDevice", d); syncDeviceControls(); }
  for (const a of APPS) {
    const c = cookieOf("show_" + a);
    if (c === null) continue;
    const on = c !== "false";
    if (appliedNow["show_" + a] !== on) {
      appliedNow["show_" + a] = on;
      settings["show_" + a] = on;
      showControl("show_" + a, on);
      apply("show_" + a, on);
    }
  }
}
for (const key of ["theme", "palette", "textSize"]) appliedNow[key] = current(key);
appliedNow.textSizeDevice = deviceSize();
if (deviceSize()) document.body.dataset.text = deviceSize();
if (channel) channel.onmessage = () => refresh();
const tell = () => { if (channel) channel.postMessage({ room }); };
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  refresh();
  if (Date.now() - lastSync >= 30000) sync();
});
window.addEventListener("pageshow", (ev) => { if (ev.persisted) { refresh(); sync(); } });
if (prefsUrl) sync();

// "Use This Device's Size" (This Device): the device's own text size, a cookie the rooms share, never sent anywhere
const deviceToggle = document.querySelector("[data-device-size]");
const deviceSelect = document.querySelector("[data-device-size-value]");
function syncDeviceControls() {
  if (!deviceToggle) return;
  const d = deviceSize();
  deviceToggle.checked = !!d;
  const row = deviceSelect && deviceSelect.closest(".item");
  if (row) row.hidden = !d;
  if (deviceSelect && d) deviceSelect.value = d;
}
if (deviceToggle) {
  syncDeviceControls();
  const set = () => {
    if (deviceToggle.checked) {
      const v = TEXT_SIZES.includes(deviceSelect && deviceSelect.value) ? deviceSelect.value : current("textSize");
      setCookie("textSizeDevice", v);
      appliedNow.textSizeDevice = v;
      apply("textSizeDevice", v);
    } else {
      dropCookie("textSizeDevice");
      appliedNow.textSizeDevice = "";
      apply("textSizeDevice", "");
    }
    syncDeviceControls();
    tell();
  };
  deviceToggle.addEventListener("change", set);
  if (deviceSelect) deviceSelect.addEventListener("change", () => { if (deviceToggle.checked) set(); });
}

// the /settings page
for (const el of document.querySelectorAll("[data-set]")) {
  const key = el.dataset.set;
  const cookie = "cookie" in el.dataset;
  if (key in settings && !cookie) {                   // cookie-backed values come server-rendered
    if (el.type === "checkbox") el.checked = !!settings[key]; else el.value = settings[key];
  }
  el.addEventListener("change", () => {
    const value = el.type === "checkbox" ? el.checked : el.value;
    const all = load();
    all[key] = value;
    save(all);
    if (key.startsWith("show_")) settings[key] = value;
    if (cookie || shared(key) || (appSpec[key] && appSpec[key].cookie)) setCookie(key, value);
    if (Object.hasOwn(appliedNow, key) || key.startsWith("show_")) appliedNow[key] = value;
    apply(key, value);
    changed(key);
    tell();
  });
}

// 3. menus. Why they close on the way out (2026-10-05): a tap on Settings in the Rooms menu followed the link with the
// <details> still open, and the back/forward cache (always on in an installed app on iOS) brought the page back exactly
// as it was left: menu open. Now a link or form inside a menu closes it before the page goes (so even the snapshot iOS
// shows during the back swipe is closed), and pageshow/popstate/pagehide close whatever is still open.
const MENUS = "details.rooms[open], details[data-menu][open]";
const SHEETS = MENUS + ", dialog[open]";
function openMenus() {
  const out = [...document.querySelectorAll(SHEETS)];
  try { out.push(...document.querySelectorAll(":popover-open")); } catch { /* no popovers in this browser */ }
  return out;
}
function shut(m) {
  if (m.tagName === "DETAILS") m.open = false;
  else if (m.tagName === "DIALOG" && m.open) { if (typeof m.close === "function") m.close(); else m.removeAttribute("open"); }
  else if (typeof m.hidePopover === "function") { try { m.hidePopover(); } catch { /* already hidden */ } }
}
// every: dialogs and popovers too (a room may open one on load, so a fresh page keeps those; menus are never open then)
function closeMenus(every) { for (const m of openMenus()) if (every || m.tagName === "DETAILS") shut(m); }
function holder(el) {                                  // the menu, sheet or popover an element sits in
  if (!el || !el.closest) return null;
  const m = el.closest(SHEETS);
  if (m) return m;
  try { return el.closest(":popover-open"); } catch { return null; }
}
document.addEventListener("click", (ev) => {
  for (const d of document.querySelectorAll(MENUS)) if (!d.contains(ev.target)) d.open = false;
  const a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
  if (!a || ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  if ((a.target && a.target !== "_self") || a.hasAttribute("download")) return;   // this page stays: leave it open
  const m = holder(a);
  if (m) shut(m);
});
document.addEventListener("submit", (ev) => {
  const m = holder(ev.target);                         // a sheet's own form the room handles (preventDefault) stays
  if (m && (m.tagName === "DETAILS" || !ev.defaultPrevented)) shut(m);
});
window.addEventListener("pageshow", (ev) => closeMenus(ev.persisted));
window.addEventListener("popstate", () => closeMenus(true));
window.addEventListener("pagehide", () => closeMenus(true));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") for (const d of document.querySelectorAll(MENUS)) d.open = false;
  if (ev.key === "/" && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
    const t = ev.target;
    if (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    // the header's search pill (v0.17), else a search page's own field; otherwise open the room's search page
    const field = document.querySelector("form.search.searchbar input[type=search]")
      || [...document.querySelectorAll("form.search input[type=search]")].find((f) => f.offsetParent !== null);
    if (field) { ev.preventDefault(); field.focus(); field.select(); return; }
    const form = document.querySelector("form.search");
    if (form && form.action) { ev.preventDefault(); location.href = form.action; }
  }
});

// Hister sign-in (8.): only with <meta name="machiya-signin">
const signinMeta = document.querySelector('meta[name="machiya-signin"]');
const signoutPath = signinMeta ? signinMeta.content || "/signout" : "";
if (signinMeta && /^\/(?![\/\\])/.test(signoutPath)) {
  for (const menu of document.querySelectorAll(".rooms .menu")) {
    if (menu.querySelector("form.signout")) continue;
    const form = document.createElement("form");
    form.method = "post";
    form.action = signoutPath;
    form.className = "signout";
    form.innerHTML = '<button type="submit">Sign Out</button>';
    menu.append(document.createElement("hr"), form);
  }
}
if (signinMeta && window.fetch) {
  const fetchOriginal = window.fetch.bind(window);
  let leaving = false;
  window.fetch = async (input, init) => {
    const res = await fetchOriginal(input, init);
    if (res.status !== 401 || leaving) return res;
    try {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.origin !== location.origin) return res;
      const data = await res.clone().json();
      if (!data || typeof data.signin !== "string") return res;
      const to = new URL(data.signin, location.href);
      if (to.protocol !== "https:" && to.protocol !== "http:") return res;
      if (to.searchParams.has("return")) to.searchParams.set("return", location.href);
      leaving = true;
      location.assign(to.href);
    } catch { /* not JSON, or unreadable: the caller handles its 401 */ }
    return res;
  };
}

// Settings: "Offline Copies" (shell.offline_row): the counts from the service worker, and Clear Offline Copies
function askWorker(msg) {
  return new Promise((resolve) => {
    const w = navigator.serviceWorker && navigator.serviceWorker.controller;
    if (!w) { resolve(null); return; }
    const ch = new MessageChannel();
    ch.port1.onmessage = (ev) => resolve(ev.data);
    w.postMessage(msg, [ch.port2]);
    setTimeout(() => resolve(null), 5000);
  });
}
// sign-out (7.): the offline copies go first; at most a second's wait, then the form goes anyway
for (const form of document.querySelectorAll('form[action="/signout"], form.signout')) {
  form.addEventListener("submit", (ev) => {
    if (form.dataset.cleared) return;
    ev.preventDefault();
    const go = () => { form.dataset.cleared = "1"; form.submit(); };
    Promise.race([askWorker({ type: "CLEAR_OFFLINE" }), new Promise((r) => setTimeout(r, 1000))]).then(go, go);
  });
}
const offlineRow = document.querySelector(".offline-copies");
if (offlineRow) {
  const count = offlineRow.querySelector("[data-offline-count]");
  const button = offlineRow.querySelector("[data-clear-offline]");
  const show = async () => {
    const s = await askWorker({ type: "OFFLINE_STATS" });
    if (!s) {                                              // no worker yet (a first visit) vs. no support at all
      count.textContent = navigator.serviceWorker ? "Nothing saved yet" : "not available here";
      button.disabled = true;
      return;
    }
    const parts = [];                                       // only what this room keeps (Konbini has no notes)
    if (s.notes) parts.push(s.notes + (s.notes === 1 ? " note" : " notes") + (s.pinned ? " (" + s.pinned + " pinned)" : ""));
    if (s.pages) parts.push(s.pages + (s.pages === 1 ? " page" : " pages"));
    count.textContent = parts.length ? parts.join(", ") : "Nothing saved yet";
  };
  button.addEventListener("click", async () => {
    button.disabled = true;
    await askWorker({ type: "CLEAR_OFFLINE" });
    await show();
    button.textContent = "Cleared";
  });
  (navigator.serviceWorker ? navigator.serviceWorker.ready : Promise.resolve()).then(show, show);
}

// updates: the app's service worker waits (no skipWaiting in install) and takes over on {type: "SKIP_WAITING"}
if ("serviceWorker" in navigator) {
  let asked = false;
  const toast = (worker) => {
    if (document.querySelector(".update-toast")) return;
    const d = document.createElement("div");
    d.className = "update-toast";
    d.setAttribute("role", "status");
    d.innerHTML = '<span>New Version</span><button type="button">Reload</button>';
    d.querySelector("button").addEventListener("click", () => {
      asked = true;
      // the worker waiting NOW: the one the toast first saw may be redundant by the tap (seen on slow phones)
      navigator.serviceWorker.getRegistration()
        .then((r) => (r && r.waiting) || worker)
        .catch(() => worker)
        .then((w) => w.postMessage({ type: "SKIP_WAITING" }));
      setTimeout(() => location.reload(), 3000);        // a worker too old to understand the message
    });
    document.body.append(d);
  };
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (asked) location.reload(); });
  navigator.serviceWorker.getRegistration().then((reg) => {
    if (!reg) return;
    if (reg.waiting && navigator.serviceWorker.controller) toast(reg.waiting);
    reg.addEventListener("updatefound", () => {
      const w = reg.installing;
      if (w) w.addEventListener("statechange", () => {
        if (w.state === "installed" && navigator.serviceWorker.controller) toast(w);
      });
    });
    let last = Date.now();                                // iOS rarely closes an installed app: check on return
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && Date.now() - last > 60000) {
        last = Date.now();
        reg.update().catch(() => {});
      }
    });
  }).catch(() => {});
}

// The header's search pill (v0.17): results as you type. It fetches the room's own search page (the form's action, ?q=)
// and swaps this page's <main> for that page's, so each room keeps one search page and one renderer. Clearing the field
// puts the page back. Enter still submits the form (a real search page load). Rooms whose results need script can
// listen for "machiya:results" on document (detail: {q}) to bind them again.
(() => {
  const form = document.querySelector("form.search.searchbar");
  const input = form && form.querySelector("input[type=search]");
  const main = document.querySelector("main");
  if (!input || !main || !window.fetch || !window.DOMParser) return;
  const action = form.getAttribute("action") || "/search";
  const startHTML = main.innerHTML, startURL = location.href, startTitle = document.title;
  const onSearchPage = new URL(form.action, location.href).pathname === location.pathname;
  let timer = 0, ctl = null, pushed = false;
  // while results show, no tab or nav item is "here" (the page under them isn't); clearing puts them back (v0.17.2)
  const marks = [...document.querySelectorAll('.tabbar a.here, .tabbar a[aria-current="page"], .nav b.here')];
  const unmark = (off) => { for (const m of marks) m.classList.toggle("here-hidden", off); };
  const show = (html, title, url) => {
    main.innerHTML = html;
    if (!onSearchPage) unmark(html !== startHTML);
    for (const f of main.querySelectorAll("form.search")) f.remove();     // the search page's own field: the pill is the field
    if (title) document.title = title;
    if (url && url !== location.href) {
      if (onSearchPage || pushed) history.replaceState({ live: true }, "", url);
      else { history.pushState({ live: true }, "", url); pushed = true; }
    }
  };
  const run = async (q) => {
    if (ctl) ctl.abort();
    if (!q.trim()) {
      main.classList.remove("live-loading");
      if (!onSearchPage) { show(startHTML, startTitle, startURL); }
      return;
    }
    ctl = new AbortController();
    const url = new URL(action, location.href);
    url.searchParams.set("q", q);
    main.classList.add("live-loading");
    try {
      const res = await fetch(url, { signal: ctl.signal, credentials: "same-origin", headers: { "X-Machiya-Live": "1" } });
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      const next = doc.querySelector("main");
      if (!next || input.value !== q) return;
      show(next.innerHTML, doc.title, url.href);
      document.dispatchEvent(new CustomEvent("machiya:results", { detail: { q } }));
    } catch (err) {
      if (err.name !== "AbortError") console.warn("live search", err);
    } finally {
      if (input.value === q) main.classList.remove("live-loading");
    }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => run(input.value), 250); });
  form.addEventListener("submit", () => { clearTimeout(timer); if (ctl) ctl.abort(); });
  const clear = form.querySelector(".clear");
  if (clear) clear.addEventListener("click", () => { input.value = ""; input.focus(); clearTimeout(timer); run(""); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && input.value) { ev.preventDefault(); input.value = ""; clearTimeout(timer); run(""); }
  });
  window.addEventListener("popstate", () => { if (pushed) location.reload(); });
})();

// 9. pull to refresh in an installed app. The gesture is a small state machine, pullStep(state, event) -> state:
//   idle --start(ok)--> armed --move past the slop, mostly downward--> pulling --end with d >= threshold--> reload
//   armed --sideways or upward--> off (until the finger lifts); any move that is no longer ok (scrolled, a menu opened,
//   a second finger, text selected, another script took the gesture) --> off; end or cancel otherwise --> idle.
// d is how far the page's content has come down: the finger's travel past the slop, times resist up to the threshold,
// then stiffer and stiffer (pullDamp), never past max. The installed app doesn't rubber-band (machiya.css), so
// machiya.js moves the content itself: everything in the page's flow under the header (body's children that aren't the
// header, the tab bar, a toast, a dialog, a popover or fixed) gets .pull-move, translateY(--pull-y). The header and the
// tab bar stay put, like an iOS app's bars; the reload mark sits in the gap that opens under the header, growing and
// turning with the pull, house blue once letting go reloads. Let go short of that and the content springs back
// (PULL.settle ms); let go past it and the content rests PULL.hold px down with the mark spinning, then the page reloads.
const PULL = { slop: 10, resist: 0.6, threshold: 70, max: 130, hold: 56, settle: 200 };
const PULL_IDLE = Object.freeze({ phase: "idle", d: 0 });
function pullDamp(travel) {
  const d = Math.max(0, travel) * PULL.resist;
  if (d <= PULL.threshold) return d;
  const over = d - PULL.threshold, room = PULL.max - PULL.threshold;
  return PULL.threshold + room * over / (over + room);                  // past ready: harder to pull, never past max
}
function pullStep(s, e) {
  if (s.phase === "reload") return s;                                   // until the page goes (or comes back: pageshow)
  if (e.type === "start") return e.ok ? { phase: "armed", x: e.x, y: e.y, d: 0 } : PULL_IDLE;
  if (e.type === "cancel") return PULL_IDLE;
  if (e.type === "end") return s.phase === "pulling" && s.d >= PULL.threshold ? { phase: "reload", d: PULL.hold } : PULL_IDLE;
  if (s.phase !== "armed" && s.phase !== "pulling") return s;          // idle, off: wait for the next touch
  if (!e.ok) return { phase: "off", d: 0 };
  const dx = e.x - s.x, dy = e.y - s.y;
  if (s.phase === "armed") {
    if (Math.abs(dx) < PULL.slop && Math.abs(dy) < PULL.slop) return s;
    if (dy < PULL.slop || dy < 1.5 * Math.abs(dx)) return { phase: "off", d: 0 };   // up, or a sideways swipe
  }
  return { phase: "pulling", x: s.x, y: s.y, d: Math.min(PULL.max, pullDamp(dy - PULL.slop)) };
}
(() => {
  const mq = window.matchMedia ? window.matchMedia("(display-mode: standalone)") : null;
  if (!((mq && mq.matches) || navigator.standalone === true)) return;   // a browser tab: its own pull to refresh
  const html = document.documentElement;
  html.classList.add("pull-refresh");                                   // machiya.css: no rubber band to compete with it
  const still = () => { try { return !!window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; } };
  const later = (fn) => (window.requestAnimationFrame ? requestAnimationFrame(fn) : setTimeout(fn, 16));
  let s = PULL_IDLE, mark = null, top = 0, moved = [], frame = 0, settling = 0;
  const scrolled = () => (window.scrollY || (document.scrollingElement || {}).scrollTop || 0) > 0;
  const selecting = () => { try { return String(window.getSelection ? getSelection() : "") !== ""; } catch { return false; } };
  const busy = () => openMenus().length > 0 || selecting();
  const pane = (el) => {                                // inside something that scrolls on its own
    for (let n = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      if (n.scrollHeight > n.clientHeight && /(auto|scroll)/.test(getComputedStyle(n).overflowY)) return true;
    }
    return false;
  };
  const skip = (el) => !el || !el.closest
    || !!el.closest("input, textarea, select, [contenteditable], .tabbar, .update-toast, [data-no-pull]") || pane(el);
  const STAYS = "header.top, .tabbar, .update-toast, .pull, dialog, [popover], script, style, template, link, noscript";
  const movers = () => [...document.body.children].filter((el) => !el.matches(STAYS)
    && !/^(fixed|absolute)$/.test(getComputedStyle(el).position || ""));
  const ensureMark = () => {
    if (mark) return;
    mark = document.createElement("div");
    mark.className = "pull";
    mark.setAttribute("aria-hidden", "true");
    mark.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"'
      + ' stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/></svg>';
    document.body.append(mark);
  };
  // the page's look for this state: the content's offset, the mark's size, turn and colour; settle = animate there
  const draw = () => {
    frame = 0;
    const loading = s.phase === "reload", d = loading ? PULL.hold : s.d;
    if (d > 0 && !moved.length) { moved = movers(); for (const el of moved) el.classList.add("pull-move"); }
    if (!mark && d === 0) return;
    ensureMark();
    const settle = loading || s.phase !== "pulling";                   // let go: the content eases to rest
    html.classList.toggle("pull-settle", settle);
    html.style.setProperty("--pull-y", Math.round(d) + "px");
    const k = loading ? 1 : Math.min(1, d / PULL.threshold);
    mark.style.top = top + "px";
    mark.style.setProperty("--grow", (0.5 + 0.5 * k).toFixed(2));
    mark.style.setProperty("--turn", Math.round(k * 270) + "deg");     // three quarters round at ready; the spin goes on from there
    mark.style.opacity = String(loading ? 1 : Math.round(Math.min(1, d / (PULL.threshold * 0.6)) * 100) / 100);
    mark.classList.toggle("ready", loading || d >= PULL.threshold);
    mark.classList.toggle("loading", loading);
    clearTimeout(settling);
    if (settle && !loading && d === 0) settling = setTimeout(rest, still() ? 0 : PULL.settle + 20);
  };
  const rest = () => {                                  // back at the top: no transform left on the page
    settling = 0;
    for (const el of moved) el.classList.remove("pull-move");
    moved = [];
    html.classList.remove("pull-settle");
  };
  const schedule = () => { if (!frame) frame = later(draw) || 1; };
  const step = (e) => {
    const was = s;
    s = pullStep(s, e);
    if (s.phase === "reload" && was.phase !== "reload") {
      draw();                                           // at once: the content goes to rest at PULL.hold, the mark spins
      setTimeout(() => location.reload(), still() ? 50 : PULL.settle);
      return;
    }
    if (s.d !== was.d) {
      if (s.phase === "pulling") schedule(); else draw();   // follow the finger once a frame; a release shows at once
    }
  };
  window.addEventListener("touchstart", (ev) => {
    const t = ev.touches[0];
    const ok = ev.touches.length === 1 && !scrolled() && !busy() && !skip(ev.target);
    if (ok) {
      const h = document.querySelector("header.top");
      top = h ? Math.max(0, h.getBoundingClientRect().bottom) : 0;
    }
    step({ type: "start", ok, x: t.clientX, y: t.clientY });
  }, { passive: true });
  window.addEventListener("touchmove", (ev) => {
    if (s.phase !== "armed" && s.phase !== "pulling") return;
    const t = ev.touches[0];
    step({ type: "move", ok: ev.touches.length === 1 && !ev.defaultPrevented && !scrolled() && !busy(),
           x: t.clientX, y: t.clientY });
  }, { passive: true });
  window.addEventListener("touchend", () => step({ type: "end" }), { passive: true });
  window.addEventListener("touchcancel", () => step({ type: "cancel" }), { passive: true });
  window.addEventListener("pageshow", (ev) => {        // back from the cache mid-reload: the page as it was, at once
    if (!ev.persisted) return;
    s = PULL_IDLE;
    draw();
    clearTimeout(settling);
    rest();
  });
})();
