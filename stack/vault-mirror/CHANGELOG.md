# Changelog: vault-mirror

## 0.1.1

- `status.json` also carries the mirror's `version`, for the landing page's status.

## 0.1.0

The first versioned release: one shared clone of the vault repository, kept current (clone once, then fetch and hard reset every `MIRROR_POLL` seconds) with a `status.json` (`head`, `synced_at`, `error`) for the readers.
