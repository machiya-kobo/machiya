# Changelog: feed-import

## 0.1.0 (unreleased: prototype, not deployed)

- The reader interface (`readers.py`) and the NewsBlur reader: GET only, with an OAuth token. It reads the stories you read one by one (the last 1,001, which is all NewsBlur keeps) and every starred story.
- The original article is fetched with smallweb's checked fetcher and sent to Hister as HTML. When it can't be fetched, or is much shorter than the feed's text (a paywall teaser), the reader's copy is stored under the article's URL instead.
- A URL Hister already has is never sent again. A starred one there without a label gets the starred label.
- Read pages get no label and keep Hister's skip rules. Starred pages get the matching topic label or the reader's name, and skip rules are ignored for them.
- The state is one sqlite file. Pending retries are kept there. `--import-legacy` takes over server's `newsblur-import.py` state.
- `--dry-run` writes nothing. `status.json` feeds the healthcheck. `FEED_IMPORT_PAUSE` skips runs during Hister's backup.
