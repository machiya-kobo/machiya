"""One identity for every room (docs/plans/identity.md): who is calling, and what they may do.

The identity file is TOML, read-only to the rooms (MACHIYA_IDENTITY_FILE), edited on its host with
`python3 -m vaultkit.identity`. It names principals (people, agents, services) and their grants; it holds no secret in
clear: passwords are scrypt hashes, stored tokens SHA-256 hashes, and the key that signs sessions and device tokens
lives in its own file (session_key_file).

A room asks `Identity.resolve(headers)` for the principal, then `principal.can(room, action)`. The proofs, in order;
the first one present decides, and a present but invalid one is refused (401), never passed over for the next:

1. `Authorization: Bearer mch_<id>_<secret>` (a stored token) or `Bearer mcd_<payload>.<sig>` (a paired device).
2. `auth="tailscale"`: Tailscale-User-Login, or for a tagged node the forwarded app capability (CAPABILITY) naming a
   principal's `tailscale_tag`. Tailscale Serve strips both headers from what a client sends.
3. `auth="header"`: a trusted proxy's login header (Remote-User and the like).
4. The `machiya_session` cookie from the built-in sign-in (`signin=True`). An invalid one is cleared, not refused.
5. `auth="open"`: no proof needed: the owner, as before this module (localhost or a trusted LAN only; the room keeps
   its Host allow-list). A token still names its holder, so an agent with one is that agent even here.

Header modes trust whoever reaches the port, so `check_bind` refuses them on a non-loopback address unless the room
says a proxy is the only way in. Without an identity file a room keeps its old *_USERS behaviour (`load_for` -> None).
"""
import base64
import binascii
import datetime
import email.header
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time

try:
    import tomllib
except ImportError:                     # Python < 3.11: the rooms' images are 3.13; the CLI says what's missing
    tomllib = None

VERSION = 1
CAPABILITY = "github.com/machiya-kobo/cap/identity"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*$")
TOKEN_ID_RE = re.compile(r"[a-z0-9]{4,8}$")
KINDS = ("person", "agent", "service")
# The rooms and what may be granted in each (the plan's table). Anything else in a file refuses to load.
ACTIONS = {
    "kura": {"read"},
    "niwa": {"read", "suggest", "publish"},
    "konbini": {"read", "write", "areas"},
    "mcp": {"use"},
    "smallweb": {"read", "save"},
}
DEFAULT_VAULTS = ("default", "shared")  # what a non-owner may read in Kura when its grant names no vaults
SESSION_COOKIE = "machiya_session"
RENEW_AFTER = 86400                     # a session cookie older than a day is re-issued on use
HARD_CAP_DAYS = 180                     # ... but never past this many days from the sign-in
SCRYPT_N, SCRYPT_R, SCRYPT_P = 1 << 14, 8, 1
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L
PAIR_LEN = 8


class IdentityError(ValueError):
    """The identity file (or a setting) can't be used: the room must not start, or must not serve."""


# -- small helpers -------------------------------------------------------------------------------------------------

def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text):
    text = text.encode() if isinstance(text, str) else text
    return base64.urlsafe_b64decode(text + b"=" * (-len(text) % 4))


def now():
    return int(time.time())


def utc(value):
    """A TOML date or datetime -> aware datetime (a date means the end of that day, UTC)."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time(23, 59, 59), datetime.timezone.utc)
    raise IdentityError("expected a date, not %r" % (value,))


def hash_password(password, salt=None):
    """-> 'scrypt$N$r$p$salt$hash' (base64url)."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, b64e(salt), b64e(digest))


def check_password(password, stored):
    """Constant-time check of `password` against hash_password's output. False on any malformed hash."""
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        if kind != "scrypt" or n > 1 << 20 or r > 32 or p > 16:
            return False
        got = hashlib.scrypt(password.encode(), salt=b64d(salt), n=n, r=r, p=p, dklen=len(b64d(digest)),
                             maxmem=256 * 1024 * 1024)
        return hmac.compare_digest(got, b64d(digest))
    except (ValueError, TypeError, binascii.Error, AttributeError):
        return False


DUMMY_HASH = hash_password("not a password", b"\0" * 16)     # spends the same time for an unknown name


def token_hash(secret):
    return "sha256:" + hashlib.sha256(secret.encode()).hexdigest()


def sign(key, purpose, payload):
    """payload (dict) -> '<b64 json>.<b64 hmac>'; `purpose` keeps a session from passing as a device token."""
    body = b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(key, purpose.encode() + b"\0" + body.encode(), hashlib.sha256).digest()
    return body + "." + b64e(mac)


