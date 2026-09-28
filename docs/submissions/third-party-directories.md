# Third-party MCP directories

Research for item 4 of `docs/discoverability-todo.md`, done 2026-09-28. Nothing here has been submitted. The owner approves each route before anything goes out.

Method. Each directory's own pages were fetched, plus web search. Listing checks used each site's search page and a web search for "mdreview". A search page can be client-rendered, so "not listed" from a search page is marked "probably not listed" unless the site's own text confirms it.

## Shared entry text

Every section below reuses this block.

| Field | Value |
|---|---|
| Name | mdreview |
| Registry name | `io.github.ranawaqas-ai/mdreview` (v0.6.0, active in the official MCP Registry since 2026-09-25) |
| Pitch (under 100 chars) | Human-in-the-loop document review: an agent pushes a draft, a person comments, the agent revises. |
| Repo | https://github.com/ranawaqas-ai/mdreview-service (Apache-2.0, personal account `ranawaqas-ai`) |
| Site | https://mdreview.space |
| Remote URL | https://app.mdreview.space/mcp (Streamable HTTP, OAuth 2.1 with dynamic client registration, 21 annotated tools, sign in with any email) |
| Privacy | https://mdreview.space/privacy/ |
| Categories | Productivity, Document review, Human-in-the-loop, Collaboration |
| Tags | markdown, latex, review, comments, human-in-the-loop, agents, oauth, remote |

Description (paste as is).

> mdreview is a review loop for documents that agents write. The agent pushes a markdown or LaTeX draft and gets a review URL. A person reads it in the browser and comments on any passage. The agent reads the open comments, revises the draft (the page reloads live), replies to or resolves each comment, and hands the turn back. It exposes 21 annotated tools over remote MCP with OAuth, so the hosted instance needs no token in claude.ai. It is open source and self-hostable.

Install snippets.

claude.ai custom connector:

```text
Settings > Connectors > Add custom connector > https://app.mdreview.space/mcp
```

Claude Code plugin:

```text
/plugin marketplace add ranawaqas-ai/mdreview-service
/plugin install mdreview@mdreview
```

