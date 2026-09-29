"""The agent-visible surface: tool schemas, the workflow INSTRUCTIONS, and this server's identity.

Static metadata only (no I/O): tools/list serves TOOLS, initialize serves SERVER_INFO + INSTRUCTIONS,
and _tools_hash() fingerprints the whole surface so a stale running server is detectable. The HTTP
side (mapping a tool call onto an endpoint) lives in client.py; the JSON-RPC plumbing in __main__.py.
"""
import json
import hashlib

PROTOCOL_VERSION = "2025-06-18"   # MCP spec revision this server targets
SERVER_INFO = {"name": "mdreview-mcp", "version": "0.1.0"}

# Surfaced to the agent on `initialize` (MCP `instructions`). Kept SHORT on purpose: a client loads
# this block per connection (a user with several mdreview aliases pays for it several times over,
# every turn), and Claude Code cuts it at ~2000 chars, so anything past that never reached an agent.
# The loop lives here; every rule that belongs to one tool lives in that tool's description, which
# is deferred and costs nothing until the tool is used.
INSTRUCTIONS = (
    "mdreview is human-in-the-loop review of a markdown document (or, with kind=\"latex\", a LaTeX "
    "paper). Loop: create_review -> hand the returned review_url to the human -> poll get_status "
    "(cheap) -> when it changes, start with list_comments(status=\"open\"), then get_feedback -> apply "
    "the edits with update_source (the human's page live-reloads) -> reply_to_comment / "
    "resolve_comment -> hand_back. get_source reads the current draft (e.g. when resuming a review). "
    "Author for the viewer's renderer: diagrams as ```mermaid blocks, math as $...$, images via "
    "attach_asset, never ASCII art or a plain fence. Operate only on reviews you "
    "created. If a tool is missing or misbehaves, read the server_info description: it says what "
    "that means and what to do. Each tool's description carries its own rules (LaTeX mode, "
    "templates, guarded saves, the turn baton)."
)

_ID = {"type": "string", "description": "the opaque review id"}
_DOCID = {"type": "string", "description": "the review id the comments belong to (the document_id)"}
_CID = {"type": "string", "description": "the comment id (cXXXXXXXXXX)"}

