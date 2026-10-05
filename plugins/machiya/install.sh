#!/usr/bin/env bash
# install.sh: install the machiya plugin (machiya-mcp's connection plus the cross-room skills) for the current user.
#   install.sh [install]   add this repo as a marketplace, install machiya@machiya at user scope, set MACHIYA_AGENT (and
#                          MACHIYA_MCP_URL when given), and add the permission rules to ~/.claude/settings.json: reads and
#                          the small writes allowed, the other writes ask, and EVERY Hister MCP tool DENIED under every name
#                          it can appear as (since plugin 0.3.0: Hister's own search and get_preview return vault notes and
#                          code documents, and its get_history browsing history; pages come from machiya-mcp's pages_search
#                          and pages_read, which never return notes or code). It also moves a machine off the separate
#                          `hister` server that `install.sh hister` added before 0.2.0. Idempotent: run it again after an update.
#   install.sh check       is the server up, and does a real call work (your tailnet identity, or the room token)?
#   install.sh uninstall   remove the plugin, the marketplace, the rules and the env settings. The Hister denies stay.
#   install.sh hister-remove   only the move off the older separate `hister` server (`hister` does the same).
# Environment: MACHIYA_MCP_URL (default https://machiya-mcp.example.ts.net/mcp), MACHIYA_REPO (this checkout; default: the
# one this script is in), MACHIYA_AGENT_NAME (what the board's history says; default user@host), MACHIYA_TOKEN_FILE (0.4.0:
# a file holding a room token hister-login made for machiya-mcp, for a machine with no Tailscale login, such as a tagged
# agents' VM; its PATH is saved, never the token, and bin/machiya-headers sends it). The URL, when set, is
# saved in the settings' env, where the plugin's .mcp.json finds it. The dev stack (docs/dev-stack.md) is one more value of
# it. Needs claude, python3, and a client on the tailnet. ~/.claude/settings.json is backed up to settings.json.bak-machiya
# before each change.
set -euo pipefail
REPO=${MACHIYA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
AGENT=${MACHIYA_AGENT_NAME:-$(id -un)@$(hostname -s)}
URL=${MACHIYA_MCP_URL:-https://machiya-mcp.example.ts.net/mcp}
SETTINGS=$HOME/.claude/settings.json
P=mcp__plugin_machiya_machiya__
command -v claude >/dev/null || { echo "install.sh: claude not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "install.sh: python3 not found" >&2; exit 1; }

settings() {   # settings add|remove
    [[ -f $SETTINGS ]] || echo '{}' > "$SETTINGS"
    cp "$SETTINGS" "$SETTINGS.bak-machiya"
    python3 - "$1" "$SETTINGS" "$P" "$AGENT" "${MACHIYA_MCP_URL:-}" "${MACHIYA_TOKEN_FILE:-}" <<'PY'
import json, os, sys
mode, path, prefix, agent, mcp_url, token_file = sys.argv[1:7]
H = "mcp__plugin_machiya_hister__"
d = json.load(open(path))
allow = [prefix + n for n in ["board_list_cards", "board_get_card", "board_review", "board_roundup", "notes_*", "collections_list",
         "collections_audit", "pages_labels", "pages_search", "pages_read", "garden_candidates", "machiya_*", "board_set_next",
         "board_log", "board_claim", "board_release"]]
ask = [prefix + n for n in ["board_add_backlog", "board_move", "board_block", "board_set_priority", "board_set_stream", "board_set_goal",
       "board_set_due", "board_set_dependencies", "board_tag", "garden_suggest", "notes_create", "notes_update", "pages_set_label",
       "pages_relabel", "collections_set", "collections_remove"]]
# Hister's own MCP never reaches the model (plugin 0.3.0, the owner's "code and notes stay out of AI", 2026-10-05): its
# search and get_preview return vault notes and code documents, its get_history the browsing history. Denied as the plugin
# 0.2.x tools and as a separately added server's, whole servers and each tool by name. A deny rule removes the tool from the
# model's view. They are never removed here, not even by uninstall.
deny = [H + "get_history", "mcp__hister__get_history", H + "search", H + "get_preview", "mcp__hister__search",
        "mcp__hister__get_preview", H + "*", "mcp__hister__*"]
# rules for tools that are gone or now denied: Hister's page reads allowed by plugin 0.2.x and by the older separate
# `hister` server
stale = [H + "search", H + "get_preview", "mcp__hister__search", "mcp__hister__get_preview"]
perm = d.setdefault("permissions", {})
for key, mine in (("allow", allow), ("ask", ask)):
    cur = perm.setdefault(key, [])
    perm[key] = [x for x in cur if x not in mine and x not in stale] + (mine if mode == "add" else [])
    if not perm[key] and key == "ask":
        del perm[key]
cur = perm.setdefault("deny", [])
perm["deny"] = cur + [x for x in deny if x not in cur]
env = d.setdefault("env", {})
if mode == "add":
    env["MACHIYA_AGENT"] = agent
    if mcp_url:
        env["MACHIYA_MCP_URL"] = mcp_url
    if token_file:
        env["MACHIYA_TOKEN_FILE"] = os.path.abspath(token_file)    # the path, never the token
else:
    for k in ("MACHIYA_AGENT", "MACHIYA_MCP_URL", "MACHIYA_TOKEN_FILE", "HISTER_MCP_URL", "HISTER_TOKEN_FILE"):
        env.pop(k, None)
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
}

old_hister() {   # the separate user-scope `hister` server that `install.sh hister` added before 0.2.0
    if claude mcp remove --scope user hister >/dev/null 2>&1; then
        echo "removed the older separate 'hister' MCP server (Hister's MCP is denied for AI clients)"
    fi
}

case ${1:-install} in
install)
    [[ -f $REPO/.claude-plugin/marketplace.json ]] || { echo "install.sh: $REPO is not the machiya checkout (no .claude-plugin/marketplace.json)" >&2; exit 1; }
    claude plugin marketplace list 2>/dev/null | grep -q '❯ machiya$' || claude plugin marketplace add "$REPO"
    claude plugin marketplace update machiya >/dev/null 2>&1 || true
    if claude plugin list 2>/dev/null | grep -q 'machiya@machiya'; then claude plugin update machiya@machiya >/dev/null 2>&1 || true
    else claude plugin install machiya@machiya --scope user; fi
    old_hister
    settings add
    echo "installed for $(id -un)@$(hostname -s) as agent '$AGENT'. Running sessions pick it up when they restart (or /reload-plugins)."
    "$0" check || echo "check failed: is this machine on the tailnet, and allowed to reach the machiya-mcp service?" ;;
check)
    base=${URL%/mcp}
    curl -fsS -m 8 "$base/healthz" | python3 -c "import sys,json; d=json.load(sys.stdin); print('server %s, %d tools, rooms: %s' % (d['version'], d['tools'], ', '.join(d['rooms'])))"
    # the call itself, with the same headers Claude Code sends (bin/machiya-headers: the room token, when one is set),
    # made in python so the token never sits in a command line
    MACHIYA_MCP_URL=$URL python3 - "$URL" "$AGENT" "$(dirname "${BASH_SOURCE[0]}")/bin/machiya-headers" <<'PY'
import json, subprocess, sys, urllib.request
url, agent, helper = sys.argv[1:4]
h = json.loads(subprocess.run([sys.executable, helper], capture_output=True, text=True).stdout or "{}")
h.update({"Content-Type": "application/json", "X-Agent": agent, "Accept": "application/json, text/event-stream"})
body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"machiya_status","arguments":{}}}'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                     # a token never follows a redirect
try:
    raw = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url, body, h), timeout=15).read().decode()
except Exception as e:
    print("machiya_status failed: %s (%s)" % (e, "room token" if "Authorization" in h else "tailnet identity"))
    sys.exit(1)
if raw.lstrip().startswith("event:") or "data:" in raw[:200]:
    raw = raw.split("data:", 1)[1].strip().splitlines()[0]
r = json.loads(raw)["result"]["structuredContent"]["rooms"]
print("rooms:", ", ".join("%s %s" % (k, "ok" if v.get("ok") else "DOWN") for k, v in r.items()),
      "(room token)" if "Authorization" in h else "(tailnet identity)")
sys.exit(1 if any(not v.get("ok") for v in r.values()) else 0)
PY
    ;;
uninstall)
    claude plugin uninstall machiya@machiya 2>/dev/null || true
    claude plugin marketplace remove machiya 2>/dev/null || true
    [[ -f $SETTINGS ]] && settings remove
    echo "removed (the Hister deny rules stay)" ;;
hister|hister-remove)
    old_hister
    [[ -f $SETTINGS ]] && settings add
    echo "Hister's MCP is denied for AI clients (mcp__plugin_machiya_hister__*, mcp__hister__*); pages come from pages_search and pages_read." ;;
*) sed -n 2,16p "$0"; exit 2 ;;
esac
