"""The dev stack's front door (stdlib only, dev only): Tailscale Serve's job in production. It listens on every
public port, terminates TLS with the throwaway dev certificate (or passes plain http when a real Tailscale Serve sits
in front of it), and forwards to the service: on Hister's port, `/machiya/` and the one path `/api/oauth/callback` go
to the hister-login helper and everything else to Hister, as Serve does on Hister's name.

One request per connection (answers carry `Connection: close`); a WebSocket upgrade is piped both ways until either side
closes. X-Forwarded-For/-Proto/-Host are added when the client didn't send them (a Serve in front sets its own).

    FRONT_SITES     JSON {"PORT": "ROUTE ROUTE ...", ...}; a ROUTE is PREFIX=URL (longest prefix wins) or =PATH=URL
                    (that path exactly), e.g. {"19204": "/machiya/=http://hister-login:8080 /=http://hister:4433"}
    FRONT_BIND      0.0.0.0
    FRONT_TLS_CERT, FRONT_TLS_KEY   PEM files: https on every port; empty: plain http
"""
import json
import os
import socket
import ssl
import sys
import threading
from urllib.parse import urlsplit

MAX_HEAD = 64 * 1024


def parse_routes(text):
    routes = []
    for item in (text or "").split():
        exact = item.startswith("=")
        prefix, _, url = item[1:].partition("=") if exact else item.partition("=")
        u = urlsplit(url)
        if not prefix.startswith("/") or u.scheme != "http" or not u.hostname:
            raise SystemExit("front: bad route %r (PREFIX=http://host:port)" % item)
        routes.append((exact, prefix, (u.hostname, u.port or 80)))
    routes.sort(key=lambda r: (not r[0], -len(r[1])))
    return routes


def pick(routes, path):
    bare = path.split("?", 1)[0]
    for exact, prefix, upstream in routes:
        if (exact and bare == prefix) or (not exact and bare.startswith(prefix)):
            return upstream
    return None


def read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            return None, b""
        data += chunk
        if len(data) > MAX_HEAD:
            return None, b""
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def pipe(src, dst):
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except (OSError, ssl.SSLError):
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except (OSError, ssl.SSLError):
                pass


def handle(client, addr, routes, tls):
    up = None
    try:
        client.settimeout(30)
        if tls is not None:
            client = tls.wrap_socket(client, server_side=True)
        head, rest = read_head(client)
        if head is None:
            return
        lines = head.decode("latin-1").split("\r\n")
        target = (lines[0].split(" ") + ["", ""])[1]
        upstream = pick(routes, target)
        if upstream is None:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 9\r\nConnection: close\r\n\r\nno route\n")
            return
        headers = [l for l in lines[1:] if l]
        names = {l.split(":", 1)[0].strip().lower() for l in headers}
        upgrade = "upgrade" in names and any(l.lower().startswith("connection:") and "upgrade" in l.lower()
                                             for l in headers)
        out = [lines[0]]
        for l in headers:
            if not upgrade and l.split(":", 1)[0].strip().lower() in ("connection", "keep-alive", "proxy-connection"):
                continue
            out.append(l)
        if not upgrade:
            out.append("Connection: close")
        if "x-forwarded-for" not in names:
            out.append("X-Forwarded-For: %s" % addr[0])
        if "x-forwarded-proto" not in names:
            out.append("X-Forwarded-Proto: %s" % ("https" if tls is not None else "http"))
        if "x-forwarded-host" not in names:
            host = next((l.split(":", 1)[1].strip() for l in headers if l.lower().startswith("host:")), "")
            if host:
                out.append("X-Forwarded-Host: %s" % host)
        up = socket.create_connection(upstream, timeout=30)
        up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + rest)
        client.settimeout(None if upgrade else 300)
        up.settimeout(None if upgrade else 300)
        t = threading.Thread(target=pipe, args=(client, up), daemon=True)
        t.start()
        pipe(up, client)
        t.join(5)
    except (OSError, ssl.SSLError):
        pass
    finally:
        for s in (up, client):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def serve(bind, port, routes, tls):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, port))
    srv.listen(128)
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a, routes, tls), daemon=True).start()


def main():
    sites = json.loads(os.environ.get("FRONT_SITES") or "{}")
    if not sites:
        sys.exit("front: FRONT_SITES is empty")
    bind = os.environ.get("FRONT_BIND", "0.0.0.0")
    cert, key = os.environ.get("FRONT_TLS_CERT", "").strip(), os.environ.get("FRONT_TLS_KEY", "").strip()
    tls = None
    if cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key or None)
    threads = []
    for port, text in sorted(sites.items()):
        routes = parse_routes(text)
        print("front: %s %s:%s %s" % ("https" if tls else "http", bind, port, " ".join(
            "%s%s->%s:%d" % ("=" if e else "", p, u[0], u[1]) for e, p, u in routes)), flush=True)
        t = threading.Thread(target=serve, args=(bind, int(port), routes, tls), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
