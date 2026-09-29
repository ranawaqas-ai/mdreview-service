"""mdreview-mcp: a thin stdio MCP server wrapping the mdreview-service HTTP API.

Stdlib only (no pip): newline-delimited JSON-RPC 2.0 over stdin/stdout. It holds a BASE
URL (MDREVIEW_BASE, default http://localhost:8137) and maps each MCP tool 1:1 onto an
existing HTTP endpoint. It adds NO state and does not change the HTTP service.

Protocol grounded against the MCP spec rev 2025-06-18 (lifecycle + tools).

  initialize                -> {protocolVersion, capabilities:{tools:{}}, serverInfo}
  notifications/initialized -> (no response)
  tools/list                -> {tools:[...20 schemas]}
  tools/call                -> {content:[{type:text,text}], isError?}   (dispatch: MR-016)

Split from the original single-file mcp_server.py: `tools` (the agent-visible schema surface +
identity/tools_hash), `client` (the HTTP client + tool->endpoint routing), `rpc` (the JSON-RPC
dispatch, shared with the service's own /mcp endpoint), and `__main__` (stdio framing + lifecycle). Run with `python -m mcp`; the legacy
`python3 src/mcp_server.py` path still works via a back-compat shim that re-exports main().

Comment workflow (MR-035): list_comments / get_comment / reply_to_comment / resolve_comment let an
agent act on a reviewer's threaded comments. Always list_comments(status="open") first and only
address what the reviewer raised; reply to discuss, resolve (justification optional but recommended)
once actually addressed. The agent never reopens — reopen is the reviewer's UI action; after one,
the comment reappears via list_comments. document_id == the review id.

Run:  MDREVIEW_BASE=http://localhost:8137 python -m mcp
      Set MDREVIEW_OPEN_BROWSER=1 to auto-open each new review_url in your default browser.
"""
