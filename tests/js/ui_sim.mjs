// A small browser for ui/machiya.js's menus (tests/test_vaultkit.py runs it with Node; no packages).
// Each "page load" imports a fresh copy of the real machiya.js into a tiny fake DOM (elements, a selector matcher good
// for the selectors machiya.js uses, window/document listeners). Prints {scenario: {...}} as JSON; the Python test
// asserts on it.
import fs from "node:fs";
import path from "node:path";

const JS = fs.readFileSync(process.env.MACHIYA_JS || path.resolve(path.dirname(new URL(import.meta.url).pathname), "../../ui/machiya.js"), "utf8");   // MACHIYA_JS: another copy (an older one, to see a test fail)
let loadNo = 0;

// -- a selector matcher: compounds (tag, .class, [attr], [attr=v], [attr="v"], :popover-open) joined by spaces --------
function splitTop(sel, ch) {
  const out = [];
  let depth = 0, cur = "";
  for (const c of sel) {
    if (c === "[") depth++;
    if (c === "]") depth--;
    if (c === ch && depth === 0) { out.push(cur); cur = ""; } else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}
function matchCompound(el, comp) {
  const re = /^([a-z][a-z0-9]*|\*)?((?:\.[\w-]+|\[[^\]]+\]|:[\w-]+)*)$/i;
  const m = comp.match(re);
  if (!m) throw new Error("selector not supported by the fake DOM: " + comp);
  if (m[1] && m[1] !== "*" && el.tagName !== m[1].toUpperCase()) return false;
  for (const part of m[2].match(/\.[\w-]+|\[[^\]]+\]|:[\w-]+/g) || []) {
    if (part[0] === ".") { if (!el.classes.has(part.slice(1))) return false; }
    else if (part[0] === ":") {
      if (part !== ":popover-open" || !el.doc.popovers) throw new SyntaxError("unsupported pseudo-class " + part);
      if (!el.popoverOpen) return false;
    } else {
      const [, name, value] = part.match(/^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/);
      if (!el.attrs.has(name)) return false;
      if (value !== undefined && el.attrs.get(name) !== value) return false;
    }
  }
  return true;
}
function matchChain(el, parts) {
  if (!matchCompound(el, parts[parts.length - 1])) return false;
  if (parts.length === 1) return true;
  for (let a = el.parentElement; a; a = a.parentElement) if (matchChain(a, parts.slice(0, -1))) return true;
  return false;
}
const matches = (el, sel) => splitTop(sel, ",").some((s) => matchChain(el, splitTop(s, " ")));

