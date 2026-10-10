# machiya-landing (machiya-kobo/machiya stack/landing): the stack's front door and status page. Stdlib Python plus
# vaultkit (vendored here at a tag with ../../vendor.sh: the shell and ui/, the auth helpers; markdown + pyyaml are
# vaultkit's own imports). Build context: this directory. Runs as uid 1000; the deploy history lives in /data.
FROM public.ecr.aws/docker/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "markdown>=3.11" pyyaml==6.0.3 \
 && useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin landing \
 && mkdir -p /data && chown 1000:1000 /data
WORKDIR /app
COPY VERSION CHANGELOG.md *.py /app/
COPY static /app/static
COPY vaultkit /app/vaultkit
# The vendored vault core must match its manifest: edits go upstream (vaultkit in machiya-kobo/machiya), then re-vendor.
RUN python3 -m vaultkit.verify
ENV LANDING_STATE=/data/landing.json PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4))['ok'] else 1)"
CMD ["python3", "/app/landing.py"]
