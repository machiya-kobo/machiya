// machiya-landing: keep the page fresh without a reload. Every minute while the page is visible (and at once when it
// comes back to the foreground after a minute or more), fetch the page again and swap in its <main> and footer.
// A failed fetch keeps what is shown and marks it, so a stale answer never looks live. On the launcher, nothing is
// swapped while the search pill has focus or text (the swap would wipe what you're typing).
const EVERY = 60_000;
let last = Date.now();

const typing = () => {
  const field = document.querySelector("form.launch input[type=search]");
  return field && (document.activeElement === field || field.value);
};

async function refresh() {
  last = Date.now();
  if (typing()) return;
  try {
    const r = await fetch(location.pathname, { credentials: "same-origin", headers: { Accept: "text/html" }, cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const doc = new DOMParser().parseFromString(await r.text(), "text/html");
    if (typing()) return;
    for (const sel of ["main.landing", "footer.foot"]) {
      const now = document.querySelector(sel), fresh = doc.querySelector(sel);
      if (now && fresh) now.replaceWith(fresh);
    }
    bindSearch();
    document.body.removeAttribute("data-offline");
  } catch {
    document.body.dataset.offline = new Date().toISOString();
  }
}

// The launcher's search pill: X clears it (Shiori's field); Enter or the magnifier submits to Shiori's search page.
function bindSearch() {
  const form = document.querySelector("form.launch");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "1";
  const input = form.querySelector("input[type=search]");
  const clear = form.querySelector(".clear");
  if (clear && input) clear.addEventListener("click", () => { input.value = ""; input.focus(); });
  form.addEventListener("submit", (ev) => { if (input && !input.value.trim()) ev.preventDefault(); });
}

bindSearch();
if (document.querySelector("main.landing")) {
  setInterval(() => { if (document.visibilityState === "visible") refresh(); }, EVERY);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - last >= EVERY) refresh();
  });
}
