# MCP server

The **`mcp` package** (`src/mcp/`) is a thin, stdlib-only **MCP** server (stdio, JSON-RPC 2.0, spec
rev `2025-06-18`) that exposes the review API as first-class tools, so an MCP-speaking agent can call
it without hand-rolling HTTP. It wraps the running HTTP service and adds no state — the service is
unchanged. `src/mcp_server.py` is a thin entry point kept at the src/ root so the path below (and any
`.mcp.json` config pointing at it) keeps working; the canonical form is `python -m mcp`.

```bash
# point it at a running mdreview-service and run it over stdio (python -m mcp is equivalent)
MDREVIEW_BASE=http://localhost:8137 python3 src/mcp_server.py
# smoke it (stdlib only, no deps):
MDREVIEW_BASE=http://localhost:8137 python3 tests/mcp_smoke.py
```

Example MCP client config (stdio):

```json
{
  "mcpServers": {
    "mdreview": {
      "command": "/opt/homebrew/opt/python@3.12/bin/python3.12",
      "args": ["/path/to/mdreview-service/src/mcp_server.py"],
      "env": { "MDREVIEW_BASE": "http://localhost:8137" }
    }
  }
}
```

**For the hosted instance**, set `"MDREVIEW_BASE": "https://app.mdreview.space"` and add
`"MDREVIEW_TOKEN": "mdr_…"` (minted on the account page) to `env`. The account page generates
this exact block with the token already filled in, so you only correct the local `args` path.

Use an **absolute interpreter path**, not a bare `python3`: `python3` resolves to whatever is first on the client's PATH (often an old system Python), and a stale or misconfigured system HTTP proxy that such an interpreter honors can make every backend call fail with a bogus "connection refused". Point `args` at the file **inside your checkout** so a `git pull` keeps the wrapper current with no rebuild or re-publish; a change to the wrapper's own code still needs one client reconnect to load (a stdio server reads its code once at startup).

## Claude Code plugin

`.claude-plugin/marketplace.json` makes this repo a plugin marketplace with one plugin,
`plugins/mdreview` (#393). The plugin carries no wrapper code of its own. `plugins/mdreview/mcp_server.py`
and `plugins/mdreview/mcp` are symlinks into `src/`, and an install copies their targets into the
plugin cache, so the plugin always ships the same wrapper the installer does. The token is a
`userConfig` option marked sensitive (keychain, never settings.json). The plugin sets
`MDREVIEW_NO_AUTO_UPDATE=1` because its version is the update channel: bump `version` in
`plugins/mdreview/.claude-plugin/plugin.json` when the wrapper changes, or `/plugin update` will not
pick it up.

The plugin runs a bare `python3`, since it can't know an absolute path on the user's machine, so the
interpreter caveat above applies to it too. `/plugin marketplace add` reads the default branch, so the
plugin becomes installable once it reaches `main`.

Checks: `tests/plugin_manifest_selfcheck.py` runs in pr-checks. `claude plugin validate . --strict` and
`claude plugin validate plugins/mdreview --strict` need the CLI, so run them by hand when a manifest changes.

## MCP Registry

`server.json` at the repo root is the entry in the official MCP Registry
(https://registry.modelcontextprotocol.io), published as `io.github.ranawaqas-ai/mdreview` and
pointing at the remote endpoint `https://app.mdreview.space/mcp`. To republish after a release that
changes the remote surface, bump `version`, then:

```bash
mcp-publisher validate
mcp-publisher login github --token "$(gh auth token)"   # gh must be signed in as ranawaqas-ai
mcp-publisher publish
mcp-publisher logout
```

## Developing mdreview-service itself

The "point `args` at your checkout" advice above is for *using* the service, where you aren't
editing `src/`. If you're developing mdreview-service, don't wire your daily-driver `mdreview` /
`mdreview-hosted` / `mdreview-staging` aliases at the checkout: a mid-edit or syntax-broken
`src/mcp/*.py` becomes what those aliases run on their next reconnect, everywhere, not just in the
session doing the edit. Install a separate, non-checkout copy for daily use
(`curl -fsSL https://mdreview.space/install.sh | MDREVIEW_MODE=local sh`, which lands in
`~/.mdreview/mdreview-service` and self-updates independently of your working tree — see
[install.sh](https://mdreview.space/install.sh) and `src/mcp/update.py`), and register a separate
alias for the checkout you're actively changing:

```bash
make dev   # background instance on localhost:8138, data in .scratch/dev-data (gitignored)
claude mcp add mdreview-dev -e MDREVIEW_BASE=http://localhost:8138 -- python3 src/mcp_server.py
```

`mdreview-dev` is safe to break; the daily-driver aliases, wired to the installed copy, aren't
touched by anything you do in the checkout.

**Tools (20):** `create_review` (markdown, title?, project?, session?,
source_path?), `list_reviews`, `get_review` (id), `get_source` (id), `get_feedback` (id), `get_status` (id),
`update_source` (id, markdown), `get_history` (id, round?), `attach_asset` (id, name, path|content_b64),
`list_assets` (id), `delete_review` (id), `server_info`, `create_comment` (author a comment), the **comment** tools `list_comments` (document_id, status?=open), `get_comment` (document_id,
comment_id), `reply_to_comment` (document_id, comment_id, text), `resolve_comment` (document_id,
comment_id, justification?), `delete_comment` (document_id, comment_id — hard-remove a junk comment),
and the **turn-baton** tools `hand_back` (document_id, message, state?=done) and `ping_working` (document_id, owner, message?) — both map onto `POST /handoff`.
`document_id` is the review id.
There is **no `reopen` tool** — reopen is the reviewer's UI action (a convention on the no-auth
service, not an enforced boundary); after a reopen the agent sees the comment again via
`list_comments`. The agent workflow (`list_comments(status="open")` first; reply to discuss, resolve
when addressed; justification optional but recommended) is encoded in the tool descriptions. A failed
call (bad/expired id, service down) returns an `isError: true` result; an unknown tool name is a
JSON-RPC `-32602` error.

**Staleness.** A stdio MCP server loads its code + tool list once at process start; editing
`src/mcp_server.py` does nothing until the client **reconnects**. `server_info` reports the *running*
wrapper's `tools_hash`/version; a **human/CI** compares it to the on-disk `python3 src/mcp_server.py
--print-version` and reconnects on a mismatch (the server can signal its identity but cannot reload
itself; an HTTP/render change needs no reconnect — the wrapper just proxies — a change to
`src/mcp_server.py` itself does).

**Reachable `review_url`.** The wrapper relays the service's `review_url`, which the service
derives from the request `Host` header unless `MDREVIEW_PUBLIC_BASE` is set. So a human handed the
link can reach it, set `MDREVIEW_PUBLIC_BASE` on the **service** (not the wrapper) to a
host-reachable URL (e.g. `https://review.example.com`); otherwise the link may be an unreachable
`localhost`.

**Exposure.** `list_reviews` (like `GET /api/reviews` and the dashboard) returns every review with
no auth — fine for the trusted-network posture, but keep auth in front if exposed.

---

Moved out of `README.md` by #257. Commands, ports, paths and env vars are
byte-identical to what shipped there; nothing was corrected in the move.
