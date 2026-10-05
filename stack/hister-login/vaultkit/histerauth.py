"""Hister's users as the one sign-in (AUTH=hister), for any room: the room's side of the hister-login helper.

A room in `<P>_AUTH=hister` mode never talks to Hister about people. It asks the helper (stack/hister-login), over the
internal network, whether a credential is good:

    GET {<P>_AUTH_URL}/v1/check   X-Machiya-Session: mhs_…   (the machiya_sso cookie, or Bearer mhs_… from an app)
                                  X-Access-Token: <t>         (a Hister token: X-Access-Token, or any other Bearer)

and caches the answer (30 s when signed in, 5 s when signed out or unavailable, 10 s when Hister's user handling is
off). The order of checks, first match decides:

1. a Hister token; present but invalid is a 401, never passed over;
2. a session id, from Bearer mhs_… or the machiya_sso cookie;
3. no credential: a page is sent to the helper's sign-in (at most once per 30 s per browser: the loop guard), an API
   call gets 401 JSON {"error": "sign in", "signin": …}; unless sign-in is known to be unavailable, then 4.;
   with MACHIYA_SIGNIN_PROVIDER (v0.21, e.g. `oidc`: Tailscale's tsidp, which knows the device) the address carries
   provider=<it>&auto=1, so the helper goes straight through that provider with no page and no tap (an installed web app
   has its own cookie jar and would otherwise ask once per app); the helper shows its page instead after a
   deliberate sign-out (the `<sign-in cookie>_out` marker, which signout() sets) or a failed round trip, and the
   loop guard's page links to the plain sign-in page;
4. unavailable (the helper or Hister unreachable, a 5xx, or user handling off): with `<P>_AUTH_FALLBACK=tailscale` a
   Tailscale-User-Login in `<P>_USERS` is admitted as the owner, with a banner and nothing cached; with `none` (Kura)
   the answer is 503.

"Signed out" (the helper's 401) never falls back: only "nobody answered" does. A Hister username outside
`<P>_HISTER_USERS` is a 403 (OAuth creates other accounts on its own), never a fallback and never a redirect.

Room sessions (v0.22, docs/identity.md "One cookie per room"): the room keeps its OWN host-only cookie,
`__Host-<sign-in cookie>_<room>` (`__Host-machiya_sso_kura`), holding a room session `mhr_…` that only this room's
origin may use. A browser without one is sent to the helper with `state=<SHA-256 of a nonce>` (the nonce stays in the
host-only cookie `…_state`); the helper, once it knows the browser, sends it back to `/machiya/callback?code=mhc_…`,
which `resolve` answers itself: it trades the code at the helper's internal `POST /v1/redeem` (one use, 60 s, bound
to this room's origin and to the nonce) for the room session and redirects to the page. Every check names this room
(`X-Machiya-Room: <origin>`, plus `<P>_AUTH_ACCEPT_ORIGINS`), so a room session copied to another room is refused.
Headless callers present a room token (`Bearer mht_…`) the helper issued for this room; Shiori's apps keep their
`Bearer mhs_…`. The old shared-domain cookie (`machiya_sso`) and Hister's raw token are still read, after the room's
own cookie, and passed to the helper, which accepts them only while its HISTER_LOGIN_LEGACY says so.

Preferences (v0.21, docs/contracts/prefs.md): in this mode a room's `/api/prefs` is the account's. The room calls
`forward_prefs`, which passes the request to the helper's `GET`/`PUT /v1/prefs` with the CALLER's own credential
(never a user id), so a container on the internal network can't read or write anyone else's settings. The check's
answer also carries the account's shared preferences (`Result.prefs`) for a fresh browser's first render.

Standard library only; nothing here is specific to one room. The helper shares `safe_return` and `hister_headers`.
"""
import base64
import collections
import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from . import prefs as vprefs
from .identity import Identity, IdentityError, Principal, check_bind, tailscale_uid

SSO_COOKIE = "machiya_sso"              # the base name; the legacy shared-domain cookie itself (read, never set here)
TRY_COOKIE = "machiya_sso_try"          # (before v0.22) the loop guard; now <host prefix><base>_<room>_try
HOST_PREFIX = "__Host-"                 # host-only, Secure, Path=/: no other host can set or widen it (https only)
COOKIE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,60}\Z")
SID_PREFIX = "mhs_"
SID_RE = re.compile(r"mhs_[A-Za-z0-9_-]{43}\Z")       # 32 random bytes, unpadded base64url
ROOM_PREFIX, RTOKEN_PREFIX, CODE_PREFIX = "mhr_", "mht_", "mhc_"
RSID_RE = re.compile(r"mhr_[A-Za-z0-9_-]{43}\Z")      # a room session: one room's (v0.22)
RTOKEN_RE = re.compile(r"mht_[A-Za-z0-9_-]{43}\Z")    # a room token: headless callers, scoped to rooms (v0.22)
CODE_RE = re.compile(r"mhc_[A-Za-z0-9_-]{43}\Z")      # a one-time code on the way back from the helper (v0.22)
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")         # the state nonce, and its SHA-256 on the way to the helper
TOKEN_RE = re.compile(r"[\x21-\x7e]{1,4096}\Z")        # printable ASCII, no spaces: anything else is unreadable
IDENTITY_PREFIXES = ("mch_", "mcd_")                   # the identity file's Bearer tokens, never a Hister token
CALLBACK_PATH = "/machiya/callback"     # where the helper sends a browser back with a code (every room, v0.22)
ORIGIN_RE = re.compile(r"https?://[a-z0-9.-]+(:[0-9]{1,5})?\Z")

CHECK_TIMEOUT = 2.0                     # /v1/check
REDEEM_TIMEOUT = 3.0                    # /v1/redeem
STATE_MAX_AGE = 600                     # the state nonce: a password typed at the helper's page has 10 minutes
ROOM_MAX_AGE = 180 * 86400              # the room cookie's most (the helper session's cap decides, server side)
PREFS_TIMEOUT = 2.0                     # /v1/prefs
MAX_ANSWER = 1 << 20                    # the most of an answer read from the helper
HEALTH_TIMEOUT = 1.0                    # /healthz
SIGNOUT_TIMEOUT = 3.0
TTL_OK, TTL_OUT, TTL_OFF, TTL_DOWN = 30, 5, 10, 5   # the room's cache, by the helper's answer (§3.5)
HEALTH_TTL = 10                         # a request with no credential re-asks /healthz when the flag is older
LOOP_WINDOW = 30                        # one redirect to the helper per browser per this many seconds
OUT_MAX_AGE = 30 * 86400                # the signed-out marker: no automatic sign-in until the next sign-in
PROVIDER_RE = re.compile(r"[a-z0-9_-]{1,32}\Z")
CACHE_MAX = 1024
MAX_RETURN = 2048

