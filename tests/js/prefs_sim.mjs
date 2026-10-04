// A small browser for ui/machiya.js's settings sync (tests/test_prefs.py runs it with Node; no packages).
// Each "page load" imports a fresh copy of the real machiya.js into a fake DOM: one room (an origin with its own
// localStorage and host cookies) in one "device" (a cookie jar whose machiya_* cookies are shared by every room, as
// MACHIYA_COOKIE_DOMAIN does). The rooms' /api/prefs are fake stores: one per room (a standalone stack) or one for all
// (the account, hister-login). Prints {scenario: {...}} as JSON; the Python test asserts on it.
import fs from "node:fs";
import path from "node:path";

// as an ES module (a plain .js file would load as CommonJS, cached once by its path): a data: URL per load
const JS = fs.readFileSync(path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../ui/machiya.js"), "utf8");
const DOMAIN = "example.test";
const APPS = ["shiori", "konbini", "niwa", "kura", "hister", "searxng", "machiya"];
let loadNo = 0, clock = 1000;

delete globalThis.BroadcastChannel;                   // one page at a time here; tabs are tested in the browser
globalThis.navigator = {};

// -- the account / a room's store, as vaultkit.prefs.Store answers ----------------------------------------------------
class Store {
  constructor(name) { this.name = name; this.rev = 0; this.prefs = {}; this.updated = {}; this.puts = []; this.down = false; }
  answer() { return { v: 1, rev: this.rev, prefs: { ...this.prefs }, updated: { ...this.updated } }; }
  handle(method, headers, body) {
    if (this.down) return { status: 503, body: { error: "preferences unavailable" } };
    if (method === "GET") {
      if (headers["If-None-Match"] === `"${this.rev}"`) return { status: 304, body: null };
      return { status: 200, body: this.answer() };
    }
    const changes = JSON.parse(body).prefs;
    this.puts.push(changes);
    const t = ++clock;
    let changed = false;
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) { if (k in this.prefs) { delete this.prefs[k]; delete this.updated[k]; changed = true; } }
      else if (this.prefs[k] !== v) { this.prefs[k] = v; this.updated[k] = t; changed = true; }
    }
    if (changed) this.rev++;
    return { status: 200, body: this.answer() };
  }
  // another device (or the import) writes
  set(changes) { this.handle("PUT", {}, JSON.stringify({ prefs: changes })); this.puts.pop(); }
}

// -- a device: a cookie jar (shared machiya_* cookies + each room's own) and each room's localStorage ------------------
class Device {
  constructor(name) { this.name = name; this.shared = {}; this.host = {}; this.storage = {}; }
  cookieString(room) {
    const own = this.host[room] || {};
    return Object.entries({ ...this.shared, ...own }).map(([k, v]) => `${k}=${v}`).join("; ");
  }
  setCookie(room, text) {
    const parts = text.split(";").map((s) => s.trim());
    const [nv, ...attrs] = parts;
    const i = nv.indexOf("=");
    const name = nv.slice(0, i), value = nv.slice(i + 1);
    const dom = attrs.some((a) => a.toLowerCase().startsWith("domain="));
    const gone = attrs.some((a) => a.toLowerCase() === "max-age=0");
    const jar = dom ? this.shared : (this.host[room] ||= {});
    if (gone) delete jar[name]; else jar[name] = value;
  }
  cookies() { return { ...this.shared }; }
}

function el(props = {}) {
  const listeners = {};
  return Object.assign({
    dataset: {}, hidden: false, value: "", checked: false, type: "",
    addEventListener(t, fn) { (listeners[t] ||= []).push(fn); },
    fire(t) { for (const fn of listeners[t] || []) fn({ target: this }); },
    closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
  }, props);
}