def unsign(key, purpose, value):
    """The payload of sign()'s output, or None if it isn't one (or was signed for another purpose)."""
    try:
        body, mac = value.split(".")
        want = hmac.new(key, purpose.encode() + b"\0" + body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, b64d(mac)):
            return None
        payload = json.loads(b64d(body))
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError, binascii.Error):
        return None


def capability_values(header, name=CAPABILITY):
    """Tailscale-App-Capabilities (JSON, maybe RFC 2047 Q-encoded) -> the list of values granted under `name`."""
    if not header:
        return []
    text = header.strip()
    if text.startswith("=?"):
        try:
            text = "".join(part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                           for part, enc in email.header.decode_header(text))
        except (ValueError, LookupError, UnicodeDecodeError):
            raise IdentityError("unreadable Tailscale-App-Capabilities")
    try:
        caps = json.loads(text)
    except ValueError:
        raise IdentityError("unreadable Tailscale-App-Capabilities")
    values = caps.get(name) if isinstance(caps, dict) else None
    return values if isinstance(values, list) else []


def is_loopback(bind):
    if bind in ("localhost", ""):
        return bind == "localhost"
    try:
        return ipaddress.ip_address(bind.strip("[]")).is_loopback
    except ValueError:
        return False


def check_bind(auth, bind, behind_proxy=False):
    """A header mode trusts whoever reaches the port: refuse it on a non-loopback bind unless a proxy is the only way
    in (the Docker sidecar, where the room's port is only on the sidecar's network)."""
    if auth in ("tailscale", "header") and not behind_proxy and not is_loopback(bind):
        raise IdentityError("auth=%s trusts a login header, so the room must listen on 127.0.0.1 behind the proxy "
                            "(or set <ROOM>_BIND_BEHIND_PROXY=1 when the proxy is the only way in), not on %r"
                            % (auth, bind))


# -- the file --------------------------------------------------------------------------------------------------------

class Principal:
    """Who is calling: a name, a kind, and what they may do. `via` says how it was proven (for logs only)."""

    def __init__(self, name, kind, owner=False, grants=None, limits=None, via=""):
        self.name, self.kind, self.owner = name, kind, owner
        self.grants = grants or {}      # {room: {"actions": set, "vaults": tuple|None}}
        self.limits = dict(limits or {})
        self.via = via

    def can(self, room, action):
        if self.owner:
            return True
        grant = self.grants.get(room)
        return bool(grant) and action in grant["actions"]

    def vaults(self, room="kura"):
        """Kura's vault scope: "*" for the owner, else the vault names (and "default"/"shared") it may read."""
        if self.owner:
            return "*"
        grant = self.grants.get(room)
        if not grant or "read" not in grant["actions"]:
            return ()
        return grant["vaults"] if grant["vaults"] is not None else DEFAULT_VAULTS

    def with_via(self, via):
        return Principal(self.name, self.kind, self.owner, self.grants, self.limits, via)

    def __repr__(self):
        return "Principal(%s, %s%s)" % (self.name, self.kind, ", owner" if self.owner else "")


def parse_grants(where, raw):
    """{room: ["read", ...]} or {room: {read = true, vaults = [...]}} -> {room: {"actions": set, "vaults": ...}}."""
    if not isinstance(raw, dict):
        raise IdentityError("%s: grants must be a table" % where)
    out = {}
    for room, spec in raw.items():
        if room not in ACTIONS:
            raise IdentityError("%s: unknown room %r in grants" % (where, room))
        vaults = None
        if isinstance(spec, list):
            actions = spec
        elif isinstance(spec, dict):
            actions = [k for k, v in spec.items() if k != "vaults" and v is True]
            bad = [k for k, v in spec.items() if k != "vaults" and v is not True and v is not False]
            if bad:
                raise IdentityError("%s: grants.%s.%s must be true or false" % (where, room, bad[0]))
            unknown = [k for k in spec if k != "vaults" and k not in ACTIONS[room]]
            if unknown:
                raise IdentityError("%s: %r is not a %s grant" % (where, unknown[0], room))
            if "vaults" in spec:
                if room != "kura":
                    raise IdentityError("%s: vaults are a kura grant" % where)
                vaults = spec["vaults"]
                if not isinstance(vaults, list) or not all(isinstance(v, str) and NAME_RE.match(v) for v in vaults):
                    raise IdentityError("%s: grants.kura.vaults must be a list of vault names" % where)
                vaults = tuple(vaults)
        else:
            raise IdentityError("%s: grants.%s must be a list or a table" % (where, room))
        for a in actions:
            if not isinstance(a, str) or a not in ACTIONS[room]:
                raise IdentityError("%s: %r is not a %s grant (%s)"
                                    % (where, a, room, ", ".join(sorted(ACTIONS[room]))))
        out[room] = {"actions": frozenset(actions), "vaults": vaults}
    return out


