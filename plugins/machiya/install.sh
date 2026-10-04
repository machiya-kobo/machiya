#!/usr/bin/env bash
# install.sh: install the machiya plugin (two MCP connections, Machiya's and Hister's, plus the cross-room skills) for the
# current user.
#   install.sh [install]   add this repo as a marketplace, install machiya@machiya at user scope, set MACHIYA_AGENT (and
#                          MACHIYA_MCP_URL / HISTER_MCP_URL when given), and add the permission rules to
#                          ~/.claude/settings.json: reads and the small writes allowed, the other writes ask, and Hister's
#                          get_history DENIED under every name it can appear as (browsing history stays out of AI context).
#                          It also moves a machine off the separate `hister` server that `install.sh hister` added before
#                          0.2.0. Idempotent: run it again after an update.
#   install.sh check       are both servers up, and does a real call through your tailnet identity work?
#   install.sh uninstall   remove the plugin, the marketplace, the rules and the env settings. The get_history deny stays.
#   install.sh hister-remove   only the move off the older separate `hister` server (`hister` does the same).
# Environment: MACHIYA_MCP_URL (default https://machiya-mcp.example.ts.net/mcp), HISTER_MCP_URL (default
# https://hister.example.ts.net/mcp), MACHIYA_REPO (this checkout; default: the one this script is in), MACHIYA_AGENT_NAME
# (what the board's history says; default user@host). The two URLs, when set, are saved in the settings' env, where the
# plugin's .mcp.json finds them. Needs claude, python3, and a client on the tailnet. ~/.claude/settings.json is backed up
# to settings.json.bak-machiya before each change.
set -euo pipefail
REPO=${MACHIYA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
AGENT=${MACHIYA_AGENT_NAME:-$(id -un)@$(hostname -s)}
URL=${MACHIYA_MCP_URL:-https://machiya-mcp.example.ts.net/mcp}
HISTER_MCP=${HISTER_MCP_URL:-https://hister.example.ts.net/mcp}
SETTINGS=$HOME/.claude/settings.json
P=mcp__plugin_machiya_machiya__
command -v claude >/dev/null || { echo "install.sh: claude not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "install.sh: python3 not found" >&2; exit 1; }

settings() {   # settings add|remove
    [[ -f $SETTINGS ]] || echo '{}' > "$SETTINGS"
    cp "$SETTINGS" "$SETTINGS.bak-machiya"
    python3 - "$1" "$SETTINGS" "$P" "$AGENT" "${MACHIYA_MCP_URL:-}" "${HISTER_MCP_URL:-}" <<'PY'
import json, sys
mode, path, prefix, agent, mcp_url, hister_url = sys.argv[1:7]
H = "mcp__plugin_machiya_hister__"
d = json.load(open(path))
allow = [prefix + n for n in ["board_list_cards", "board_get_card", "board_review", "board_roundup", "notes_*", "collections_list",
         "collections_audit", "pages_labels", "garden_candidates", "machiya_*", "board_set_next", "board_log", "board_claim",
         "board_release"]] + [H + "search", H + "get_preview"]
ask = [prefix + n for n in ["board_add_backlog", "board_move", "board_block", "board_set_priority", "board_set_stream", "board_set_goal",
       "board_set_due", "board_set_dependencies", "board_tag", "garden_suggest", "notes_create", "notes_update", "pages_set_label",
       "pages_relabel", "collections_set", "collections_remove"]]
# Hister's history (visits and opened results) never reaches the model: denied as the plugin's tool and as a separately
# added server's. A deny rule removes the tool from the model's view. It is never removed here, not even by uninstall.
deny = [H + "get_history", "mcp__hister__get_history"]
# rules for tools that are gone: machiya-mcp's page reads (0.7.0; Hister's MCP has them) and the older separate `hister`
# server's allow rules
stale = [prefix + "pages_search", prefix + "pages_read", "mcp__hister__search", "mcp__hister__get_preview"]
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
    for k, v in (("MACHIYA_MCP_URL", mcp_url), ("HISTER_MCP_URL", hister_url)):
        if v:
            env[k] = v
else:
    for k in ("MACHIYA_AGENT", "MACHIYA_MCP_URL", "HISTER_MCP_URL"):
        env.pop(k, None)
json.dump(d, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
}

old_hister() {   # the separate user-scope `hister` server that `install.sh hister` added before 0.2.0: the plugin brings it now
    if claude mcp remove --scope user hister >/dev/null 2>&1; then
        echo "removed the older separate 'hister' MCP server (the plugin brings Hister's MCP now)"
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
    "$0" check || echo "check failed: is this machine on the tailnet, and allowed to reach the machiya-mcp and hister services?" ;;
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
sys.exit(1 if bad else 0)"
    curl -fsS -m 10 -X POST "$HISTER_MCP" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' |
        python3 -c "import sys,json; print('hister tools:', ', '.join(t['name'] for t in json.load(sys.stdin)['result']['tools']), '(get_history denied)')" ;;
uninstall)
    claude plugin uninstall machiya@machiya 2>/dev/null || true
    claude plugin marketplace remove machiya 2>/dev/null || true
    [[ -f $SETTINGS ]] && settings remove
    echo "removed (the get_history deny rules stay)" ;;
hister|hister-remove)
    old_hister
    [[ -f $SETTINGS ]] && settings add
    echo "Hister's MCP comes with the plugin now (mcp__plugin_machiya_hister__*); get_history is denied." ;;
*) sed -n 2,17p "$0"; exit 2 ;;
esac