OK, SIGNED_OUT, NOT_ALLOWED, UNAVAILABLE, FALLBACK = "ok", "signed-out", "not-allowed", "unavailable", "fallback"
CALLBACK = "callback"                   # the way back from the helper: always a redirect (respond)
BANNER_TEXT = "Signed in through the tailnet: sign-in is unavailable"
REFUSED_TEXT = {"legacy-off": "this room no longer takes Hister's token or the shared sign-in cookie: use a room "
                              "token (hister-login's sessions page) or sign in",
                "wrong-room": "that sign-in belongs to another room"}


# -- shared with the helper ------------------------------------------------------------------------------------------

def _host_entry(entry):
    """'Host.example[:port]' -> ('host.example', port or None); None when unreadable."""
    entry = (entry or "").strip().lower()
    if not entry or any(c.isspace() or c in "/\\@?#" for c in entry):
        return None
    try:
        u = urlsplit("https://" + entry)
        host, port = u.hostname, u.port
    except ValueError:
        return None
    if not host or u.username is not None:
        return None
    return host, (None if port in (None, 443) else port)


def safe_return(url, hosts, schemes=()):
    """`url` when it is a safe place to send a browser after signing in, else None (the helper then goes to Hister's
    own page). Safe means: a string of at most 2048 characters with no control characters, whitespace or backslash,
    and either
      - an absolute https:// URL, no userinfo, whose host (with its port, when it isn't 443) is exactly one of `hosts`
        ("kura.example.ts.net", or "kura.example.test:8443"), or
      - for the app flow only, a URL in one of `schemes` (e.g. "shiori"), with no userinfo and no fragment (the helper
        adds its own).
    Nothing is normalised into a different address: what passes is returned unchanged."""
    if not isinstance(url, str) or not url or len(url) > MAX_RETURN:
        return None
    if any(ord(c) < 33 or ord(c) == 127 or 128 <= ord(c) < 160 or c == "\\" for c in url):
        return None
    try:
        u = urlsplit(url)
        scheme = u.scheme.lower()
        if scheme == "https":
            if u.username is not None or u.password is not None or "@" in u.netloc or not u.hostname:
                return None
            port = u.port                           # a malformed port raises ValueError
            want = (u.hostname.lower(), None if port in (None, 443) else port)
            allowed = {h for h in (_host_entry(x) for x in hosts or ()) if h}
            return url if want in allowed else None
        if scheme and scheme in {s.strip().lower() for s in schemes or () if s.strip()}:
            if scheme in ("http", "https", "javascript", "data", "file", "vbscript", "blob"):
                return None
            if "@" in u.netloc or u.fragment or "#" in url:
                return None
            return url
    except ValueError:
        return None
    return None


def signin_url(signin, return_to):
    """The helper's sign-in address with `return=<return_to>` (when there is one)."""
    if not return_to:
        return signin
    return signin + ("&" if "?" in signin else "?") + urlencode({"return": return_to})


def origin_of(url):
    """'https://Kura.Example:443/x' -> 'https://kura.example'; 'http://h:8080/' -> 'http://h:8080'; None when it isn't
    an http(s) address with a host. A room's origin (its public address) is what a room session is bound to; the helper
    and the rooms both write it this way, so they compare equal strings."""
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url):
        return None
    try:
        u = urlsplit(url.strip())
        scheme, host, port = u.scheme.lower(), (u.hostname or "").lower(), u.port
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host or u.username is not None or "@" in u.netloc:
        return None
    if port == {"https": 443, "http": 80}[scheme]:
        port = None
    out = "%s://%s%s" % (scheme, host, "" if port is None else ":%d" % port)
    return out if ORIGIN_RE.match(out) else None


def origins_header(value):
    """X-Machiya-Room's value -> its origins (at most 8, each as origin_of writes it); [] when unreadable."""
    out = []
    for part in (value or "").split(","):
        o = origin_of(part.strip())
        if not o or len(out) >= 8:
            return []
        out.append(o)
    return out


def state_hash(nonce):
    """The state on the way to the helper: SHA-256 of the room's nonce, unpadded base64url (43 characters). The nonce
    itself stays in the browser's host-only cookie, so whoever sees the address can't redeem the code."""
    return base64.urlsafe_b64encode(hashlib.sha256(nonce.encode("ascii")).digest()).rstrip(b"=").decode()


def new_nonce():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


_TOKENS = {}
_TOKENS_LOCK = threading.Lock()


def hister_headers(token_file=""):
    """The headers every vaultkit-based Hister caller sends (docs/contracts/hister.md): `Origin: hister://` and, when
    `token_file` names a readable non-empty file, `X-Access-Token` with its contents (stripped). The file is re-read
    when it changes (inode, mtime, size), so a rotated token is picked up without a restart. The token is never
    logged; a missing file is said once on stderr, by path."""
    out = {"Origin": "hister://"}
    if not token_file:
        return out
    try:
        st = os.stat(token_file)
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _TOKENS_LOCK:
        cached = _TOKENS.get(token_file)
        if cached and cached[0] == stamp:
            token = cached[1]
        else:
            token = ""
            if stamp is not None:
                try:
                    with open(token_file, encoding="utf-8") as f:
                        token = f.read().strip()
                except (OSError, UnicodeError):
                    token = ""
            if not token and (not cached or cached[1]):
                print("histerauth: no Hister token in %s; calling Hister without one" % token_file,
                      file=sys.stderr, flush=True)
            _TOKENS[token_file] = (stamp, token)
    if token and TOKEN_RE.match(token):
        out["X-Access-Token"] = token
    return out


# -- the room's side -------------------------------------------------------------------------------------------------