class Config:
    """The parsed identity file: principals and the lookups `resolve` needs. Built only from a valid file."""

    KNOWN_TOP = {"version", "session_key_file", "session_days", "tailscale_capability", "principals", "pairing"}
    KNOWN = {"kind", "owner", "tailscale", "tailscale_tag", "proxy", "password", "session_epoch", "grants", "limits",
             "tokens", "revoked_devices"}

    def __init__(self, data, base_dir="."):
        self.data = data
        if data.get("version") != VERSION:
            raise IdentityError("identity file: version must be %d" % VERSION)
        unknown = set(data) - self.KNOWN_TOP
        if unknown:
            raise IdentityError("identity file: unknown setting %r" % sorted(unknown)[0])
        key_file = data.get("session_key_file")
        if not isinstance(key_file, str) or not key_file:
            raise IdentityError("identity file: session_key_file is required")
        self.key_file = key_file if os.path.isabs(key_file) else os.path.join(base_dir, key_file)
        self.session_days = data.get("session_days", 30)
        if not isinstance(self.session_days, int) or not 1 <= self.session_days <= HARD_CAP_DAYS:
            raise IdentityError("identity file: session_days must be 1..%d" % HARD_CAP_DAYS)
        self.capability = data.get("tailscale_capability", CAPABILITY)
        if not isinstance(self.capability, str) or "/" not in self.capability:
            raise IdentityError("identity file: tailscale_capability must look like domain/path")
        self.principals, self.by_login, self.by_tag, self.by_proxy, self.tokens = {}, {}, {}, {}, {}
        self.raw = {}
        for name, p in (data.get("principals") or {}).items():
            self.add(name, p)
        self.pairing = []
        for i, entry in enumerate(data.get("pairing") or []):
            self.pairing.append(self.pair_entry(i, entry))

    def add(self, name, p):
        where = "principal %r" % name
        if not NAME_RE.match(name):
            raise IdentityError("%s: a name is lowercase letters, digits and -" % where)
        if not isinstance(p, dict):
            raise IdentityError("%s must be a table" % where)
        unknown = set(p) - self.KNOWN
        if unknown:
            raise IdentityError("%s: unknown setting %r" % (where, sorted(unknown)[0]))
        kind = p.get("kind")
        if kind not in KINDS:
            raise IdentityError("%s: kind must be one of %s" % (where, ", ".join(KINDS)))
        owner = p.get("owner", False)
        if owner not in (True, False) or (owner and kind != "person"):
            raise IdentityError("%s: owner = true is for a person" % where)
        if "password" in p and kind != "person":
            raise IdentityError("%s: only a person signs in with a password" % where)
        if "password" in p and not (isinstance(p["password"], str) and p["password"].startswith("scrypt$")):
            raise IdentityError("%s: password must be a scrypt hash (use the CLI's passwd)" % where)
        epoch = p.get("session_epoch", 1)
        if not isinstance(epoch, int) or epoch < 1:
            raise IdentityError("%s: session_epoch must be a whole number from 1" % where)
        grants = parse_grants(where, p.get("grants", {}))
        limits = p.get("limits", {})
        if not isinstance(limits, dict) or not all(isinstance(v, int) and v >= 0 for v in limits.values()):
            raise IdentityError("%s: limits must be whole numbers" % where)
        for field, index in (("tailscale", self.by_login), ("proxy", self.by_proxy)):
            values = p.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                raise IdentityError("%s: %s must be a list of logins" % (where, field))
            for v in values:
                key = v.strip().lower()
                if key in index:
                    raise IdentityError("%s: %s login %r also belongs to %r" % (where, field, v, index[key]))
                index[key] = name
        tag = p.get("tailscale_tag")
        if tag is not None:
            if not isinstance(tag, str) or not NAME_RE.match(tag):
                raise IdentityError("%s: tailscale_tag is lowercase letters, digits and -" % where)
            if tag in self.by_tag:
                raise IdentityError("%s: tailscale_tag %r also belongs to %r" % (where, tag, self.by_tag[tag]))
            self.by_tag[tag] = name
        for t in p.get("tokens", []):
            if not isinstance(t, dict) or not TOKEN_ID_RE.match(str(t.get("id", ""))):
                raise IdentityError("%s: a token needs an id of 4-8 lowercase letters and digits" % where)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(t.get("hash", ""))):
                raise IdentityError("%s: token %s needs a sha256 hash (use the CLI's token mint)" % (where, t["id"]))
            if t["id"] in self.tokens:
                raise IdentityError("%s: token id %s is used twice" % (where, t["id"]))
            expires = utc(t["expires"]) if "expires" in t else None
            self.tokens[t["id"]] = (name, t["hash"], expires, str(t.get("label", "")))
        revoked = p.get("revoked_devices", [])
        if not isinstance(revoked, list) or not all(isinstance(d, str) for d in revoked):
            raise IdentityError("%s: revoked_devices must be a list of device ids" % where)
        self.principals[name] = Principal(name, kind, owner, grants, limits)
        self.raw[name] = {"password": p.get("password"), "epoch": epoch, "revoked": frozenset(revoked)}

    def pair_entry(self, i, e):
        where = "pairing entry %d" % (i + 1)
        if not isinstance(e, dict) or e.get("principal") not in self.principals:
            raise IdentityError("%s: names no principal in this file" % where)
        if self.principals[e["principal"]].kind != "person":
            raise IdentityError("%s: only a person pairs a device" % where)
        if not str(e.get("code", "")).startswith("scrypt$") or not TOKEN_ID_RE.match(str(e.get("device", ""))):
            raise IdentityError("%s: needs a code hash and a device id (use the CLI's pair)" % where)
        return {"principal": e["principal"], "code": e["code"], "device": e["device"],
                "label": str(e.get("label", "")), "expires": utc(e.get("expires"))}


