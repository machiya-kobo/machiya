# code-import (machiya-kobo/machiya stack/code-import): the owner's Forgejo and GitHub repos, into Hister. Stdlib Python.
# Build context: the machiya repo root (as feed-import). Runs as uid 1000; the state (one sqlite file) and status.json
# live in /data. It listens on nothing. The dev fakes (dev/) and the tests stay out of the image.
FROM public.ecr.aws/docker/library/python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a
RUN apk add --no-cache tzdata && adduser -D -u 1000 -h /data -s /sbin/nologin codeimport
WORKDIR /app
COPY stack/code-import/codeimport.py stack/code-import/forges.py stack/code-import/forgejo.py \
     stack/code-import/github.py stack/code-import/hister.py stack/code-import/render.py \
     stack/code-import/secretscan.py stack/code-import/store.py stack/code-import/tokens.py \
     stack/code-import/VERSION stack/code-import/CHANGELOG.md /app/
ENV CODE_IMPORT_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
HEALTHCHECK --interval=120s --timeout=5s --start-period=60s --retries=2 \
  CMD python3 -c "import json,sys; sys.exit(0 if json.load(open('/data/status.json')).get('ok') else 1)"
CMD ["python3", "/app/codeimport.py"]
