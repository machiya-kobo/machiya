# Changelog: code-import

## 0.1.0

- New: the owner's repos on **Forgejo** (API v1, one read-only token) and **GitHub** (REST, one fine-grained read-only token per owner) go into Hister as documents at their real forge URLs, marked `metadata.source: code`, with `code_host`, `code_repo` (one token: `owner__repo`), `code_repo_name`, `code_kind` (repo, readme, doc, issue, pr, release), `code_state` (open, closed, merged), `code_private` (`"true"`/`"false"`) and `code_updated`. The owner's decisions of 2026-10-05: every repo he owns except forks, archived repos and `obsidian`, `pass-store`, `backup`; repo cards, READMEs and markdown docs, issues and PRs (title, body, state; no comments), releases; no code bodies.
- A repo on both forges is indexed once (`CODE_IMPORT_TWINS`), with the other copy's link on every document.
- Every document is sent with `html`, after code-import's own secret scan (redact by default, or refuse); `skip_sensitive_check` is never set. Files named like secrets are never read.
- A URL Hister already holds as the owner's own page is left alone; only documents whose `metadata.source` is `code` are ever replaced or deleted. Deleted, renamed, moved, archived and newly excluded repos, and deleted docs, releases and issues, are withdrawn.
- Cursors per owner (Forgejo's issue search) and per repo (GitHub), push stamps per repo, and GitHub's conditional GETs keep a quiet run cheap; a full run every `CODE_IMPORT_FULL_INTERVAL` (6 h).
- `--dry-run` writes nothing; `--once [--full]`; `status.json` feeds the healthcheck. Fake Forgejo and GitHub servers for tests and the dev stack in `dev/`.
