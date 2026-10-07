# Try Machiya with the sample vault

The whole stack on an invented vault (a paper-lantern workshop and a trip to Kyoto), in about ten minutes. [`tools/quickstart-test`](../../tools/quickstart-test) runs every block marked `quickstart:` on a fresh clone, so these are exactly the commands that were tested. To run it on your own notes instead, see the [Quickstart](../../README.md#quickstart).

## What you need

- `git` and `curl`
- Docker Engine 24+ with Compose 2.20+, or rootless Podman 4.9+ with the `docker-compose` plugin and its socket on (`systemctl --user enable --now podman.socket`; then type `podman compose` wherever it says `docker compose`)
- About 2 GB of disk, on amd64 or arm64
- These ports free on `127.0.0.1`: 8081, 8082, 8083, 4433, 8888, 1965 and 7070 ([`compose/.env.example`](../../compose/.env.example) moves any of them)

On a clean Debian or Ubuntu machine, install them, then log out and back in (or run `newgrp docker`) so you're in the `docker` group:

<!-- quickstart: packages-debian -->
```bash
sudo apt-get update
sudo apt-get install -y git curl docker.io docker-compose
sudo usermod -aG docker "$USER"
```

(Elsewhere: Docker's own packages for [your system](https://docs.docker.com/engine/install/), or Podman as above.)

**1. Get the code.** The stack builds its services from their own repositories, checked out side by side:

<!-- quickstart: clone -->
```bash
mkdir machiya-stack && cd machiya-stack
git clone https://github.com/machiya-kobo/machiya.git
git clone https://github.com/machiya-kobo/kura.git
git clone https://github.com/machiya-kobo/niwa.git
git clone https://github.com/machiya-kobo/konbini.git
```

**2. Set the demo up.** `demo-init` makes the sample vault a git repository, gives Konbini its own clone, and writes a `.env` for this machine: your user and group ids, a fresh SearXNG secret, no login (safe only because every port binds `127.0.0.1`), every app on.

<!-- quickstart: init -->
```bash
cd machiya/compose
./demo-init
```

**3. Start it.** The first start builds three images and pulls Hister, SearXNG and Valkey:

<!-- quickstart: up -->
```bash
docker compose up -d --build
```

**4. Check that it is up.** The loop waits (up to three minutes) for the services to start and the three rooms to read the vault:

<!-- quickstart: check -->
```bash
for i in $(seq 90); do
  curl -sf http://127.0.0.1:8083/api/status | grep -q '"ready": true' &&
  curl -sf http://127.0.0.1:8082/api/status | grep -q '"ready": true' &&
  curl -sf http://127.0.0.1:8081/healthz >/dev/null &&
  curl -sf http://127.0.0.1:4433/ >/dev/null &&
  curl -sf http://127.0.0.1:8888/healthz >/dev/null && break
  sleep 2
done
curl -s http://127.0.0.1:8083/api/status | grep -m1 -o '"notes": [0-9]*'
curl -s http://127.0.0.1:8082/api/status | grep -m1 -o '"published": [0-9]*'
curl -s http://127.0.0.1:8081/api/status | grep -m1 -o '"cards": [0-9]*'
curl -s -o /dev/null -w 'hister %{http_code}\n' http://127.0.0.1:4433/
curl -s http://127.0.0.1:8888/healthz; echo
for port in 8083 8082 8081; do curl -s http://127.0.0.1:$port/ | grep -o '<title>[^<]*'; done
```

You should see:

<!-- quickstart-expect: check -->
```text
"notes": 27
"published": 9
"cards": 10
hister 200
OK
<title>Kura
<title>Niwa
<title>Konbini
```

Open them in a browser (the **Rooms** menu in each header moves between them):

| | Address | What you see |
|---|---|---|
| **Kura** | http://localhost:8083/ | all 27 notes: folders, tags, backlinks, full-text search (try `bamboo`) |
| **Niwa** | http://localhost:8082/ | the garden: the 9 published notes, growth stages, and a gemini capsule on `gemini://localhost:1965/` |
| **Konbini** | http://localhost:8081/ | the board: 10 cards across every column, plus Review, Plan and Calendar |
| **Hister** | http://localhost:4433/ | the pages-and-notes index; Kura has pushed the notes into it (search `bamboo`) |
| **SearXNG** | http://localhost:8888/ | web search (it needs the internet to answer) |

**5. Watch a change travel.** Publishing a note in Niwa commits to the vault; Kura picks the commit up and shows the note as published:

<!-- quickstart: try -->
```bash
curl -s "http://127.0.0.1:8083/api/note?path=Notes/Candle%20vs%20LED.md" | grep -o '"published": [a-z]*'
curl -s -o /dev/null -X POST http://127.0.0.1:8082/publish -H "Origin: http://127.0.0.1:8082" \
  --data-urlencode "rel=Notes/Candle vs LED.md" -d on=1 -d confirm=1
for i in $(seq 90); do
  curl -s "http://127.0.0.1:8083/api/note?path=Notes/Candle%20vs%20LED.md" | grep -q '"published": true' && break
  sleep 3
done
curl -s "http://127.0.0.1:8083/api/note?path=Notes/Candle%20vs%20LED.md" | grep -o '"published": [a-z]*'
git -C data/vault.git log --format='%an: %s' -1
```

<!-- quickstart-expect: try -->
```text
"published": false
"published": true
garden: garden: 1 change (publish Candle vs LED)
```

(In the browser you would use Niwa's **Publish** button on a note's page; the command above is the same form post.)

**6. Stop it, or start over.** Your data is the `data/` folder next to `compose.yml`:

<!-- quickstart: stop -->
```bash
docker compose down -v
rm -rf data .env
```

## One shared copy of the vault

By default every app keeps its own copy of the vault. With `mirror.yml`, one `vault-mirror` service keeps a single clone: Kura reads it, and Niwa and Konbini borrow its git objects. That's how the stack runs for real, one flag away:

<!-- quickstart: mirror -->
```bash
./demo-init --mirror
docker compose up -d --build
for i in $(seq 90); do
  curl -sf http://127.0.0.1:8083/api/status | grep -q '"ready": true' &&
  curl -sf http://127.0.0.1:8082/api/status | grep -q '"ready": true' &&
  curl -sf http://127.0.0.1:8081/healthz >/dev/null && break
  sleep 2
done
docker compose exec -T niwa sh -c 'cat /data/repo/.git/objects/info/alternates' | sed 's#.*/data/#.../data/#'
docker compose down -v
rm -rf data .env
```

<!-- quickstart-expect: mirror -->
```text
.../data/mirror/vault/.git/objects
```
