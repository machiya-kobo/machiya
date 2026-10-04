// machiya.js: shared behaviour for Machiya's web rooms (docs/ui.md). Loaded as a module on every page, before the
// app's own script. It does these things:
//  1. settings: every [data-set] control on /settings saves to localStorage under "<room>Settings" (per device, like
//     Shiori); controls marked data-cookie also set a cookie of the same name, which the server reads for the first
//     render (theme, textSize, …). Theme and text size apply at once.
//     Shared settings (v0.6): with <body data-cookie-domain> (MACHIYA_COOKIE_DOMAIN), theme, textSize and the Apps
//     show_* switches are written as machiya_<key> cookies on that domain, so one choice covers every room on this
//     device (ts.net is on the Public Suffix List: <tailnet>.ts.net is the site, every room shares its cookies).
//  2. the Apps setting: rooms and neighbours switched off are hidden from the switcher.
//  3. the Rooms menu (<details class="rooms">) closes on Escape or a click outside.
//  4. "/" focuses the search page's field, or opens the room's search page (form.search's action), unless you're typing.
//  5. updates (v0.6): when a new service worker is waiting, a "New Version · Reload" toast; Reload tells it to take
//     over (postMessage {type: "SKIP_WAITING"}) and reloads once it has. Checks for updates on return to the app.
//  6. server preferences (v0.12): with <meta name="machiya-prefs" content="/api/prefs"> (shell.page(prefs_url=)),
//     theme, palette and text size follow the person. On load the server's `theme` / `palette` / `text_size` replace
//     the cookies when they differ (applied without a reload); a change on /settings is also PUT there. Cookies stay the fast path for the
//     first paint and offline; any failure (offline, 401, 404) is silent. Only known values are ever applied.
//  7. sign-out (v0.13): a form posting to /signout first empties the service worker's offline copies (CLEAR_OFFLINE),
//     so whoever uses this device next can't read what was kept; the server's answer also clears the HTTP cache.
// Apps can listen for `machiya:setting` events ({detail: {key, value}}) to react to their own settings.

const room = document.body.dataset.room || "app";
const storeKey = room + "Settings";
const domain = document.body.dataset.cookieDomain || "";
const shared = (key) => key === "theme" || key === "palette" || key === "textSize" || key.startsWith("show_");
// the themes (vaultkit/palettes.py PALETTES; a test checks the two lists match): body.palette-<key>, none = Tokyo Night
const PALETTES = ["tokyo-night", "solarized", "nord", "dracula", "catppuccin", "gruvbox", "rose-pine", "kanagawa",
                  "everforest", "ayu"];

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
  } else if (key === "palette") {
    for (const c of [...b.classList]) if (c.startsWith("palette-")) b.classList.remove(c);
    if (PALETTES.includes(value) && value !== PALETTES[0]) b.classList.add("palette-" + value);
  } else if (key === "textSize") {
    b.dataset.text = value;
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
const KNOWN = { theme: ["system", "night", "day"], palette: PALETTES,
                textSize: ["xsmall", "small", "standard", "large", "xlarge"] };
const SERVER_KEY = { theme: "theme", palette: "palette", textSize: "text_size" };
let changedHere = false;                              // a choice made on this page wins over a late server answer
function current(key) {                               // as shell.prefs() reads it: the shared cookie first
  let v = readCookie("machiya_" + key) ?? readCookie(key);
  if (v === "auto") v = "system";
  return KNOWN[key].includes(v) ? v : { theme: "system", palette: PALETTES[0], textSize: "standard" }[key];
}
function pushPrefs() {
  if (!prefsUrl) return;
  const body = JSON.stringify({ prefs: { theme: current("theme"), palette: current("palette"),
                                         text_size: current("textSize") } });
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
    // the header's search pill (v0.17), else a search page's own field; otherwise open the room's search page
    const field = document.querySelector("form.search.bar input[type=search]")
      || [...document.querySelectorAll("form.search input[type=search]")].find((f) => f.offsetParent !== null);
    if (field) { ev.preventDefault(); field.focus(); field.select(); return; }
    const form = document.querySelector("form.search");
    if (form && form.action) { ev.preventDefault(); location.href = form.action; }
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
// sign-out (7.): the offline copies go first; at most a second's wait, then the form goes anyway
for (const form of document.querySelectorAll('form[action="/signout"]')) {
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
  const form = document.querySelector("form.search.bar");
  const input = form && form.querySelector("input[type=search]");
  const main = document.querySelector("main");
  if (!input || !main || !window.fetch || !window.DOMParser) return;
  const action = form.getAttribute("action") || "/search";
  const startHTML = main.innerHTML, startURL = location.href, startTitle = document.title;
  const onSearchPage = new URL(form.action, location.href).pathname === location.pathname;
  let timer = 0, ctl = null, pushed = false;
  const show = (html, title, url) => {
    main.innerHTML = html;
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