class El {
  constructor(doc, tag, attrs = {}, kids = []) {
    this.doc = doc; this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.classes = new Set();
    this.children = []; this.parentElement = null; this.listeners = {}; this.dataset = {}; this.events = [];
    this.scrollHeight = 0; this.clientHeight = 0; this.overflowY = "visible"; this.popoverOpen = false; this.rect = {};
    this.style = { setProperty(k, v) { this[k] = v; } };
    for (const [k, v] of Object.entries(attrs)) this.setAttribute(k, v);
    for (const k of kids) this.append(k);
    const self = this;
    this.classList = {
      add: (...c) => c.forEach((x) => self.classes.add(x)), remove: (...c) => c.forEach((x) => self.classes.delete(x)),
      contains: (c) => self.classes.has(c),
      toggle: (c, on) => { if (on === undefined ? !self.classes.has(c) : on) self.classes.add(c); else self.classes.delete(c); },
      [Symbol.iterator]: () => self.classes[Symbol.iterator](),
    };
  }
  setAttribute(k, v) {
    this.attrs.set(k, String(v));
    if (k === "class") { this.classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
    if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(v);
  }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  set className(v) { this.setAttribute("class", v); }
  get className() { return [...this.classes].join(" "); }
  get open() { return this.attrs.has("open"); }
  set open(v) { if (v) this.attrs.set("open", ""); else this.attrs.delete("open"); }
  get target() { return this.getAttribute("target") || ""; }
  get content() { return this.getAttribute("content") || ""; }
  set innerHTML(v) { this.html = v; }
  close() { if (this.tagName !== "DIALOG") throw new TypeError("not a dialog"); this.open = false; this.events.push("close"); }
  hidePopover() { this.popoverOpen = false; this.events.push("hidePopover"); }
  append(...kids) { for (const k of kids) { k.parentElement = this; this.children.push(k); } }
  contains(o) { for (let n = o; n; n = n.parentElement) if (n === this) return true; return false; }
  closest(sel) { for (let n = this; n; n = n.parentElement) if (matches(n, sel)) return n; return null; }
  matches(sel) { return matches(this, sel); }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  getBoundingClientRect() { return { top: 0, bottom: 0, ...this.rect }; }
  *walk() { for (const k of this.children) { yield k; yield* k.walk(); } }
}

// one page load. build(h) makes the body's children with h(tag, attrs, ...kids); opts: standalone ("media" | "ios" |
// false), popovers (the browser knows :popover-open), scrollY, selection
async function load(build, opts = {}) {
  const doc = { popovers: opts.popovers !== false };
  const h = (tag, attrs, ...kids) => new El(doc, tag, attrs || {}, kids);
  const root = h("html"), head = h("head"), body = h("body", { "data-room": "kura" });
  root.append(head, body);
  const named = {};
  const reg = (name, el) => { named[name] = el; return el; };
  body.append(...build(h, reg));
  const docListeners = {}, winListeners = {}, winOptions = {};
  const state = { reloads: 0, scrollY: opts.scrollY || 0, selection: opts.selection || "" };
  const all = () => [...root.walk()];
  Object.assign(doc, {
    body, documentElement: root, cookie: "", visibilityState: "visible", scrollingElement: { scrollTop: 0 },
    querySelectorAll(sel) { return all().filter((el) => matches(el, sel)); },
    querySelector(sel) { return all().find((el) => matches(el, sel)) || null; },
    addEventListener(t, fn) { (docListeners[t] ||= []).push(fn); },
    dispatchEvent() { return true; },
    createElement: (tag) => h(tag),
  });
  globalThis.document = doc;
  globalThis.window = globalThis;
  globalThis.addEventListener = (t, fn, o) => { (winListeners[t] ||= []).push(fn); (winOptions[t] ||= []).push(o); };
  Object.defineProperty(globalThis, "scrollY", { configurable: true, get: () => state.scrollY });
  globalThis.navigator = opts.standalone === "ios" ? { standalone: true } : {};
  globalThis.matchMedia = (q) => ({ matches: opts.standalone === "media" && q === "(display-mode: standalone)" });
  globalThis.getSelection = () => ({ toString: () => state.selection });
  globalThis.getComputedStyle = (el) => ({ overflowY: (el && el.overflowY) || "visible", getPropertyValue: () => "" });
  globalThis.location = { href: "https://kura.example.test/", origin: "https://kura.example.test", pathname: "/",
                          reload() { state.reloads++; } };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  delete globalThis.BroadcastChannel;
  globalThis.fetch = async () => { throw new TypeError("offline"); };
  await import("data:text/javascript;base64," + Buffer.from(JS + "\n// ui load " + (++loadNo)).toString("base64"));
  const fire = (list, ev) => { for (const fn of list || []) fn(ev); return ev; };
  const event = (extra) => ({ button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
                              defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra });
  const touches = (pts) => pts.map(([x, y]) => ({ clientX: x, clientY: y }));
  const page = {
    named, state, root, body,
    click(target, extra = {}) {
      if (!target) throw new Error("click: no such element");
      const ev = event({ target, ...extra });
      for (let n = target; n; n = n.parentElement) fire(n.listeners.click, ev);   // the element's own, then up
      return fire(docListeners.click, ev);
    },
    submit(form, prevented = false) {
      if (!form) throw new Error("submit: no such form"); return fire(docListeners.submit, event({ target: form, defaultPrevented: prevented })); },
    key(key) { return fire(docListeners.keydown, event({ key, target: body })); },
    win(type, extra = {}) { return fire(winListeners[type], event(extra)); },
    // a touch gesture: start at pts[0] on target, then each point as a move; end unless keep
    pull(target, pts, { keep = false, fingers = 1, prevented = false, during } = {}) {
      fire(winListeners.touchstart, event({ target, touches: touches(Array(fingers).fill(pts[0])) }));
      pts.slice(1).forEach((p, i) => {
        if (during) during(i);
        fire(winListeners.touchmove, event({ target, touches: touches(Array(fingers).fill(p)), defaultPrevented: prevented }));
      });
      if (!keep) fire(winListeners.touchend, event({ target, touches: [] }));
    },
    mark() { return doc.querySelector("div.pull"); },
    menusOpen() { return doc.querySelectorAll("details[open], dialog[open]").length; },
    listeners: (t) => (winListeners[t] || []).length,
    options: (t) => winOptions[t] || [],
  };
  return page;
}

// a room's page: the header (with its Rooms menu), a content disclosure, a scrolling pane, a dialog, a popover, and
// the phone's tab bar with its Rooms sheet (Settings, Sign Out)
function roomPage(h, reg) {
  const menu = (where) => h("details", { class: "rooms" }, h("summary", {}),
    h("nav", { class: "menu" },
      reg(where === "header" ? "headerKonbini" : "konbini", h("a", { href: "https://konbini.example.test/", "data-room": "konbini" })),
      reg(where === "header" ? "headerBlank" : "blank", h("a", { href: "https://hister.example.test/", target: "_blank" })),
      reg("settings" + (where === "header" ? "H" : ""), h("a", { href: "/settings" })),
      reg("signout" + (where === "header" ? "H" : ""), h("form", { class: "signout" }, h("button", {})))));
  const header = reg("header", h("header", { class: "top" }, reg("headerRooms", menu("header"))));
  header.rect = { bottom: 112 };
  const pane = reg("pane", h("div", { class: "pane" }, reg("paneItem", h("p", {}))));
  pane.overflowY = "auto"; pane.scrollHeight = 900; pane.clientHeight = 300;
  const flat = reg("flat", h("div", {}, reg("flatItem", h("p", {}))));    // overflow auto but nothing to scroll
  flat.overflowY = "auto"; flat.scrollHeight = 100; flat.clientHeight = 100;
  return [
    header,
    h("main", {},
      reg("para", h("p", {})),
      reg("field", h("input", { type: "text" })),
      reg("disclosure", h("details", {}, h("summary", {}), reg("disclosureLink", h("a", { href: "/n/a" })))),
      reg("cardMenu", h("details", { "data-menu": "" }, h("summary", {}), reg("cardLink", h("a", { href: "/p/x" })))),
      reg("noPull", h("div", { "data-no-pull": "" }, reg("noPullItem", h("p", {})))),
      pane, flat),
    reg("sheet", h("dialog", { class: "sheet" }, reg("sheetLink", h("a", { href: "/p/card" })),
                   reg("sheetForm", h("form", {}, h("button", {}))))),
    reg("popover", h("div", { popover: "" }, reg("popoverLink", h("a", { href: "/n/b" })))),
    h("nav", { class: "tabbar" }, reg("tab", h("a", { href: "/" })), reg("tabRooms", menu("tab"))),
  ];
}
const openAll = (p) => {
  for (const k of ["headerRooms", "tabRooms", "cardMenu", "disclosure", "sheet"]) p.named[k].open = true;
  p.named.popover.popoverOpen = true;
};
const snapshot = (p) => Object.fromEntries(["headerRooms", "tabRooms", "cardMenu", "disclosure", "sheet"]
  .map((k) => [k, p.named[k].open]).concat([["popover", p.named.popover.popoverOpen]]));

const out = {};

// -- menus ----------------------------------------------------------------------------------------------------------
{
  const r = {};
  let p = await load(roomPage);
  p.named.tabRooms.open = true;                                 // the owner's report: Rooms open, tap Settings
  p.click(p.named.settings);
  r.settingsTap = p.named.tabRooms.open;
  p.named.headerRooms.open = true;
  p.click(p.named.headerKonbini);
  r.otherRoomTap = p.named.headerRooms.open;

  p.named.tabRooms.open = true;                                 // a page restored with its menu still open
  p.win("pageshow", { persisted: true });
  r.bfcache = p.named.tabRooms.open;

  openAll(p);
  p.win("pageshow", { persisted: true });
  r.bfcacheAll = snapshot(p);
  r.sheetCloseEvent = p.named.sheet.events.includes("close");

  openAll(p);
  p.win("pageshow", { persisted: false });                      // a fresh load: a room's own dialog may be meant
  r.freshLoad = snapshot(p);

  openAll(p);
  p.win("popstate");
  r.popstate = snapshot(p);

  openAll(p);
  p.win("pagehide", { persisted: true });
  r.pagehide = snapshot(p);

  p.named.tabRooms.open = true;
  p.click(p.named.blank);                                       // target=_blank: this page stays, so does the menu
  r.blankTap = p.named.tabRooms.open;
  p.click(p.named.settings, { metaKey: true });                 // a new tab
  r.metaTap = p.named.tabRooms.open;
  p.click(p.named.settings, { defaultPrevented: true });        // a room handled the tap itself
  r.preventedTap = p.named.tabRooms.open;

  p.named.disclosure.open = true;                               // a disclosure in the page is content, not a menu
  p.click(p.named.disclosureLink);
  r.disclosureTap = p.named.disclosure.open;
  p.named.cardMenu.open = true;
  p.click(p.named.cardLink);
  r.cardMenuTap = p.named.cardMenu.open;
  p.named.sheet.open = true;
  p.click(p.named.sheetLink);
  r.sheetTap = p.named.sheet.open;
  p.named.popover.popoverOpen = true;
  p.click(p.named.popoverLink);
  r.popoverTap = p.named.popover.popoverOpen;

  p.named.tabRooms.open = true;
  p.submit(p.named.signout, true);                              // Sign Out: machiya.js holds the form for a moment
  r.signoutSubmit = p.named.tabRooms.open;
  p.named.sheet.open = true;
  p.submit(p.named.sheetForm, true);                            // the room's own form in its sheet (fetch): stays
  r.sheetFormHandled = p.named.sheet.open;
  p.submit(p.named.sheetForm, false);                           // a real submit leaves the page
  r.sheetFormSubmit = p.named.sheet.open;

  p.named.tabRooms.open = true; p.named.cardMenu.open = true;
  p.key("Escape");
  r.escape = [p.named.tabRooms.open, p.named.cardMenu.open];
  p.named.tabRooms.open = true;
  p.click(p.named.para);
  r.outside = p.named.tabRooms.open;

  p = await load(roomPage, { popovers: false });                // a browser without popovers: no errors
  openAll(p);
  p.win("pageshow", { persisted: true });
  r.noPopoverSupport = snapshot(p);
  out.menus = r;
}

process.stdout.write(JSON.stringify(out));
