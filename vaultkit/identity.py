"""One identity for every room (docs/identity.md): who is calling, and what they may do.

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
import stat
import sys
import threading
import time

try:
    import tomllib
except ImportError:                     # Python < 3.11: the rooms' images are 3.13; the CLI says what's missing
    tomllib = None

VERSION = 1
CAPABILITY = "github.com/machiya-kobo/cap/identity"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")          # \Z, not $: "name\n" must not pass
TOKEN_ID_RE = re.compile(r"[a-z0-9]{4,8}\Z")
UID_RE = re.compile(r"[a-z0-9]{8,32}\Z")
B64_RE = re.compile(r"[A-Za-z0-9_-]*\Z")
BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
MAX_NAME = 64                           # longer sign-in names can't exist; refused before any hashing
KINDS = ("person", "agent", "service")
# The rooms and what may be granted in each (the plan's table). Anything else in a file refuses to load.
ACTIONS = {
    "kura": {"read"},
    "niwa": {"read", "suggest", "publish"},
    "konbini": {"read", "write", "areas"},
    "mcp": {"use"},
    "smallweb": {"read", "save"},
    "landing": {"read"},                # the stack's front door and status page (stack/landing)
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
    """Strict unpadded base64url: anything b64e wouldn't have written (padding, +/, spaces, junk) is a ValueError,
    so a signature has exactly one spelling."""
    if isinstance(text, bytes):
        text = text.decode("ascii")
    if not isinstance(text, str) or not B64_RE.match(text) or len(text) % 4 == 1:
        raise ValueError("not base64url")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64e(raw) != text:
        raise ValueError("not canonical base64url")
    return raw


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
        if not valid_hash(stored):
            return False
        _, _, _, _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=b64d(salt), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
        return hmac.compare_digest(got, b64d(digest))
    except (ValueError, TypeError, binascii.Error, AttributeError, UnicodeError):
        return False


def valid_hash(stored):
    """hash_password's own format and parameters only: a hand-written hash can't make a room spend seconds or
    hundreds of MiB per check."""
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        return (kind, n, r, p) == ("scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P)) \
            and len(b64d(salt)) == 16 and len(b64d(digest)) == 32
    except (ValueError, TypeError, AttributeError, binascii.Error, UnicodeError):
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
    if not key or not isinstance(value, str):
        return None                     # no usable key (it changed or vanished): no signed proof is good
    try:
        body, mac = value.split(".")
        if not B64_RE.match(body):
            return None
        want = b64e(hmac.new(key, purpose.encode() + b"\0" + body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want.encode(), mac.encode("ascii")):
            return None
        payload = json.loads(b64d(body))
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        return None


def capability_values(header, name=CAPABILITY):
    """Tailscale-App-Capabilities (JSON, maybe RFC 2047 encoded) -> the list of values granted under `name`.
    IdentityError for anything unreadable, never another exception (hostile input from the network)."""
    if not header:
        return []
    try:
        text = header.strip()
        if text.startswith("=?"):
            text = "".join(part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                           for part, enc in email.header.decode_header(text))
        if len(text) > 16384:
            raise ValueError("too long")
        caps = json.loads(text)
    except Exception:                   # HeaderParseError, LookupError, UnicodeError, ValueError, RecursionError...
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

    def __init__(self, name, kind, owner=False, grants=None, limits=None, via="", uid="", tailscale=()):
        self.name, self.kind, self.owner = name, kind, owner
        self.uid = uid or name          # the file's id (a person's is random): what rooms key stored data on
        self.grants = grants or {}      # {room: {"actions": set, "vaults": tuple|None}}
        self.limits = dict(limits or {})
        self.via = via
        self.tailscale = tuple(tailscale)   # the file's Tailscale logins (finds preferences stored before the file)

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
        return Principal(self.name, self.kind, self.owner, self.grants, self.limits, via, self.uid, self.tailscale)

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
    KNOWN = {"id", "kind", "owner", "tailscale", "tailscale_tag", "proxy", "password", "session_epoch", "grants",
             "limits", "tokens", "revoked_devices"}
    KNOWN_TOKEN = {"id", "hash", "label", "expires"}
    KNOWN_PAIRING = {"principal", "code", "device", "label", "expires"}

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
        principals = data.get("principals", {})
        if not isinstance(principals, dict):
            raise IdentityError("identity file: principals must be a table of tables")
        for name, p in principals.items():
            self.add(name, p)
        uids = {}
        for name in self.principals:            # ids key stored data (preferences): never two principals on one
            uid = self.raw[name]["uid"]
            if uid in uids:
                raise IdentityError("principal %r: id %r is also %r's" % (name, uid, uids[uid]))
            uids[uid] = name
        pairing = data.get("pairing", [])
        if not isinstance(pairing, list):
            raise IdentityError("identity file: pairing must be an array of tables ([[pairing]])")
        self.pairing = [self.pair_entry(i, entry) for i, entry in enumerate(pairing)]

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
        if "password" in p and not valid_hash(p["password"]):
            raise IdentityError("%s: password must be the CLI's scrypt hash (use passwd)" % where)
        # Sessions and device tokens carry the id, so a person deleted and added again under the same name starts
        # afresh. Only people get those, so a person needs one; agents and services (stored tokens, tagged nodes) may
        # leave it out.
        uid = p.get("id", None if kind == "person" else name)
        if not isinstance(uid, str) or not (uid == name and kind != "person" or UID_RE.match(uid)):
            raise IdentityError("%s: id is 8-32 lowercase letters and digits, required for a person (the CLI's add "
                                "sets it)" % where)
        epoch = p.get("session_epoch", 1)
        if not isinstance(epoch, int) or epoch < 1:
            raise IdentityError("%s: session_epoch must be a whole number from 1" % where)
        grants = parse_grants(where, p.get("grants", {}))
        limits = p.get("limits", {})
        if not isinstance(limits, dict) or not all(
                BARE_KEY_RE.match(k) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
                for k, v in limits.items()):
            raise IdentityError("%s: limits are name = whole number" % where)
        for field, index in (("tailscale", self.by_login), ("proxy", self.by_proxy)):
            values = p.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                raise IdentityError("%s: %s must be a list of logins" % (where, field))
            for v in values:
                # Tailscale logins are case-insensitive; a proxy's are compared exactly (htpasswd users are not)
                key = v.strip().lower() if field == "tailscale" else v.strip()
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
        tokens = p.get("tokens", [])
        if not isinstance(tokens, list):
            raise IdentityError("%s: tokens must be an array of tables" % where)
        for t in tokens:
            if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not TOKEN_ID_RE.match(t["id"]):
                raise IdentityError("%s: a token needs an id of 4-8 lowercase letters and digits" % where)
            extra = sorted(set(t) - self.KNOWN_TOKEN)
            if extra:
                raise IdentityError("%s: token %s: unknown setting %r" % (where, t["id"], extra[0]))
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(t.get("hash", ""))):
                raise IdentityError("%s: token %s needs a sha256 hash (use the CLI's token mint)" % (where, t["id"]))
            if t["id"] in self.tokens:
                raise IdentityError("%s: token id %s is used twice" % (where, t["id"]))
            expires = utc(t["expires"]) if "expires" in t else None
            self.tokens[t["id"]] = (name, t["hash"], expires, str(t.get("label", "")))
        revoked = p.get("revoked_devices", [])
        if not isinstance(revoked, list) or not all(isinstance(d, str) for d in revoked):
            raise IdentityError("%s: revoked_devices must be a list of device ids" % where)
        self.principals[name] = Principal(name, kind, owner, grants, limits, uid=uid,
                                          tailscale=[v.strip() for v in p.get("tailscale", [])])
        self.raw[name] = {"password": p.get("password"), "epoch": epoch, "revoked": frozenset(revoked), "uid": uid}

    def pair_entry(self, i, e):
        where = "pairing entry %d" % (i + 1)
        if not isinstance(e, dict) or not isinstance(e.get("principal"), str) or e["principal"] not in self.principals:
            raise IdentityError("%s: names no principal in this file" % where)
        if set(e) - self.KNOWN_PAIRING:
            raise IdentityError("%s: unknown setting %r" % (where, sorted(set(e) - self.KNOWN_PAIRING)[0]))
        if self.principals[e["principal"]].kind != "person":
            raise IdentityError("%s: only a person pairs a device" % where)
        if not valid_hash(e.get("code")) or not isinstance(e.get("device"), str) or not TOKEN_ID_RE.match(e["device"]):
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
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise IdentityError("identity file %s: %s" % (path, e))
    try:
        config = Config(data, os.path.dirname(os.path.abspath(path)))
    except IdentityError:
        raise
    except (TypeError, AttributeError, ValueError, KeyError, RecursionError) as e:    # a shape Config didn't foresee
        raise IdentityError("identity file %s: unexpected shape (%s)" % (path, type(e).__name__))
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
    """At most `limit` attempts per key in `window` seconds, in memory (per room process). An attempt is counted when
    it starts (`take`), before the slow check, so parallel requests can't all slip past; a success gives it back
    (`forgive`). At most MAX_KEYS keys are kept: past that the least recently used are forgotten."""

    MAX_KEYS = 4096

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits, self.lock = {}, threading.Lock()

    def take(self, key):
        """Count an attempt for `key`; False (and nothing counted) when it's over the limit."""
        with self.lock:
            t = time.monotonic()
            hits = [h for h in self.hits.pop(key, ()) if h > t - self.window]
            if len(hits) >= self.limit:
                self.hits[key] = hits
                return False
            hits.append(t)
            self.hits[key] = hits                  # re-inserted: the dict's order is least recently used first
            if len(self.hits) > self.MAX_KEYS:
                # forget the least recently used keys that aren't locked: a flood of junk names can't free a locked one
                for k in [k for k, v in self.hits.items() if len(v) < self.limit][:len(self.hits) - self.MAX_KEYS]:
                    del self.hits[k]
                while len(self.hits) > self.MAX_KEYS:   # all locked: drop the oldest (5 failures each, many addresses)
                    del self.hits[next(iter(self.hits))]
            return True

    def forgive(self, key):
        with self.lock:
            hits = self.hits.get(key)
            if hits:
                hits.pop()


