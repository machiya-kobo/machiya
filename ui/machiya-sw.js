// machiya-sw.js: the shared service-worker core of Machiya's web rooms (docs/ui.md "Service worker"). Vendored with vaultkit's ui/. Each room's /sw.js is two lines, rendered by
// vaultkit.shell.service_worker():
//
//   importScripts("/static/machiya-sw.js?v=<UI_VERSION>");
//   machiyaSW({version: "<room build>-<UI>", precache: [...], notes: {match: "^/n/", limit: 200}, ...});
//
// Caches (per origin, so per room):
//   static-<version>  the shell, precached; cache-first. Replaced on every deploy.
//   notes-v1          notes read (network-first), the `limit` most recently read; pinned ones are never evicted.
//   pages-v1          other pages (network-first), the `pages` most recently read.
//   assets-v1         attachments (stale-while-revalidate), the `assets` most recent.
// The -v1 caches survive deploys; only old static-*/pages-<hash> caches are deleted.
//
// Rules: a new worker waits for the page's "New Version · Reload" toast (SKIP_WAITING); only ok, same-origin, not
// redirected, not `Cache-Control: no-store` responses are stored (a no-store answer also drops any stored copy);
// a navigation waits at most `timeout` ms for the network, then shows the stored copy (marked data-offline for the
// offline banner) or /offline.
// Pins: a note page carrying <meta name="machiya-offline" content="pin"> is kept for good; with `pins` (a URL that
// answers {"urls": [...]}) the worker also fetches every pinned note, at most hourly, so it's there before it's read.
// Messages: SKIP_WAITING; CLEAR_OFFLINE (drop notes, pages, assets); OFFLINE_STATS -> {notes, pinned, pages, assets}.

