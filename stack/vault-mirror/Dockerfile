# vault-mirror (machiya-kobo/machiya stack/vault-mirror): git + the vaultkit package from this repo. Build context: the
# machiya repo root (the compose file points at it). Runs as uid 1000 (0.1.3: the image's own USER, not only compose's
# `user:`); it writes only under /data (the clone, status.json; HOME), which must be writable by that uid.
FROM public.ecr.aws/docker/library/python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin mirror \
 && mkdir -p /data && chown 1000:1000 /data
WORKDIR /app
COPY vaultkit/front.py vaultkit/git.py /app/vaultkit/
COPY stack/vault-mirror/mirror.py /app/
RUN printf '' > /app/vaultkit/__init__.py
ENV HOME=/data PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
HEALTHCHECK --interval=60s --timeout=5s --start-period=15m --retries=3 \
  CMD python3 -c "import json,time,sys; d=json.load(open('/data/status.json')); sys.exit(0 if d.get('head') and time.time()-(d.get('synced_at') or 0) < 600 else 1)"
CMD ["python3", "-u", "/app/mirror.py"]
