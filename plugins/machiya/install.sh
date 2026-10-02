#!/usr/bin/env bash
# install.sh: install the machiya plugin (the MCP connection plus the cross-room skills) for the current user.
#   install.sh [install]   add this repo as a marketplace, install machiya@machiya at user scope, set MACHIYA_AGENT, and add
#                          the permission rules to ~/.claude/settings.json (reads and the small writes allowed; the other
#                          writes ask). Idempotent: run it again after an update.
#   install.sh check       is the server up, and does a real call through your tailnet identity work?
#   install.sh uninstall   remove the plugin, the marketplace, the rules and MACHIYA_AGENT.
#   install.sh hister      also enable Hister's own MCP (search, get_preview; read-only) at user scope, with get_history DENIED
#                          (browsing history stays out of AI context). `install.sh hister-remove` undoes it.
# Environment: HISTER_MCP_URL (default https://hister.example.ts.net/mcp), MACHIYA_REPO (this checkout; default: the one this script is in), MACHIYA_AGENT_NAME (what the board's history
# says; default user@host), MACHIYA_MCP_URL (default https://machiya-mcp.example.ts.net/mcp). Needs claude, python3, and a
# client on the tailnet. ~/.claude/settings.json is backed up to settings.json.bak-machiya before each change.
set -euo pipefail
REPO=${MACHIYA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
AGENT=${MACHIYA_AGENT_NAME:-$(id -un)@$(hostname -s)}
URL=${MACHIYA_MCP_URL:-https://machiya-mcp.example.ts.net/mcp}
SETTINGS=$HOME/.claude/settings.json
P=mcp__plugin_machiya_machiya__
HISTER_MCP=${HISTER_MCP_URL:-https://hister.example.ts.net/mcp}
command -v claude >/dev/null || { echo "install.sh: claude not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "install.sh: python3 not found" >&2; exit 1; }

settings() {   # settings add|remove
    [[ -f $SETTINGS ]] || echo '{}' > "$SETTINGS"
    cp "$SETTINGS" "$SETTINGS.bak-machiya"
    python3 - "$1" "$SETTINGS" "$P" "$AGENT" <<'PY'
import json, sys
mode, path, prefix, agent = sys.argv[1:5]
d = json.load(open(path))
allow = ["board_list_cards", "board_get_card", "board_review", "board_roundup", "notes_*", "pages_search", "pages_read",
         "collections_list", "collections_audit", "pages_labels", "garden_candidates", "machiya_*", "board_set_next", "board_log", "board_claim", "board_release"]
ask = ["board_add_backlog", "board_move", "board_block", "board_set_priority", "board_set_stream", "board_set_goal",
       "board_set_due", "board_set_dependencies", "board_tag", "garden_suggest", "notes_create", "notes_update", "pages_set_label", "pages_relabel", "collections_set", "collections_remove"]
perm = d.setdefault("permissions", {})
for key, names in (("allow", allow), ("ask", ask)):
    cur = perm.setdefault(key, [])
    mine = [prefix + n for n in names]
    perm[key] = [x for x in cur if x not in mine] + (mine if mode == "add" else [])
    if not perm[key] and key == "ask":
        del perm[key]
env = d.setdefault("env", {})
if mode == "add":
    env["MACHIYA_AGENT"] = agent
else:
    env.pop("MACHIYA_AGENT", None)
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
}

hister_rules() {   # hister_rules add|remove
    [[ -f $SETTINGS ]] || echo '{}' > "$SETTINGS"
    cp "$SETTINGS" "$SETTINGS.bak-machiya"
    python3 - "$1" "$SETTINGS" <<'PY'
import json, sys
mode, path = sys.argv[1:3]
d = json.load(open(path))
perm = d.setdefault("permissions", {})
for key, names in (("allow", ["search", "get_preview"]), ("deny", ["get_history"])):
    mine = ["mcp__hister__" + n for n in names]
    cur = perm.setdefault(key, [])
    perm[key] = [x for x in cur if x not in mine] + (mine if mode == "add" else [])
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
}

case ${1:-install} in
install)
    [[ -f $REPO/.claude-plugin/marketplace.json ]] || { echo "install.sh: $REPO is not the machiya checkout (no .claude-plugin/marketplace.json)" >&2; exit 1; }
    claude plugin marketplace list 2>/dev/null | grep -q '❯ machiya$' || claude plugin marketplace add "$REPO"
    claude plugin marketplace update machiya >/dev/null 2>&1 || true
    if claude plugin list 2>/dev/null | grep -q 'machiya@machiya'; then claude plugin update machiya@machiya >/dev/null 2>&1 || true
    else claude plugin install machiya@machiya --scope user; fi
    settings add
    echo "installed for $(id -un)@$(hostname -s) as agent '$AGENT'. Running sessions pick it up when they restart (or /reload-plugins)."
    "$0" check || echo "check failed: is this machine on the tailnet, and allowed to reach the machiya-mcp service?" ;;
check)
    base=${URL%/mcp}
    curl -fsS -m 8 "$base/healthz" | python3 -c "import sys,json; d=json.load(sys.stdin); print('server %s, %d tools, rooms: %s' % (d['version'], d['tools'], ', '.join(d['rooms'])))"
    curl -fsS -m 15 -X POST "$URL" -H 'Content-Type: application/json' -H "X-Agent: $AGENT" \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"machiya_status","arguments":{}}}' |
        python3 -c "
import sys, json
r = json.load(sys.stdin)['result']['structuredContent']['rooms']
bad = [k for k, v in r.items() if not v.get('ok')]
print('rooms:', ', '.join('%s %s' % (k, 'ok' if v.get('ok') else 'DOWN') for k, v in r.items()))
sys.exit(1 if bad else 0)" ;;
uninstall)
    claude plugin uninstall machiya@machiya 2>/dev/null || true
    claude plugin marketplace remove machiya 2>/dev/null || true
    [[ -f $SETTINGS ]] && settings remove
    echo "removed" ;;
hister)
    claude mcp list 2>/dev/null | grep -q '^hister:' || claude mcp add --transport http --scope user hister "$HISTER_MCP"
    hister_rules add
    echo "Hister's MCP enabled (search, get_preview); get_history is denied. Restart the session, or /reload-plugins."
    curl -fsS -m 10 -X POST "$HISTER_MCP" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -c "import sys,json; print('hister tools:', ', '.join(t['name'] for t in json.load(sys.stdin)['result']['tools']))" ;;
hister-remove)
    claude mcp remove --scope user hister 2>/dev/null || true
    [[ -f $SETTINGS ]] && hister_rules remove
    echo "Hister's MCP removed" ;;
*) sed -n 2,16p "$0"; exit 2 ;;
esac
