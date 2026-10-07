# Reach Machiya from your other devices

The [Quickstart](../../README.md#quickstart) listens on `127.0.0.1` with no sign-in. To use it from your phone or laptop:

1. Put a proxy in front that sets `Tailscale-User-Login`. A Tailscale sidecar does, and adds TLS.
2. In `.env`, set `KURA_AUTH`, `NIWA_AUTH` and `KONBINI_AUTH` to `tailscale`.
3. List your login in `KURA_USERS`, `NIWA_USERS` and `KONBINI_USERS`.
4. Set the `*_BIND_BEHIND_PROXY=1` lines. Without them the apps refuse a header sign-in on a non-loopback address.

Other ways in:

- People, agents, passwords or your own auth proxy: [identity.md](../identity.md).
- Hister's users as the one sign-in for every app: [hister-login](../services/hister-login.md). The reference compose doesn't run it yet; the [dev stack](../dev-stack.md) has it wired up.
