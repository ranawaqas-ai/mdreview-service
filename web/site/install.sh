#!/bin/sh
# mdreview installer.  One command:
#
#   curl -fsSL https://mdreview.space/install.sh | sh
#
# It asks how you want to run mdreview:
#   Local   - run it yourself, open to everyone, no account. Starts a server on localhost:8137
#             and wires Claude Code to it. No Docker.
#   Hosted  - connect Claude Code to the managed instance at app.mdreview.space (sign in with any
#             email; needs a token minted on the Account page).
#
# Non-interactive: set MDREVIEW_MODE=local|hosted (and MDREVIEW_TOKEN=mdr_... for hosted).
set -eu

HOSTED="https://app.mdreview.space"
TARBALL="https://github.com/ranawaqas-ai/mdreview-service/archive/refs/heads/main.tar.gz"
HOME_DIR="$HOME/.mdreview"
DEST="$HOME_DIR/mdreview-service"
PORT="${MDREVIEW_PORT:-8137}"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- prerequisites (both modes need python3 to run the wrapper + the claude CLI to wire it) ---
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3, then re-run."
command -v claude  >/dev/null 2>&1 || die "the 'claude' CLI (Claude Code) is not on PATH. Install Claude Code first."
command -v curl    >/dev/null 2>&1 || die "curl not found."
command -v tar     >/dev/null 2>&1 || die "tar not found."
PY="$(python3 -c 'import sys; print(sys.executable)')"
[ -n "$PY" ] || die "could not resolve the python3 interpreter path."

# --- choose mode (env wins; else prompt on the terminal, since stdin is the piped script) ---
MODE="${MDREVIEW_MODE:-}"
if [ -z "$MODE" ]; then
  if ( : < /dev/tty ) 2>/dev/null; then
    say "How do you want to run mdreview?"
    say "  1) Local   run it yourself, open to everyone, no account (a server on localhost:$PORT)"
    say "  2) Hosted  connect to $HOSTED (sign in with any email, needs a token)"
    printf 'Choose [1/2]: ' > /dev/tty
    IFS= read -r ans < /dev/tty 2>/dev/null || ans=""
    case "$ans" in
      1|local|Local|L|l) MODE=local ;;
      2|hosted|Hosted|H|h) MODE=hosted ;;
      *) die "unrecognized choice: '$ans' (expected 1 or 2)." ;;
    esac
  else
    die "no terminal to prompt. Re-run with MDREVIEW_MODE=local (or =hosted MDREVIEW_TOKEN=mdr_...)."
  fi
fi
case "$MODE" in local|hosted) ;; *) die "invalid MDREVIEW_MODE '$MODE' (use 'local' or 'hosted')." ;; esac

# --- fetch the code (both modes need it on disk) ---
say "Downloading mdreview..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$TARBALL" -o "$TMP/repo.tgz" || die "download failed ($TARBALL)."
tar -xzf "$TMP/repo.tgz" -C "$TMP" || die "extract failed."
set -- "$TMP"/mdreview-service-*
SRC="$1"
[ -f "$SRC/src/mcp_server.py" ] || die "unexpected archive layout (no src/mcp_server.py)."
mkdir -p "$HOME_DIR"
rm -rf "$DEST"
mv "$SRC" "$DEST"
# Marker that this is an installer-managed tree: the wrapper self-updates from its server only when
# present (a repo/dev checkout has none), so #90's auto-update never clobbers a working tree.
: > "$DEST/.managed"
WRAPPER="$DEST/src/mcp_server.py"
"$PY" "$WRAPPER" --print-version >/dev/null 2>&1 || die "the wrapper failed to run under $PY."

wire_agent() {   # $1 = MDREVIEW_BASE ; extra -e args in $2.. (already split)
  base="$1"; shift
  claude mcp remove mdreview -s user >/dev/null 2>&1 || true
  claude mcp add mdreview -s user -e "MDREVIEW_BASE=$base" "$@" -- "$PY" "$WRAPPER"
}

if [ "$MODE" = "local" ]; then
  DATA="$HOME_DIR/data"; mkdir -p "$DATA"
  # ponytail: the local server is a plain background process (no supervisor, no restart-on-boot).
  # Re-run this installer (or the printed command) to restart it. Docker would give restart:always;
  # this trades that for "no Docker".
  if [ -f "$HOME_DIR/server.pid" ] && kill -0 "$(cat "$HOME_DIR/server.pid" 2>/dev/null)" 2>/dev/null; then
    kill "$(cat "$HOME_DIR/server.pid")" 2>/dev/null || true; sleep 0.5
  fi
  PYTHONPATH="$DEST/src" MDREVIEW_DATA="$DATA" PORT="$PORT" nohup "$PY" -m mdreview \
    > "$HOME_DIR/server.log" 2>&1 &
  echo $! > "$HOME_DIR/server.pid"
  # wait for it to answer
  i=0; until curl -fsS -o /dev/null "http://localhost:$PORT/healthz" 2>/dev/null; do
    i=$((i+1)); [ "$i" -gt 20 ] && die "server did not start; see $HOME_DIR/server.log"; sleep 0.25
  done
  wire_agent "http://localhost:$PORT"   # local is no-auth: no token
  say ""
  say "Local mdreview is running at http://localhost:$PORT  (PID $(cat "$HOME_DIR/server.pid"))."
  say "Restart Claude Code, then create_review(...) opens the review in your browser there."
  say "Stop:    kill \$(cat $HOME_DIR/server.pid)"
  say "Restart: curl -fsSL https://mdreview.space/install.sh | MDREVIEW_MODE=local sh"
else
  TOKEN="${MDREVIEW_TOKEN:-}"
  if [ -z "$TOKEN" ] && ( : < /dev/tty ) 2>/dev/null; then
    printf 'Paste your token from %s (Account page): ' "$HOSTED" > /dev/tty
    stty -echo < /dev/tty 2>/dev/null || true
    IFS= read -r TOKEN < /dev/tty 2>/dev/null || TOKEN=""
    stty echo < /dev/tty 2>/dev/null || true
    printf '\n' > /dev/tty 2>/dev/null || true
  fi
  case "$TOKEN" in
    mdr_*_*) : ;;
    *) die "that doesn't look like a token (expected mdr_<id>_<secret>). Mint one at $HOSTED." ;;
  esac
  wire_agent "$HOSTED" -e "MDREVIEW_TOKEN=$TOKEN"
  say ""
  say "Connected to hosted mdreview ($HOSTED). Quit and reopen Claude Code."
fi