// one page load of `room` on `device`; settings: build the /settings page's controls too
async function visit(device, room, store, { settings = false, appPrefs = null, deviceRow = false } = {}) {
  // the first render, as shell.prefs()/shell.page() draw it from the cookies (the shared one first)
  const jar = { ...(device.host[room] || {}), ...Object.fromEntries(Object.entries(device.shared)
    .filter(([k]) => k.startsWith("machiya_")).map(([k, v]) => [k.slice(8), v])) };
  const classes = new Set(["theme-" + (jar.theme || "system")]);
  if (jar.palette && jar.palette !== "tokyo-night") classes.add("palette-" + jar.palette);
  const docListeners = {}, winListeners = {};
  const ls = (device.storage[room] ||= {});
  const controls = {};
  const all = [];
  if (settings) {
    for (const key of ["theme", "palette", "textSize"]) {
      const c = el({ type: "select-one", dataset: { set: key, cookie: "" } });
      controls[key] = c; all.push(c);
    }
    for (const a of APPS) {
      const c = el({ type: "checkbox", checked: true, dataset: { set: "show_" + a } });
      controls["show_" + a] = c; all.push(c);
    }
    for (const local of Object.keys(appPrefs || {})) {
      const c = el({ type: appPrefs[local].type === "bool" ? "checkbox" : "select-one", dataset: { set: local } });
      controls[local] = c; all.push(c);
    }
  }
  const deviceRowEl = el({ hidden: true });
  const deviceToggle = deviceRow ? el({ type: "checkbox" }) : null;
  const deviceSelect = deviceRow ? el({ value: "standard", closest: () => deviceRowEl }) : null;
  const metas = { prefs: el({ content: "/api/prefs" }) };
  if (appPrefs) metas.app = el({ content: JSON.stringify(appPrefs) });
  const body = {
    dataset: { room, cookieDomain: DOMAIN, text: jar.textSizeDevice || jar.textSize || "standard" },
    classList: { add: (c) => classes.add(c), remove: (...cs) => cs.forEach((c) => classes.delete(c)),
                 [Symbol.iterator]: () => classes[Symbol.iterator]() },
    append() {},
  };
  globalThis.document = {
    body, visibilityState: "visible",
    get cookie() { return device.cookieString(room); },
    set cookie(v) { device.setCookie(room, v); },
    querySelector(sel) {
      if (sel === 'meta[name="machiya-prefs"]') return store ? metas.prefs : null;
      if (sel === 'meta[name="machiya-app-prefs"]') return metas.app || null;
      if (sel === "[data-device-size]") return deviceToggle;
      if (sel === "[data-device-size-value]") return deviceSelect;
      return null;
    },
    querySelectorAll(sel) {
      if (sel === "[data-set]") return all;
      const m = sel.match(/^\[data-set="(.+)"\]$/);
      if (m) return controls[m[1]] ? [controls[m[1]]] : [];
      return [];
    },
    addEventListener(t, fn) { (docListeners[t] ||= []).push(fn); },
    dispatchEvent() { return true; },
    createElement: () => el(),
  };
  globalThis.window = globalThis;
  globalThis.addEventListener = (t, fn) => { (winListeners[t] ||= []).push(fn); };
  globalThis.location = { href: `https://${room}.${DOMAIN}/settings`, origin: `https://${room}.${DOMAIN}` };
  globalThis.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); } };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  const log = [];
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    if (!store || store.offline) throw new TypeError("network");
    const r = store.handle(method, init.headers || {}, init.body);
    log.push({ method, body: init.body ? JSON.parse(init.body) : null, status: r.status });
    return { status: r.status, ok: r.status < 300, json: async () => r.body, clone() { return this; } };
  };
  await import("data:text/javascript;base64," + Buffer.from(JS + "\n// load " + (++loadNo)).toString("base64"));
  await settle();
  const page = {
    classes: () => [...classes], text: () => body.dataset.text, log, controls, deviceToggle, deviceSelect,
    async change(key, value) {
      const c = controls[key];
      if (c.type === "checkbox") c.checked = value; else c.value = value;
      c.fire("change");
      await settle();
    },
    async device(on, size) {
      if (size) deviceSelect.value = size;
      deviceToggle.checked = on;
      deviceToggle.fire("change");
      await settle();
    },
    async returnToTab() {
      for (const fn of docListeners.visibilitychange || []) fn();
      await settle();
    },
  };
  return page;
}

const settle = () => new Promise((r) => setTimeout(r, 20));
const theme = (dev) => dev.cookies().machiya_theme;
const out = {};

// 1. the §2.1 revert, standalone stack: each room its own store, one browser. Before the fix the cookie went
//    day -> night -> day; now Konbini's stale copy never undoes the choice, and is brought up to date.
{
  const laptop = new Device("laptop");
  const kura = new Store("kura"), konbini = new Store("konbini");
  kura.set({ theme: "night" }); konbini.set({ theme: "night" });
  await visit(laptop, "kura", kura);                         // both rooms seen once: the browser is in step
  await visit(laptop, "konbini", konbini);
  const k = await visit(laptop, "kura", kura, { settings: true });
  await k.change("theme", "day");
  const after = [theme(laptop)];
  await visit(laptop, "konbini", konbini);
  after.push(theme(laptop));
  await visit(laptop, "kura", kura);
  after.push(theme(laptop));
  out.revert = { cookies: after, kura: kura.prefs.theme, konbini: konbini.prefs.theme, kuraPuts: kura.puts,
                 konbiniPuts: konbini.puts };
}