class Result:
    """What `resolve` decided.
    principal: an identity.Principal (the owner) or None.
    status: 200, 401 (signed out / no credential), 403 (a Hister user this room doesn't admit), 503 (sign-in
        unavailable and no fallback).
    reason: ok | signed-out | not-allowed | unavailable | fallback.
    cookies: Set-Cookie values the room must send (its room cookie set or cleared, the state, the loop guard).
    location: for a page, where to redirect (302) now; None when the loop guard says show a page instead. With
        reason "callback" (the way back from the helper, /machiya/callback) it is the page to go to, the room cookie
        is in `cookies`, and respond() always answers 302.
    signin: the helper's sign-in address for this page (for the API's 401 JSON and the "Sign in" link).
    banner: show the fallback banner (BANNER_TEXT).
    actor: for the log: hister:<u>, app:<u>, token:<u> or fallback:<login>.
    prefs: the account's shared preferences ({"theme", "palette", "text_size", "apps_hidden"}, as the helper's check
        said, up to 30 s old), for a first render without cookies (shell.prefs(cookies, account=result.prefs)); {}
        when there is no account (the fallback, Tailscale only) or the helper didn't say."""

    def __init__(self, principal=None, status=200, reason=OK, cookies=(), location=None, signin="", banner=False,
                 actor="", error="", prefs=None):
        self.principal, self.status, self.reason = principal, status, reason
        self.cookies, self.location, self.signin = list(cookies), location, signin
        self.banner, self.actor, self.error = banner, actor, error
        self.prefs = dict(prefs or {})

    def __bool__(self):
        return self.principal is not None

    def __repr__(self):
        return "Result(%s, %d, %s)" % (self.principal, self.status, self.reason)

    def json(self):
        """The JSON body of a refused API call."""
        if self.status == 401:
            out = {"error": "sign in", "signin": self.signin}
            if self.error:                  # why the helper refused a credential (legacy-off, wrong-room)
                out["reason"], out["detail"] = self.error, REFUSED_TEXT.get(self.error, "")
            return out
        if self.status == 403:
            return {"error": "this account has no access here"}
        return {"error": "sign-in is unavailable", "reason": self.reason}


def _http_fetch(base):
    """A fetch(method, path, headers, timeout) -> (status, body) for an http(s)://host[:port] base URL. OSError (and
    http.client's errors) for anything that isn't an answer. No proxies, no redirects followed."""
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise IdentityError("the sign-in service's address must be http(s)://host[:port], not %r" % base)
    prefix = u.path.rstrip("/")
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection

    def fetch(method, path, headers, timeout, body=None):
        conn = cls(u.hostname, u.port, timeout=timeout)
        try:
            conn.request(method, prefix + path, body=body, headers=dict(headers or {}))
            resp = conn.getresponse()
            return resp.status, resp.read(MAX_ANSWER)
        except (http.client.HTTPException, ValueError) as e:
            raise OSError(str(e))
        finally:
            conn.close()
    return fetch


def sso_cookie_name(env=None):
    """MACHIYA_SSO_COOKIE: the sign-in cookie's name, `machiya_sso` unless set (a second stack under the same cookie
    domain, such as a dev stack on the same tailnet, sets its own so the two never read each other's cookie). The
    rooms, landing and the helper must agree. IdentityError for anything but letters, digits, _ and -."""
    env = os.environ if env is None else env
    name = (env.get("MACHIYA_SSO_COOKIE") or "").strip() or SSO_COOKIE
    if not COOKIE_NAME_RE.match(name):
        raise IdentityError("MACHIYA_SSO_COOKIE must be a cookie name (letters, digits, _ and -), not %r" % name)
    return name


