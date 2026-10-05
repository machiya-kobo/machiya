# hister-login (machiya-kobo/machiya stack/hister-login): Hister's users as the one sign-in for the Machiya rooms.
# Stdlib Python plus vaultkit (vendored here at a tag with ../../vendor.sh, like the other consumers: markdown + pyyaml).
# Build context: this directory. Runs as uid 1000; the SQLite state lives in /data (mount /srv/hister-login there).
FROM public.ecr.aws/docker/library/python:3.13-slim
RUN pip install --no-cache-dir "markdown>=3.11" pyyaml==6.0.3 \
 && useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin hister-login
WORKDIR /app
COPY hister_login.py /app/
COPY static /app/static
COPY vaultkit /app/vaultkit
# The vendored vault core must match its manifest: edits go upstream (vaultkit in machiya-kobo/machiya), then re-vendor.
RUN python3 -m vaultkit.verify
ENV HISTER_LOGIN_DB=/data/hister-login.sqlite3 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080 8081
# healthy = the helper answers (its /healthz is 503 while Hister is down; that is Hister's health, not the helper's)
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import http.client as h; c = h.HTTPConnection('127.0.0.1', 8081, timeout=4); c.request('GET', '/healthz'); c.getresponse().read(); c.close()"]
CMD ["python3", "/app/hister_login.py"]
