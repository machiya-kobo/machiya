# Machiya documentation

Start with the [README](../README.md) for what Machiya is, then:

| If you want to… | Read |
|---|---|
| understand how the parts fit | [architecture.md](architecture.md) (diagrams), [principles.md](principles.md) (what "standalone" means, the vocabulary) |
| run a service | [services/](services/) (one page per service), [install/](install/), [../compose/](../compose/) and [../config/](../config/) (reference deployment) |
| write a client or an integration | [contracts/](contracts/): the Kura, Konbini, Hister and small-web APIs; [frontmatter.md](frontmatter.md): the note fields every service reads and writes |
| change the look | [design.md](design.md), [ui.md](ui.md) (shared stylesheet, script, shell) |
| reuse the vault code | [vaultkit.md](vaultkit.md) |
| add people, agents, sign-in or Shiori devices | [identity.md](identity.md) (optional; off by default); Hister's users as the sign-in: [services/hister-login.md](services/hister-login.md) |
| connect Claude Code | [services/mcp.md](services/mcp.md) and the plugin in [../plugins/machiya/](../plugins/machiya/) |

**Words used throughout.** A **room** is one of Machiya's web apps (Kura, Niwa, Konbini); the **owner** is the user a deployment is for (the Tailscale login in `*_USERS`, or the identity file's `owner = true` principal); the **vault** is the Markdown notes in a git repository; a **card** is a note the board shows as a project; a **work vault** (or **private vault**) is any other vault that must never reach a search index, a model or a feed; a **shared vault** is one the owner marked `+shared` in Kura's config, treated like the default vault at its `/v/<name>/` address. A **principal** is who is calling, as the identity file names it: a person, an agent or a service. A **grant** is what a principal may do in one room (`kura` `read` with its vaults, `konbini` `write`, …). The **identity file** is the optional, read-only TOML file (`MACHIYA_IDENTITY_FILE`) that lists the principals, how each proves who it is, and their grants ([identity.md](identity.md)).
