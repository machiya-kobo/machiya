# Principles: every service stands alone

Each service runs by itself, so any mix of them can be deployed. A service that needs another one talks to it only over HTTP, and loses that one feature when it isn't there.

1. **Own repo, own image, own stack.** A deployment is a compose project with the service, its Tailscale sidecar and its Tailscale Service name. For example `<deployments>/<name>/` (compose, `serve.json`, `secrets.list`).
2. **Own copy of the data.** A vault service clones the vault itself and keeps the clone current:

   | Setting | Meaning |
   |---|---|
   | `<NAME>_REPO_URL` | https, ssh or file URL of the vault repo |
   | `<NAME>_REPO_SUBDIR` | the vault folder inside it (default: the repository root) |
   | `<NAME>_REPO_BRANCH` | default: the remote's |
   | `<NAME>_REPO_TOKEN_FILE` / `<NAME>_REPO_USER` | https credential, a token read from a file |
   | `<NAME>_POLL` | seconds between fetches (default 60) |

   No shared databases, and no reading another service's files. **The one exception is the stack's vault copy:** when the services run together as the Machiya stack, the stack's `vault-mirror` owns one full clone. Kura reads it read-only (`KURA_REPO_DIR` set, `KURA_REPO_URL` empty), and the writers (Niwa, Konbini) keep small clones that borrow its objects (`<NAME>_REPO_REFERENCE`, `vaultkit.borrow`). Brought up standalone, every service owns its own copy, as above, and that must always keep working.
3. **Own state, rebuildable.** Anything a service stores can be rebuilt from git. Exceptions are named in the service's README and backed up: Kura's push log into Hister, Niwa's link-rot records, and Konbini's events and claims.
4. **Integrations are optional URLs** (`KURA_URL`, `NIWA_URL`, `KONBINI_URL`, `HISTER_URL`, `SEARXNG_URL`). When one is unset, the feature that needs it is off (no sister links, no push, no card link) and nothing else breaks.
5. **HTTP only, and polite to Hister.** Every Hister call sends `Origin: hister://` and the owner's token from a file (`X-Access-Token`; ignored by a Hister without users), never changes a document's owner, and nobody runs `hister index --force` on a URL Hister already has (it would replace imported metadata). See [contracts/hister.md](contracts/hister.md).
6. **Owner gate, or the identity file's grants.** Without an identity file, pages and APIs answer only a `Tailscale-User-Login` in the service's allow-list (`*` = anyone the tailnet lets through; unset means nobody), or everyone with `*_AUTH=open` on `127.0.0.1`. With one (`MACHIYA_IDENTITY_FILE`, [identity.md](identity.md)), every request is a principal proven by a token, Tailscale, a trusted proxy or the built-in sign-in, and may do only what its grants say: no proof or a bad one is 401, no grant 403, and a header the caller writes (`X-Agent`, `Origin`) is never a permission. Or a Hister sign-in through [hister-login](services/hister-login.md) (`*_AUTH=hister`), with the Tailscale gate as an optional fallback for when sign-in is unavailable (never for a signed-out caller). The tailnet grant or the proxy is the outer gate.
7. **Health and changes.** `GET /api/status` (Konbini: `/api/health`) reports the synced commit, when it last synced and a count. The monitoring probes use it. `GET /api/changelog` serves the app's own `CHANGELOG.md` (`text/markdown`, at most 64 KiB, behind the same gate as its status; `vaultkit.changelog`), so the [landing page](services/landing.md) can say what a deploy brought without a forge or a token.
8. **Shared code is vendored.** [vaultkit](vaultkit.md) is copied into each service at a tag. A service's build fails if its copy was edited in place.
9. **Same look.** Tokyo Night / Day themes, a phone tab bar, installable as a PWA, and a service worker for the shell. Modern HTML only: no Machiya app serves HTML 3.2 or a LAN listener. Niwa's gemini and gopher mirrors are the small-web exception.

10. **Metadata goes in frontmatter.** Anything a service knows about a note (board column, next, garden stage, stream, goal, due date) is a frontmatter field on that note, never a separate note or a service's private database.

## What each service must not do

- Kura doesn't write to the vault.
- Niwa writes only the garden fields: `publish`, `growth`, `confidence`, `garden_pin`.
- Konbini writes only board frontmatter and new stub notes. It never edits a note body.
- Shiori doesn't build folder, tag or backlink browsing; it links to Kura, which owns browsing.
- SearXNG never queries Hister, because SearXNG is shared and Hister is private to the owner.
