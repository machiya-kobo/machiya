// machiya-landing: keep the status fresh without a reload. Every minute while the page is visible (and at once when it
// comes back to the foreground after a minute or more), fetch the page again and swap in its <main> and footer.
// A failed fetch keeps what is shown and marks it, so a stale answer never looks live.
const EVERY = 60_000;
let last = Date.now();

async function refresh() {
  last = Date.now();
  try {
    const r = await fetch(location.pathname, { credentials: "same-origin", headers: { Accept: "text/html" }, cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const doc = new DOMParser().parseFromString(await r.text(), "text/html");
    for (const sel of ["main.landing", "footer.foot"]) {
      const now = document.querySelector(sel), fresh = doc.querySelector(sel);
      if (now && fresh) now.replaceWith(fresh);
    }
    document.body.removeAttribute("data-offline");
  } catch {
    document.body.dataset.offline = new Date().toISOString();
  }
}

if (document.querySelector("main.landing")) {
  setInterval(() => { if (document.visibilityState === "visible") refresh(); }, EVERY);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - last >= EVERY) refresh();
  });
}
