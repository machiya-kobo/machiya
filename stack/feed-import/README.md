# feed-import

Puts what you read in a feed reader into [Hister](../../docs/services/hister.md), on its own.

- **Stories you read** go in as visited pages: no label.
- **Stories you star** go in as saved pages, with a label.

The readers are NewsBlur, Miniflux, FreshRSS and Feedbin, all through one interface (`readers.py`). Each one only reads: none marks, stars or changes anything. It is stdlib Python with one sqlite file, and it listens on nothing.

**Status: 0.1.x, early.** NewsBlur is the reader in daily use; Miniflux, FreshRSS and Feedbin are built and tested against fakes.

## What a run does

Runs happen every `FEED_IMPORT_INTERVAL` seconds. For each reader:

1. **Starred, then read.** It reads the starred entries, then the read ones, newest first, and stops at the first page with nothing new. The first run walks the whole history: that is the backfill. Entries that are pending a retry are taken from the state, not from the reader's pages.
2. **Hister is asked first** (`GET /api/document`). If Hister already has the URL, nothing is sent: a page is never indexed again ([contracts/hister.md](../../docs/contracts/hister.md)). The one exception: a starred entry whose page has no label gets the starred label (`POST /api/label`).
3. **The original article is fetched once**, as a browser would open it. It uses stack/smallweb's checked fetcher:
   - http(s) only, and never a private address unless `FEED_IMPORT_FETCH_ALLOW` names it;
   - every redirect is checked; at most 5 MB and 30 s.
   The page goes to Hister as HTML (`POST /api/add`) and Hister extracts the text, as it does for a visited page. If the redirect lands on a URL Hister already has, nothing is sent.
4. **The reader's copy** of the story is stored instead, under the article's URL with `<reader>_content: copy`, in these cases:
   - the original can't be fetched for good: a 4xx, not HTML, refused, or too big;
   - it timed out, got a 429 or got a 5xx on `FEED_IMPORT_MAX_TRIES` runs;
   - its text is under 1/`FEED_IMPORT_COPY_RATIO` of the copy's, which looks like a paywall teaser. A copy under 500 characters never triggers this.
   An entry with neither an original nor a copy is skipped.

### What Hister gets

| | A read story | A starred story |
|---|---|---|
| `label` | none | the first of its reader tags that names a Hister topic label (from the aliases in `/api/rules`), else `FEED_IMPORT_STARRED_LABEL` (default `starred`). Only NewsBlur and FreshRSS have the owner's own tags on an entry |
| `added` | the read time if the reader has one (Miniflux: `changed_at`). Otherwise when it was first seen read, or in a backfill the story's own date | the star time if the reader has one (NewsBlur; Miniflux: `changed_at`). Otherwise when first seen, or the story's date in a backfill |
| skip rules | apply (406 means skipped) | ignored (`metadata.ignore_skip_rules`): a save is deliberate |
| `metadata` | `source`, `via` = the reader, `client: feed-import`, and the reader's own keys (`newsblur_story_hash`, `_feed_id`, `_feed`, `_folder`, `_tags`, `_stream`, `_content`, `_copy_reason`, `_permalink`, `_starred`) | the same |

- Hister's answers: 422 (sensitive content) and any other 4xx are final. If Hister is down (5xx, unreachable), the run stops and nothing is counted against an entry.
- A Hister page has one label. The reader's feed and folder go in metadata, not labels. Hister v0.20.0 matches a metadata query only on a whole value in lowercase, so these work: `metadata.source:newsblur`, `metadata.newsblur_feed_id:7`, `metadata.newsblur_stream:read` and `metadata.newsblur_content:copy`. Feed titles and folders are there for display: a mixed-case value like `Tech` isn't matched.
- Unstarring changes nothing in Hister.

## NewsBlur

It uses GET calls only, with an OAuth access token. NewsBlur's scopes are not checked per call, so the token could change state; this reader never sends anything but GET.

