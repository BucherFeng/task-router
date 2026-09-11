#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$REPO_ROOT/plugins/task-router"
PLUGIN_DEST="$HOME/plugins/task-router"
MARKETPLACE="$HOME/.agents/plugins/marketplace.json"
PLUGIN_NAME="task-router"

if [ ! -d "$PLUGIN_SRC" ]; then
    echo "error: $PLUGIN_SRC not found; run this script from the repository root" >&2
    exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
    echo "error: codex CLI not found in PATH" >&2
    exit 1
fi

echo "==> copying plugin to $PLUGIN_DEST"
mkdir -p "$HOME/plugins"
rm -rf "$PLUGIN_DEST"
cp -R "$PLUGIN_SRC" "$PLUGIN_DEST"

echo "==> registering $PLUGIN_NAME in $MARKETPLACE"
mkdir -p "$(dirname "$MARKETPLACE")"
MARKETPLACE_NAME=$(python3 - "$MARKETPLACE" "$PLUGIN_NAME" <<'PYEOF'
import json
import sys

path, plugin_name = sys.argv[1], sys.argv[2]
entry = {
    "name": plugin_name,
    "source": {"source": "local", "path": "./plugins/" + plugin_name},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except FileNotFoundError:
    data = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

plugins = [p for p in data.get("plugins", []) if p.get("name") != plugin_name]
plugins.append(entry)
data["plugins"] = plugins
data.setdefault("interface", {"displayName": "Personal"})

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")

print(data.get("name", "personal"))
PYEOF
)

echo "==> installing $PLUGIN_NAME from marketplace '$MARKETPLACE_NAME'"
codex plugin add "$PLUGIN_NAME@$MARKETPLACE_NAME"

echo
echo "Done. Start a NEW Codex thread to pick up the plugin."
echo "Check that routing.json lists model ids that exist in your /model catalog;"
echo "if not, edit ~/plugins/task-router/skills/task-router/routing.json."