HASHING = threading.BoundedSemaphore(4)     # scrypt costs 16 MiB and ~50 ms: at most four at once per room


# -- resolving a request ---------------------------------------------------------------------------------------------

# auth=open: everyone, as before identities; ":open" is an id no name in a file can be
OPEN_OWNER = Principal("local", "person", owner=True, via="open", uid=":open")


def tailscale_uid(login):
    """The preferences key of a Tailscale login without an identity file: "ts:" + the first 32 hex digits of the
    SHA-256 of the login, trimmed and lowercased (Tailscale logins are case-insensitive). Always hashed: it fits
    Prefs' 64 characters however long the login, and the room's SQLite file holds no email address."""
    return "ts:" + hashlib.sha256(login.strip().lower().encode("utf-8")).hexdigest()[:32]


def ambient(auth, headers):
    """A principal for preferences in a room WITHOUT an identity file (load_for gave None). The room's old gate
    (*_USERS, or open mode's Host allow-list) decides who gets in; this only names whose preferences these are, so the
    room calls it only after that gate admitted the request. Never a grant: it is the owner because the old gate
    lets only the owner in.

    auth "tailscale": the one Tailscale-User-Login (None when it is missing, empty, sent twice or holds a control
    character), as Principal(login, "person", owner=True, via="tailscale", uid=tailscale_uid(login)).
    auth "open": OPEN_OWNER (uid ":open"). Anything else: None (no preferences)."""
    auth = (auth or "").strip().lower()
    if auth == "open":
        return OPEN_OWNER
    if auth != "tailscale":
        return None
    values = Identity.header_values(headers, "Tailscale-User-Login")
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    login = values[0].strip()
    if not login or any(ord(c) < 32 or ord(c) == 127 for c in login):
        return None
    return Principal(login, "person", owner=True, via="tailscale", uid=tailscale_uid(login), tailscale=(login,))