def read_file(path):
    """-> (Config, key bytes). IdentityError for anything unusable: a room refuses to start rather than guess."""
    if tomllib is None:
        raise IdentityError("the identity file needs Python 3.11 or newer (tomllib)")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise IdentityError("identity file %s: %s" % (path, e.strerror or e))
    except tomllib.TOMLDecodeError as e:
        raise IdentityError("identity file %s: %s" % (path, e))
    config = Config(data, os.path.dirname(os.path.abspath(path)))
    try:
        with open(config.key_file, "rb") as f:
            key = f.read().strip()
    except OSError as e:
        raise IdentityError("session key %s: %s" % (config.key_file, e.strerror or e))
    if len(key) < 32:
        raise IdentityError("session key %s: too short (the CLI's init writes 32 random bytes)" % config.key_file)
    return config, key


# -- throttling ------------------------------------------------------------------------------------------------------

class Throttle:
    """At most `limit` failures per key in `window` seconds, in memory (per room process)."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits, self.lock = {}, threading.Lock()

    def blocked(self, key):
        with self.lock:
            cutoff = time.monotonic() - self.window
            hits = [t for t in self.hits.get(key, ()) if t > cutoff]
            if hits:
                self.hits[key] = hits
            else:
                self.hits.pop(key, None)
            return len(hits) >= self.limit

    def fail(self, key):
        with self.lock:
            self.hits.setdefault(key, []).append(time.monotonic())
            if len(self.hits) > 10000:              # a flood of addresses: forget the oldest windows
                cutoff = time.monotonic() - self.window
                self.hits = {k: v for k, v in self.hits.items() if v and v[-1] > cutoff}


# -- resolving a request ---------------------------------------------------------------------------------------------

OPEN_OWNER = Principal("local", "person", owner=True, via="open")    # auth=open: everyone, as before identities


class Result:
    """What `resolve` decided. `principal` is None when nobody proved who they are (`status` 401) or the proof names
    nobody in the file (`status` 403). `cookies` are Set-Cookie values the room must send (a renewed or cleared
    session). `error` is safe to show; it never contains a secret."""

    def __init__(self, principal=None, status=200, error="", cookies=()):
        self.principal, self.status, self.error, self.cookies = principal, status, error, list(cookies)

    def __bool__(self):
        return self.principal is not None


class Identity:
    """A room's view of the identity file. `room` names the room (its grants); `auth`: tailscale | header | open;
    `header`: the proxy's login header for auth=header; `signin`: the built-in sign-in is on; `secure`: cookies get
    Secure (the room is served over https); `cookie_domain`: MACHIYA_COOKIE_DOMAIN, so one sign-in covers every room."""

    def __init__(self, path, room, auth="tailscale", header="", signin=False, secure=True, cookie_domain=""):
        if room not in ACTIONS:
            raise IdentityError("unknown room %r" % room)
        if auth not in ("tailscale", "header", "open"):
            raise IdentityError("auth must be tailscale, header or open, not %r" % auth)
        if auth == "header" and not re.fullmatch(r"[A-Za-z0-9-]+", header or ""):
            raise IdentityError("auth=header needs the proxy's login header name")
        self.path, self.room, self.auth, self.header = path, room, auth, header
        self.signin, self.secure, self.cookie_domain = signin, secure, cookie_domain
        self.lock = threading.Lock()
        self.stamp, self.checked = None, 0.0
        self.config, self.key = read_file(path)
        self.stamp = self._stamp()
        self.signin_names = Throttle(5, 900)
        self.signin_addrs = Throttle(20, 900)
        self.pair_addrs = Throttle(5, 600)

    def _stamp(self):
        st = os.stat(self.path)
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def current(self):
        """The config, re-read when the file changed (checked at most once a second). A file that turned invalid
        keeps the last good one and says so on stderr: a typo never opens or locks the rooms mid-flight."""
        with self.lock:
            if time.monotonic() - self.checked >= 1:
                self.checked = time.monotonic()
                try:
                    stamp = self._stamp()
                    if stamp != self.stamp:
                        self.config, self.key = read_file(self.path)
                        self.stamp = stamp
                except (OSError, IdentityError) as e:
                    print("identity: keeping the last good identity file: %s" % e, file=sys.stderr, flush=True)
            return self.config, self.key

    # -- the proofs

    def resolve(self, headers, client=""):
        """headers: a mapping with .get (case-insensitive, like http.server's). client: the peer address, for logs."""
        config, key = self.current()
        auth = (headers.get("Authorization") or "").strip()
        if auth:
            return self._bearer(config, key, auth)
        if self.auth == "tailscale":
            login = (headers.get("Tailscale-User-Login") or "").strip()
            if login:
                name = config.by_login.get(login.lower())
                if not name:
                    return Result(None, 403, "this login has no access")
                return Result(config.principals[name].with_via("tailscale"))
            caps = headers.get("Tailscale-App-Capabilities")
            if caps:
                return self._capability(config, caps)
        elif self.auth == "header":
            login = (headers.get(self.header) or "").strip()
            if login:
                name = config.by_proxy.get(login.lower())
                if not name:
                    return Result(None, 403, "this login has no access")
                return Result(config.principals[name].with_via("proxy"))
        if self.signin:
            cookie = self.cookie_value(headers.get("Cookie"))
            if cookie:
                session = self._session(config, key, cookie)
                if session or self.auth != "open":
                    return session
        if self.auth == "open":
            return Result(OPEN_OWNER)
        return Result(None, 401, "sign in first" if self.signin else "no identity")

    def _bearer(self, config, key, value):
        scheme, _, token = value.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return Result(None, 401, "unreadable Authorization")
        if token.startswith("mch_"):
            tid, _, secret = token[4:].partition("_")
            entry = config.tokens.get(tid)
            # compare even for an unknown id, so a guess at ids doesn't time differently
            ok = hmac.compare_digest(token_hash(secret), entry[1] if entry else token_hash("x" + secret))
            if not (entry and ok and secret):
                return Result(None, 401, "unknown token")
            if entry[2] and entry[2] < datetime.datetime.now(datetime.timezone.utc):
                return Result(None, 401, "token expired")
            return Result(config.principals[entry[0]].with_via("token:" + tid))
        if token.startswith("mcd_"):
            payload = unsign(key, "device", token[4:])
            if not payload or payload.get("p") not in config.principals:
                return Result(None, 401, "unknown device token")
            raw = config.raw[payload["p"]]
            if payload.get("e") != raw["epoch"] or payload.get("d") in raw["revoked"]:
                return Result(None, 401, "device signed out")
            return Result(config.principals[payload["p"]].with_via("device:%s" % payload.get("d")))
        return Result(None, 401, "unknown token")

    def _capability(self, config, header):
        try:
            values = capability_values(header, config.capability)
        except IdentityError as e:
            return Result(None, 401, str(e))
        names = {v.get("principal") for v in values if isinstance(v, dict)}
        if len(names) != 1:
            return Result(None, 401 if not names else 403, "the tailnet grant names no single principal")
        name = config.by_tag.get(names.pop() or "")
        if not name:
            return Result(None, 403, "this tagged node has no access")
        return Result(config.principals[name].with_via("tailscale-tag"))

    def _session(self, config, key, cookie):
        payload = unsign(key, "session", cookie)
        clear = [self.cookie("", 0)]
        if not payload or payload.get("p") not in config.principals:
            return Result(None, 401, "sign in again", clear)
        t = now()
        if not isinstance(payload.get("exp"), int) or payload["exp"] <= t:
            return Result(None, 401, "session expired", clear)
        if payload.get("e") != config.raw[payload["p"]]["epoch"]:
            return Result(None, 401, "signed out", clear)
        cookies = []
        if t - int(payload.get("iat", 0)) > RENEW_AFTER:
            cookies = [self.issue(config, key, payload["p"], first=int(payload.get("auth", payload.get("iat", t))))]
        return Result(config.principals[payload["p"]].with_via("session"), 200, "", cookies)

    # -- sessions and the built-in sign-in

    @staticmethod
    def cookie_value(header):
        for part in (header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE:
                return v.strip()
        return ""

    def cookie(self, value, max_age):
        attrs = ["%s=%s" % (SESSION_COOKIE, value), "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
        if self.secure:
            attrs.append("Secure")
        if self.cookie_domain:
            attrs.append("Domain=" + self.cookie_domain)
        return "; ".join(attrs)

    def issue(self, config, key, name, first=None):
        """A Set-Cookie value for a session of `name`: 30 days (session_days) from now, never past the hard cap."""
        t = now()
        first = first or t
        exp = min(t + config.session_days * 86400, first + HARD_CAP_DAYS * 86400)
        payload = {"p": name, "e": config.raw[name]["epoch"], "iat": t, "auth": first, "exp": exp}
        return self.cookie(sign(key, "session", payload), max(0, exp - t))

    def sign_in(self, name, password, client=""):
        """The built-in sign-in: -> Result with the session cookie, or 401 (wrong name or password, the same answer
        and the same time) or 429 (too many failures for this name or this address). The throttle is a trade-off:
        five wrong passwords lock a name for 15 minutes for everyone (guessing costs more than a lockout here), and
        behind a proxy every client shares the proxy's address."""
        if not self.signin:
            return Result(None, 404, "sign-in is off in this room")
        config, key = self.current()
        name = (name or "").strip().lower()
        if self.signin_names.blocked(name) or self.signin_addrs.blocked(client):
            return Result(None, 429, "too many tries; wait a few minutes")
        p = config.principals.get(name)
        stored = config.raw[name]["password"] if p else None
        ok = check_password(password or "", stored or DUMMY_HASH) and bool(stored) and p.kind == "person"
        if not ok:
            self.signin_names.fail(name)
            self.signin_addrs.fail(client)
            return Result(None, 401, "wrong name or password")
        return Result(p.with_via("session"), 200, "", [self.issue(config, key, name)])

    def sign_out(self):
        return [self.cookie("", 0)]

    def pair(self, code, client=""):
        """A Shiori device trades a one-time code (the CLI's `pair`) for a device token. -> (Result, token or "")."""
        config, key = self.current()
        if self.pair_addrs.blocked(client):
            return Result(None, 429, "too many tries; wait a few minutes"), ""
        code = re.sub(r"[\s-]", "", code or "").upper()
        t = datetime.datetime.now(datetime.timezone.utc)
        for e in config.pairing:
            if e["expires"] > t and check_password(code, e["code"]):
                payload = {"p": e["principal"], "d": e["device"], "e": config.raw[e["principal"]]["epoch"],
                           "iat": now()}
                return Result(config.principals[e["principal"]].with_via("device:" + e["device"])), \
                    "mcd_" + sign(key, "device", payload)
        self.pair_addrs.fail(client)
        return Result(None, 401, "unknown or expired code"), ""


def load_for(room, env=None, bind="0.0.0.0", secure=True):
    """The room's Identity from MACHIYA_IDENTITY_FILE and <ROOM>_AUTH / _AUTH_HEADER / _SIGNIN / _BIND_BEHIND_PROXY,
    or None when no identity file is set (the room keeps its old *_USERS gate). IdentityError for a bad setup."""
    env = os.environ if env is None else env
    path = (env.get("MACHIYA_IDENTITY_FILE") or "").strip()
    if not path:
        return None
    prefix = {"konbini": "KANBAN"}.get(room, room.upper())
    auth = (env.get(prefix + "_AUTH") or "tailscale").strip().lower()
    signin = (env.get(prefix + "_SIGNIN") or "").strip().lower() in ("1", "on", "true", "yes")
    behind = (env.get(prefix + "_BIND_BEHIND_PROXY") or "").strip().lower() in ("1", "on", "true", "yes")
    check_bind(auth, bind, behind)
    return Identity(path, room, auth, (env.get(prefix + "_AUTH_HEADER") or "").strip(), signin, secure,
                    (env.get("MACHIYA_COOKIE_DOMAIN") or "").strip())


# -- writing the file (the CLI) --------------------------------------------------------------------------------------

def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, datetime.datetime):
        return utc(v).astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join("%s = %s" % (k, toml_value(x)) for k, x in v.items()) + " }"
    raise IdentityError("can't write %r to TOML" % (v,))


def dump(data):
    """The identity file's data -> TOML text, in a fixed order. Comments aren't kept (tomllib reads, never writes)."""
    out = ["# Machiya identity file (docs/plans/identity.md). Written by `python3 -m vaultkit.identity`;",
           "# hand edits are fine, but the CLI rewrites the file and doesn't keep comments.", ""]
    for k in ("version", "session_key_file", "session_days", "tailscale_capability"):
        if k in data:
            out.append("%s = %s" % (k, toml_value(data[k])))
    for name, p in (data.get("principals") or {}).items():
        out += ["", "[principals.%s]" % name]
        for k, v in p.items():
            if k != "tokens":
                out.append("%s = %s" % (k, toml_value(v)))
        for t in p.get("tokens", []):
            out += ["[[principals.%s.tokens]]" % name] + ["%s = %s" % (k, toml_value(v)) for k, v in t.items()]
    for e in data.get("pairing") or []:
        out += ["", "[[pairing]]"] + ["%s = %s" % (k, toml_value(v)) for k, v in e.items()]
    return "\n".join(out) + "\n"


def write_file(path, data):
    """Check, then replace the file atomically, keeping its mode and a .bak of the old one. The rooms see the new file
    within a second; mount its directory, not the file (a file mount keeps the old inode after a rename)."""
    text = dump(data)
    Config(tomllib.loads(text), os.path.dirname(os.path.abspath(path)))      # never write what a room would refuse
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600
    if os.path.exists(path):
        with open(path, "rb") as f, open(path + ".bak", "wb") as b:
            b.write(f.read())
        os.chmod(path + ".bak", mode)
    tmp = "%s.tmp%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def new_token(data, name, label="", days=None):
    """Add a stored token to principal `name` in `data`; -> the token (shown once)."""
    ids = {t["id"] for p in data["principals"].values() for t in p.get("tokens", [])}
    while True:
        tid = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(6))
        if tid not in ids:
            break
    secret = b64e(secrets.token_bytes(32))
    entry = {"id": tid, "hash": token_hash(secret), "label": label}
    if days:
        t = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        entry["expires"] = t + datetime.timedelta(days=days)
    data["principals"][name].setdefault("tokens", []).append(entry)
    return "mch_%s_%s" % (tid, secret)


def new_pairing(data, name, label="", minutes=10):
    """Add a pairing entry for principal `name`; -> (code shown once, device id)."""
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LEN))
    device = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(6))
    t = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    expires = t + datetime.timedelta(minutes=minutes)
    data.setdefault("pairing", []).append({"principal": name, "code": hash_password(code), "device": device,
                                           "label": label, "expires": expires})
    return code[:4] + "-" + code[4:], device


