// machiya.js: shared behaviour for Machiya's web rooms (docs/ui.md). Loaded as a module on every page, before the
// app's own script. It does five things:
//  1. settings: every [data-set] control on /settings saves to localStorage under "<room>Settings" (per device, like
//     Shiori); controls marked data-cookie also set a cookie of the same name, which the server reads for the first
//     render (theme, textSize, …). Theme and text size apply at once.
//     Shared settings (v0.6): with <body data-cookie-domain> (MACHIYA_COOKIE_DOMAIN), theme, textSize and the Apps
//     show_* switches are written as machiya_<key> cookies on that domain, so one choice covers every room on this
//     device (ts.net is on the Public Suffix List: <tailnet>.ts.net is the site, every room shares its cookies).
//  2. the Apps setting: rooms and neighbours switched off are hidden from the switcher.
//  3. the Rooms menu (<details class="rooms">) closes on Escape or a click outside.
//  4. "/" focuses the room's search field (form.search), unless you're typing somewhere.
//  5. updates (v0.6): when a new service worker is waiting, a "New Version · Reload" toast; Reload tells it to take
//     over (postMessage {type: "SKIP_WAITING"}) and reloads once it has. Checks for updates on return to the app.
//  6. server preferences (v0.12): with <meta name="machiya-prefs" content="/api/prefs"> (shell.page(prefs_url=)),
//     theme and text size follow the person. On load the server's `theme` / `text_size` replace the cookies when they
//     differ (applied without a reload); a change on /settings is also PUT there. Cookies stay the fast path for the
//     first paint and offline; any failure (offline, 401, 404) is silent. Only known values are ever applied.
// Apps can listen for `machiya:setting` events ({detail: {key, value}}) to react to their own settings.

const room = document.body.dataset.room || "app";
const storeKey = room + "Settings";
const domain = document.body.dataset.cookieDomain || "";
const shared = (key) => key === "theme" || key === "textSize" || key.startsWith("show_");

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
function apply(key, value) {
  const b = document.body;
  if (key === "theme") {
    b.classList.remove("theme-system", "theme-night", "theme-day", "theme-auto");
    b.classList.add("theme-" + (value === "auto" ? "system" : value));
  } else if (key === "textSize") {
    b.dataset.text = value;
  } else if (key.startsWith("show_")) {
    const which = key.slice(5);
    for (const a of document.querySelectorAll(`.rooms .menu [data-room="${which}"]`)) a.hidden = value === false;
  }
  document.dispatchEvent(new CustomEvent("machiya:setting", { detail: { key, value } }));
}

const settings = load();
// shared show_* cookies (another room may have changed them) win over this room's localStorage copy
if (domain) {
  for (const part of document.cookie.split(";")) {
    const name = part.split("=")[0].trim();
    if (name.startsWith("machiya_show_")) settings[name.slice(8)] = readCookie(name) !== "false";
  }
}
// hide rooms switched off on this device (every page)
for (const [k, v] of Object.entries(settings)) if (k.startsWith("show_") && v === false) apply(k, v);

// server preferences (6.): the values machiya.js itself writes, and their keys in /api/prefs
const prefsUrl = (document.querySelector('meta[name="machiya-prefs"]') || {}).content || "";
const KNOWN = { theme: ["system", "night", "day"], textSize: ["xsmall", "small", "standard", "large", "xlarge"] };
const SERVER_KEY = { theme: "theme", textSize: "text_size" };
let changedHere = false;                              // a choice made on this page wins over a late server answer
function current(key) {                               // as shell.prefs() reads it: the shared cookie first
  let v = readCookie("machiya_" + key) ?? readCookie(key);
  if (v === "auto") v = "system";
  return KNOWN[key].includes(v) ? v : (key === "theme" ? "system" : "standard");
}
function pushPrefs() {
  if (!prefsUrl) return;
  const body = JSON.stringify({ prefs: { theme: current("theme"), text_size: current("textSize") } });
  fetch(prefsUrl, { method: "PUT", credentials: "same-origin", body,
                    headers: { "Content-Type": "application/json", Accept: "application/json" } }).catch(() => {});
}
if (prefsUrl) {
  fetch(prefsUrl, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      const p = data && data.prefs;
      if (!p || typeof p !== "object" || changedHere) return;
      for (const key of Object.keys(KNOWN)) {
        const value = p[SERVER_KEY[key]];
        if (!KNOWN[key].includes(value) || value === current(key)) continue;   // unknown values are never applied
        setCookie(key, value);
        const all = load();
        all[key] = value;
        save(all);
        for (const el of document.querySelectorAll(`[data-set="${key}"]`)) el.value = value;
        apply(key, value);
      }
    })
    .catch(() => {});
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
    if (cookie || (domain && shared(key))) setCookie(key, value);
    apply(key, value);
    if (Object.hasOwn(KNOWN, key)) { changedHere = true; pushPrefs(); }
  });
}

// the Rooms menu: close on Escape or an outside click
document.addEventListener("click", (ev) => {
  for (const d of document.querySelectorAll("details.rooms[open]")) if (!d.contains(ev.target)) d.open = false;
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") for (const d of document.querySelectorAll("details.rooms[open]")) d.open = false;
  if (ev.key === "/" && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
    const t = ev.target;
    if (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    const field = document.querySelector("form.search input[type=search]");
    if (field) { ev.preventDefault(); field.focus(); field.select(); }
  }
});

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
