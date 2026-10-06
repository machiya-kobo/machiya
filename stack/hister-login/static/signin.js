// hister-login's sign-in page: the password goes straight to Hister's own /api/login on this origin (the helper never
// sees it); Hister sets its host-only session cookie, and the page then goes back to /machiya/signin, which finds that
// session, issues machiya_sso and sends the browser where it started.
const form = document.getElementById("hister-signin");
const alertBox = document.querySelector("[data-error]");
function show(text) {
  alertBox.textContent = text;
  alertBox.hidden = false;
}
if (form) {
  // a password manager's auto-submit calls form.submit(), which skips the submit event: send it through it
  form.submit = () => form.requestSubmit();
  const username = document.getElementById("signin-username");
  const password = document.getElementById("signin-password");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    alertBox.hidden = true;
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ username: username.value.trim(), password: password.value }),
      });
      if (res.ok) {
        location.replace(form.dataset.next || "/machiya/signin");
        return;
      }
      show(res.status === 401 ? "Wrong name or password."
        : res.status === 403 ? "Password sign-in is off here; use another way below."
        : "Sign-in failed (" + res.status + "). Try again.");
    } catch {
      show("Hister can't be reached. Try again in a minute.");
    }
    button.disabled = false;
  });
}
