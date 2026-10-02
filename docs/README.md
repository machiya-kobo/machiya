# Machiya documentation

Start with the [README](../README.md) for what Machiya is, then:

| If you want to… | Read |
|---|---|
| understand how the parts fit | [architecture.md](architecture.md) (diagrams), [principles.md](principles.md) (what "standalone" means, the vocabulary) |
| run a service | [services/](services/) (one page per service), [install/](install/), [../compose/](../compose/) and [../config/](../config/) (reference deployment) |
| write a client or an integration | [contracts/](contracts/): the Kura, Konbini, Hister and small-web APIs; [frontmatter.md](frontmatter.md): the note fields every service reads and writes |
| change the look | [design.md](design.md), [ui.md](ui.md) (shared stylesheet, script, shell) |
| reuse the vault code | [vaultkit.md](vaultkit.md) |
| connect Claude Code | [services/mcp.md](services/mcp.md) and the plugin in [../plugins/machiya/](../plugins/machiya/) |

**Words used throughout.** A **room** is one of Machiya's web apps (Kura, Niwa, Konbini); the **owner** is the single user a deployment is for (the identity the Tailscale header names); the **vault** is the Markdown notes in a git repository; a **card** is a note the board shows as a project; **work vault** means any other vault that must never reach a search index, a model or a feed.
