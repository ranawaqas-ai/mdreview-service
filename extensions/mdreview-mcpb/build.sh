#!/usr/bin/env bash
# Build the mdreview Claude Desktop extension (.mcpb). Output: .scratch/mcpb/mdreview-<version>.mcpb
# Uses the official CLI through npx (per-user cache, no global install).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STAGE="$ROOT/.scratch/mcpb/stage"
OUT_DIR="$ROOT/.scratch/mcpb"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$HERE/manifest.json")"
OUT="$OUT_DIR/mdreview-$VERSION.mcpb"
MCPB=(npx --yes @anthropic-ai/mcpb)

rm -rf "$STAGE"
mkdir -p "$STAGE/server"
cp "$HERE/manifest.json" "$HERE/icon.png" "$STAGE/"
[ -f "$HERE/PRIVACY.md" ] && cp "$HERE/PRIVACY.md" "$STAGE/"
cp "$ROOT/LICENSE" "$STAGE/"
# Real files only (cp -R copies symlink targets with -L), and no bytecode caches.
cp -L "$ROOT/src/mcp_server.py" "$STAGE/server/mcp_server.py"
cp -RL "$ROOT/src/mcp" "$STAGE/server/mcp"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete
if find "$STAGE" -type l | grep -q .; then echo "symlink in staging dir" >&2; exit 1; fi

"${MCPB[@]}" validate "$STAGE"
"${MCPB[@]}" pack "$STAGE" "$OUT"
echo "built $OUT"
