# machiya-mcp (machiya-kobo/machiya stack/mcp): Machiya's MCP server. Stdlib Python for the server itself; the notes write tools
# also need git, ssh and vaultkit (vendored here at a tag with ../../vendor.sh, like the other consumers: markdown +
# pyyaml). Build context: this directory. Runs as uid 1000; the audit log and the notes write clone live in /data.
FROM public.ecr.aws/docker/library/python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "markdown>=3.11" pyyaml==6.0.3 \
 && useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin mcp
WORKDIR /app
COPY mcp.py backend.py envelope.py textver.py notes_writer.py CHANGELOG.md /app/
COPY rooms /app/rooms
COPY vaultkit /app/vaultkit
# The vendored vault core must match its manifest: edits go upstream (vaultkit in machiya-kobo/machiya), then re-vendor.
RUN python3 -m vaultkit.verify
ENV HOME=/data MCP_LOG=/data/mcp.log PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4))['ok'] else 1)"
CMD ["python3", "/app/mcp.py"]
