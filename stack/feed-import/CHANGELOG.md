# Changelog: feed-import

## 0.1.0

- The reader interface (`readers.py`) and the NewsBlur reader: GET only, with an OAuth token. It reads the stories you read one by one (the last 1,001, which is all NewsBlur keeps) and every starred story.
- The original article is fetched with smallweb's checked fetcher and sent to Hister as HTML. When it can't be fetched, or is much shorter than the feed's text (a paywall teaser), the reader's copy is stored under the article's URL instead.
- A URL Hister already has is never sent again. A starred one there without a label gets the starred label.
- Read pages get no label and keep Hister's skip rules. Starred pages get the matching topic label or the reader's name, and skip rules are ignored for them.
- The state is one sqlite file. Pending retries are kept there. `--import-legacy` takes over server's `newsblur-import.py` state.
- `--dry-run` writes nothing. `status.json` feeds the healthcheck. `FEED_IMPORT_PAUSE` skips runs during Hister's backup.
- Three more readers through the same interface, each tested against a fake of its API:
  - **Miniflux**: an API key; `changed_at` paging;
  - **FreshRSS**: Google Reader API, user plus API password; the read and starred id lists are diffed;
  - **Feedbin**: e-mail plus password; read entries over a look-back window plus recently read; starred ids.
- A starred page that no tag matches gets the label `starred`, as the owner decided 2026-10-04 (it was the reader's name). A star without a time gets the time it was first seen, or the story's date in a backfill.