# -- the CLI ---------------------------------------------------------------------------------------------------------

USAGE = """python3 -m vaultkit.identity [--file PATH] COMMAND   (PATH defaults to $MACHIYA_IDENTITY_FILE)

  init                                   a new file and session key (0600) next to it
  check                                  validate the file and key; print the principals
  add NAME --kind person|agent|service [--owner]
  grant NAME ROOM ACTION...              e.g. grant mcp konbini read write; grant mcp kura read --vaults default shared
  passwd NAME                            set a person's password (asked twice)
  token mint NAME [--label L] [--days N] print a new token (once)
  token revoke ID
  pair NAME [--label L] [--minutes 10]   print a one-time code for a Shiori device
  pair --cancel DEVICE
  device revoke NAME DEVICE              sign one paired device out
  epoch bump NAME                        sign out every session and device of NAME
  tag NAME TAG                           the tailscale_tag of a tagged node's principal
  login NAME tailscale|proxy LOGIN       add a Tailscale or proxy login
"""


def main(argv=None):
    import getpass
    args = list(sys.argv[1:] if argv is None else argv)
    path = os.environ.get("MACHIYA_IDENTITY_FILE", "")
    if args[:1] == ["--file"] and len(args) > 1:
        path, args = args[1], args[2:]
    if not args or args[0] in ("-h", "--help") or not path:
        print(USAGE if path or args[:1] in (["-h"], ["--help"]) else "set --file or MACHIYA_IDENTITY_FILE\n\n" + USAGE)
        return 0 if args[:1] in (["-h"], ["--help"]) else 2

    def opt(flag, default=None, many=False):
        if flag not in args:
            return default
        i = args.index(flag)
        if many:
            vals = args[i + 1:]
            del args[i:]
            return vals
        if i + 1 >= len(args):
            raise SystemExit("%s needs a value" % flag)
        val = args[i + 1]
        del args[i:i + 2]
        return val

    cmd = args.pop(0)
    if cmd == "init":
        if os.path.exists(path):
            raise SystemExit("%s exists" % path)
        key = os.path.join(os.path.dirname(os.path.abspath(path)), "session.key")
        if not os.path.exists(key):
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(b64e(secrets.token_bytes(32)) + "\n")
        write_file(path, {"version": VERSION, "session_key_file": os.path.basename(key), "session_days": 30,
                          "tailscale_capability": CAPABILITY, "principals": {}})
        print("wrote %s and %s" % (path, key))
        return 0
    try:
        config, _ = read_file(path)
    except IdentityError as e:
        raise SystemExit("identity: %s" % e)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    data.setdefault("principals", {})

    def principal(name):
        if name not in data["principals"]:
            raise SystemExit("no principal %r" % name)
        return data["principals"][name]

    if cmd == "check":
        for name, p in config.principals.items():
            def shown(room, g):
                vaults = (" vaults=" + ",".join(g["vaults"])) if g["vaults"] else ""
                return "%s: %s%s" % (room, " ".join(sorted(g["actions"])), vaults)
            grants = "owner" if p.owner else ", ".join(shown(r, g) for r, g in sorted(p.grants.items())) or "nothing"
            print("%-16s %-8s %s" % (name, p.kind, grants))
        print("ok: %d principals, %d tokens, %d pairing codes" % (len(config.principals), len(config.tokens),
                                                                 len(config.pairing)))
        return 0
    if cmd == "add":
        kind, owner = opt("--kind"), "--owner" in args
        args[:] = [a for a in args if a != "--owner"]
        if len(args) != 1 or kind not in KINDS:
            raise SystemExit("add NAME --kind person|agent|service [--owner]")
        if args[0] in data["principals"]:
            raise SystemExit("%r exists" % args[0])
        data["principals"][args[0]] = {"kind": kind, **({"owner": True} if owner else {})}
    elif cmd == "grant":
        vaults = opt("--vaults", many=True)
        if len(args) < 3:
            raise SystemExit("grant NAME ROOM ACTION... [--vaults V...]")
        p = principal(args[0])
        room, actions = args[1], args[2:]
        g = p.setdefault("grants", {})
        current = g.get(room, [])
        if isinstance(current, dict):
            current = [k for k, v in current.items() if k != "vaults" and v is True]
        merged = sorted(set(current) | set(actions))
        g[room] = ({a: True for a in merged} | {"vaults": vaults}) if vaults is not None else merged
    elif cmd == "passwd":
        if len(args) != 1:
            raise SystemExit("passwd NAME")
        p = principal(args[0])
        pw = getpass.getpass("new password for %s: " % args[0])
        if len(pw) < 12:
            raise SystemExit("use at least 12 characters")
        if getpass.getpass("again: ") != pw:
            raise SystemExit("they differ")
        p["password"] = hash_password(pw)
    elif cmd == "token" and args[:1] == ["mint"]:
        label, days = opt("--label", ""), opt("--days")
        if len(args) != 2:
            raise SystemExit("token mint NAME [--label L] [--days N]")
        principal(args[1])
        token = new_token(data, args[1], label, int(days) if days else None)
        write_file(path, data)
        print(token)
        print("shown once; the file keeps only its hash", file=sys.stderr)
        return 0
    elif cmd == "token" and args[:1] == ["revoke"] and len(args) == 2:
        found = False
        for p in data["principals"].values():
            kept = [t for t in p.get("tokens", []) if t["id"] != args[1]]
            found |= len(kept) != len(p.get("tokens", []))
            if "tokens" in p:
                p["tokens"] = kept
        if not found:
            raise SystemExit("no token %s" % args[1])
    elif cmd == "pair":
        cancel = opt("--cancel")
        if cancel:
            before = len(data.get("pairing", []))
            data["pairing"] = [e for e in data.get("pairing", []) if e["device"] != cancel]
            if len(data["pairing"]) == before:
                raise SystemExit("no pairing for device %s" % cancel)
        else:
            label, minutes = opt("--label", ""), int(opt("--minutes", "10"))
            if len(args) != 1 or not 1 <= minutes <= 60:
                raise SystemExit("pair NAME [--label L] [--minutes 1..60]")
            principal(args[0])
            t = datetime.datetime.now(datetime.timezone.utc)
            data["pairing"] = [e for e in data.get("pairing", []) if utc(e["expires"]) > t]    # drop expired ones
            code, device = new_pairing(data, args[0], label, minutes)
            write_file(path, data)
            print(code)
            print("device %s; type the code in Shiori within %d minutes" % (device, minutes), file=sys.stderr)
            return 0
    elif cmd == "device" and args[:1] == ["revoke"] and len(args) == 3:
        p = principal(args[1])
        p["revoked_devices"] = sorted(set(p.get("revoked_devices", [])) | {args[2]})
    elif cmd == "epoch" and args[:1] == ["bump"] and len(args) == 2:
        p = principal(args[1])
        p["session_epoch"] = int(p.get("session_epoch", 1)) + 1
    elif cmd == "tag" and len(args) == 2:
        principal(args[0])["tailscale_tag"] = args[1]
    elif cmd == "login" and len(args) == 3 and args[1] in ("tailscale", "proxy"):
        p = principal(args[0])
        p[args[1]] = sorted(set(p.get(args[1], [])) | {args[2]})
    else:
        print(USAGE)
        return 2
    try:
        write_file(path, data)
    except IdentityError as e:
        raise SystemExit("identity: not written: %s" % e)
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
