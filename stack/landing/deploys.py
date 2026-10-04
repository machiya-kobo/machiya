"""Recent deploys: what the landing page has seen change, and what each change brought (docs/services/landing.md).

A deploy is a version change the page itself observed: each poll compares every app's version (and vendored vaultkit)
with the last one seen and records the change with its time. That works with any mix of apps and needs no forge or
registry access. What a version brought comes from the app's CHANGELOG.md ("## X.Y.Z" sections), fetched from a URL
the deployment names per app (LANDING_CHANGELOGS); without one, a deploy shows its versions only.

The history lives in one small JSON file (LANDING_STATE). It is the page's only state, and losing it loses only the
list of past deploys: the next poll starts a new one.
"""
import json
import os
import re
import threading

KEEP = 100                      # events kept in the file
VERSION_RE = re.compile(r"^##\s+\[?v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\]?(?:\s|$)")


def vtuple(v):
    """'0.6.8' -> (0, 6, 8); anything else -> None (a build hash, a date version)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


class History:
    """The versions last seen and the changes observed, kept in `path` ("" = in memory only)."""

    def __init__(self, path=""):
        self.path, self.lock = path, threading.Lock()
        self.seen, self.events, self.since, self.error = {}, [], None, ""
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.seen = {k: v for k, v in (data.get("seen") or {}).items() if isinstance(v, dict)}
                self.events = [e for e in (data.get("events") or []) if isinstance(e, dict)][:KEEP]
                self.since = data.get("since")
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError) as e:
                self.error = "history unreadable (%s); starting a new one" % type(e).__name__

    def observe(self, apps, now):
        """Compare each app's version with the last seen; record changes. True when something was recorded."""
        changed = False
        with self.lock:
            if self.since is None:
                self.since, changed = now, True
            for key, a in apps.items():
                version, kit = a.get("version") or "", a.get("vaultkit") or ""
                if a.get("state") in ("absent", "down") or not version:
                    continue
                last = self.seen.get(key)
                if last is None:
                    self.seen[key] = {"version": version, "vaultkit": kit, "at": now}
                    changed = True
                    continue
                if last.get("version") != version or (kit and last.get("vaultkit") and last.get("vaultkit") != kit):
                    self.events.insert(0, {"app": key, "from": last.get("version"), "to": version,
                                           "vaultkit_from": last.get("vaultkit") or "", "vaultkit_to": kit, "at": now})
                    del self.events[KEEP:]
                    self.seen[key] = {"version": version, "vaultkit": kit, "at": now}
                    changed = True
                elif kit and not last.get("vaultkit"):
                    last["vaultkit"], changed = kit, True
            if changed:
                self.save()
        return changed

    def save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"since": self.since, "seen": self.seen, "events": self.events}, f, indent=1)
            os.replace(tmp, self.path)
            self.error = ""
        except OSError as e:
            self.error = "history not saved (%s)" % (e.strerror or type(e).__name__)

    def recent(self, now, days=30, limit=12):
        with self.lock:
            return [e for e in self.events if isinstance(e.get("at"), (int, float)) and now - e["at"] <= days * 86400][:limit]


# -- changelogs --------------------------------------------------------------------------------------------------

def plain(md):
    """One changelog item as plain text: no emphasis, code ticks or link targets."""
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"(\*\*|__|`)", "", md)
    return " ".join(md.split())


def parse(text):
    """CHANGELOG.md -> {version: [items]} in file order. Items are the section's top-level bullets, or its paragraphs
    when it has none; a section's sub-headings and nested bullets fold into the item above."""
    out, version, items, cur = {}, None, [], None

    def close():
        if version is not None:
            out[version] = [plain(i) for i in items if plain(i)]

    for line in text.splitlines():
        m = VERSION_RE.match(line)
        if m:
            close()
            version, items, cur = m.group(1), [], None
            continue
        if version is None:
            continue
        if line.startswith("## "):          # another level-2 heading ends the version
            close()
            version, items, cur = None, [], None
            continue
        if re.match(r"^[-*] ", line):
            items.append(line[2:])
            cur = len(items) - 1
        elif not line.strip():
            cur = None
        elif line.startswith("#"):
            continue
        elif cur is not None:
            items[cur] += " " + line.strip()
        else:
            items.append(line.strip())
            cur = len(items) - 1
    close()
    return out


def between(sections, old, new):
    """[(version, items)] the changelog says came with a move from `old` to `new`, newest first: every section above
    old up to new; just new's when the order is unknown (no old, or not X.Y.Z)."""
    hi, lo = vtuple(new), vtuple(old)
    if hi is None:
        return [(new, sections[new])] if new in sections else []
    if lo is None or lo >= hi:
        return [(new, sections[new])] if new in sections else []
    picked = [(v, items) for v, items in sections.items() if vtuple(v) and lo < vtuple(v) <= hi]
    return sorted(picked, key=lambda p: vtuple(p[0]), reverse=True)
