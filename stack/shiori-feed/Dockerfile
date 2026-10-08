# shiori-feed (machiya-kobo/machiya stack/shiori-feed): an RSS feed of any Hister search, for Shiori's Subscribe. Stdlib
# Python only. Build context: this directory. Runs as uid 1000 and keeps no state (a read-only root file system is fine).
# The image listens on 0.0.0.0:8080, so it needs a gate: SHIORI_FEED_AUTH=tailscale with SHIORI_FEED_TRUSTED_PROXIES
# (or SHIORI_FEED_BIND_BEHIND_PROXY=1), or proxy with SHIORI_FEED_TRUSTED_PROXIES. It refuses to start without one.
FROM public.ecr.aws/docker/library/python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a
RUN adduser -D -u 1000 -H -s /sbin/nologin shiorifeed
WORKDIR /app
COPY shiori_feed.py CHANGELOG.md /app/
ENV SHIORI_FEED_BIND=0.0.0.0 SHIORI_FEED_PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/shiori/healthz', timeout=4)"
CMD ["python3", "/app/shiori_feed.py"]
