# Dev seed: the extra vaults

Laid over `sample-vault/` by `compose/dev/dev init`: `work/` is a **private** vault (Kura's `KURA_VAULTS` lists it without
`+shared`: never pushed to Hister, never cached, invisible to agents unless named) and `team/` is a **shared** one. The
default vault stays `personal/`. Everything here is invented; keep it that way.