| Call | Use |
|---|---|
| `/reader/read_stories?page=&limit=50&order=newest` | stories marked read one by one, newest first |
| `/reader/starred_story_hashes?include_timestamps=true` | every starred story with its time |
| `/reader/starred_stories?h=…` | up to 100 starred stories in full |
| `/reader/feeds?flat=true&update_counts=false` | feed titles and folders |

Limits of NewsBlur's API, read from its source:
- **Only the last 1,001 stories read one by one** (a Redis list), whatever the account tier. NewsBlur keeps no read time.
- **"Mark all as read" is not in that list.**
- Calls are spaced 2 s apart.

## Miniflux, FreshRSS, Feedbin

| Reader | Calls | What to set up |
|---|---|---|
| **Miniflux** | `GET /v1/entries?status=read` and `?starred=true`, `order=changed_at&direction=desc`, 100 a page. Paging stops at a page with nothing new | the server's URL, and an API key (Settings → API Keys) in a file |
| **FreshRSS** | Google Reader API at `<url>/api/greader.php`. `POST /accounts/ClientLogin` once per run, then the read and starred id lists (`stream/items/ids`, 1,000 a page), diffed against the state, and `POST stream/items/contents` for the new ones. Both POSTs only read | the server's URL, the user name, and the **API password** (Profile → API management; API access enabled in Authentication) in a file |
| **Feedbin** | `GET /v2/entries.json?read=true&since=<look-back>` (all pages), `/v2/recently_read_entries.json`, `/v2/starred_entries.json` then `entries.json?ids=` (100 a call), `/v2/subscriptions.json`, `/v2/taggings.json` | the account's e-mail, and its password in a file (Feedbin has no API tokens) |

What they don't keep, and so what the importer can't know:
- **Miniflux** keeps no separate read or star time: `changed_at` is the last change of either. It deletes read entries after `CLEANUP_ARCHIVE_READ_DAYS`, 60 days by default; starred entries are kept.
- **FreshRSS** keeps no read or star time, and lists entries in the order it got them, not the order you read them. So every run walks the whole read id list (cheap: ids only). It purges read entries after about 3 months by default (`keep_period`).
- **Feedbin** keeps no read or star time. `since` filters on when Feedbin got an entry, so a run covers entries from the last `FEED_IMPORT_FEEDBIN_LOOKBACK` days (14). It also covers older entries that Feedbin's recently-read list names; that list only records posts kept open 10 s in Feedbin's web app.

Inoreader is left out: its API needs a Pro plan, OAuth refresh tokens, and 100 requests a day.

## Settings

