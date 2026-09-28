# Discoverability todo

Goal: people who use MCP can find mdreview and install it in a minute. Working checklist, last updated 2026-09-28. Directory submissions and gate evidence stay on the GitHub epics (#393 plugin, #396 claude.ai directory, #397 registry and Desktop extension).

Constraint: no Team or Enterprise plan (owner decision, 2026-09-25), so the claude.ai connector directory is out. Anything below must work without one.

## Already done

- [x] Remote connector live at `https://app.mdreview.space/mcp` (v0.6.0). Any claude.ai user can add it by URL.
- [x] Claude Code plugin installable from GitHub: `/plugin marketplace add ranawaqas-ai/mdreview-service`.
- [x] Official MCP Registry entry, `io.github.ranawaqas-ai/mdreview` v0.6.0 (PR #408).
- [x] Privacy policy live at `https://mdreview.space/privacy/`.

## The six items

| # | Item | Status | Who |
|---|---|---|---|
| 1 | Fix the site and README copy | todo | Claude |
| 2 | Claude Desktop extension directory | todo | Claude builds, owner submits |
| 3 | Claude Code plugin directory | ready, not submitted | Claude drafts answers, owner submits |
| 4 | Third-party MCP directories | todo | Claude prepares, owner approves each |
| 5 | Other AI tools (Cursor, VS Code, ChatGPT) | todo | Claude |
| 6 | Announcements | todo | Claude drafts, owner posts |

### 1. Fix the site and README copy

Visitors currently read "Invite-only in preview, request access", but prod lets anyone sign in with an email link. Every other channel sends people here, so this comes first.

- [ ] `web/site/index.html`: remove the invite-only line (around line 253).
- [ ] `README.md`: remove "Access is invite-only" (around line 29).
- [ ] Add an "Add to Claude" section to the site and README: the connector URL and the Settings, Connectors, Add custom connector steps.
- [ ] Check `web/site/docs/*.md` for other invite-only wording.
- [ ] Ship through a PR into `dev`, then the next `dev` to `main` release (Pages deploys from `main`).

### 2. Claude Desktop extension directory

Package the MCP server as an `.mcpb` bundle and submit it at https://clau.de/desktop-extention-submission (no org needed).

- [ ] Write `manifest.json` (manifest_version 0.2 or later) with a `privacy_policies` array pointing at `https://mdreview.space/privacy/`.
- [ ] Add a "Privacy Policy" section to the README (the submission page requires it for local connectors).
- [ ] Add a 512px `icon.png` and a few example prompts.
- [ ] Build with the `@anthropic-ai/mcpb` CLI and install the bundle in Claude Desktop to test it.
- [ ] Owner submits the form.

### 3. Claude Code plugin directory

Submit the existing plugin at https://clau.de/plugin-directory-submission. PRs to `anthropics/claude-plugins-official` are closed automatically.

- [ ] Draft the form answers (name, description, repo, homepage, privacy URL).
- [ ] Run `claude plugin validate . --strict` once more before submitting.
- [ ] Owner submits the form.

### 4. Third-party MCP directories

Where people browse for MCP servers. Some may pick up the official registry on their own. That has not been checked.

- [ ] Check which of these already list mdreview: Smithery, Glama, PulseMCP, mcp.so.
- [ ] Submit to the ones that do not.
- [ ] Open a PR to the awesome-mcp-servers list on GitHub.
- [ ] Owner approves each submission before it goes out.

### 5. Other AI tools

The OAuth server only accepts redirects to claude.ai, claude.com and loopback addresses, so other clients cannot connect yet.

- [ ] Find the exact OAuth callback address of each client (Cursor, VS Code, ChatGPT). Not verified yet.
- [ ] Add them to `ALLOWED_REDIRECTS` in `src/mdreview/hosted/oauth.py`, with a test for each and a security check (this is the list that stops code theft).
- [ ] Write a one-line install snippet per client for the site and README.

### 6. Announcements

Directories are passive. Posts bring bursts of users.

- [ ] Record a 30-second demo GIF: agent pushes a draft, a person comments, the agent revises.
- [ ] Draft posts for r/ClaudeAI, r/mcp, Show HN and X.
- [ ] Owner posts them, after items 1 and 2 are live so the landing page is right.

## Order

1, then 2 and 3, then 4, then 6. Item 5 can slot in whenever the OAuth work is convenient.
