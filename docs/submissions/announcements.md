# Announcement drafts

Drafts only. Nothing here has been posted. The owner posts them, under their own name and accounts.

Before posting, check that https://mdreview.space shows the "Add mdreview to your agent" section (it does as of v0.7.0) and that https://app.mdreview.space/mcp connects from claude.ai. Every claim below is one the repo can back up. Numbers of users, stars or downloads are deliberately absent.

## The facts every post draws on

- An agent writes a markdown or LaTeX draft and pushes it to mdreview. A person opens it in the browser and leaves threaded comments anchored to the text. The agent reads the comments over MCP or HTTP, revises the draft, replies and resolves. The page live-reloads.
- The viewer renders GFM, Mermaid diagrams, LaTeX math, footnotes and highlighted code. LaTeX papers get a split view with a compiled PDF. Every round is kept, with a diff between rounds.
- Ways in: a claude.ai custom connector (`https://app.mdreview.space/mcp`, OAuth, no token to copy), a Claude Code plugin (`/plugin marketplace add ranawaqas-ai/mdreview-service`), or the installer. It is also listed in the official MCP Registry as `io.github.ranawaqas-ai/mdreview`.
- 21 tools, each annotated as read-only, write or destructive. Sign in with any email. Source is Apache-2.0 at https://github.com/ranawaqas-ai/mdreview-service.
- Honest limits: it is a one-person project, the Claude Desktop extension is untested inside Claude Desktop, and clients other than claude.ai and Claude Code have had little testing.

## Show HN

**Title** (80 characters or fewer):

`Show HN: mdreview, threaded human review for documents AI agents write`

**Link:** https://mdreview.space

**First comment** (post it right after submitting):

> I kept ending up with a plan or design doc written by an AI agent, and the only way to correct it was to paste my notes back into the chat. That loses which sentence each note was about, and the agent has no way to tell me which notes it has handled.
>
> mdreview is a small review loop for that. The agent pushes a markdown or LaTeX draft over MCP or HTTP. I open it in the browser, select text and comment, and the comments come back to the agent as threads anchored to the text. It revises, replies and resolves, and my page reloads with a diff of what changed.
>
> It runs as a remote MCP server, so in claude.ai you add https://app.mdreview.space/mcp as a custom connector and sign in. There is also a Claude Code plugin. Diagrams (Mermaid), math and LaTeX papers with a compiled PDF render in the viewer.
>
> It is Apache-2.0 and a one-person project. I would like to hear where the loop breaks for you, especially with clients other than Claude, which I have tested least.
>
> Source: https://github.com/ranawaqas-ai/mdreview-service

Timing: weekday morning US Eastern. Stay near the thread for the first two hours and answer every comment.

## r/ClaudeAI

**Title:** `I built a way to review Claude's documents with threaded comments, and it is now a claude.ai connector`

> When Claude writes a plan or spec, my feedback used to go back as a pasted blob. I built mdreview so I can open the draft in a browser, comment on specific sentences, and have Claude read those comments, revise, and resolve them.
>
> To try it in claude.ai: Settings, Connectors, Add custom connector, and enter `https://app.mdreview.space/mcp`. Sign in with your email and approve. Then ask Claude to create a review of something it wrote. In Claude Code, `/plugin marketplace add ranawaqas-ai/mdreview-service` and then `/plugin install mdreview@mdreview`.
>
> It is free and open source (Apache-2.0): https://github.com/ranawaqas-ai/mdreview-service. I am the only maintainer. Tell me what is missing.

Attach the demo GIF. Read the subreddit's self-promotion rules first; I have not checked them.

## r/mcp

**Title:** `mdreview: a remote MCP server for human review of agent-written docs (OAuth, 21 annotated tools)`

> mdreview is a human-in-the-loop review server. The agent creates a review from markdown or LaTeX, a person comments in the browser, and the agent reads the comments as threads and updates the draft.
>
> Implementation notes for anyone building a remote server: Streamable HTTP with JSON responses only and no session id, an in-house OAuth 2.1 server with dynamic client registration and PKCE S256, and a redirect allowlist matched as one whole string after an early bug where a backslash trick made Python and a browser disagree about the host. Every tool carries a title and readOnly or destructive hints.
>
> Endpoint: `https://app.mdreview.space/mcp`. Registry: `io.github.ranawaqas-ai/mdreview`. Source (Apache-2.0): https://github.com/ranawaqas-ai/mdreview-service
>
> It works with claude.ai and Claude Code today. I am working on Cursor and VS Code, so reports from other clients are useful.

## X thread

1. `AI agents write plans and specs. Correcting them by pasting notes into a chat loses which sentence each note was about. I built mdreview: comment on the text in a browser, and the agent reads your comments as threads, revises, and resolves them.` (attach the demo GIF)
2. `The agent pushes a markdown or LaTeX draft. You select text and comment. The agent replies, resolves, and your page reloads with a diff.`
3. `In claude.ai: Settings, Connectors, Add custom connector, https://app.mdreview.space/mcp. Sign in with your email. In Claude Code: /plugin marketplace add ranawaqas-ai/mdreview-service`
4. `Mermaid diagrams, math, footnotes, and LaTeX papers with a compiled PDF all render in the viewer. Every round is kept, with a diff.`
5. `Open source, Apache-2.0, one maintainer. Source and MCP Registry entry: https://github.com/ranawaqas-ai/mdreview-service` 

## One paragraph for directories and newsletters

mdreview is a human-in-the-loop review server for AI agents. An agent pushes a markdown or LaTeX draft over MCP or HTTP, a person leaves threaded comments in the browser, and the agent reads them, revises and resolves. It runs as a remote MCP server with OAuth (a claude.ai custom connector), as a Claude Code plugin, and from a local installer. Apache-2.0. https://mdreview.space

## Before any of this goes out

- [ ] Owner reviews the demo GIF (`.scratch/demo/mdreview-demo.gif`). Known flaws: the Mermaid diagram is out of view, and click circles show. Re-record if wanted.
- [ ] Decide whether to commit the GIF to `web/site/` and embed it in the README and the site, which helps every channel.
- [ ] Run the connector once against prod from claude.ai, end to end, so the posts describe something you have seen work.
- [ ] Check each subreddit's rules on self-promotion.