| Env | Default | |
|---|---|---|
| `FEED_IMPORT_READERS` | `newsblur` | comma list of `newsblur`, `miniflux`, `freshrss`, `feedbin` |
| `FEED_IMPORT_NEWSBLUR_URL` | — | the server the token belongs to: `https://newsblur.com` or a self-hosted NewsBlur's address (required for newsblur; a token from another server gets `authenticated: false`) |
| `FEED_IMPORT_NEWSBLUR_TOKEN_FILE` | — | a file holding a NewsBlur OAuth access token (required for newsblur) |
| `FEED_IMPORT_MINIFLUX_URL` | — | the Miniflux server, e.g. `https://miniflux.example` (required for miniflux) |
| `FEED_IMPORT_MINIFLUX_TOKEN_FILE` | — | a file holding a Miniflux API key (required for miniflux) |
| `FEED_IMPORT_FRESHRSS_URL` | — | the FreshRSS server, e.g. `https://freshrss.example` (`/api/greader.php` is added) (required for freshrss) |
| `FEED_IMPORT_FRESHRSS_USER` | — | the FreshRSS user name (required for freshrss) |
| `FEED_IMPORT_FRESHRSS_PASSWORD_FILE` | — | a file holding that user's API password (required for freshrss) |
| `FEED_IMPORT_FEEDBIN_URL` | `https://api.feedbin.com` | |
| `FEED_IMPORT_FEEDBIN_USER` | — | the Feedbin account's e-mail (required for feedbin) |
| `FEED_IMPORT_FEEDBIN_PASSWORD_FILE` | — | a file holding its password (required for feedbin) |
| `FEED_IMPORT_FEEDBIN_LOOKBACK` | `14` | days of read entries each run walks |
| `FEED_IMPORT_HISTER_URL` | — | `http://hister:4433` (required, except for `--dry-run`) |
| `FEED_IMPORT_HISTER_TOKEN_FILE` | — | the owner's Hister token (the file's first line), sent as `X-Access-Token` on every Hister call ([contracts/hister.md](../../docs/contracts/hister.md)); a Hister without users ignores it. Set but missing, empty or not a token: refuses to start. Re-read when the file changes (a file that vanishes keeps the last good value); while it is set, a redirect from Hister is never followed. Unset means no token is sent |
| `FEED_IMPORT_INTERVAL` | `600` | seconds between runs (at least 60) |
| `FEED_IMPORT_PAUSE` | — | `Sun 02:20-02:50`: no runs in that weekly slot (Hister's backup), in `FEED_IMPORT_TZ` (default `UTC`) |
| `FEED_IMPORT_STARRED_LABEL` | `starred` | label for a starred page that no tag matches |
| `FEED_IMPORT_TAG_LABELS` | `1` | `0`: never take a label from the reader's tags |
| `FEED_IMPORT_MAX_TRIES` | `3` | runs that may retry a timeout, 429 or 5xx before the copy is stored |
| `FEED_IMPORT_COPY_RATIO` | `1.5` | the paywall rule above |
| `FEED_IMPORT_SOCKS` | — | `socks5h://proxy:1080`: fetch the originals through a SOCKS5 proxy, as smallweb does. Unset means direct |
| `FEED_IMPORT_FETCH_ALLOW` | — | private hosts or CIDRs an original may be fetched from (smallweb's `SMALLWEB_FETCH_ALLOW`) |
| `FEED_IMPORT_DATA` | `/data` | `feed-import.sqlite3` (the state and cursor) and `status.json` |

`status.json` holds `ok`, `running`, `last_success`, `failures_in_a_row`, `error` and the counts per status. `ok` turns false after a failed run once no run has succeeded for three intervals. The image's healthcheck reads it. Alert on it only: counts are information.

## Running

```sh
python3 feedimport.py --dry-run --limit 10            # print what would be sent: at most 10 per stream per reader
                                                       # (10 starred + 10 read); no state, no Hister writes
python3 feedimport.py --dry-run --no-fetch --stream read
python3 feedimport.py --import-legacy state.json       # once: take over an older newsblur-import.py script's state
python3 feedimport.py --once                           # one run
python3 feedimport.py                                  # the service
podman build -f stack/feed-import/Dockerfile -t feed-import:dev .     # from the repo root
```

- A dry run asks Hister only which URLs it has, and only if `FEED_IMPORT_HISTER_URL` is set.
- A dry run still fetches the originals (GETs) unless you pass `--no-fetch`.

## Tests

```sh
cd stack/feed-import && python3 -m unittest discover -s tests
```

The tests need no network. They run against local fakes of NewsBlur, Miniflux, FreshRSS, Feedbin, Hister and the article servers, with smallweb's fetcher allowed to reach `127.0.0.1`. Every fake records its requests, and a test fails if a reader sends anything that could change state.

## Layout

| File | |
|---|---|
| `feedimport.py` | the pipeline, the dry run, the service loop and `status.json` |
| `readers.py` | the interface (`Entry`, `Reader`) and the readers' shared HTTP client |
| `newsblur.py`, `miniflux.py`, `freshrss.py`, `feedbin.py` | the readers |
| `hister.py` | the four Hister calls and Hister's URL normalisation |
| `store.py` | the state |