# The 20 tools (mostly 1:1 with the HTTP API; hand_back + ping_working both map onto POST /handoff).
# Static metadata served by tools/list.
TOOLS = [
    {
        "name": "create_review",
        "description": "Create a review from markdown; returns the id and the review and feedback urls. The "
                       "viewer renders GFM, Mermaid diagrams (```mermaid fenced blocks), LaTeX math ($...$ "
                       "inline, $$...$$ display), GFM footnotes, syntax-highlighted fenced code (label the "
                       "language) and images (attach them with attach_asset). A flow, decision tree, state "
                       "machine or architecture reads best as a ```mermaid diagram, since a plain code fence "
                       "renders as monospace text. Optional project, session and source_path record where "
                       "the draft came from, for the dashboard. kind=\"latex\" (default \"markdown\") makes a "
                       "research-paper review instead: `markdown` then carries raw LaTeX (a single .tex "
                       "document), shown in a split viewer with a live server-compiled PDF, and the markdown "
                       "rules do not apply. If the content is LaTeX (a .tex source_path, or a body with "
                       "\\documentclass or \\begin{document}), set kind=\"latex\"; a latex-enabled server "
                       "rejects the create otherwise. kind cannot change later. Recreating a review in the "
                       "other format makes a new review with a new id and url, and its comments and history "
                       "do not carry over.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "markdown": {"type": "string", "description": "the document to review (raw LaTeX when kind=latex)"},
                "title": {"type": "string"},
                "project": {"type": "string"},
                "session": {"type": "string"},
                "source_path": {"type": "string"},
                "kind": {"type": "string", "enum": ["markdown", "latex"],
                         "description": "review kind; default markdown. latex = an Overleaf-style paper "
                                        "review. IMMUTABLE: you cannot change a review's kind later; "
                                        "re-creating a review in the other format makes a NEW, separate "
                                        "review with re-authored content (comments/history do not carry "
                                        "over). Warn the human and offer to link the two."},
                "template": {"type": "string",
                             "description": "for a latex review, start from a named template instead "
                                            "of a blank .tex: it seeds the source (unless you also pass "
                                            "markdown) and supplies the document class/style. Bundled: "
                                            "ieee, acm, arxiv, lncs, elsevier (CTAN classes). "
                                            "Download-on-miss (fetched + cached on first use): acl, "
                                            "iclr2026, and more. GET /api/latex/templates lists the "
                                            "current ids; an unknown id returns 400 with the list. A "
                                            "template only applies at creation of a latex review; there "
                                            "is no re-template of an existing review (see the kind "
                                            "caveat: re-creating in another format is a new review)."},
            },
            "required": ["markdown"],
        },
    },
    {
        "name": "list_reviews",
        "description": "List every review with its status (awaiting/feedback/resolved), note counts, and revision.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_review",
        "description": "Get one review's metadata.",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "get_source",
        "description": "Get a review's current source — the draft you edit (markdown, or raw LaTeX for a "
                       "kind=latex review). Read it before applying feedback when you didn't keep the "
                       "draft in memory (e.g. a resumed session). By default the result is the raw "
                       "document verbatim, exactly as before. Pass with_revision=true to instead get a "
                       "JSON envelope {\"source\": ..., \"revision\": N}: the revision is the "
                       "optimistic-concurrency token for update_source's expected_revision, issued "
                       "atomically with this read (same response), so use THIS — never a separate "
                       "get_status poll — as the token source when you plan a guarded save.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _ID,
                "with_revision": {"type": "boolean",
                                  "description": "opt-in: return {\"source\", \"revision\"} JSON instead "
                                                 "of the raw document; the revision pairs with "
                                                 "update_source's expected_revision. Default false = "
                                                 "raw document, unchanged."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_feedback",
        "description": "Get a review's human feedback: structured notes (now including a projection of "
                       "the comments) plus the rendered markdown block. For the full threads use list_comments.",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "get_status",
        "description": "Cheap poll: a review's source_updated, feedback_updated, and comments_updated "
                       "timestamps. Watch comments_updated for new/changed comment threads. Also carries "
                       "revision and can_edit (#288) — but for a guarded update_source take the revision "
                       "from get_source(with_revision=true), the read that gave you the text, never from "
                       "this poll. source_updated_by (#289) names who authored the current draft: "
                       "\"reviewer\" means the human edited the document since your last write, so "
                       "re-read the source before saving; \"agent\" (the default) means the last write "
                       "was yours.",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "update_source",
        "description": "Push a revised draft (applied edits). Snapshots a history round and live-reloads the "
                       "human's page. A markdown review is rendered as in create_review (Mermaid, math, "
                       "highlighted code). For a kind=latex review the draft is raw LaTeX (a single .tex "
                       "document) and the server recompiles it to PDF on each push. The human can edit the "
                       "document too, so save with a guard: read via get_source(with_revision=true) and pass "
                       "that revision as expected_revision. A stale revision fails with HTTP 409 and writes "
                       "nothing. On a 409, re-read the source, re-apply your change onto the new text and "
                       "save with the fresh revision; never resend the earlier draft, which would silently "
                       "overwrite the human's edit. Omitting expected_revision is an unconditional write.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _ID,
                "markdown": {"type": "string", "description": "the new draft"},
                "expected_revision": {"type": "integer",
                                      "description": "optional optimistic-concurrency guard: the revision "
                                                     "your draft is based on, taken from "
                                                     "get_source(with_revision=true) — the same read that "
                                                     "gave you the text, never a separate status poll. "
                                                     "409 = stale: re-read, re-apply onto the new text, "
                                                     "retry with the new revision; never resend the old "
                                                     "draft. Omit for an unconditional write."},
            },
            "required": ["id", "markdown"],
        },
    },
    {
        "name": "get_history",
        "description": "List a review's past rounds; with `round`, fetch one past draft plus the feedback it received.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": _ID, "round": {"type": "integer", "description": "round number; omit for the list"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_git_url",
        "description": "Returns a git clone URL for a review's history: one commit per past round plus a "
                       "live tip commit, so git log, git diff and git blame work with standard tools. Only "
                       "available when the server has git-tracked history enabled; otherwise the call "
                       "returns 404. Read-only: no push, no branching.",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "delete_review",
        "description": "Delete a review and its data.",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "attach_asset",
        "description": "Attach an image to a review so the viewer serves and renders it. Pass the file bytes "
                       "as `content_b64` (base64) and `name` as the exact src the draft uses (for example "
                       "\"/assets/x.png\" or \"fig/y.svg\"). Attach once; it survives every update_source "
                       "revision. On the local stdio server you can pass `path` instead, a local file path "
                       "the wrapper reads and encodes for you, which keeps large files out of the "
                       "conversation. A remote server cannot read your files, so it needs `content_b64`. "
                       "Returns the stored name and the served url.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": _ID,
                "name": {"type": "string", "description": "the draft <img> src this asset backs (the match key)"},
                "path": {"type": "string", "description": "local file path, read and base64-encoded by the local stdio server (not available on a remote server)"},
                "content_b64": {"type": "string", "description": "the file bytes, base64-encoded (required on a remote server)"},
            },
            "required": ["id", "name"],
        },
    },
    {
        "name": "list_assets",
        "description": "List a review's attached assets (name, stored name, served url, bytes, ctype).",
        "inputSchema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]},
    },
    {
        "name": "create_comment",
        "description": "Author a NEW comment on a document, anchored to a quoted phrase — use this to "
                       "leave review feedback at a specific spot (you act as a reviewer). Pass "
                       "`quoted_text` = the exact phrase from the source to anchor to (the viewer "
                       "highlights it wherever it occurs) and `text` = your comment. Omit `quoted_text` "
                       "for a document-level (unanchored) note. Attributed to `role` (default `agent`). "
                       "After this, the reviewer (or you) can reply/resolve it like any thread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": _DOCID,
                "quoted_text": {"type": "string", "description": "exact phrase to anchor + highlight (omit for a doc-level note)"},
                "text": {"type": "string", "description": "the comment body"},
                "role": {"type": "string", "enum": ["agent", "reviewer"],
                         "description": "attribution; default agent"},
            },
            "required": ["document_id", "text"],
        },
    },
    {
        "name": "list_comments",
        "description": "List comments on a document, filtered by `status`: open (default), resolved, "
                       "reopened or all. Reply to a comment to discuss it; resolve it once it is addressed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": _DOCID,
                "status": {"type": "string", "enum": ["open", "resolved", "reopened", "all"],
                           "description": "filter; default open"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "get_comment",
        "description": "Fetch one comment thread in full — every reply plus the status_history "
                       "(the open -> resolved -> reopened transitions).",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": _DOCID, "comment_id": _CID},
            "required": ["document_id", "comment_id"],
        },
    },
    {
        "name": "reply_to_comment",
        "description": "Add a reply to a comment WITHOUT resolving it. Use this when a comment is a "
                       "question or needs discussion, or to respond before you decide — resolve only "
                       "once the issue is actually addressed.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": _DOCID, "comment_id": _CID,
                           "text": {"type": "string", "description": "your reply"}},
            "required": ["document_id", "comment_id", "text"],
        },
    },
    {
        "name": "resolve_comment",
        "description": "Mark a comment resolved once you've actually addressed it. `justification` is "
                       "OPTIONAL — provide a short note (appended to the thread, attributed to you, the "
                       "agent) to explain what you did, or omit to resolve silently; a clear note is "
                       "recommended because the reviewer can REOPEN the thread, so it reduces "
                       "back-and-forth. On resolve the comment leaves the active document and moves to "
                       "the Resolved panel. You never reopen — after a reviewer reopens, you'll see the "
                       "comment again via list_comments (status reopened/open) and can reply or resolve "
                       "again.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": _DOCID, "comment_id": _CID,
                           "justification": {"type": "string",
                                             "description": "optional note explaining the resolution"}},
            "required": ["document_id", "comment_id"],
        },
    },
    {
        "name": "delete_comment",
        "description": "Permanently deletes a comment and its whole thread. This cannot be undone. Use it to "
                       "remove a comment created by mistake; resolve_comment marks a real comment addressed "
                       "and keeps it in the Resolved panel.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": _DOCID, "comment_id": _CID},
            "required": ["document_id", "comment_id"],
        },
    },
    {
        "name": "hand_back",
        "description": "Hand the turn back to the reviewer. Sets turn=reviewer on the review, so the "
                       "viewer's banner reads 'Agent updated the draft, your turn' (state=done, the default) "
                       "or 'Agent needs you' (state=blocked, used when you replied with a question instead "
                       "of finishing); `message` is shown in that banner. Use it after update_source and the "
                       "replies or resolves on the comments you addressed, or when blocked; for blocked, "
                       "pair it with a comment reply that asks the question. Reopening a comment is a "
                       "reviewer action in the viewer, not an agent action. Not available on a kind=latex "
                       "review, which has no turn baton.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": _DOCID,
                "message": {"type": "string",
                            "description": "one-line summary shown in the reviewer's banner (e.g. 'addressed 3 comments, 1 question')"},
                "state": {"type": "string", "enum": ["done", "blocked"],
                          "description": "done (default) when finished; blocked when you need the reviewer"},
            },
            "required": ["document_id", "message"],
        },
    },
    {
        "name": "ping_working",
        "description": "Claim or renew your lease on a review while you hold the turn. Find work by "
                       "polling list_reviews / get_status for reviews you own with turn=='agent'; on "
                       "one, call ping_working right away and then periodically while you work, so the "
                       "viewer shows 'Agent is working…' instead of a stale 'Agent may have stopped' "
                       "hint. `owner` is YOUR opaque session id; a review already leased by a DIFFERENT "
                       "owner returns an error (HTTP 409) — back off and skip it (another agent holds "
                       "it). Does NOT change whose turn it is. Not available on a kind=latex review: "
                       "there is no turn baton in latex mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": _DOCID,
                "owner": {"type": "string",
                          "description": "your opaque agent/session id; identifies who holds the lease"},
                "message": {"type": "string", "description": "optional short status shown in the banner"},
            },
            "required": ["document_id", "owner"],
        },
    },
    {
        "name": "server_info",
        "description": "Reports this running MCP server's name, version, protocol_version, tools_hash, "
                       "tool_count and tool_names. It describes only the running process and makes no call "
                       "to the service. A human or CI compares tools_hash to `python3 mcp_server.py "
                       "--print-version` on disk; if they differ, the running process is stale and the MCP "
                       "client should be reconnected to load the new code. An MCP-only agent has no on-disk "
                       "value to compare with, so it cannot make that check itself.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# MCP tool annotations (#394). Clients use them to decide what needs the user's confirmation, and
# the Connectors Directory requires them on every tool. openWorldHint is false throughout: every
# tool touches only the caller's own mdreview data. update_source is marked destructive although
# history keeps the prior round, because it replaces the draft the human is looking at.
_READ, _WRITE, _DESTRUCTIVE = (
    {"readOnlyHint": True, "openWorldHint": False},
    {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
)
_ANNOTATIONS = {
    "list_reviews": ("List reviews", _READ),
    "get_review": ("Get review", _READ),
    "get_source": ("Get review source", _READ),
    "get_feedback": ("Get review feedback", _READ),
    "get_status": ("Get review status", _READ),
    "get_history": ("Get review history", _READ),
    "get_git_url": ("Get review git URL", _READ),
    "list_assets": ("List review assets", _READ),
    "list_comments": ("List comments", _READ),
    "get_comment": ("Get comment", _READ),
    "server_info": ("Server info", _READ),
    "create_review": ("Create review", _WRITE),
    "attach_asset": ("Attach asset", _WRITE),
    "create_comment": ("Create comment", _WRITE),
    "reply_to_comment": ("Reply to comment", _WRITE),
    "resolve_comment": ("Resolve comment", _WRITE),
    "hand_back": ("Hand review back", _WRITE),
    "ping_working": ("Signal agent is working", _WRITE),
    "update_source": ("Update review source", _DESTRUCTIVE),
    "delete_review": ("Delete review", _DESTRUCTIVE),
    "delete_comment": ("Delete comment", _DESTRUCTIVE),
}
for _tool in TOOLS:
    _title, _hints = _ANNOTATIONS[_tool["name"]]   # KeyError here = a new tool with no annotation
    _tool["title"] = _title
    _tool["annotations"] = dict(_hints, title=_title)


def _tools_hash():
    """sha256 (12 hex) over the agent-visible surface: the TOOLS schema + INSTRUCTIONS. Changes
    automatically whenever a tool or the workflow text changes, so it can't silently drift from a
    hand-bumped `version` string. One canonical input — every surface that reports a hash uses this."""
    canon = json.dumps(TOOLS, sort_keys=True) + INSTRUCTIONS
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


SERVER_INFO["tools_hash"] = _tools_hash()   # surfaced in initialize's serverInfo + the server_info tool

TOOL_NAMES = {t["name"] for t in TOOLS}


def _server_info():
    """The running wrapper's own identity (no HTTP call — reports THIS process, not the service)."""
    return {
        "name": SERVER_INFO["name"],
        "version": SERVER_INFO["version"],
        "protocol_version": PROTOCOL_VERSION,
        "tools_hash": SERVER_INFO["tools_hash"],
        "tool_count": len(TOOLS),
        "tool_names": sorted(t["name"] for t in TOOLS),
    }
