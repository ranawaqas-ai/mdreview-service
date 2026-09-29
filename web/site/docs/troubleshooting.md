# Troubleshooting

Each entry is a **symptom → fix**.

## Images don't load in the viewer

**Symptom:** an `<img>` in your draft shows as broken; the src is relative (`../img/y.svg`),
site-root (`/assets/x.png`), or a bare name.

**Fix:** the service has your document, not your asset directory. Attach the bytes once with
`POST /api/reviews/<id>/assets` (or `attach_asset(path=…)` over MCP), keyed by the **exact src** the
draft uses. The viewer then serves and repoints the image, and the attachment survives every
`PUT /source`. Absolute `http(s)` and `data:` URIs already work without attaching. See
[Images](#/guide) in the Guide.

## An MCP tool I expect is missing

**Symptom:** a tool like `hand_back` or `ping_working` isn't listed, or calls fail after you edited
`mcp_server.py`.

**Fix:** a stdio MCP server loads its code and tool list **once at process start** — editing the
server file does nothing until the client **reconnects**. Reconnect the MCP client. (A pure
render/HTTP change on the service needs no reconnect; only changes to the MCP server code do.) If
the server is configured with a path that moved, update the config to point at `src/mcp_server.py`
and reconnect.

## 409 when claiming the lease

**Symptom:** claiming the working lease (`{state:"working", owner:…}`) returns **409 Conflict**.

**Fix:** another agent holds a **live** lease on that review — one agent per review. Back off and
skip it. A lease whose last renewal is older than `LEASE_TTL_S` (180s) is stale and can be taken
over, but only while `turn == "agent"` — so a 409 means the holder is alive, not merely present.

## Dates look shifted by a few hours

**Symptom:** timestamps in the viewer don't match your local clock.

**Fix:** the viewer renders dates in **Europe/London**. This is expected; it's not a storage bug —
the underlying timestamps are correct.

## I can't tell when the human is finished

**Symptom:** no explicit "submit" event from the reviewer.

**Fix:** there isn't one by design — feedback streams as they type. Either watch `turn` flip to
`"agent"` on `/status` (the human pressed "Send to agent"), or watch `comments_updated` go quiet
for a few minutes while non-zero. See
[Knowing when the human is done](#/quickstart) in the Quickstart.

## Another agent seems to see my reviews

**Symptom:** reviews from different agents appear together; there's no tenant boundary.

**Fix:** the service is **no-auth, id-only**. Isolation between reviews is the opaque `id`
namespace and nothing more — ownership by `project`/`session` is a tag convention, not enforcement.
If you expose the service beyond localhost, put a proxy/token in front and don't assume isolation
between tenants beyond the `id`.

## Still stuck?

The [project README](https://github.com/ranawaqas-ai/mdreview-service#readme) has the full API
table, every env var, and the watcher runbook.
