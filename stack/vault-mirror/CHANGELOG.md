# Changelog: vault-mirror

## 0.1.2

- A failed clone, fetch or reset is reported (`error` in `status.json`, the last good `head` and `synced_at` kept) instead of a fresh "synced" on the old copy (sweep 2026-10, MACH-F-4). The status's `error` never carries credentials from a URL.
- Symlinks committed to the vault are never checked out (`core.symlinks=false`; sweep KURA-2): needs vaultkit 0.22.0's `git.py`.

## 0.1.1

- `status.json` also carries the mirror's `version`, for the landing page's status.

## 0.1.0

The first versioned release: one shared clone of the vault repository, kept current (clone once, then fetch and hard reset every `MIRROR_POLL` seconds) with a `status.json` (`head`, `synced_at`, `error`) for the readers.
