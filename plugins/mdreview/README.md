# mdreview

Human-in-the-loop review of documents written by an agent. Claude pushes a markdown or LaTeX
draft to mdreview, a person reads it in the browser and leaves comments on specific passages, and
Claude reads those comments, revises the draft, replies, and hands it back for another round.

## Install

```
/plugin marketplace add ranawaqas-ai/mdreview-service
/plugin install mdreview@mdreview
```

Claude Code asks for an agent token when you enable the plugin. Sign in at
https://app.mdreview.space with any email address (you get a one-time sign-in link), open the
Account page, and create a token under Agent tokens. Claude Code keeps it in your keychain.

## Use it

Ask Claude to put a draft up for review, for example "write the design doc and send it to mdreview".
Claude returns a review link. Open it, comment on the text, and tell Claude you are done. Claude
then reads your comments and updates the draft, and the page reloads with the new version.

## What the plugin runs

The plugin starts one local MCP server with `python3` (standard library only, no packages are
installed) from `mcp_server.py` and the `mcp/` folder inside the plugin. The server has 21 tools
that create, read, update, comment on and delete reviews.

## What data leaves your machine

The server sends requests only to https://app.mdreview.space. Each request carries your agent
token. The tools send the document text and comments you or Claude write, and the
`attach_asset` tool reads a file from a path Claude gives it and uploads that file to your review.
Nothing else is read from your machine, and the plugin does not read tokens or credentials from
your environment. Documents stay in your account until you delete them. See
https://mdreview.space/privacy/ for storage, sharing and deletion.

The server's self-update code is switched off in the plugin (`MDREVIEW_NO_AUTO_UPDATE=1`), so the
plugin's files only change when you update the plugin. The server can open a review link in your
browser only if you set `MDREVIEW_OPEN_BROWSER`.

## Support

rana.waqas.works@gmail.com, or open an issue at https://github.com/ranawaqas-ai/mdreview-service.
Licensed under Apache-2.0.
