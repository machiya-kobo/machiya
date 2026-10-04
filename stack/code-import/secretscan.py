"""code-import's own secret scan, run on every document's text before anything reaches Hister.

Hister's check (422) is narrow: six default patterns, and only on `html` for a web document. A repo's README, docs,
issues and release notes can hold a pasted key, so this scan is wider. What it finds is either redacted (the default:
the match becomes `[redacted]`, and the document says `code_redacted: "true"`) or the whole document is refused
(CODE_IMPORT_SECRETS=refuse). It never logs what it found, only the kinds.

Files whose names say "secret" are refused before they are read (secret_name).
"""
import fnmatch
import math
import posixpath
import re

REDACTED = "[redacted]"

# (kind, pattern). Specific token shapes first; the generic assignment last.
PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
                               re.S)),
    ("age-key", re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{58}")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}")),
    ("tailscale-key", re.compile(r"\btskey-[a-z]+-[A-Za-z0-9]{8,}-[A-Za-z0-9]{16,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("url-password", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@/]{6,}@")),
]
# `password = "…"`, `api_key: …`, `TOKEN=…` with a long, random-looking value. Placeholders (<…>, ${…}, xxx, your-…,
# changeme, example) are left alone.
ASSIGN = re.compile(r"(?i)\b([a-z0-9_.\-]*(?:passw(?:or)?d|passwd|secret|token|api[_\-]?key|access[_\-]?key|"
                    r"client[_\-]?secret|auth)[a-z0-9_.\-]*)(\s*[:=]\s*|\s*=>\s*)([\"']?)([^\s\"'<>`]{16,})\3")
PLACEHOLDER = re.compile(r"(?i)^(?:\$\{?|%|<|\{\{)|x{4,}|\*{4,}|\.{3}|your[_\-]|changeme|example|placeholder|dummy|"
                         r"redacted|_file$|^/|^https?://|^\w+\(")

# Names refused outright (research §2 (d)), matched against the file's base name, case-insensitively.
SECRET_NAMES = [".env*", "*.pem", "*.key", "id_*", "*.gpg", "*.age", "secrets*", "*.p12", "*.pfx", "*.kdbx"]


def secret_name(path):
    base = posixpath.basename(path).lower()
    return any(fnmatch.fnmatchcase(base, pat) for pat in SECRET_NAMES)


def entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def random_looking(value):
    """A value that looks like a credential, not a word: long, mixed, high entropy."""
    if PLACEHOLDER.search(value):
        return False
    classes = sum(bool(re.search(p, value)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    return len(value) >= 16 and classes >= 2 and entropy(value) >= 3.5


def scan(text):
    """[(kind, start, end)] of the likely secrets in text, non-overlapping, in order."""
    hits = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append((kind, m.start(), m.end()))
    for m in ASSIGN.finditer(text or ""):
        value = m.group(4)
        if random_looking(value):
            hits.append(("assignment", m.start(4), m.end(4)))
    hits.sort(key=lambda h: (h[1], -h[2]))
    out, end = [], -1
    for h in hits:
        if h[1] >= end:
            out.append(h)
            end = h[2]
    return out


def redact(text):
    """(text with every hit replaced by [redacted], sorted kinds found)."""
    hits = scan(text)
    if not hits:
        return text, []
    parts, pos = [], 0
    for kind, start, end in hits:
        parts.append(text[pos:start])
        parts.append(REDACTED)
        pos = end
    parts.append(text[pos:])
    return "".join(parts), sorted({h[0] for h in hits})