class HisterAuth:
    """One room's AUTH=hister settings and its check cache. Build it with load_for(); `fetch` is for tests."""

    def __init__(self, room, signin, users, public_url, auth_url="", fallback="tailscale", fallback_users=(),
                 cookie_domain="", secure=True, fetch=None, clock=time.monotonic, sso_cookie=SSO_COOKIE, provider="",
                 accept_origins=()):
        if fallback not in ("tailscale", "none"):
            raise IdentityError("%s: the fallback must be tailscale or none, not %r" % (room, fallback))
        if not signin:
            raise IdentityError("%s: AUTH=hister needs the helper's sign-in address" % room)
        if not users:
            raise IdentityError("%s: AUTH=hister needs the Hister usernames it admits" % room)
        if "*" in users or "*" in fallback_users:
            raise IdentityError("%s: '*' is refused in hister mode: name the owner" % room)
        if fallback == "none" and not auth_url:
            raise IdentityError("%s: AUTH=hister with no fallback needs the sign-in service's address" % room)
        if cookie_domain and not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", cookie_domain):
            raise IdentityError("MACHIYA_COOKIE_DOMAIN must be a domain name, not %r" % cookie_domain)
        self.room, self.signin, self.public_url = room, signin, public_url.rstrip("/")
        self.users = frozenset(users)
        self.auth_url, self.fallback = auth_url, fallback
        self.fallback_users = frozenset(u.strip().lower() for u in fallback_users if u.strip())
        if not COOKIE_NAME_RE.match(sso_cookie or ""):
            raise IdentityError("MACHIYA_SSO_COOKIE must be a cookie name, not %r" % sso_cookie)
        self.cookie_domain, self.secure = cookie_domain, secure
        self.origin = origin_of(self.public_url)
        if not self.origin:
            raise IdentityError("%s: its public address must be http(s)://host[:port], not %r" % (room, public_url))
        accepted = []
        for o in accept_origins or ():
            norm = origin_of(o)
            if not norm or urlsplit(o.strip()).path not in ("", "/"):
                raise IdentityError("%s: an accepted origin is scheme://host[:port] only, not %r" % (room, o))
            if norm != self.origin and norm not in accepted:
                accepted.append(norm)
        self.audiences = [self.origin] + accepted[:7]   # X-Machiya-Room: this room first, then those it also accepts
        key = re.sub(r"[^a-z0-9_-]", "", room.lower())[:20] or "room"
        # v0.22: the room's own cookies are host-only (__Host- over https: no other host can set, read or widen them)
        # and carry the room's name, so rooms sharing a host (a dev stack on one name, ports apart) never collide
        self.sso_cookie = sso_cookie                # the legacy shared-domain cookie (read after the room's own)
        self.room_cookie = (HOST_PREFIX if secure else "") + sso_cookie + "_" + key
        self.state_cookie = self.room_cookie + "_state"
        self.try_cookie = self.room_cookie + "_try"
        self.out_cookie = self.room_cookie + "_out" # a deliberate sign-out here: no automatic sign-in from this room
        if provider and not PROVIDER_RE.match(provider):
            raise IdentityError("MACHIYA_SIGNIN_PROVIDER must be a provider's name (a-z, 0-9, _, -), not %r" % provider)
        self.provider = provider
        self.fetch = fetch or (_http_fetch(auth_url) if auth_url else None)
        self.clock = clock
        self.cache = collections.OrderedDict()      # key -> (expires, outcome): least recently used first
        self.lock = threading.Lock()
        self.health = (None, True)                  # (checked at, available)
        self.fallback_total = 0
        self.standalone = not auth_url              # no helper: Tailscale identity only (said at start-up)
        one = sorted(self.fallback_users)
        self.prefs_login = one[0] if fallback == "tailscale" and len(one) == 1 else ""

    # -- settings-derived pieces

    def uid_for(self, username):
        """The preferences key of the owner: the fallback login's tailscale_uid when there is exactly one (so the
        fallback sees the same settings), else "hi:" + a hash of the Hister username."""
        if self.prefs_login:
            return tailscale_uid(self.prefs_login)
        return "hi:" + hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]

    def signin_location(self, path="/", auto=True, state=None):
        """The 302 target for a page at `path` (a local path, query included): the helper's sign-in with return=
        this room's public address + path, state=<SHA-256 of `state`> when a nonce is given (the helper then sends
        a code back to /machiya/callback), and provider=<MACHIYA_SIGNIN_PROVIDER> when set and `auto` (the automatic
        sign-in). Never built from Host."""
        if not isinstance(path, str) or not path.startswith("/") or path[1:2] in ("/", "\\") \
                or any(ord(c) < 33 or ord(c) == 127 for c in path) or len(path) > MAX_RETURN - 200:
            path = "/"
        if urlsplit(path).path == CALLBACK_PATH:
            path = "/"                              # never back to a used code
        url = signin_url(self.signin, self.public_url + quote(path, safe="/?&=%:@!$'()*+,;~-._"))
        if state:
            url += ("&" if "?" in url else "?") + urlencode({"state": state_hash(state)})
        if auto and self.provider:
            url += ("&" if "?" in url else "?") + urlencode({"provider": self.provider, "auto": "1"})
        return url

    def _cookie(self, name, value, max_age, domain=True):
        attrs = ["%s=%s" % (name, value), "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
        if self.secure:
            attrs.append("Secure")
        if domain and self.cookie_domain:
            attrs.append("Domain=" + self.cookie_domain)
        return "; ".join(attrs)

    def host_cookie(self, name, value, max_age):
        """A cookie of this room's own host (no Domain): the room cookie, its state, guard and marker."""
        return self._cookie(name, value, max_age, domain=False)

    def clear_cookies(self):
        """Set-Cookie values that drop this room's sign-in: its own cookie, and the legacy shared-domain machiya_sso
        (on the domain, and host-only for a stray copy)."""
        out = [self.host_cookie(self.room_cookie, "", 0), self._cookie(self.sso_cookie, "", 0)]
        if self.cookie_domain:
            out.append(self._cookie(self.sso_cookie, "", 0, domain=False))
        return out

    # -- reading the request

    @staticmethod
    def cookie_values(header, name):
        out = []
        for part in (header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name and v.strip():
                out.append(v.strip())
        return out

    def credential(self, headers):
        """-> (kind, value, from_cookie). kind:
          "room"   a room session (mhr_): this room's own cookie, or Bearer;
          "rtoken" a room token (Bearer mht_), for headless callers;
          "sid"    a helper id (mhs_): Bearer from Shiori's apps, or the legacy shared-domain cookie;
          "token"  Hister's raw token (X-Access-Token, or any other Bearer): legacy, the helper decides;
          "bad"    present but unreadable; None: nothing.
        The first present decides: X-Access-Token, Authorization, this room's cookie, the legacy cookie."""
        for name in ("Authorization", "X-Access-Token"):
            if len(Identity.header_values(headers, name)) > 1:
                return "bad", "", False
        token = headers.get("X-Access-Token")
        if token is not None:
            token = token.strip()
            return ("token", token, False) if TOKEN_RE.match(token) else ("bad", "", False)
        auth = headers.get("Authorization")
        if auth is not None:
            scheme, _, value = auth.strip().partition(" ")
            value = value.strip()
            if scheme.lower() != "bearer" or not TOKEN_RE.match(value or " "):
                return "bad", "", False
            for prefix, rx, kind in ((SID_PREFIX, SID_RE, "sid"), (ROOM_PREFIX, RSID_RE, "room"),
                                     (RTOKEN_PREFIX, RTOKEN_RE, "rtoken")):
                if value.startswith(prefix):
                    return (kind, value, False) if rx.match(value) else ("bad", "", False)
            if value.startswith(IDENTITY_PREFIXES + (CODE_PREFIX,)):
                return "bad", "", False             # the identity file's tokens aren't combined with hister mode yet
            return "token", value, False
        cookie = headers.get("Cookie")
        mine = self.cookie_values(cookie, self.room_cookie)
        for value in mine[:4]:
            if RSID_RE.match(value):
                return "room", value, True
        if mine:
            return "bad", "", True                  # this room's cookie, but not a room session: cleared, then sign-in
        for value in self.cookie_values(cookie, self.sso_cookie)[:4]:
            if SID_RE.match(value):
                return "sid", value, True           # legacy: the shared-domain cookie (the helper decides)
        if self.cookie_values(cookie, self.sso_cookie):
            return "bad", "", True                  # a machiya_sso that can't be one of ours: cleared, then sign-in
        return None, "", False

    def _cred_headers(self, kind, value):
        """The headers that carry a credential to the helper, with this room's origins (X-Machiya-Room)."""
        name = "X-Access-Token" if kind == "token" else "X-Machiya-Session"
        return {name: value, "X-Machiya-Room": ", ".join(self.audiences)}

    # -- the cache

    def _key(self, kind, value):
        return kind + ":" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _cached(self, key):
        with self.lock:
            hit = self.cache.get(key)
            if not hit:
                return None
            if hit[0] <= self.clock():
                del self.cache[key]
                return None
            self.cache.move_to_end(key)
            return hit[1]

    def _store(self, key, outcome, ttl):
        with self.lock:
            self.cache[key] = (self.clock() + ttl, outcome)
            self.cache.move_to_end(key)
            while len(self.cache) > CACHE_MAX:
                self.cache.popitem(last=False)

    def forget(self, kind, value):
        with self.lock:
            self.cache.pop(self._key(kind, value), None)

    def _set_health(self, available):
        with self.lock:
            was = self.health[1]
            self.health = (self.clock(), available)
        if was != available:
            print("histerauth: %s: sign-in is %s" % (self.room, "available again" if available else
                  "unavailable" + ("; falling back to the tailnet identity" if self.fallback == "tailscale" else
                                   "; refusing everyone (no fallback)")), file=sys.stderr, flush=True)

    # -- asking the helper

    def check(self, kind, value):
        """-> ("ok", username, user_id, shared prefs) | ("out",) | ("off",) | ("down",), cached."""
        key = self._key(kind, value)
        hit = self._cached(key)
        if hit is not None:
            return hit
        try:
            status, body = self.fetch("GET", "/v1/check", dict(self._cred_headers(kind, value),
                                                               Accept="application/json"), CHECK_TIMEOUT)
        except (OSError, http.client.HTTPException):
            status, body = None, b""
        outcome, ttl = ("down",), TTL_DOWN
        data = None
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = None
        wrong_room = kind == "room" and isinstance(data, dict) and data.get("room") not in self.audiences
        if status == 200 and isinstance(data, dict) and isinstance(data.get("username"), str) and data["username"] \
                and not wrong_room:
            outcome, ttl = ("ok", data["username"], data.get("user_id"), vprefs.shared_only(data.get("prefs"))), TTL_OK
        elif status in (400, 401, 403) or (status == 200 and wrong_room):
            reason = data.get("reason") if isinstance(data, dict) else None
            outcome, ttl = ("out", "wrong-room" if wrong_room else reason if reason in REFUSED_TEXT else ""), TTL_OUT
        elif status == 503 and isinstance(data, dict) and data.get("reason") == "user-handling-off":
            outcome, ttl = ("off",), TTL_OFF
            print("histerauth: %s: Hister's user handling is OFF: nobody is signed in through it" % self.room,
                  file=sys.stderr, flush=True)
        self._set_health(outcome[0] in ("ok", "out"))
        self._store(key, outcome, ttl)
        return outcome

    def available(self):
        """The health flag (§4.3): the outcome of the last check, or the helper's /healthz when that is older than
        HEALTH_TTL seconds (1 s timeout)."""
        if self.standalone:
            return False
        with self.lock:
            at, ok = self.health
        if at is not None and self.clock() - at < HEALTH_TTL:
            return ok
        try:
            status, _ = self.fetch("GET", "/healthz", {"Accept": "application/json"}, HEALTH_TIMEOUT)
            ok = status == 200
        except (OSError, http.client.HTTPException):
            ok = False
        self._set_health(ok)
        return ok

    # -- deciding

    def resolve(self, headers, is_page=True, path="/"):
        """headers: http.server's message (or a mapping with .get); is_page: a top-level page load (else an API,
        XHR or service-worker call: never redirected); path: the page's local path + query, for return=. Never
        raises: anything unexpected is refused (503, no fallback)."""
        try:
            return self._resolve(headers, is_page, path)
        except Exception as e:          # a request thread must never die on hostile input; fail closed
            print("histerauth: %s: %s while checking a request" % (self.room, type(e).__name__),
                  file=sys.stderr, flush=True)
            return Result(None, 503, UNAVAILABLE, signin=self.signin)

    def _resolve(self, headers, is_page, path):
        if self.standalone:
            return self._fallback(headers, banner=False)
        if isinstance(path, str) and urlsplit(path).path == CALLBACK_PATH:
            return self._callback(headers, path)
        kind, value, from_cookie = self.credential(headers)
        guard_set = bool(self.cookie_values(headers.get("Cookie"), self.try_cookie))
        if kind == "bad":
            return self._signed_out(is_page, path, guard_set, from_cookie, headers)
        if kind is None:
            if not self.available():
                return self._unavailable(headers, UNAVAILABLE)
            return self._signed_out(is_page, path, guard_set, False, headers)
        outcome = self.check(kind, value)
        if outcome[0] == "ok":
            username = outcome[1]
            via = "token" if kind in ("token", "rtoken") else ("hister" if from_cookie else "app")
            cookies = [self.host_cookie(self.try_cookie, "", 0)] if guard_set else []
            if username not in self.users:
                return Result(None, 403, NOT_ALLOWED, cookies, actor="%s:%s" % (via, username))
            p = Principal(username, "person", owner=True, via=via, uid=self.uid_for(username))
            return Result(p, 200, OK, cookies, actor="%s:%s" % (via, username),
                          prefs=outcome[3] if len(outcome) > 3 else None)
        if outcome[0] == "out":
            res = self._signed_out(is_page, path, guard_set, from_cookie, headers)
            res.error = outcome[1] if len(outcome) > 1 else ""
            return res
        return self._unavailable(headers, UNAVAILABLE)

    def marked(self, headers):
        """This browser signed out here on purpose: no automatic sign-in from this room until the next sign-in."""
        return bool(self.cookie_values(headers.get("Cookie"), self.out_cookie)) if headers is not None else False

    def _trip(self, path, auto, cookies):
        """A trip to the helper that comes back with a code: a fresh nonce in this browser's host-only state cookie,
        its SHA-256 in the address. -> the helper's sign-in address."""
        nonce = new_nonce()
        cookies.append(self.host_cookie(self.state_cookie, nonce, STATE_MAX_AGE))
        return self.signin_location(path, auto=auto, state=nonce)

    def _signed_out(self, is_page, path, guard_set, clear, headers=None):
        """401: a page goes to the helper (once per LOOP_WINDOW) and comes back with a code, an API call gets JSON
        (its sign-in address has no state: machiya.js takes the page there, the helper signs the browser in and sends
        it back to the page, which then makes its own trip). Never a fallback."""
        cookies = self.clear_cookies() if clear else []
        auto = not self.marked(headers)
        if not is_page:
            return Result(None, 401, SIGNED_OUT, cookies, signin=self.signin_location(path, auto=auto))
        if guard_set:                   # the last trip to the helper didn't stick: a page with a link, not a loop;
            where = self._trip(path, False, cookies)                        # the link is the page, not another
            return Result(None, 401, SIGNED_OUT, cookies, None, signin=where)   # automatic round trip
        where = self._trip(path, auto, cookies)
        cookies.append(self.host_cookie(self.try_cookie, "1", LOOP_WINDOW))
        return Result(None, 401, SIGNED_OUT, cookies, where, signin=where)

    # -- the way back from the helper (v0.22)

    def _local(self, url):
        """`url` when it is this room's own address (its public URL or below), else the room's front page."""
        if isinstance(url, str) and (url == self.public_url or url.startswith(self.public_url + "/")) \
                and not any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in url) and len(url) <= MAX_RETURN:
            return url
        return self.public_url + "/"

    def _callback(self, headers, path):
        """GET /machiya/callback?code=mhc_…: trade the code at the helper (POST /v1/redeem, with this room's origin
        and the nonce from this browser's state cookie) for a room session, set it as this room's cookie and go to the
        page the trip started from. A code that isn't good (used, expired, another room's, another browser's) shows
        the sign-in page with a link, never a loop; the helper unreachable is a 503 page."""
        try:
            code = (parse_qs(urlsplit(path).query, max_num_fields=4).get("code") or [""])[0]
        except ValueError:
            code = ""
        nonce = next((v for v in self.cookie_values(headers.get("Cookie"), self.state_cookie)[:4]
                      if NONCE_RE.match(v)), "")
        done = [self.host_cookie(self.state_cookie, "", 0), self.host_cookie(self.try_cookie, "", 0)]
        status, data = None, None
        if CODE_RE.match(code) and nonce:
            try:
                status, body = self.fetch("POST", "/v1/redeem", {
                    "X-Machiya-Code": code, "X-Machiya-Room": self.origin, "X-Machiya-State": nonce,
                    "Accept": "application/json", "Content-Length": "0"}, REDEEM_TIMEOUT)
                data = json.loads(body) if body else None
            except (OSError, http.client.HTTPException, ValueError):
                data = None                         # no answer (status None), or one that isn't JSON (kept)
        else:
            status = 400
        if status == 200 and isinstance(data, dict) and isinstance(data.get("session"), str) \
                and RSID_RE.match(data["session"]) and isinstance(data.get("username"), str) and data["username"]:
            try:
                max_age = min(max(int(data.get("max_age") or ROOM_MAX_AGE), 60), ROOM_MAX_AGE)
            except (TypeError, ValueError):
                max_age = ROOM_MAX_AGE
            session = data["session"]
            self._store(self._key("room", session), ("ok", data["username"], data.get("user_id"),
                                                     vprefs.shared_only(data.get("prefs"))), TTL_OK)
            self._set_health(True)
            cookies = [self.host_cookie(self.room_cookie, session, max_age)] + done \
                + [self.host_cookie(self.out_cookie, "", 0)]
            return Result(None, 302, CALLBACK, cookies, location=self._local(data.get("return")),
                          actor="callback:" + data["username"])
        if status in (400, 401, 403, 404, 409, 410) or (status == 200):
            cookies = done[1:]                          # the guard cleared; a fresh nonce (not a cleared one) behind
            where = self._trip("/", False, cookies)     # the link
            return Result(None, 401, SIGNED_OUT, cookies, None, signin=where, actor="callback:refused")
        self._set_health(False)
        return Result(None, 503, UNAVAILABLE, done, signin=self.signin_location("/", auto=False),
                      actor="callback:unavailable")

    def _unavailable(self, headers, reason):
        if self.fallback == "tailscale":
            return self._fallback(headers, banner=True)
        return Result(None, 503, reason, signin=self.signin_location("/"))

    def _fallback(self, headers, banner=True):
        """The Tailscale identity (Tailscale Serve sets the header and strips a client's own copy): a login in
        <P>_USERS is the owner. Nothing is cached."""
        values = Identity.header_values(headers, "Tailscale-User-Login")
        login = values[0].strip() if len(values) == 1 and isinstance(values[0], str) else ""
        if not login or any(ord(c) < 32 or ord(c) == 127 for c in login):
            return Result(None, 503, UNAVAILABLE, signin=self.signin_location("/"))
        if login.lower() not in self.fallback_users:
            return Result(None, 403, NOT_ALLOWED, actor="fallback:" + login)
        with self.lock:
            self.fallback_total += 1
        p = Principal(login, "person", owner=True, via="fallback" if banner else "tailscale",
                      uid=tailscale_uid(login), tailscale=(login,))
        return Result(p, 200, FALLBACK if banner else OK, banner=banner,
                      actor=("fallback:" if banner else "tailscale:") + login)

    # -- sign-out

    def signout(self, headers):
        """A room's POST /signout (the room checks it is same-origin first): the helper ends the Hister session, the
        browser's helper session and every room session made from it (so every room follows within its cache), and
        remembers that this browser signed out on purpose; this drops the cached answer, clears the room's cookie and
        sets its own marker (the next trip from here shows the helper's page). -> (ended, cookies): `ended` False when
        the helper couldn't be reached (the room still clears its cookie; other rooms follow when sign-in is back)."""
        kind, value, _ = self.credential(headers)
        cookies = self.clear_cookies() + [self.host_cookie(self.try_cookie, "", 0),
                                          self.host_cookie(self.state_cookie, "", 0),
                                          self.host_cookie(self.out_cookie, "1", OUT_MAX_AGE)]  # no automatic way back
        if kind not in ("sid", "room"):
            return True, cookies
        self.forget(kind, value)
        if self.standalone:
            return False, cookies
        try:
            status, _ = self.fetch("POST", "/v1/signout", dict(self._cred_headers(kind, value), **{
                "Content-Length": "0"}), SIGNOUT_TIMEOUT)
        except (OSError, http.client.HTTPException):
            status = None
        self._store(self._key(kind, value), ("out",), TTL_OUT)
        return status in (200, 204), cookies

    # -- preferences (docs/contracts/prefs.md)

    def prefs_state(self, result):
        """The Shared section's state for this request (shell.shared_section): "account" when the caller's settings
        are the account's, "unavailable" in the fallback (no account while sign-in is down), "standalone" when this
        room runs without the helper (its own store)."""
        if self.standalone:
            return "standalone"
        if result is None or result.principal is None or result.reason == FALLBACK:
            return "unavailable"
        return "account"

    def forward_prefs(self, result, method, headers, body=b"", origins=(), secure=None):
        """A room's GET/PUT /api/prefs in this mode, after its gate admitted `result`: the account's preferences, from
        the helper's /v1/prefs, asked with the caller's own credential. -> (status, [(header, value)], body), or None
        when this room has no helper (the Tailscale identity only): then it keeps its own store (signin.handle_prefs).
          - the fallback (sign-in unavailable): 503, nothing kept here; machiya.js keeps the change and sends it later;
          - a PUT carried by the sign-in cookie must be same-origin (`origins`: the room's public address), else 403;
            one with Bearer or X-Access-Token is a client's, as for every API;
          - If-None-Match goes along (304), the answer comes back as it is (the contract's JSON, ETag "<rev>");
          - the helper unreachable or failing: 503 {"error": "preferences unavailable"}; signed out there: 401 with
            the sign-in address. The body never reaches a log."""
        from . import signin as vsignin             # signin imports shell; histerauth stays importable on its own
        if self.standalone:
            return None
        if result is None or result.principal is None:
            return vsignin._json(401, {"error": "sign in", "signin": self.signin_location("/")})
        if result.reason == FALLBACK or getattr(result.principal, "via", "") in ("fallback", "tailscale"):
            return vsignin._json(503, {"error": "preferences are unavailable while sign-in is down",
                                       "reason": "fallback"})
        if method not in ("GET", "PUT"):
            return vsignin._json(405, {"error": "GET or PUT"}, [("Allow", "GET, PUT")])
        kind, value, from_cookie = self.credential(headers)
        if kind not in ("sid", "room", "rtoken", "token"):
            return vsignin._json(401, {"error": "sign in", "signin": self.signin_location("/")})
        out = dict(self._cred_headers(kind, value), Accept="application/json")
        data = None
        if method == "PUT":
            if from_cookie and not vsignin.same_origin(headers, self.secure if secure is None else secure, origins):
                return vsignin._json(403, {"error": "cross-site write refused"})
            if body is None or len(body) > vprefs.MAX_BODY:
                return vsignin._json(413, {"error": "request body too large"})
            if vsignin._media(headers) != "application/json":
                return vsignin._json(415, {"error": "send JSON"})
            out["Content-Type"] = "application/json"
            out["Content-Length"] = str(len(body))
            data = body
        else:
            tags = Identity.header_values(headers, "If-None-Match")
            if len(tags) == 1 and isinstance(tags[0], str) and re.fullmatch(r'(W/)?"[0-9]{1,18}"', tags[0].strip()):
                out["If-None-Match"] = tags[0].strip()
        try:
            status, answer = self.fetch(method, "/v1/prefs", out, PREFS_TIMEOUT, data) if data is not None \
                else self.fetch(method, "/v1/prefs", out, PREFS_TIMEOUT)
        except (OSError, http.client.HTTPException):
            status, answer = None, b""
        if status == 304:
            return 304, [("Cache-Control", "no-store"), ("ETag", out["If-None-Match"])], b""
        try:
            parsed = json.loads(answer) if answer else None
        except ValueError:
            parsed = None
        if status == 200 and isinstance(parsed, dict) and isinstance(parsed.get("prefs"), dict) \
                and isinstance(parsed.get("rev"), int):
            if method == "PUT":                     # the first render's copy follows at once on this room
                self._new_prefs(kind, value, parsed["prefs"])
            return vsignin.prefs_answer(200, parsed["rev"], parsed["prefs"], parsed.get("updated") or {})
        if status in (400, 413, 415) and isinstance(parsed, dict):
            return vsignin._json(status, {"error": str(parsed.get("error") or "refused")[:300]})
        if status in (401, 403):
            self.forget(kind, value)
            return vsignin._json(401, {"error": "sign in", "signin": self.signin_location("/")})
        return vsignin._json(503, {"error": "preferences unavailable"})

    def _new_prefs(self, kind, value, prefs):
        key = self._key(kind, value)
        with self.lock:
            hit = self.cache.get(key)
            if hit and hit[1][0] == "ok":
                self.cache[key] = (hit[0], hit[1][:3] + (vprefs.shared_only(prefs),))

    # -- default answers (a room may use its own pages instead)

    def respond(self, result, is_page=True, ctx=None):
        """(status, [(header, value)], body) for a refused `result`: a 302 to the helper, or a short page in the
        shared shell (401 with a "Sign In" link, 403, 503), or JSON for an API call."""
        from . import shell, websafe
        headers = [("Cache-Control", "no-store")] + websafe.base_headers()          # v0.22: on every answer
        headers += [("Set-Cookie", websafe.header_value(c)) for c in result.cookies]
        if result.reason == CALLBACK and result.location:      # the way back from the helper: always a redirect,
            return 302, [h for h in headers if h[0] != "Referrer-Policy"] + [      # and the code's address goes
                ("Location", websafe.header_value(result.location)), ("Referrer-Policy", "no-referrer")], b""  # nowhere
        if not is_page:
            return result.status, headers + [("Content-Type", "application/json")], json.dumps(result.json()).encode()
        if result.location:
            return 302, headers + [("Location", websafe.header_value(result.location))], b""
        headers = [h for h in headers if h[0] not in ("X-Frame-Options", "Referrer-Policy", "X-Content-Type-Options")] \
            + shell.security_headers()
        _, name, _, _ = shell.room_info(self.room)
        if result.status == 401:
            body = shell.message("Sign In", "%s is private. Sign in to continue." % name,
                                 [(result.signin, "Sign In")])
        elif result.status == 403:
            body = shell.message("No Access", "This account can't open %s." % name)
        else:
            body = shell.message("Sign-In Is Unavailable", "%s needs sign-in, and it can't be reached right now. "
                                 "Try again in a minute." % name, [("/", "Try Again")])
        html = shell.page(ctx or shell.Prefs(), self.room, "%s · %s" % (name, "Sign In"),
                          shell.header(self.room, [], "", {}, settings=False) + body, links={}, manifest=False)
        return result.status, headers + [("Content-Type", "text/html; charset=utf-8")], html.encode()


def banner_html(text=BANNER_TEXT):
    """The fallback banner (machiya.css .machiya-banner): put it at the top of <main> when result.banner is set."""
    import html
    return '<div class="machiya-banner" role="status">%s</div>' % html.escape(text)


def signin_meta(signout_path="/signout"):
    """<meta name="machiya-signin" content="/signout"> for page(head=…): turns on machiya.js's Hister sign-in
    behaviour (8.): a fetch answered 401 with a "signin" address takes the page there, and the Rooms menus gain a
    Sign Out row posting to `signout_path` (a local path; anything else gives "")."""
    import html
    if not isinstance(signout_path, str) or not signout_path.startswith("/") or signout_path[1:2] in ("/", "\\") \
            or any(c.isspace() or ord(c) < 32 for c in signout_path):
        return ""
    return '<meta name="machiya-signin" content="%s">\n' % html.escape(signout_path)


def _flag(env, name):
    return (env.get(name) or "").strip().lower() in ("1", "on", "true", "yes")


def _list(raw):
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def load_for(room, env=None, bind="0.0.0.0", secure=True, fetch=None):
    """The room's HisterAuth from <P>_AUTH=hister and its settings (docs: Machiya hister-login §4.1), or None when the
    room isn't in hister mode. <P> is the room's prefix (KURA, NIWA, KANBAN for konbini).

        <P>_AUTH_URL          the helper's internal address (http://hister-login:8081)
        <P>_AUTH_SIGNIN_URL   the helper's public sign-in (https://hister.example.ts.net/machiya/signin); required
        <P>_HISTER_USERS      the Hister usernames admitted, comma-separated; required; never *
        <P>_AUTH_FALLBACK     tailscale (default) or none
        <P>_USERS             (konbini: KANBAN_TAILNET_USERS) the Tailscale logins admitted in the fallback; never *
        <P>_PUBLIC_URL        (konbini: KANBAN_BOARD_URL) this room's address, for return=; required
        MACHIYA_COOKIE_DOMAIN the shared cookie's domain, for clearing machiya_sso
        MACHIYA_SSO_COOKIE    the sign-in cookie's name (default machiya_sso; the helper must use the same)
        MACHIYA_SIGNIN_PROVIDER  sign in automatically through that provider of the helper's (`oidc`: tsidp);
                              empty (the default): the helper's page
        <P>_AUTH_ACCEPT_ORIGINS  (v0.22, optional) other origins whose room sessions this room accepts, comma-
                              separated scheme://host[:port]: Shiori's hosted pages, whose nginx passes their own
                              room cookie on to Kura and Konbini

    IdentityError (the room must not start) for: no sign-in address, no usernames, a *, an unknown fallback, no
    helper address with fallback none, no public URL, or an identity file at the same time (not combined yet). No
    helper address with the tailscale fallback runs, with a warning, on the Tailscale identity only. With the
    tailscale fallback, check_bind applies as in tailscale mode (<P>_BIND_BEHIND_PROXY)."""
    env = os.environ if env is None else env
    prefix = {"konbini": "KANBAN"}.get(room, room.upper())
    if (env.get(prefix + "_AUTH") or "").strip().lower() != "hister":
        return None
    if (env.get("MACHIYA_IDENTITY_FILE") or "").strip():
        raise IdentityError("%s_AUTH=hister with an identity file isn't supported yet: unset one of them" % prefix)
    users_env = "KANBAN_TAILNET_USERS" if room == "konbini" else prefix + "_USERS"
    url_env = "KANBAN_BOARD_URL" if room == "konbini" else prefix + "_PUBLIC_URL"
    fallback = (env.get(prefix + "_AUTH_FALLBACK") or "tailscale").strip().lower()
    signin = (env.get(prefix + "_AUTH_SIGNIN_URL") or "").strip()
    users = _list(env.get(prefix + "_HISTER_USERS"))
    fallback_users = _list(env.get(users_env))
    auth_url = (env.get(prefix + "_AUTH_URL") or "").strip().rstrip("/")
    public_url = (env.get(url_env) or "").strip()
    if not signin:
        raise IdentityError("%s_AUTH=hister needs %s_AUTH_SIGNIN_URL (the helper's sign-in page)" % (prefix, prefix))
    if not signin.startswith(("https://", "http://")):
        raise IdentityError("%s_AUTH_SIGNIN_URL must be an http(s) address" % prefix)
    if not users:
        raise IdentityError("%s_AUTH=hister needs %s_HISTER_USERS (the owner's Hister username)" % (prefix, prefix))
    if "*" in users:
        raise IdentityError("%s_HISTER_USERS: '*' is refused (OAuth creates accounts on its own)" % prefix)
    if fallback not in ("tailscale", "none"):
        raise IdentityError("%s_AUTH_FALLBACK must be tailscale or none, not %r" % (prefix, fallback))
    if "*" in fallback_users:
        raise IdentityError("%s: '*' is refused in hister mode: name the owner's Tailscale login" % users_env)
    if fallback == "none" and not auth_url:
        raise IdentityError("%s_AUTH=hister with %s_AUTH_FALLBACK=none needs %s_AUTH_URL" % (prefix, prefix, prefix))
    if not public_url.startswith(("https://", "http://")):
        raise IdentityError("%s_AUTH=hister needs %s (this room's address, for the way back)" % (prefix, url_env))
    if fallback == "tailscale":
        check_bind("tailscale", bind, _flag(env, prefix + "_BIND_BEHIND_PROXY"))
    if not auth_url:
        print("%s: hister mode, but no sign-in service: Tailscale identity only" % room, file=sys.stderr, flush=True)
    return HisterAuth(room, signin, users, public_url, auth_url, fallback, fallback_users,
                      (env.get("MACHIYA_COOKIE_DOMAIN") or "").strip().lstrip("."), secure, fetch,
                      sso_cookie=sso_cookie_name(env),
                      provider=(env.get("MACHIYA_SIGNIN_PROVIDER") or "").strip().lower(),
                      accept_origins=_list(env.get(prefix + "_AUTH_ACCEPT_ORIGINS")))
