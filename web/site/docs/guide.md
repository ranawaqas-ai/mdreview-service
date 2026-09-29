# Guide

## Rich content: author to the renderer

The viewer renders the way a Jekyll/MathJax site publishes, so author your draft to it directly
instead of dropping in ASCII art or screenshots.

- **Math** (KaTeX): inline `$…$` / `\(…\)`, display `$$…$$` / `\[…\]`. A lone or currency `$` in
  prose (`$5 and $10`) stays literal, as does `$` inside code. Nothing to enable — just POST the
  markdown.
- **Diagrams**: a flow / state machine / architecture belongs in a ` ```mermaid ` fenced block, not
  ASCII art or a plain fence (which renders as monospace text).
- **Code**: label the fence (` ```python `) for syntax highlighting; unlabelled code is
  best-effort auto-detected.
- **Footnotes**: GFM `[^id]` refs plus `[^id]: …` definitions render as superscript links to an
  ordered back-reference section.

### Images

The service has your document, not your asset directory, so a relative or site-root `<img src>`
won't load on its own. **Attach the bytes once** and the viewer serves and repoints the `<img>`
for you — the attachment survives every `PUT /source`:

```bash
b64=$(base64 -i fig/plot.png | tr -d '\n')
curl -s -X POST "$BASE/api/reviews/<id>/assets" -H 'Content-Type: application/json' \
  -d "{\"name\":\"fig/plot.png\",\"content_b64\":\"$b64\"}"
```

Key the attachment by the **exact `src`** the draft uses (full path or a unique basename); the
viewer matches by full src then basename. Over MCP, pass `path=` instead and the wrapper encodes
the file itself, so the bytes never pass through your context. Absolute `http(s)` and `data:` URIs
already work as-is. Author figures **light-background-first** — they're matted on a light surface so
a light figure stays legible on a dark review pane.

## Threaded comments

Comments are the primary feedback surface: a reviewer highlights text and a thread opens in the
margin. They live server-side (shared by the viewer and MCP) with an `open → resolved → reopened`
machine the service enforces. Your loop:

```bash
# 1. List what the reviewer raised (default open).
curl -s "$BASE/api/reviews/<id>/comments?status=open"

# 2. Reply to discuss WITHOUT resolving (a question, a clarification):
curl -s -X POST "$BASE/api/reviews/<id>/comments/<cid>/reply" \
  -H 'Content-Type: application/json' -d '{"text":"Do you mean X or Y?","role":"agent"}'

# 3. Resolve once you've actually addressed it (justification optional but recommended):
curl -s -X POST "$BASE/api/reviews/<id>/comments/<cid>/resolve" \
  -H 'Content-Type: application/json' -d '{"justification":"Fixed in the next revision."}'
```

Always list open comments first and only address what the reviewer raised. **You never reopen** —
reopen is the reviewer's action. Roles `reviewer`/`agent` are attribution, not auth. Poll
`comments_updated` on `/status` to know when threads changed.

## The turn baton

A review is a back-and-forth workspace, backed by a `turn` baton (`reviewer` | `agent`) and an
agent lease, both on `GET /status`. Your side of the loop:

1. **Find work.** Poll for reviews with `turn == "agent"` — the human handed you that one with the
   viewer's **"Send to agent"** button.
2. **Claim the lease.** Call the handoff endpoint with `{state:"working", owner:"<your id>"}` right
   away and periodically while you work, so the viewer shows *"Agent is working…"* and a live
   progress timeline instead of a stale hint. A review already leased by a different, live owner
   returns **409** — back off and skip it (one agent per review).
3. **Act, then hand back.** The open comments are the instruction — read them, edit,
   `PUT /source`, reply/resolve the comments you addressed, then hand back with
   `{to:"reviewer", state:"done", message:"…"}`. The turn returns to the human with your one-line
   summary. If you're blocked, hand back with `state:"blocked"` **and** leave a comment reply with
   the question.

The human can **take the turn back** at any moment, so don't assume you still hold it across a long
job — re-check `turn` and keep renewing the lease.

## MCP server (optional)

The [`mcp` package](https://github.com/ranawaqas-ai/mdreview-service#mcp-server-optional) is a
thin, stdlib-only stdio MCP server that exposes the API as first-class tools (`create_review`,
`list_reviews`, `update_source`, the comment tools, and the turn-baton tools `hand_back` /
`ping_working`), so an MCP-speaking agent calls it without hand-rolling HTTP:

```bash
MDREVIEW_BASE=http://localhost:8137 python -m mcp     # or: python3 src/mcp_server.py
```

It wraps the running service and adds no state. Set `MDREVIEW_PUBLIC_BASE` on the service so a
relayed `review_url` is reachable. **Note:** a stdio MCP server loads its tool list once at start —
if a tool you expect is missing, the running server is stale and the client needs to **reconnect**
(see [Troubleshooting](#/troubleshooting)).

## Self-hosting

Each container is independent; point your `BASE` at wherever it runs.

```bash
make up                                   # localhost:8137
# or pick another host port:
docker run -d -p 9000:8080 -v my-mdreview:/data mdreview-service
```

| Var | Default | Meaning |
|-----|---------|---------|
| `PORT` | `8080` | in-container listen port |
| `MDREVIEW_DATA` | `/data` | storage dir (mount a volume) |
| `MDREVIEW_PUBLIC_BASE` | empty | if set, `review_url` uses it; otherwise the request Host header |

The service has **no auth** — if you expose it beyond localhost, put a proxy/token in front; the
`id` namespace is the only isolation between reviews. See the
[full config and API](https://github.com/ranawaqas-ai/mdreview-service#api) in the README.
