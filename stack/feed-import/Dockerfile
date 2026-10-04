# feed-import (machiya-kobo/machiya stack/feed-import): what its owner reads in a feed reader, into Hister. Stdlib Python.
# Build context: the machiya repo root, because the original articles are fetched with stack/smallweb's checked fetcher
# (web.py + smolnet.py, unchanged). Runs as uid 1000; the state (one sqlite file) and status.json live in /data.
# It listens on nothing.
FROM public.ecr.aws/docker/library/python:3.13-alpine
RUN apk add --no-cache tzdata && adduser -D -u 1000 -h /data -s /sbin/nologin feedimport
WORKDIR /app
COPY stack/smallweb/web.py stack/smallweb/smolnet.py /app/
COPY stack/feed-import/feedimport.py stack/feed-import/readers.py stack/feed-import/newsblur.py \
     stack/feed-import/hister.py stack/feed-import/store.py /app/
ENV FEED_IMPORT_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
HEALTHCHECK --interval=120s --timeout=5s --start-period=60s --retries=2 \
  CMD python3 -c "import json,sys; sys.exit(0 if json.load(open('/data/status.json')).get('ok') else 1)"
CMD ["python3", "/app/feedimport.py"]
