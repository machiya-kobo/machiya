# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use GitHub's private vulnerability reporting (this repository's **Security** tab, then **Report a vulnerability**), with what you found, how to reproduce it, and what it affects. You'll get an answer within a week, and a fix or a plan before anything is disclosed.

## What's in scope

- **vaultkit:** a path that reads or writes outside the vault, a token or credential leaking into a URL, argv, a log line or `.git/config` (the `Mirror` and `GitSync` code), a way around the HTML sanitizer in the renderer, or frontmatter handling that corrupts a note.
- **Identity** (`vaultkit/identity.py`, `vaultkit/signin.py`, [docs/identity.md](docs/identity.md)): passing as a principal without its proof, doing what the identity file doesn't grant, reading a private vault outside a principal's scope, a session, token or paired device that outlives its revocation, a password, token or hash in a page, a log line or a URL, or a sign-in or pairing that skips its throttle or the same-origin rule.
- **machiya-mcp** (`stack/mcp/`): reaching a tool without being an allowed user (`MCP_USERS`, `MCP_AUTH`, or the identity file's `mcp` `use` grant); a tool that takes the caller outside its fixed route table or field allow-lists; a private work-vault note reaching a model; a write that skips the rate limits, the apply tokens or the refusals documented in `docs/services/mcp.md`; an `Origin` or `Referer` sent to Hister.
- **The plugin** (`plugins/machiya/`): `install.sh` writing anything outside the user's Claude Code settings, or widening a permission beyond the documented rules.
- **The reference compose and config** (`compose/`, `config/`): a default that exposes a service beyond localhost or the tailnet.

Hister, SearXNG, Tailscale, Obsidian and the other projects Machiya builds on are separate: report their problems to them. The services trust the `Tailscale-User-Login` header (and, with `*_AUTH=header`, your proxy's), so they must listen on `127.0.0.1` behind `tailscale serve` or the proxy; running one on a reachable address with `*_AUTH=tailscale`, `header` or `open`, or setting `*_BIND_BEHIND_PROXY=1` where the proxy isn't the only way in, is a misconfiguration, not a vulnerability. So is an identity file or `session.key` readable by others, or a `MACHIYA_COOKIE_DOMAIN` shared with sites you don't trust.

## Supported versions

Fixes go into the latest release on the main branch.
