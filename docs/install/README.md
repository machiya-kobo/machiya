# Installing Machiya

Pick the way that fits your machine. Every app also runs on its own.

| Where | How |
|---|---|
| Any machine with Docker or Podman | The whole stack: the [Quickstart](../../README.md#quickstart). To try it on invented notes first: [the sample vault](sample-vault.md). |
| Linux, without containers | Each app's own Quickstart: [Kura](https://github.com/machiya-kobo/kura#quickstart), [Niwa](https://github.com/machiya-kobo/niwa#quickstart), [Konbini](https://github.com/machiya-kobo/konbini#quickstart). |
| FreeBSD, NetBSD, OpenBSD | [Installing on the BSDs](bsd.md): the apps as rc.d services. |
| One app in a container | Each app's "More ways to run it": [Kura](https://github.com/machiya-kobo/kura#more-ways-to-run-it), [Niwa](https://github.com/machiya-kobo/niwa#more-ways-to-run-it), [Konbini](https://github.com/machiya-kobo/konbini#more-ways-to-run-it). |
| iPhone, iPad, Mac, Linux, Haiku, the web | Shiori, the search app: [its Quickstart](https://github.com/machiya-kobo/shiori#quickstart), [Linux](https://github.com/machiya-kobo/shiori#linux), [Haiku](https://github.com/machiya-kobo/shiori#haiku). |

The images are built for amd64 and arm64. Nothing is reachable from other machines until you put a proxy in front; see [remote access](remote-access.md).
