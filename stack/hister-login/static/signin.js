// hister-login's sign-in page: the password goes straight to Hister's own /api/login on this origin (the helper never
// sees it); Hister sets its host-only session cookie, and the page then goes back to /machiya/signin, which finds that
// session, issues machiya_sso and sends the browser where it started. There is no <form>, as on Hister's own sign-in
// page: the button's click or Enter in a field signs in, and nothing on the page can post to the helper.
const box = document.getElementById("hister-signin");
const alertBox = document.querySelector("[data-error]");
function show(text) {
  alertBox.textContent = text;
  alertBox.hidden = false;
}
if (box) {
  const username = document.getElementById("signin-username");
  const password = document.getElementById("signin-password");
  const button = box.querySelector("button");
  let busy = false;
  async function signIn() {
    if (busy) return;
    for (const field of [username, password]) {
      if (!field.reportValidity()) return;      // required: the browser says which field is empty
    }
    busy = true;
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
        location.replace(box.dataset.next || "/machiya/signin");
        return;
      }
      show(res.status === 401 ? "Wrong name or password."
        : res.status === 403 ? "Password sign-in is off here; use another way below."
        : "Sign-in failed (" + res.status + "). Try again.");
    } catch {
      show("Hister can't be reached. Try again in a minute.");
    }
    busy = false;
    button.disabled = false;
  }
  button.addEventListener("click", signIn);
  for (const field of [username, password]) {
    field.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.isComposing) {
        ev.preventDefault();
        signIn();
      }
    });
  }
}
