"""A stub OIDC provider for the dev stack, shaped like tsidp: discovery (openid, code, authorization_code, S256,
client_secret_post), /authorize (signs in STUB_LOGIN at once, as tsidp does from WhoIs), /token (checks the client
secret, the redirect URI and the PKCE verifier) and /userinfo (email, username and name; no preferred_username).
The browser reaches /authorize through the front proxy (STUB_PUBLIC); Hister reaches /token and /userinfo directly
(STUB_INTERNAL). Dev only."""
import base64
import hashlib
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

PUBLIC = os.environ.get("STUB_PUBLIC", "https://idp.machiya.test:19043")
INTERNAL = os.environ.get("STUB_INTERNAL", "http://stub-idp:9000")
CLIENT_ID = os.environ.get("STUB_CLIENT_ID", "hister")
with open(os.environ.get("STUB_CLIENT_SECRET_FILE", "/run/secrets/oidc-client-secret")) as f:
    CLIENT_SECRET = f.read().strip()
LOGIN = os.environ.get("STUB_LOGIN", "owner@passkey")
EMAIL = os.environ.get("STUB_EMAIL", LOGIN + ".idp.machiya.test")
REDIRECT = os.environ.get("STUB_REDIRECT", "https://hister.machiya.test:19043/api/oauth/callback?provider=oidc")
CODES, TOKENS = {}, {}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("stub-idp %s %s" % (self.command, urlsplit(self.path).path), flush=True)

    def send(self, status, data=None, headers=()):
        body = json.dumps(data).encode() if data is not None else b""
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        if data is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/.well-known/openid-configuration":
            return self.send(200, {
                "issuer": PUBLIC, "authorization_endpoint": PUBLIC + "/authorize",
                "token_endpoint": INTERNAL + "/token", "userinfo_endpoint": INTERNAL + "/userinfo",
                "scopes_supported": ["openid", "email", "profile"], "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post"]})
        if u.path == "/authorize":
            if q.get("client_id") != CLIENT_ID or q.get("redirect_uri") != REDIRECT or q.get("response_type") != "code":
                return self.send(400, {"error": "invalid_request"})
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                return self.send(400, {"error": "pkce required"})
            code = secrets.token_urlsafe(24)
            CODES[code] = q["code_challenge"]
            sep = "&" if "?" in REDIRECT else "?"
            return self.send(302, None, [("Location", REDIRECT + sep + urlencode({"code": code,
                                                                                    "state": q.get("state", "")}))])
        if u.path == "/userinfo":
            token = (self.headers.get("Authorization") or "")[7:]
            if token not in TOKENS:
                return self.send(401, {"error": "invalid_token"})
            return self.send(200, {"sub": "stub-1", "email": EMAIL, "username": LOGIN, "name": "Dev Owner"})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if urlsplit(self.path).path != "/token":
            return self.send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        challenge = CODES.pop(form.get("code", ""), None)
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return self.send(401, {"error": "invalid_client"})
        if challenge is None or form.get("redirect_uri") != REDIRECT:
            return self.send(400, {"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        want = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if want != challenge:
            return self.send(400, {"error": "invalid_grant (pkce)"})
        token = secrets.token_urlsafe(24)
        TOKENS[token] = LOGIN
        self.send(200, {"access_token": token, "token_type": "Bearer", "expires_in": 300, "id_token": "unused"})


if __name__ == "__main__":
    ThreadingHTTPServer((os.environ.get("STUB_BIND", "0.0.0.0"), int(os.environ.get("STUB_PORT", "9000"))),
                        H).serve_forever()