function machiyaSW(cfg) {
  const VERSION = cfg.version;
  const STATIC = "static-" + VERSION;
  const NOTES = "notes-v1", PAGES = "pages-v1", ASSETS = "assets-v1";
  const OURS = [STATIC, NOTES, PAGES, ASSETS];
  const TIMEOUT = cfg.timeout || 2500;
  const re = (list) => (list || []).map((s) => new RegExp(s));
  const BYPASS = re(["^/sw\\.js$", "^/manifest\\.webmanifest$"].concat(cfg.bypass || []));   // never touched
  const NETWORK = re(cfg.network);          // navigations never stored (search, settings): network, else /offline
  const NOTE = re(cfg.notes ? [cfg.notes.match] : []);
  const ASSET = re(cfg.assetMatch);
  const LIMIT = { [NOTES]: (cfg.notes && cfg.notes.limit) || 200, [PAGES]: cfg.pages || 30, [ASSETS]: cfg.assets || 100 };
  const PIN_META = /<meta\s+name="machiya-offline"\s+content="pin"/i;
  const OFFLINE = cfg.offline || "/offline";
  let pinsSynced = 0;

  const hit = (list, path) => list.some((r) => r.test(path));

  self.addEventListener("install", (e) => {
    // no skipWaiting(): an update waits for the toast; the very first worker activates at once anyway
    e.waitUntil(caches.open(STATIC).then((c) => c.addAll(cfg.precache || [])));
  });

  self.addEventListener("activate", (e) => {
    e.waitUntil(caches.keys()
      .then((keys) => Promise.all(keys
        .filter((k) => !OURS.includes(k) && /^(static|pages)-/.test(k))   // our old versions only; -v1 caches stay
        .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
  });

  self.addEventListener("message", (e) => {
    const type = e.data && e.data.type;
    const reply = (msg) => { if (e.ports && e.ports[0]) e.ports[0].postMessage(msg); };
    if (type === "SKIP_WAITING") self.skipWaiting();
    else if (type === "CLEAR_OFFLINE") {
      e.waitUntil(Promise.all([NOTES, PAGES, ASSETS].map((n) => caches.delete(n))).then(() => reply({ cleared: true })));
    } else if (type === "OFFLINE_STATS") {
      e.waitUntil(stats().then(reply));
    } else if (type === "SYNC_PINS") {
      pinsSynced = 0;
      e.waitUntil(syncPins().then(() => reply({ synced: true })));
    }
  });

  function storable(res) {
    return res && res.ok && res.type === "basic" && !res.redirected &&
      !/no-store/i.test(res.headers.get("Cache-Control") || "");
  }
  const noStore = (res) => /no-store/i.test((res && res.headers.get("Cache-Control")) || "");
  const pinned = (res) => !!res && res.headers.get("X-Machiya-Pinned") === "1";

  async function stamped(res, pin) {
    // a copy that says when it was stored (the offline banner shows its age) and whether it's pinned
    const headers = new Headers(res.headers);
    headers.set("X-Cached-At", new Date().toISOString());
    if (pin) headers.set("X-Machiya-Pinned", "1"); else headers.delete("X-Machiya-Pinned");
    return new Response(await res.clone().arrayBuffer(), { status: res.status, statusText: res.statusText, headers });
  }

  async function trim(name) {
    // least recently read first: put() moves a key to the end, and reads re-put. Pinned copies are skipped.
    const cache = await caches.open(name);
    const keys = await cache.keys();
    let extra = keys.length - LIMIT[name];
    for (const k of keys) {
      if (extra <= 0) break;
      if (name === NOTES && pinned(await cache.match(k))) continue;
      await cache.delete(k);
      extra--;
    }
  }

  async function store(name, req, res) {
    const cache = await caches.open(name);
    if (!storable(res)) {
      if (noStore(res)) await cache.delete(req);         // e.g. a note moved under Archive/: forget any copy
      return;
    }
    let pin = false;
    if (name === NOTES) pin = PIN_META.test(await res.clone().text());
    await cache.put(req, await stamped(res, pin));
    await trim(name);
  }

  async function fromCache(req, navigate, failed) {
    // `failed`: the server's own error (502 from tailscale serve while the app is down), used when nothing is stored
    for (const name of [NOTES, PAGES]) {
      const cache = await caches.open(name);
      const got = await cache.match(req);
      if (!got) continue;
      cache.put(req, got.clone());                        // a read counts as recent use (LRU)
      if (!navigate) return got;
      const at = got.headers.get("X-Cached-At") || "";
      const html = (await got.text()).replace("<body ", '<body data-offline="' + at + '" ');
      return new Response(html, { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } });
    }
    if (failed) return failed;
    return offlinePage();
  }

  async function offlinePage() {
    const off = await caches.match(OFFLINE);
    return off || new Response("offline", { status: 503, headers: { "Content-Type": "text/plain" } });
  }

  function withinTimeout(p) {
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(() => resolve(null), TIMEOUT); });
    return Promise.race([p, late]).catch(() => null).finally(() => clearTimeout(timer));
  }

  async function networkFirst(e, name) {
    // the network for at most TIMEOUT; an answer that comes later still refreshes the stored copy
    const req = e.request;
    const net = fetch(req).then((res) => {
      if (name) e.waitUntil(store(name, req, res.clone()).catch(() => {}));   // stored in the background
      return res;
    });
    e.waitUntil(net.catch(() => {}));
    const res = await withinTimeout(net);
    if (!res) return name ? fromCache(req, true) : offlinePage();
    if (res.status >= 500) return name ? fromCache(req, true, res) : res;
    return res;
  }

  async function cacheFirst(req) {
    const cache = await caches.open(STATIC);
    const got = await cache.match(req);
    if (got) return got;
    const res = await fetch(req);
    if (storable(res)) cache.put(req, res.clone());
    return res;
  }

  async function staleWhileRevalidate(e) {
    const cache = await caches.open(ASSETS);
    const got = await cache.match(e.request);
    const net = fetch(e.request).then(async (res) => {
      if (storable(res)) { await cache.put(e.request, res.clone()); await trim(ASSETS); }
      return res;
    });
    e.waitUntil(net.catch(() => {}));
    return got || net;
  }

  async function syncPins() {
    // the room's list of pinned notes ({"urls": ["/n/..."]}): fetch each into notes-v1, pinned; unpin the rest
    if (!cfg.pins || Date.now() - pinsSynced < 3600 * 1000) return;
    pinsSynced = Date.now();
    let urls;
    try {
      const r = await fetch(cfg.pins, { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      urls = new Set(((await r.json()).urls || []).map((u) => new URL(u, self.location.origin).href));
    } catch (err) { return; }
    const cache = await caches.open(NOTES);
    for (const k of await cache.keys()) {
      const res = await cache.match(k);
      if (pinned(res) && !urls.has(k.url)) await cache.put(k, await stamped(res, false));
    }
    for (const u of urls) {
      try {
        const res = await fetch(u, { credentials: "same-origin" });
        if (storable(res)) await cache.put(u, await stamped(res, true));
        else if (noStore(res)) await cache.delete(u);
      } catch (err) { /* offline: next time */ }
    }
  }

  async function stats() {
    // read-only: opening a cache would create it again right after CLEAR_OFFLINE
    const out = { notes: 0, pinned: 0, pages: 0, assets: 0 };
    const keys = async (name) => ((await caches.has(name)) ? (await caches.open(name)).keys() : []);
    if (await caches.has(NOTES)) {
      const notes = await caches.open(NOTES);
      for (const k of await notes.keys()) { out.notes++; if (pinned(await notes.match(k))) out.pinned++; }
    }
    out.pages = (await keys(PAGES)).length;
    out.assets = (await keys(ASSETS)).length;
    return out;
  }

  self.addEventListener("fetch", (e) => {
    const req = e.request;
    const url = new URL(req.url);
    if (req.method !== "GET" || url.origin !== self.location.origin) return;
    const p = url.pathname;
    if (hit(BYPASS, p)) return;
    if (p.startsWith("/static/")) { e.respondWith(cacheFirst(req)); return; }
    if (hit(ASSET, p)) { e.respondWith(staleWhileRevalidate(e)); return; }
    const navigate = req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html");
    if (!navigate) return;
    if (cfg.pins) e.waitUntil(syncPins());
    if (hit(NETWORK, p)) { e.respondWith(networkFirst(e, null)); return; }
    e.respondWith(networkFirst(e, hit(NOTE, p) ? NOTES : PAGES));
  });
}
