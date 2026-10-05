# Changelog: code-import

## 0.1.4

- **`CODE_IMPORT_EXCLUDE` is empty by default.** It named three repos (`obsidian`, `pass-store`, `backup`), which were one deployment's choices, not the product's. A deployment that relied on the default must now set it: to keep the old behaviour, `CODE_IMPORT_EXCLUDE=obsidian,pass-store,backup`. Without it those repos, if they exist, are imported on the next run. The setting itself is unchanged (`name` or `owner/name`, any case).

## 0.1.3

- **The secret scan catches more formats** (the 2026-10 sweep, MACH-F-6):
  - quoted keys (JSON's `"api_key": "…"`, `'client_secret': '…'`);
  - `Authorization: Bearer <token>` and `curl -u user:<password>` / `--user` (only the secret is redacted; `$TOKEN`, `<token>`, `user:pass` are left alone);
  - Discord bot tokens and webhooks, Telegram bot tokens;
  - healthchecks ping URLs (`hc-ping.com/<uuid>` or `/<ping key>/<slug>`, a self-hosted `/ping/<uuid>`).

  Private-repo text with these used to reach Hister unredacted.

## 0.1.2

- **`CODE_IMPORT_README_ONLY`**: a comma list of `host:owner/repo` imported as their **repo card and README only**: no other docs, issues, PRs or releases, and never named in `caps`. For a large repo (a fork of a firmware tree, say, with thousands of markdown docs) whose card and README are enough.
  - Listing a repo there withdraws its other documents on the next completed run of its source; the mode change forces that repo to be read again, even in an incremental run. A source that fails withdraws nothing, as before.
  - Taking a repo off the list imports its docs, releases, issues and PRs again on the next run.
  - A bad item (not `forgejo:` or `github:` plus `owner/repo`) refuses to start.

## 0.1.1

The first production run stopped at one TLS handshake timeout (`github(owner) GET /repos/owner/repo/releases`), before GitHub's machiya-kobo was reached. Now:
- **Every forge call is tried again on a transient failure**: a TLS, connect or read timeout, a reset or dropped connection, a 5xx, a 429 or a secondary rate limit. It waits 2, 8 and 30 s (`CODE_IMPORT_RETRY_DELAYS`), or the forge's `Retry-After` when longer; a wait over 120 s fails the call instead. 401, 403 and 404 are never tried again. The run's counts say how many retries there were.
- **Each source (an owner on a forge: `forgejo:owner`, `github:machiya-kobo`, …) is on its own.** A source that still fails is recorded, and the others go on: their documents land.
  - A failed source withdraws nothing. Its withdrawals, repo stamps and full-run mark are held until the whole source completes, so the next good run picks them up.
  - A source whose twins' source failed uses that source's last listing from the state. If that source has never been listed, it waits ("waiting for …").
  - Full runs are per source too (`last_full:<source>`), so the first 0.1.1 run is a full one for each source; unchanged documents cost nothing.
- **`status.json`**:
  - `sources`: per source, `ok`, `error`, `repos`, `last_success`, `last_full`;
  - `caps`: every repo over `CODE_IMPORT_MAX_DOCS`, by name, with its count of markdown docs, for the owner to decide;
  - `ok` is true only when every source succeeded (with the usual grace of three intervals), and `last_success` is the last run where every source did; `error` names the failed sources;
  - the global `last_full` is gone (per source now).
- `--once` and `--dry-run` exit 1 when any source failed; the dry run still prints the others.
- `CODE_IMPORT_TIMEOUT` (60 s) per call.
- The fake forges take injected failures (a read timeout, a reset, 503, 429) for the tests.

## 0.1.0

- New: the owner's repos on **Forgejo** (API v1, one read-only token) and **GitHub** (REST, one fine-grained read-only token per owner) go into Hister as documents at their real forge URLs, marked `metadata.source: code`, with `code_host`, `code_repo` (one token: `owner__repo`), `code_repo_name`, `code_kind` (repo, readme, doc, issue, pr, release), `code_state` (open, closed, merged), `code_private` (`"true"`/`"false"`) and `code_updated`. The owner's decisions of 2026-10-05: every repo he owns except forks, archived repos, mirrors and `obsidian`, `pass-store`, `backup`; repo cards, READMEs and markdown docs, issues and PRs (title, body, state; no comments), releases; no code bodies.
- Markdown in vendored, sample and test trees (`node_modules/`, `vendor/`, `third_party/`, `sample-vault/`, `tests/`, `test/`, `fixtures/`, `examples/`) and hidden folders is skipped by default; READMEs, `docs/` and the other markdown stay (owner, 2026-10-05).
- A repo on both forges is indexed once (`CODE_IMPORT_TWINS`), with the other copy's link on every document.
- Every document is sent with `html`, after code-import's own secret scan (redact by default, or refuse); `skip_sensitive_check` is never set. Files named like secrets are never read.
- A URL Hister already holds as the owner's own page is left alone; only documents whose `metadata.source` is `code` are ever replaced or deleted. Deleted, renamed, moved, archived and newly excluded repos, and deleted docs, releases and issues, are withdrawn.
- Cursors per owner (Forgejo's issue search) and per repo (GitHub), push stamps per repo, and GitHub's conditional GETs keep a quiet run cheap; a full run every `CODE_IMPORT_FULL_INTERVAL` (6 h).
- `--dry-run` writes nothing; `--once [--full]`; `status.json` feeds the healthcheck. Fake Forgejo and GitHub servers for tests and the dev stack in `dev/`.
