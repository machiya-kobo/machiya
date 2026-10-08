# Release signing

Machiya's release tags are signed; day-to-day commits are not. The tags are made as the GitHub user **machiya-bot**, a member of the machiya-kobo organization, so GitHub shows them as Verified. Shiori also signs its release's `SHA256SUMS`.

## Making a release tag

```
tools/release-tag -C ~/git/kura v0.10.1 -m "Kura 0.10.1"
git -C ~/git/kura push origin v0.10.1      # and to Forgejo
```

`tools/release-tag` refuses a tag that already exists or a commit that isn't on `origin/main`. It makes an annotated tag with machiya-bot as the tagger and signs it with this machine's own key, then checks the signature against [`release/allowed_signers`](../release/allowed_signers) before keeping it. The key is read from pass (`hosts/<host>/machiya-bot-signing-key`) into a private directory on tmpfs only while git signs, and removed afterwards.

Each signing machine has its own key, listed in `release/allowed_signers` and registered on machiya-bot as a Signing Key. Adding a machine means a new key, a new line and a new registration. Removing one means deleting its line and its registration.

## Checking one

```
git -c gpg.ssh.allowedSignersFile=release/allowed_signers tag -v v0.10.1
ssh-keygen -Y verify -f release/allowed_signers -I 339386069+machiya-bot@users.noreply.github.com -n file -s SHA256SUMS.sig < SHA256SUMS
```

The deploy tools check signatures against their own pinned copy of the list, never one from the repo being released. A signed tag must verify. Once an image has had a signed release, an unsigned tag for it is refused. Tags from before the first signed release stay unsigned.