class Result:
    """What `resolve` decided. `principal` is None when nobody proved who they are (`status` 401) or the proof names
    nobody in the file (`status` 403). `cookies` are Set-Cookie values the room must send (a renewed or cleared
    session). `error` is safe to show; it never contains a secret."""

    def __init__(self, principal=None, status=200, error="", cookies=()):
        self.principal, self.status, self.error, self.cookies = principal, status, error, list(cookies)

    def __bool__(self):
        return self.principal is not None


def whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


class Identity:
    """A room's view of the identity file. `room` names the room (its grants); `auth`: tailscale | header | open;
    `header`: the proxy's login header for auth=header; `signin`: the built-in sign-in is on; `secure`: cookies get
    Secure (the room is served over https); `cookie_domain`: MACHIYA_COOKIE_DOMAIN, so one sign-in covers every room;
    `accept_caps`: read Tailscale-App-Capabilities (tagged nodes). Only turn it on where Serve forwards and strips that
    header (`--accept-app-caps`, Tailscale v1.92+): an older Serve passes a client's own copy straight through."""

    def __init__(self, path, room, auth="tailscale", header="", signin=False, secure=True, cookie_domain="",
                 accept_caps=False):
        if room not in ACTIONS:
            raise IdentityError("unknown room %r" % room)
        if auth not in ("tailscale", "header", "open"):
            raise IdentityError("auth must be tailscale, header or open, not %r" % auth)
        if auth == "header" and not re.fullmatch(r"[A-Za-z0-9-]+", header or ""):
            raise IdentityError("auth=header needs the proxy's login header name")
        if cookie_domain and not re.fullmatch(r"\.?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", cookie_domain):
            raise IdentityError("MACHIYA_COOKIE_DOMAIN must be a domain name, not %r" % cookie_domain)
        self.path, self.room, self.auth, self.header = path, room, auth, header
        self.signin, self.secure, self.cookie_domain, self.accept_caps = signin, secure, cookie_domain, accept_caps
        self.lock = threading.Lock()
        self.checked = 0.0
        self.config, self.key = read_file(path)
        self.stamp = self._stamp(self.config)
        self.signin_names = Throttle(5, 900)
        self.signin_addrs = Throttle(20, 900)
        self.pair_addrs = Throttle(5, 600)

    def _stamp(self, config):
        """The identity file's and the session key's (inode, mtime, size): a change to either is a reload."""
        out = []
        for path in (self.path, config.key_file):
            try:
                st = os.stat(path)
                out.append((st.st_ino, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def current(self):
        """The config, re-read when the file or the key changed (checked at most once a second). A file that turned
        invalid keeps the last good grants and says so on stderr (a typo never opens or locks the rooms mid-flight),
        but a key that changed and can't be read drops the old key: no session or device token passes until it can."""
        with self.lock:
            if time.monotonic() - self.checked >= 1:
                self.checked = time.monotonic()
                # the stamp is taken before reading, so a write that lands during the read is seen next time
                stamp = self._stamp(self.config)
                if stamp != self.stamp:
                    try:
                        config, key = read_file(self.path)
                        if config.key_file != self.config.key_file:
                            stamp = self._stamp(config)
                        self.config, self.key, self.stamp = config, key, stamp
                    except (OSError, IdentityError) as e:
                        if stamp[1] != self.stamp[1]:
                            self.key = None
                        self.stamp = stamp
                        print("identity: keeping the last good identity file%s: %s"
                              % ("" if self.key else " without a session key", e), file=sys.stderr, flush=True)
            return self.config, self.key

    # -- the proofs

    @staticmethod
    def header_values(headers, name):
        """Every value of a header (http.server's get_all), so a duplicate can be refused rather than half-read."""
        get_all = getattr(headers, "get_all", None)
        if get_all is not None:
            return get_all(name) or []
        value = headers.get(name)
        return [] if value is None else [value]

    def resolve(self, headers, client=""):
        """headers: http.server's message (or a mapping with .get). client: the peer address. Never raises: anything
        unreadable is a 401."""
        try:
            return self._resolve(headers)
        except Exception as e:          # a request thread must never die on hostile input
            print("identity: refused an unreadable request (%s)" % type(e).__name__, file=sys.stderr, flush=True)
            return Result(None, 401, "unreadable identity")

    def _resolve(self, headers):
        config, key = self.current()
        names = ["Authorization", "Tailscale-User-Login", "Tailscale-App-Capabilities"]
        if self.auth == "header":
            names.append(self.header)
        for name in names:
            if len(self.header_values(headers, name)) > 1:
                return Result(None, 401, "%s twice" % name)
        auth = headers.get("Authorization")
        if auth is not None:            # present, even empty, it's a proof to check, never one to skip
            return self._bearer(config, key, auth.strip())
        if self.auth == "tailscale":
            login = (headers.get("Tailscale-User-Login") or "").strip()
            if login:
                name = config.by_login.get(login.lower())
                if not name:
                    return Result(None, 403, "this login has no access")
                return Result(config.principals[name].with_via("tailscale"))
            caps = headers.get("Tailscale-App-Capabilities")
            if caps and self.accept_caps:
                return self._capability(config, caps)
        elif self.auth == "header":
            login = (headers.get(self.header) or "").strip()
            if login:
                name = config.by_proxy.get(login)
                if not name:
                    return Result(None, 403, "this login has no access")
                return Result(config.principals[name].with_via("proxy"))
        if self.signin:
            cookies = self.cookie_values(headers.get("Cookie"))
            if cookies:
                session = self._session(config, key, cookies)
                if session or self.auth != "open":
                    return session
                return Result(OPEN_OWNER, 200, "", session.cookies)    # open mode: still clear the bad cookie
        if self.auth == "open":
            return Result(OPEN_OWNER)
        return Result(None, 401, "sign in first" if self.signin else "no identity")

    def _bearer(self, config, key, value):
        scheme, _, token = value.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token or len(token) > 4096:
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
            name = payload.get("p") if payload else None
            if not isinstance(name, str) or name not in config.principals:
                return Result(None, 401, "unknown device token")
            raw = config.raw[name]
            device = payload.get("d")
            if payload.get("u") != raw["uid"] or not whole(payload.get("e")) or payload["e"] != raw["epoch"] \
                    or not isinstance(device, str) or device in raw["revoked"]:
                return Result(None, 401, "device signed out")
            return Result(config.principals[name].with_via("device:" + device))
        return Result(None, 401, "unknown token")

    def _capability(self, config, header):
        try:
            values = capability_values(header, config.capability)
        except IdentityError as e:
            return Result(None, 401, str(e))
        named = [v.get("principal") for v in values if isinstance(v, dict) and "principal" in v]
        if not named:
            return Result(None, 401, "the tailnet grant names no principal")
        if not all(isinstance(n, str) for n in named) or len(set(named)) != 1:
            return Result(None, 403, "the tailnet grant names no single principal")
        name = config.by_tag.get(named[0])
        if not name:
            return Result(None, 403, "this tagged node has no access")
        return Result(config.principals[name].with_via("tailscale-tag"))

    def _session(self, config, key, cookies):
        """The first of the request's session cookies that holds (a sibling site on the shared domain may have set
        another, narrower one); 401 and a cleared cookie when none does."""
        t = now()
        for cookie in cookies[:4]:
            payload = unsign(key, "session", cookie)
            name = payload.get("p") if payload else None
            if not isinstance(name, str) or name not in config.principals:
                continue
            raw = config.raw[name]
            if payload.get("u") != raw["uid"] or not all(whole(payload.get(k)) for k in ("e", "iat", "auth", "exp")):
                continue
            if payload["exp"] <= t or payload["e"] != raw["epoch"]:
                continue
            renew = [self.issue(config, key, name, first=payload["auth"])] if t - payload["iat"] > RENEW_AFTER else []
            return Result(config.principals[name].with_via("session"), 200, "", renew)
        return Result(None, 401, "sign in again", [self.cookie("", 0)])

    # -- sessions and the built-in sign-in

    @staticmethod
    def cookie_values(header):
        out = []
        for part in (header or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE and v.strip():
                out.append(v.strip())
        return out

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
        payload = {"p": name, "u": config.raw[name]["uid"], "e": config.raw[name]["epoch"], "iat": t, "auth": first,
                   "exp": exp}
        return self.cookie(sign(key, "session", payload), max(0, exp - t))

    def sign_in(self, name, password, client=""):
        """The built-in sign-in: -> Result with the session cookie, or 401 (wrong name or password, the same answer
        and the same time) or 429 (too many failures for this name or this address). The throttle is a trade-off:
        five wrong passwords lock a name for 15 minutes for everyone (guessing costs more than a lockout here), and
        behind a proxy every client shares the proxy's address."""
        if not self.signin:
            return Result(None, 404, "sign-in is off in this room")
        if not isinstance(name, str) or not isinstance(password, str):
            return Result(None, 401, "wrong name or password")
        config, key = self.current()
        name = name.strip().lower()
        if not NAME_RE.match(name) or len(name) > MAX_NAME or len(password) > 1024:
            return Result(None, 401, "wrong name or password")      # no such principal can exist: no hashing
        if not self.signin_addrs.take(client):
            return Result(None, 429, "too many tries; wait a few minutes")
        if not self.signin_names.take(name):
            return Result(None, 429, "too many tries; wait a few minutes")
        if not key:
            self.signin_names.forgive(name)
            self.signin_addrs.forgive(client)
            return Result(None, 503, "sign-in is unavailable (no session key)")
        if not HASHING.acquire(blocking=False):
            self.signin_names.forgive(name)
            self.signin_addrs.forgive(client)
            return Result(None, 429, "busy; try again in a moment")
        try:
            p = config.principals.get(name)
            stored = config.raw[name]["password"] if p else None
            ok = check_password(password, stored or DUMMY_HASH) and bool(stored) and p.kind == "person"
        finally:
            HASHING.release()
        if not ok:
            return Result(None, 401, "wrong name or password")
        self.signin_names.forgive(name)
        self.signin_addrs.forgive(client)
        return Result(p.with_via("session"), 200, "", [self.issue(config, key, name)])

    def sign_out(self):
        return [self.cookie("", 0)]

    def pair(self, code, client=""):
        """A Shiori device trades a one-time code (the CLI's `pair`) for a device token. -> (Result, token or "")."""
        if not isinstance(code, str) or len(code) > 64:
            return Result(None, 401, "unknown or expired code"), ""
        config, key = self.current()
        if not self.pair_addrs.take(client):
            return Result(None, 429, "too many tries; wait a few minutes"), ""
        if not key:
            self.pair_addrs.forgive(client)
            return Result(None, 503, "pairing is unavailable (no session key)"), ""
        code = re.sub(r"[\s-]", "", code).upper()
        t = datetime.datetime.now(datetime.timezone.utc)
        if not HASHING.acquire(blocking=False):
            self.pair_addrs.forgive(client)
            return Result(None, 429, "busy; try again in a moment"), ""
        try:
            found = next((e for e in config.pairing if e["expires"] > t and check_password(code, e["code"])), None)
        finally:
            HASHING.release()
        if not found:
            return Result(None, 401, "unknown or expired code"), ""
        name = found["principal"]
        payload = {"p": name, "u": config.raw[name]["uid"], "d": found["device"], "e": config.raw[name]["epoch"],
                   "iat": now()}
        token = "mcd_" + sign(key, "device", payload)
        return Result(config.principals[name].with_via("device:" + found["device"])), token


def load_for(room, env=None, bind="0.0.0.0", secure=True):
    """The room's Identity from MACHIYA_IDENTITY_FILE and <ROOM>_AUTH / _AUTH_HEADER / _SIGNIN / _BIND_BEHIND_PROXY /
    _ACCEPT_APP_CAPS,
    or None when no identity file is set (the room keeps its old *_USERS gate). IdentityError for a bad setup.

    `secure` (default True) puts Secure on the session cookie, and vaultkit.signin then accepts only https pages as
    same-origin. A room passes secure=False only when it is really served over plain http (no https public URL: a
    localhost or LAN setup); a browser drops a Secure cookie set over http, so the sign-in would silently fail. Leave
    it True whenever any proxy in front of the room speaks https."""
    env = os.environ if env is None else env
    path = (env.get("MACHIYA_IDENTITY_FILE") or "").strip()
    if not path:
        return None
    prefix = {"konbini": "KANBAN"}.get(room, room.upper())
    auth = (env.get(prefix + "_AUTH") or "tailscale").strip().lower()
    signin = (env.get(prefix + "_SIGNIN") or "").strip().lower() in ("1", "on", "true", "yes")
    behind = (env.get(prefix + "_BIND_BEHIND_PROXY") or "").strip().lower() in ("1", "on", "true", "yes")
    check_bind(auth, bind, behind)
    caps = (env.get(prefix + "_ACCEPT_APP_CAPS") or "").strip().lower() in ("1", "on", "true", "yes")
    return Identity(path, room, auth, (env.get(prefix + "_AUTH_HEADER") or "").strip(), signin, secure,
                    (env.get("MACHIYA_COOKIE_DOMAIN") or "").strip(), caps)


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
        return "{ " + ", ".join("%s = %s" % (toml_key(k), toml_value(x)) for k, x in v.items()) + " }"
    raise IdentityError("can't write %r to TOML" % (v,))


def toml_key(k):
    """A key as TOML reads it back: bare when it can be, else quoted (a hand-written key never becomes syntax)."""
    if not isinstance(k, str):
        raise IdentityError("can't write the key %r to TOML" % (k,))
    return k if BARE_KEY_RE.match(k) else json.dumps(k, ensure_ascii=False)


def dump(data):
    """The identity file's data -> TOML text, in a fixed order. Comments aren't kept (tomllib reads, never writes)."""
    out = ["# Machiya identity file (docs/identity.md). Written by `python3 -m vaultkit.identity`;",
           "# hand edits are fine, but the CLI rewrites the file and doesn't keep comments.", ""]
    for k in ("version", "session_key_file", "session_days", "tailscale_capability"):
        if k in data:
            out.append("%s = %s" % (k, toml_value(data[k])))
    for name, p in (data.get("principals") or {}).items():
        out += ["", "[principals.%s]" % toml_key(name)]
        for k, v in p.items():
            if k != "tokens":
                out.append("%s = %s" % (toml_key(k), toml_value(v)))
        for t in p.get("tokens", []):
            out += ["[[principals.%s.tokens]]" % toml_key(name)] + \
                ["%s = %s" % (toml_key(k), toml_value(v)) for k, v in t.items()]
    for e in data.get("pairing") or []:
        out += ["", "[[pairing]]"] + ["%s = %s" % (toml_key(k), toml_value(v)) for k, v in e.items()]
    return "\n".join(out) + "\n"


def write_file(path, data):
    """Check, then replace the file atomically with the same mode and owner, keeping a .bak of the old one. Nothing is
    written through a symlink: the new file is a fresh temp file in the same directory (mkstemp), and the .bak is
    created anew with O_EXCL | O_NOFOLLOW. The rooms see the new file within a second; mount its directory, not the
    file (a file mount keeps the old inode after a rename)."""
    import tempfile
    text = dump(data)
    Config(tomllib.loads(text), os.path.dirname(os.path.abspath(path)))      # never write what a room would refuse
    folder = os.path.dirname(os.path.abspath(path))
    old = os.lstat(path) if os.path.lexists(path) else None
    if old is not None and not stat.S_ISREG(old.st_mode):
        raise IdentityError("%s is not a regular file (a symlink?): not writing through it" % path)
    mode = stat.S_IMODE(old.st_mode) if old else 0o600
    if old is not None:
        with open(path, "rb") as f:
            previous = f.read()
        bak = path + ".bak"
        if os.path.lexists(bak):
            os.unlink(bak)
        fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "wb") as b:
            b.write(previous)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".identity-", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        if old is not None and (old.st_uid, old.st_gid) != (os.geteuid(), os.getegid()):
            try:
                os.fchown(fd, old.st_uid, old.st_gid)      # run as root over a room-readable file: keep it readable
            except PermissionError:
                print("identity: warning: can't keep %s owned by uid %d gid %d; check the rooms can still read it"
                      % (path, old.st_uid, old.st_gid), file=sys.stderr, flush=True)
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


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

  setup [--owner NAME] [--tailscale LOGIN] [--password] [--proxy LOGIN] [--rooms kura,niwa,konbini,mcp] [--yes]
                                         first time: the file, its key and an owner who can sign in, then the
                                         settings to paste into each room (PATH defaults to
                                         ./machiya-identity/identity.toml); never overwrites, only adds logins
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


SETUP_PATH = os.path.join(".", "machiya-identity", "identity.toml")
# room -> (its settings' prefix, the setting that names its public URL)
SETUP_ROOMS = {"kura": ("KURA", "KURA_PUBLIC_URL"), "niwa": ("NIWA", "NIWA_PUBLIC_URL"),
               "konbini": ("KANBAN", "KANBAN_BOARD_URL"), "mcp": ("MCP", None)}
SETUP_USAGE = ("setup [--owner NAME] [--tailscale LOGIN] [--password] [--proxy LOGIN] "
               "[--rooms kura,niwa,konbini,mcp] [--yes]")


def new_uid():
    return "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(16))


def write_key(path):
    """A new session key next to the identity file (0600), as init writes it; an existing key is kept."""
    key = os.path.join(os.path.dirname(os.path.abspath(path)), "session.key")
    if not os.path.exists(key):
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(b64e(secrets.token_bytes(32)) + "\n")
    return key


def setup(path, args, interactive=None):
    """`setup`: a first identity file with an owner who can sign in, and the settings for each room. Never overwrites
    the file: on an existing one it adds only the requested logins to the owner. -> 0 ok, 2 usage, 1 refused."""
    import getpass

    def usage(why):
        print("identity setup: %s\nusage: python3 -m vaultkit.identity [--file PATH] %s" % (why, SETUP_USAGE),
              file=sys.stderr)
        return 2

    def refuse(why):
        print("identity setup: refused: %s" % why, file=sys.stderr)
        return 1

    owner, tailscale, proxy, password, yes, rooms = "owner", None, None, False, False, list(SETUP_ROOMS)
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        if flag in ("--password", "--yes"):
            password, yes = password or flag == "--password", yes or flag == "--yes"
            continue
        if flag not in ("--owner", "--tailscale", "--proxy", "--rooms"):
            return usage("unknown argument %r" % flag)
        if not rest or not rest[0].strip() or rest[0].startswith("--"):
            return usage("%s needs a value" % flag)
        value = rest.pop(0).strip()
        if flag == "--owner":
            owner = value
        elif flag == "--tailscale":
            tailscale = value
        elif flag == "--proxy":
            proxy = value
        else:
            rooms = list(dict.fromkeys(r.strip().lower() for r in value.split(",") if r.strip()))
            bad = [r for r in rooms if r not in SETUP_ROOMS]
            if bad or not rooms:
                return usage("--rooms takes %s, not %r" % (",".join(SETUP_ROOMS), value))
    if not NAME_RE.match(owner) or len(owner) > MAX_NAME:
        return usage("--owner: a name is lowercase letters, digits and -")
    if tomllib is None:
        return refuse("the identity file needs Python 3.11 or newer (tomllib)")
    if interactive is None:
        interactive = not (yes or tailscale or proxy or password) and sys.stdin.isatty()
    if interactive:
        try:
            tailscale = input("Your Tailscale login (blank to skip): ").strip() or None
            password = input("Set a password for the built-in sign-in? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return refuse("no answer")

    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    exists = os.path.lexists(path)
    if exists:
        try:
            config, _ = read_file(path)
        except IdentityError as e:
            return refuse("%s (fix it, or set --file to a new path)" % e)
        with open(path, "rb") as f:
            data = tomllib.load(f)
        others = [n for n, p in config.principals.items() if p.owner and n != owner]
        if others:
            return refuse("%r is already the owner in %s; rerun with --owner %s" % (others[0], path, others[0]))
        if owner in config.principals and not config.principals[owner].owner:
            return refuse("%r exists in %s and isn't the owner; choose another --owner" % (owner, path))
        if tailscale and config.by_login.get(tailscale.lower(), owner) != owner:
            return refuse("the Tailscale login %r belongs to %r" % (tailscale, config.by_login[tailscale.lower()]))
        if proxy and config.by_proxy.get(proxy, owner) != owner:
            return refuse("the proxy login %r belongs to %r" % (proxy, config.by_proxy[proxy]))
        if owner in config.principals and password and config.raw[owner]["password"]:
            return refuse("%r already has a password; change it with passwd %s" % (owner, owner))
        data.setdefault("principals", {})
    else:
        data = {"version": VERSION, "session_key_file": "session.key", "session_days": 30,
                "tailscale_capability": CAPABILITY, "principals": {}}
    found = owner in data["principals"]
    before = dump(data)
    p = data["principals"].setdefault(owner, {"id": new_uid(), "kind": "person", "owner": True})
    if tailscale and tailscale.lower() not in {t.lower() for t in p.get("tailscale", [])}:
        p["tailscale"] = sorted(set(p.get("tailscale", [])) | {tailscale})
    if proxy:
        p["proxy"] = sorted(set(p.get("proxy", [])) | {proxy})
    if not (p.get("tailscale") or p.get("proxy") or password or p.get("password")):
        return refuse("%r would have no way to sign in: give --tailscale LOGIN, --proxy LOGIN or --password" % owner)
    if password:
        pw = getpass.getpass("new password for %s: " % owner)
        if len(pw) < 12:
            return refuse("use at least 12 characters")
        if getpass.getpass("again: ") != pw:
            return refuse("the two passwords differ")
        p["password"] = hash_password(pw)
    try:
        Config(tomllib.loads(dump(data)), folder)    # checked before anything is created
    except IdentityError as e:
        return refuse("not written: %s" % e)

    if not exists:
        if not os.path.isdir(folder):
            os.makedirs(folder, mode=0o700)
            os.chmod(folder, 0o700)                 # whatever the umask
        key = write_key(path)
        print("wrote %s and %s" % (path, key))
    elif dump(data) == before:
        print("%s: %r is already the owner; nothing to add" % (path, owner))
    elif found:
        print("%s: %r is already the owner; added only the logins asked for" % (path, owner))
    else:
        print("%s: added the owner %r" % (path, owner))
    if not exists or dump(data) != before:          # an unchanged file isn't rewritten (it would lose its comments)
        try:
            write_file(path, data)
        except IdentityError as e:
            return refuse("not written: %s" % e)

    signin, behind_proxy = bool(p.get("password")), bool(p.get("tailscale") or p.get("proxy"))
    print("\n# Paste into each room's settings (.env or the container's environment):")
    for room in rooms:
        prefix, url = SETUP_ROOMS[room]
        print("\n# %s" % room.capitalize())
        print("MACHIYA_IDENTITY_FILE=%s" % path)
        if signin:
            print("%s_SIGNIN=1" % prefix)
            if url:
                print("%s=http://localhost:PORT    # the address people open; sign-in checks it" % url)
        if behind_proxy:
            print("%s_BIND_BEHIND_PROXY=1    # Docker or a sidecar: Tailscale or the proxy is the only way in" % prefix)
    urls = [SETUP_ROOMS[r][1] for r in rooms if SETUP_ROOMS[r][1]] if signin else []
    cli = "python3 -m vaultkit.identity --file %s" % path
    print("\nNotes:")
    print("- Mount the directory, not the file, read-only into each room at the same path:")
    print("    -v %s:%s:ro" % (folder, folder))
    print("  (the CLI replaces the file atomically; a file mount would keep the old one). The directory is 0700 and")
    print("  the files 0600: the rooms' user must be able to read them (chown -R, or chgrp -R and g+rX).")
    if urls:
        print("- Replace PORT in %s with each room's real address (same-origin checks compare it)." % ", ".join(urls))
    steps = []
    if "mcp" in rooms:
        steps.append("%s add mcp --kind agent" % cli)
        if "konbini" in rooms:
            steps.append("%s grant mcp konbini read write" % cli)
        steps.append("%s token mint mcp --label mcp" % cli)
    if "kura" in rooms:
        steps.append("%s pair %s --label phone     # a one-time code for Shiori" % (cli, owner))
    if steps:
        print("- Next steps:")
        for line in steps:
            print("    " + line)
    return 0


def main(argv=None):
    import getpass
    args = list(sys.argv[1:] if argv is None else argv)
    path = os.environ.get("MACHIYA_IDENTITY_FILE", "")
    if args[:1] == ["--file"] and len(args) > 1:
        path, args = args[1], args[2:]
    if args[:1] == ["setup"]:
        return setup(path or SETUP_PATH, args[1:])
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
        key = write_key(path)
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
        data["principals"][args[0]] = {"id": new_uid(), "kind": kind, **({"owner": True} if owner else {})}
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
