# smallweb (machiya-kobo/machiya stack/smallweb): the small-web gateway for Shiori. Stdlib Python only. Build context: this
# directory. Runs as uid 1000; state (TOFU known hosts, caches, the save log) in /data.
FROM public.ecr.aws/docker/library/python:3.13-alpine
RUN adduser -D -u 1000 -h /data -s /sbin/nologin smallweb
WORKDIR /app
COPY smallweb.py smolnet.py web.py engines.py render.py store.py selftest.py smallweb.css /app/
ENV SMALLWEB_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4))['ok'] else 1)"
CMD ["python3", "/app/smallweb.py"]
