# shiori-ai (machiya-kobo/machiya stack/shiori-ai): page summaries and web-search answers for Shiori on the web. Stdlib
# Python only. Build context: this directory. Runs as uid 1000; the caches and daily counters (one SQLite file) live in
# /data. The image listens on 0.0.0.0:8080, so it needs a gate: SHIORI_AI_AUTH=tailscale with SHIORI_AI_TRUSTED_PROXIES
# (or SHIORI_AI_BIND_BEHIND_PROXY=1), or proxy with SHIORI_AI_TRUSTED_PROXIES. It refuses to start without one.
FROM public.ecr.aws/docker/library/python:3.13-alpine
RUN adduser -D -u 1000 -h /data -s /sbin/nologin shioriai
WORKDIR /app
COPY shiori_ai.py CHANGELOG.md /app/
ENV SHIORI_AI_DATA=/data SHIORI_AI_BIND=0.0.0.0 SHIORI_AI_PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"
CMD ["python3", "/app/shiori_ai.py"]