// 2. one account (hister-login) behind every room: a change in Kura shows in Konbini, Niwa and landing on the next
//    load, and on another device; only the key that changed is sent
{
  const account = new Store("account");
  const laptop = new Device("laptop"), phone = new Device("phone");
  await visit(laptop, "kura", account);
  const k = await visit(laptop, "kura", account, { settings: true });
  await k.change("palette", "nord");
  const rooms = {};
  for (const room of ["konbini", "niwa", "machiya"]) {
    const p = await visit(laptop, room, account);
    rooms[room] = p.classes().includes("palette-nord");
  }
  const ph = await visit(phone, "niwa", account);
  out.account = { puts: account.puts, rooms, phone: ph.classes().includes("palette-nord"),
                  phoneCookie: phone.cookies().machiya_palette };
}

// 3. the phone changes Text Size later: the laptop follows on its next load (the account's newer value wins), and
//    the phone's PUT carried text_size only (it can't undo the laptop's palette)
{
  const account = new Store("account");
  const laptop = new Device("laptop"), phone = new Device("phone");
  await (await visit(laptop, "kura", account, { settings: true })).change("palette", "dracula");
  const p = await visit(phone, "konbini", account, { settings: true });
  await p.change("textSize", "xlarge");
  const l = await visit(laptop, "niwa", account);
  out.newer = { puts: account.puts, laptopText: l.text(), laptopPalette: l.classes().includes("palette-dracula"),
                account: account.prefs };
}

// 4. offline: a change waits as pending, then goes first at the next contact (before the answer is applied)
{
  const account = new Store("account");
  const laptop = new Device("laptop");
  await visit(laptop, "kura", account);
  account.offline = true;
  const k = await visit(laptop, "kura", account, { settings: true });
  await k.change("theme", "night");
  const pending = JSON.parse(laptop.storage.kura.machiyaPrefsPending || "{}");
  account.set({ theme: "day" });                              // meanwhile, from elsewhere (older than the offline change? no: later)
  account.offline = false;
  const again = await visit(laptop, "kura", account);
  out.offline = { pending, first: again.log[0], account: account.prefs.theme, cookie: theme(laptop),
                  left: laptop.storage.kura.machiyaPrefsPending };
}

// 5. Use This Device's Size: local only. The account's text size still lands in the shared cookie, but this device
//    shows its own; another device shows the account's
{
  const account = new Store("account");
  const laptop = new Device("laptop"), phone = new Device("phone");
  const p = await visit(phone, "kura", account, { settings: true, deviceRow: true });
  await p.device(true, "xlarge");
  const phonePuts = account.puts.length;
  account.set({ text_size: "small" });
  const p2 = await visit(phone, "niwa", account);
  const l = await visit(laptop, "konbini", account);
  out.device = { phonePuts, phoneText: p2.text(), phoneShared: phone.cookies().machiya_textSize,
                 deviceCookie: phone.cookies().machiya_textSizeDevice, laptopText: l.text() };
}

// 6. Apps (apps_hidden <-> show_*), fill-the-blanks, unknown values, a per-app key
{
  const account = new Store("account");
  const laptop = new Device("laptop"), phone = new Device("phone");
  laptop.shared.machiya_palette = "gruvbox";                 // chosen on this browser before the account existed
  const spec = { group: { key: "konbini.group", type: "choice", values: ["area", "family"], cookie: true } };
  const k = await visit(laptop, "konbini", account, { settings: true, appPrefs: spec });
  const filled = { ...account.prefs };
  await k.change("show_searxng", false);
  await k.change("group", "family");
  account.set({ theme: "sepia" });                            // a value no page knows: never applied
  const p = await visit(phone, "konbini", account, { appPrefs: spec });
  out.apps = { filled, puts: account.puts, phoneHidden: phone.cookies().machiya_show_searxng,
               phoneGroup: (phone.host.konbini || {}).group, phoneTheme: phone.cookies().machiya_theme,
               phoneClasses: p.classes() };
}

// 7. the account down: nothing breaks, the cookies carry on, the change waits
{
  const account = new Store("account");
  const laptop = new Device("laptop");
  await visit(laptop, "kura", account);
  account.down = true;
  const k = await visit(laptop, "kura", account, { settings: true });
  await k.change("theme", "day");
  out.down = { cookie: theme(laptop), classes: k.classes(), pending: laptop.storage.kura.machiyaPrefsPending };
}

console.log(JSON.stringify(out));
