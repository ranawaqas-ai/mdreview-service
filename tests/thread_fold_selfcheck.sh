#!/usr/bin/env bash
# thread_fold_selfcheck.sh - #387's runnable regression check for the comment rail's thread fold
# and per-entry clamp (web/app/viewer.html threadHtml() / applyClamps()).
#
# Provisions a throwaway local instance, seeds four threads over the local-tier comments API at
# the fold's exact boundaries (5 entries with long middle/tail/newest entries, 4 entries, 3
# entries, and a resolved 5-entry thread), then hands off to scripts/thread-fold-check.mjs, which
# samples the RENDERED outcome in headless Chrome. All assertions live there, named; this wrapper
# only provisions.
#
#   bash tests/thread_fold_selfcheck.sh    # exit 0 = pass
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
scratch="$here/.scratch/thread_fold_data"
rm -rf "$scratch"; mkdir -p "$scratch"
freeport(){ python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
port="$(freeport)"
cleanup(){ [ -n "${srv:-}" ] && kill "$srv" 2>/dev/null; return 0; }
trap cleanup EXIT

MDREVIEW_DATA="$scratch/d" PORT="$port" MDREVIEW_WEB_DIR="$here/web/app" \
  PYTHONPATH="$here/src" python3 -m mdreview >"$scratch/s.log" 2>&1 &
srv=$!
up=0
for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$port/healthz" && { up=1; break; }; sleep 0.25; done
[ "$up" = 1 ] || { echo "FAIL - server never came up on :$port"; sed -n '1,8p' "$scratch/s.log"; exit 1; }

ids=$(python3 - "$port" <<'PY'
import json
import sys
import urllib.request

port = sys.argv[1]
base = "http://127.0.0.1:%s" % port
# Empty ProxyHandler: an enabled-but-dead system proxy otherwise silently swallows loopback
# requests that curl serves fine (bit this project before).
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"}, method="POST")
    return json.load(opener.open(req, timeout=15))


md = "# Fixture\n\nBlock one.\n\nBlock two.\n\nBlock three.\n\nBlock four.\n"
rid = call("/api/reviews", {"markdown": md})["id"]

# ~150 words: well past the clamp cap plus its slack in a 284px card, so the clamp decision is
# not sitting on the boundary.
LONG = ("Closer. The word understanding is the real reason, and cutting the phrase was right. "
        "Not accepted yet, for one thing. Read your proposed sentence and count how many times "
        "the noun appears, then ask what the second one does to the reason. ") * 4
SHORT = "Short reply."


def make(block_num, thread):
    """thread: [(role, text), ...]. First entry creates the comment; the rest are replies."""
    role0, text0 = thread[0]
    cid = call("/api/reviews/%s/comments" % rid,
               {"anchor": {"block_num": str(block_num), "quoted_text": ""},
                "text": text0, "role": role0})["comment_id"]
    for role, text in thread[1:]:
        call("/api/reviews/%s/comments/%s/reply" % (rid, cid), {"text": text, "role": role})
    return cid


# A: 5 entries -> folds to root + 2 earlier replies + last two. idx1 long (hidden until unfold,
# then clampable), idx3 long (visible, clampable), idx4 long AND newest AND agent (never clamped,
# carries the Addressed badge).
a = make(1, [("reviewer", "Root: the issue."), ("agent", LONG), ("reviewer", SHORT),
             ("reviewer", LONG), ("agent", LONG)])
# B: 4 entries -> root + last two would hide ONE entry; never fold one, so renders in full.
b = make(2, [("reviewer", "Root."), ("agent", SHORT), ("reviewer", SHORT), ("agent", SHORT)])
# C: 3 entries -> nothing to fold.
c = make(3, [("reviewer", "Root."), ("agent", SHORT), ("reviewer", SHORT)])
# D: resolved thread -> same fold inside the Resolved panel's .rcard. Four entries plus the
# resolve justification, which the API appends as a fifth (agent) entry.
d = make(4, [("reviewer", "Root."), ("agent", SHORT), ("reviewer", SHORT), ("agent", SHORT)])
call("/api/reviews/%s/comments/%s/resolve" % (rid, d), {"justification": "Done."})

print(json.dumps({"rid": rid, "a": a, "b": b, "c": c, "d": d}))
PY
)
[ -n "$ids" ] || { echo "FAIL - could not seed the comment fixture"; exit 1; }
rid=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['rid'])" "$ids")

node "$here/scripts/thread-fold-check.mjs" "http://127.0.0.1:$port/review/$rid" "$ids"
