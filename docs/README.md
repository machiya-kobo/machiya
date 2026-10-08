# Machiya documentation

Start with the [README](../README.md) for what Machiya is, then:

| If you want to… | Read |
|---|---|
| understand how the parts fit | [architecture.md](architecture.md) (diagrams), [principles.md](principles.md) (what "standalone" means, the vocabulary) |
| run a service | [services/](services/) (one page per service), [install/](install/), [../compose/](../compose/) and [../config/](../config/) (reference deployment) |
| develop or test against the whole stack | [dev-stack.md](dev-stack.md): every service on synthetic data, in containers or natively, on this machine or a test VM |
| write a client or an integration | [contracts/](contracts/): the Kura, Konbini, Hister and small-web APIs, and the settings that follow a person ([prefs.md](contracts/prefs.md)); [frontmatter.md](frontmatter.md): the note fields every service reads and writes |
| change the look | [design.md](design.md), [ui.md](ui.md) (shared stylesheet, script, shell) |
| write copy (page, READMEs, UI text) | [voice.md](voice.md) |
| reuse the vault code | [vaultkit.md](vaultkit.md) |
| add people, agents, sign-in or Shiori devices | [identity.md](identity.md) (optional; off by default); Hister's users as the sign-in: [services/hister-login.md](services/hister-login.md) |
| connect Claude Code | [services/mcp.md](services/mcp.md) and the plugin in [../plugins/machiya/](../plugins/machiya/) |

**Words used throughout.** A **room** is one of Machiya's web apps (Kura, Niwa, Konbini); the **owner** is the user a deployment is for (the Tailscale login in `*_USERS`, or the identity file's `owner = true` principal); the **vault** is the Markdown notes in a git repository; a **card** is a note the board shows as a project; a **work vault** (or **private vault**) is any other vault that must never reach a search index, a model or a feed; a **shared vault** is one the owner marked `+shared` in Kura's config, treated like the default vault at its `/v/<name>/` address. A **principal** is who is calling, as the identity file names it: a person, an agent or a service. A **grant** is what a principal may do in one room (`kura` `read` with its vaults, `konbini` `write`, …). The **identity file** is the optional, read-only TOML file (`MACHIYA_IDENTITY_FILE`) that lists the principals, how each proves who it is, and their grants ([identity.md](identity.md)).

## What's in this repo

- **[docs/](./)**: [architecture](architecture.md), [principles](principles.md), [voice](voice.md), the [service docs](services/), [API contracts](contracts/), the [frontmatter schema](frontmatter.md), the [design](design.md) and its [shared UI](ui.md), and [install guides](install/).
- **[vaultkit/](../vaultkit/)**: the shared vault core (frontmatter, notes, wikilinks, the Markdown renderer, a git mirror). Each app vendors it at a tag: [docs/vaultkit.md](vaultkit.md).
- **[ui/](../ui/)**: the web apps' shared stylesheet and script (themes, tab bar, Rooms menu, offline shell): [docs/ui.md](ui.md).
- **[stack/](../stack/)**: small services beside the apps:
  - `mcp/`: one MCP server for the board, notes, saved pages, labels, collections and the garden ([docs](services/mcp.md))
  - `landing/`: the stack's front page and its `/status` page ([docs](services/landing.md))
  - `hister-login/`: Hister's users as the one sign-in for every app ([docs](services/hister-login.md))
  - `smallweb/`: Gemini and Gopher search for Shiori, and saving the pages Shiori asks for into Hister
  - `feed-import/`: what you read and star in a feed reader, into Hister
  - `code-import/`: your Forgejo and GitHub repos, into Hister for Shiori's Code view ([docs](services/code-import.md))
  - `shiori-feed/`: an RSS feed of any Hister search, for Shiori's Subscribe ([docs](services/shiori-feed.md))
  - `shiori-ai/`: page summaries and web-search answers for Shiori on the web ([docs](services/shiori-ai.md))
  - `vault-mirror/`: one shared clone of the vault
- **[plugins/machiya/](../plugins/machiya/)**: a Claude Code plugin: the MCP server plus skills for the backlog, the weekly review, recall, garden suggestions and tidying labels.
- **[compose/](../compose/)**: the reference compose, and [compose/dev/](../compose/dev/), the dev stack on synthetic data ([docs/dev-stack.md](dev-stack.md)).
- **[config/](../config/)**: reference config for Hister and SearXNG.
- **[sample-vault/](../sample-vault/)**: the invented vault.