Generic MCP client (stdio wrapper, token from https://app.mdreview.space Account page). This is the snippet from `web/site/docs/mcp.md`. Other clients cannot use the remote URL yet, because the OAuth server only allows redirects to claude.ai, claude.com and loopback addresses (todo item 5).

```json
{
  "mcpServers": {
    "mdreview": {
      "command": "/absolute/path/to/python3",
      "args": ["/path/to/mdreview-service/src/mcp_server.py"],
      "env": {
        "MDREVIEW_BASE": "https://app.mdreview.space",
        "MDREVIEW_TOKEN": "mdr_xxx"
      }
    }
  }
}
```

## Glama

Status. Listed twice, both unclaimed.

- Server listing: https://glama.ai/mcp/servers/ranawaqas-ai/mdreview-service. It carries the old README text ("invite-only, Google sign-in") and a disabled deploy button ("This server cannot be deployed").
- Connector listing: https://glama.ai/mcp/connectors/io.github.ranawaqas-ai/mdreview. Indexed from the official registry, health shows "Unhealthy" (last check 2026-09-28) because the endpoint needs OAuth and Glama has no credentials. It shows categories "Workplace & Productivity" and "App Automation".

Route. Auto-indexed, so no submission is needed. The owner claims each listing.

- Server listing: sign in to Glama with GitHub and press Claim. The repo belongs to a personal account, and Glama's `glama.json` post says personal repositories can claim by authenticating with GitHub; the `glama.json` file is required for organization repos. Once claimed the owner can set the Docker image (Glama does not use the repo Dockerfile until then), edit metadata and see usage.
- Connector listing: claim through the "Claim ownership" dialog, then add credentials under Admin, Test Profile so health checks and tool discovery can sign in. The connector claim is verified by serving `/.well-known/glama.json` with a token from that dialog (`$schema` and `claim` fields), per a third-party PR that did it; I did not find Glama's own page for this. That is a server change and needs the token first.

Requirements. A Glama account. A test login for the connector health check (an mdreview account the owner controls). Optional `glama.json` in the repo root:

```json
{
  "$schema": "https://glama.ai/mcp/schemas/server.json",
  "maintainers": ["ranawaqas-ai"]
}
```

I did not add this file. It is not required for a personal repo, and I could not confirm it is needed.

Effort. About 20 minutes. Worth doing first because two other lists below require the connector badge to be healthy.

## Smithery

Status. Probably not listed. The search page for "mdreview" showed no match (weak check).

Route. Self-serve publish of a remote server at https://smithery.ai/new by entering the public HTTPS URL, or from the CLI:

```text
smithery mcp publish "https://app.mdreview.space/mcp" -n @ranawaqas-ai/mdreview
```

Source: https://smithery.ai/docs/build/publish.

Requirements. A Smithery account. Streamable HTTP transport (met). OAuth is supported and Smithery registers itself with Client ID Metadata Documents. Smithery scans the server for tools; if the scan cannot get past OAuth, the fallback is a static `/.well-known/mcp/server-card.json` with `serverInfo`, `authentication` and `tools`. Whether Smithery can complete the scan against an OAuth server that only allows claude.ai, claude.com and loopback redirects is unknown, and it is the main risk. No repo file, Dockerfile or `smithery.yaml` is needed for a remote server.

Effort. 10 minutes if the scan works, about 2 hours if a server-card endpoint has to be added. Optional afterwards, Settings, Verification.

Entry text. Use the shared block. Namespace `@ranawaqas-ai/mdreview`.

## PulseMCP

Status. Probably not listed (search for "mdreview" returned 0 servers).

Route. None right now. https://www.pulsemcp.com/submit says "We are not accepting new MCP server or client submissions right now" (page dated 2026-09-03) and says the official registry is the best path, and that it will pick registry entries up automatically when it resumes.

Requirements. The official registry entry, which already exists.

Effort. None. Recheck the submit page in a few weeks. If it is still paused, search PulseMCP again for `mdreview` once it resumes.

## mcp.so

Status. Probably not listed (search for "mdreview" showed no match; weak check).

Route. https://mcp.so/submit. The submit form offers types "MCP Server", "Remote Server", "MCP Client" and "AI Agent". Required fields seen are repository URL, name and server config. The home page describes submission as opening an issue on their GitHub repo, and the form page mentions a paid $39 tier for instant publication. I could not confirm whether the free path is the form or a GitHub issue, or whether sign-in is needed.

Requirements. Repo URL (met). Pick "Remote Server" and give the URL. A sign-in may be needed, which means an account.

Effort. 10 minutes. Skip the paid tier.

Entry text. Type Remote Server. Name mdreview. Repo and URL from the shared block. Server config:

```json
{
  "mcpServers": {
    "mdreview": {
      "url": "https://app.mdreview.space/mcp"
    }
  }
}
```

## awesome-remote-mcp-servers (recommended over the main list)

Status. Not listed (searched the README for "mdreview", no hit).

Route. Pull request to https://github.com/punkpeye/awesome-remote-mcp-servers. Rules from its `CONTRIBUTING.md`:

- The server answers MCP `initialize` at a public URL over Streamable HTTP or SSE.
- Anyone can use it, and public sign-up is fine. Invite-only servers are excluded (mdreview qualifies now that any email can sign in, provided item 1 lands first).
- It must be listed as a Glama connector, and every entry carries the Glama connector badge. mdreview is already indexed (see Glama above).
- Description at most 120 characters (the README says 150). Authentication marker 🔐 for OAuth.
- Alphabetical within a category.
- The account opening the PR must have starred the repo, or the PR is not merged. Starring is an outward action, so it is the owner's.
- CI checks endpoint response and badge validity. The connector shows "Unhealthy" today, so claim it and add a test profile first.

Effort. 15 minutes after the Glama claim.

Entry, under "Workplace & Productivity". The Glama badge URL returned 200 as an SVG on 2026-09-28. The list is not strictly alphabetical, so put it where the maintainers' order suggests when the PR is made.

```markdown
- [mdreview](https://mdreview.space) `https://app.mdreview.space/mcp`
  [![mdreview MCP connector](https://glama.ai/mcp/connectors/io.github.ranawaqas-ai/mdreview/badges/score.svg)](https://glama.ai/mcp/connectors/io.github.ranawaqas-ai/mdreview)
  🔐 - Push a markdown or LaTeX draft, read a person's comments and revise it, with live reload.
```

PR title:

```text
Add mdreview to Workplace & Productivity
```

PR body:

```markdown
Adds mdreview, a review loop for documents that agents write. The agent pushes a markdown or LaTeX draft, a person comments in the browser, and the agent reads the comments, revises, and resolves them.

- Endpoint: https://app.mdreview.space/mcp (Streamable HTTP, OAuth 2.1 with dynamic client registration)
- Open to anyone: sign in with any email
- Glama connector: https://glama.ai/mcp/connectors/io.github.ranawaqas-ai/mdreview
- Official MCP Registry: io.github.ranawaqas-ai/mdreview
- Source (Apache-2.0): https://github.com/ranawaqas-ai/mdreview-service
```

Do not add the robot emojis to the title. The main list's contributing file offers them to automated agents only, and the owner is submitting this.

## awesome-mcp-servers (punkpeye)

Status. Not listed (searched the README for "mdreview", no hit).

Route. Pull request to https://github.com/punkpeye/awesome-mcp-servers, editing `README.md`. Its scope line says the list is for servers with a public GitHub repo that you install and run yourself, and that remote-only servers belong in the remote list. mdreview has both a self-hostable repo and a hosted endpoint, so it fits, but a maintainer could still redirect it. Rules from `CONTRIBUTING.md`:

- Name linked to the repo, a brief description, the right category, one server per line, existing format and style, alphabetical within a category, accurate links.
- The file does not require a Glama badge, but every existing entry has one, so include it (the server listing badge returned 200 as an SVG on 2026-09-28).
- No PR template exists (the `.github/PULL_REQUEST_TEMPLATE.md` path returned 404).
- The 🤖🤖🤖 title suffix is for automated agents. Leave it off.

Effort. 15 minutes. Do it after the remote list, and after the Glama server listing is claimed and its stale text is fixed.

Entry, under "Workplace & Productivity". Legend used is 🐍 Python, ☁️ cloud service (the hosted endpoint) and 🏠 local service (self-host and the stdio wrapper). Other entries use 🍎 🪟 🐧 for OS support, and I have not verified mdreview on Windows, so those are left out.

```markdown
- [ranawaqas-ai/mdreview-service](https://github.com/ranawaqas-ai/mdreview-service) [![ranawaqas-ai/mdreview-service MCP server](https://glama.ai/mcp/servers/ranawaqas-ai/mdreview-service/badges/score.svg)](https://glama.ai/mcp/servers/ranawaqas-ai/mdreview-service) 🐍 ☁️ 🏠 - Human-in-the-loop review of agent-written markdown and LaTeX. A person comments in the browser, the agent reads the comments and revises.
```

PR title:

```text
Add ranawaqas-ai/mdreview-service
```

PR body:

```markdown
Adds mdreview to Workplace & Productivity. An agent pushes a markdown or LaTeX draft, a person comments in the browser, and the agent reads the comments, revises, and resolves them. 21 tools.

- Repo (Apache-2.0): https://github.com/ranawaqas-ai/mdreview-service
- Self-host: `make up` serves on localhost:8137
- Hosted remote endpoint: https://app.mdreview.space/mcp (Streamable HTTP, OAuth, sign in with any email)
- Glama: https://glama.ai/mcp/servers/ranawaqas-ai/mdreview-service
- Official MCP Registry: io.github.ranawaqas-ai/mdreview
```

## modelcontextprotocol/servers

Status. Not applicable. The README no longer lists community servers. It says to browse the official MCP Registry, where mdreview is already published. Nothing to submit.

## Other directories that are alive

### mcpservers.org (wong2 list)

Status. Probably not listed (search for "mdreview" returned 403 to the fetch tool, so this is unchecked; a web search found no mdreview page).

Route. Web form at https://mcpservers.org/submit. Fields are name, category (this one fits "Productivity"), short description, repo, website or docs link, optional official registry name, a "supports remote connections" checkbox, and contact email. Free review takes up to 2 weeks. A $39 premium tier exists, skip it.

Requirements. None beyond the form. Contact email is the owner's to choose.

Effort. 10 minutes. Registry name to enter is `io.github.ranawaqas-ai/mdreview`.

### MCP Market

Status. Unknown, not searched successfully.

Route. Form at https://mcpmarket.com/submit with a GitHub repo URL or a "Remote MCP" option. The free queue averages 4 to 6 weeks, and a paid $29 tier lists within 24 hours. Skip the paid tier.

Requirements. Repo URL. I could not confirm the remote option's exact fields.

Effort. 10 minutes. Low priority given the wait.

### Cline MCP Marketplace

Status. Not listed as far as I know (not checked in the app).

Route. Open an issue on https://github.com/cline/mcp-marketplace with the repo URL and a 400x400 PNG logo, and confirm that Cline installed the server from the docs alone. Review takes a couple of days.

Requirements. It expects a repo, and the README does not say whether URL-only remote servers are supported. Cline would have to use the stdio wrapper with a token, or an OAuth remote flow that this server does not yet allow for Cline's redirect. That makes this a poor fit today. The submission also needs a 400x400 PNG logo, and I did not look for one in the repo.

Effort. High relative to payoff. Skip until item 5 (other clients) is done.

### Docker MCP Catalog

Status. Not listed as far as I know.

Route. Pull request to https://github.com/docker/mcp-registry. Remote servers are accepted, as a folder with `server.yaml` (`type: remote`, `remote.transport_type: streamable-http`, `remote.url`), an empty `tools.json` (`[]`) and a `readme.md`. Entries appear in Docker Desktop's MCP Toolkit within 24 hours of approval. Apache-2.0 meets its license rule.

Requirements. The OAuth block in the `server.yaml` example is provider-based, and I could not confirm it works for a server that runs its own OAuth with dynamic client registration. Docker Desktop is also another client whose redirect the mdreview OAuth server may not allow. Treat as unverified.

Effort. About an hour with testing. Skip until item 5.

## Recommended order

1. Claim both Glama listings and add a test profile. It fixes the stale "invite-only" text, turns the connector healthy, and unblocks the remote list.
2. Land item 1 (site and README copy) before any PR goes out, so every link lands on correct copy.
3. awesome-remote-mcp-servers PR.
4. awesome-mcp-servers PR.
5. Smithery publish.
6. mcp.so and mcpservers.org forms.
7. PulseMCP: nothing to do, recheck later.
8. Skip for now: MCP Market (long wait), Cline and Docker (client redirect and fit unverified).

| Directory | Status | Route | Account needed | Effort | Priority |
|---|---|---|---|---|---|
| Glama | Listed twice, unclaimed | Claim on glama.ai | Yes (GitHub sign-in) | 20 min | 1 |
| awesome-remote-mcp-servers | Not listed | PR, repo must be starred | GitHub | 15 min | 2 |
| awesome-mcp-servers | Not listed | PR | GitHub | 15 min | 3 |
| Smithery | Probably not listed | smithery.ai/new or CLI | Yes | 10 min to 2 h | 4 |
| mcp.so | Probably not listed | Submit form or GitHub issue | Likely | 10 min | 5 |
| mcpservers.org | Probably not listed | Web form | No | 10 min | 6 |
| PulseMCP | Probably not listed | Paused, auto-ingests the registry | No | 0 | wait |
| MCP Market | Unknown | Web form, 4 to 6 week queue | Unknown | 10 min | low |
| Cline marketplace | Unknown | GitHub issue | GitHub | high | skip |
| Docker MCP Catalog | Unknown | PR to docker/mcp-registry | GitHub | 1 h | skip |
| modelcontextprotocol/servers | Not applicable | Points to the registry | | | none |

## What could not be confirmed

- Whether Smithery can scan the server through OAuth.
- Whether mcp.so's free path is the form or a GitHub issue, and whether sign-in is required.
- Every "probably not listed" result, since search pages may render client-side.
- Glama's own documentation for the connector claim token. The mechanism comes from a third-party PR.
- Whether the Glama connector health check can pass without extra server changes.
- Cline and Docker remote OAuth compatibility.
