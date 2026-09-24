# mdreview-service

A containerized markdown review microservice. An agent POSTs markdown, gets back a review URL
for a human, and polls feedback over HTTP. One service handles many reviews, isolated by id.
No per-process spawning, no shared filesystem with the agent.

**Landing page:** [mdreview.space](https://mdreview.space/) (served from
GitHub Pages via `.github/workflows/pages.yml`; source in `web/site/`).

**Docs:** [mdreview.space/docs](https://mdreview.space/docs/): onboarding,
how-to, and troubleshooting, rendered through the service's own markdown renderer (source in
`web/site/docs/`).

## Getting started: hosted or self-hosted

Two ways to use mdreview; pick one.

**1. Hosted (online, one command).** A managed instance runs at
**[mdreview.space](https://mdreview.space)** (app at **[app.mdreview.space](https://app.mdreview.space)**).
Sign in with Google, open **Connect your agent**, and mint an API token. Then, on the machine
running your agent (needs the `claude` CLI + `python3`):

```sh
curl -fsSL https://mdreview.space/install.sh | MDREVIEW_TOKEN=mdr_xxx sh
```

That fetches the stdlib-only MCP wrapper into `~/.mdreview` and registers it with Claude Code at
user scope; quit and reopen Claude Code and you are connected. Omit `MDREVIEW_TOKEN=…` to be
prompted for it instead. Access is **invite-only** (an email allowlist), so this works only if the
instance owner has added your Google email; otherwise ask for an invite, or self-host below. (To
wire it up by hand, or for a non-Claude-Code MCP client, see [MCP server](#mcp-server-optional).)

Or install it as a Claude Code plugin, with no installer. Inside Claude Code run
`/plugin marketplace add ranawaqas-ai/mdreview-service`, then `/plugin install mdreview@mdreview`.
It prompts for the token and keeps it in your keychain. Plugin updates arrive through
`/plugin update`; the wrapper does not update itself inside a plugin.

**2. Self-hosted (local).** Clone and run it yourself (no account, no auth, on `localhost`). See
[Run](#run), then point your agent's MCP `MDREVIEW_BASE` at `http://localhost:8137`. This is the
path for anyone: no invite needed.

Stdlib Python only (tiny image, no pip installs). Self-contained: the marked, Mermaid, KaTeX,
highlight.js, and footnote renderers are vendored and served from `/static`, so the browser needs no
CDN. The viewer renders Markdown the way a Jekyll/MathJax site does: **LaTeX math** (inline `$…$` /
`\(…\)`, display `$$…$$` / `\[…\]`; prose/currency `$` left literal), **Mermaid** diagrams, **GFM
footnotes** (`[^id]` refs → an ordered back-ref section), and **syntax-highlighted** fenced code (a
dual-scheme theme that reads on light and dark panes).

## Run

```bash
make up        # serves on http://localhost:8137
# or:
docker build -f infra/Dockerfile -t mdreview-service .
docker run -d -p 8137:8080 -v mdreview-data:/data mdreview-service
```

`make up` (compose) is the canonical local-docker path; it serves on 8137 and reuses the
named `mdreview-data` volume, so a rebuild/recreate preserves your reviews.

Health check: `curl localhost:8137/healthz` -> `{"ok":true}`.

Feedback and source persist in the `/data` volume across restarts.

### Migrating a legacy hand-run container

If you have an older instance started by hand (`docker run` on a nonstandard port such as
:8139), move it onto the canonical compose flow without losing data: the `mdreview-data`
volume is reused as-is:

```bash
docker rm -f mdreview     # stop the hand-run container (the mdreview-data volume survives)
make up                   # compose recreates it on 8137, mounting the same mdreview-data volume
```

Because the compose volume is now declared with an explicit `name: mdreview-data` (not a
project-prefixed `infra_mdreview-data`), `make up` mounts the very volume your old container
owned. Confirm with `curl localhost:8137/healthz` and check your reviews are still listed.

## The flow

1. Agent: `POST /api/reviews {markdown, title}` -> `{id, review_url, feedback_url, ...}`
2. Agent hands `review_url` to a human.
3. Human opens it, selects text or clicks a paragraph number, types notes (auto-saved).
4. Agent polls `GET /api/reviews/{id}/status` then `GET /api/reviews/{id}/feedback`.
5. Agent applies edits and `PUT /api/reviews/{id}/source {markdown}` -> the human's page
   live-reloads and addressed notes are struck through. Repeat as needed.

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | in-container listen port |
| `MDREVIEW_DATA` | `/data` | storage dir (mount a volume) |
| `MDREVIEW_PUBLIC_BASE` | empty | if set (e.g. `https://review.example.com`), `review_url`/`feedback_url` use it; otherwise the request Host header is used |
| `MDREVIEW_ENABLE_LATEX` | off | opt-in: enable the LaTeX paper review mode (see below). Requires the `mdreview-service-latex` image (Tectonic); the default slim image has no LaTeX toolchain |

## Operator guides

The runbooks that used to live here, moved out so this page stays readable:

| Guide | What it covers |
|---|---|
| [HTTP API](docs/operations/api.md) | Every route, request and response shape |
| [MCP server](docs/operations/mcp-server.md) | Running the stdio server, smoke tests |
| [LaTeX paper review](docs/operations/latex-review.md) | Enabling it, templates, the compile loop, the image runbook |
| [Watcher](docs/operations/watcher.md) | The agent watcher, trusted-base mode, containerized runs |
| [Design system](docs/design/design-system-spec.md) | §01-§10, the UI rules that tickets cite |
| [Autonomous runs](docs/process/autonomous-run.md) | How agents ship changes here |

## Notes

- Multi-tenant by id, so concurrent reviews never collide. No auth (intended for trusted /
  local networks); put it behind a reverse proxy with auth if exposing it.
- The dashboard (`/`) and `GET /api/reviews` list **across all reviews**: fine for the
  trusted-network posture, but a reason to keep auth in front when exposed.
- The **MCP wrapper** above was designed in [docs/future-mcp.md](docs/future-mcp.md), kept as its
  design/decision record.
- For agent integration details, see [CLAUDE.md](CLAUDE.md).
- A non-Docker, per-file CLI version lives in `../mdreview` (writes feedback to a file next to
  the source). This service is the networked, multi-session form.

## License

[Apache License 2.0](LICENSE).
