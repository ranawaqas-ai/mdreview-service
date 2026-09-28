# Discoverability todo

Goal: people who use MCP can find mdreview and install it in a minute. Working checklist, last updated 2026-09-28. Directory submissions and gate evidence stay on the GitHub epics (#393 plugin, #396 claude.ai directory, #397 registry).

## What changed on 2026-09-28

Anthropic's docs now say any paid Claude plan (Pro, Max, Team or Enterprise) can submit to the directory through https://claude.ai/directory/manage. The earlier "Team or Enterprise only" reading is out of date. The directory no longer accepts desktop extensions (MCPB). A plugin and a remote connector are two separate submissions. Sources: https://claude.com/docs/directory/publish and https://claude.com/docs/connectors/building/submission.

## Already done

- [x] Remote connector live at `https://app.mdreview.space/mcp` (v0.6.0). Any claude.ai user can add it by URL.
- [x] Claude Code plugin installable from GitHub: `/plugin marketplace add ranawaqas-ai/mdreview-service`.
- [x] Official MCP Registry entry, `io.github.ranawaqas-ai/mdreview` v0.6.0 (PR #408). Glama already indexed it from there.
- [x] Privacy policy live at `https://mdreview.space/privacy/`.

## The six items

| # | Item | Status |
|---|---|---|
| 1 | Site and README copy | done, merged (PR #410), goes live with the next `dev` to `main` release |
| 2 | Claude Desktop extension | built and merged (PR #412), but the directory no longer accepts it; only a hand-installed download |
| 3 | Claude Code plugin directory | submission pack merged (PR #411); blocked by the symlink layout |
| 4 | Third-party MCP directories | researched (PR #413); submissions are the owner's |
| 5 | Other AI tools | research merged (PR #414); code change not started |
| 6 | Announcements | demo GIF recorded, awaiting review; posts not drafted |

### 1. Site and README copy (done)

- [x] Removed the stale "invite-only" wording from the site, README and installer.
- [x] Added an "Add mdreview to your agent" section (connector, plugin, installer).
- [ ] Ships with the next release to `main`.
- [ ] Owner runs the connector against prod from claude.ai once. So far it has been tested on staging only.

### 2. Claude Desktop extension (parked)

Bundle source is in `extensions/mdreview-mcpb/` (manifest, icon, build script, README). It builds and validates, and its stdio server answered correctly with no token. It was not tested inside Claude Desktop.

- [ ] Decide whether to publish the `.mcpb` as a GitHub release download for Desktop users, or leave it unpublished.

### 3. Claude Code plugin directory

Submit at https://claude.ai/directory/manage, choose "Plugin bundle". Answers are ready in `docs/submissions/plugin-directory.md`.

- [ ] Owner confirms which Claude plan the account is on (Pro or Max is enough).
- [ ] Fix the blocker: the directory's Validate step blocks symlinks, and `plugins/mdreview/mcp_server.py` is one. Proposed fix is real copies of the wrapper plus a test that they match `src/`. Needs the owner's yes.
- [ ] Decide what to do about the self-update code in `src/mcp/update.py`, which the security scan may flag (the plugin already sets `MDREVIEW_NO_AUTO_UPDATE=1`).
- [ ] Release to `main`, submit, then press Publish once the portal shows Approved.

### 3b. Claude connector directory (same portal, #396)

Submit the remote server as an "MCP connector". Reaches claude.ai web, Desktop, mobile and Cowork.

- [ ] Owner confirms a paid plan.
- [ ] Reviewer login. The form asks for credentials to a fully populated account, and sign-in is an emailed link that reviewers cannot read. Owner picks an approach.
- [ ] Icon, docs URL and privacy URL are ready (`https://mdreview.space/docs/#/mcp`, `https://mdreview.space/privacy/`).
- [ ] Owner submits; connectors get an automatic scan and are listed as Community by default.

### 4. Third-party MCP directories

Details, entry text and PR bodies are in `docs/submissions/third-party-directories.md`.

- [ ] Claim both Glama listings and add a test login. This fixes the stale "invite-only" text and the "Unhealthy" badge.
- [ ] Submit to Smithery (smithery.ai/new), mcp.so (Remote Server form), mcpservers.org and MCP Market (4 to 6 week queue).
- [ ] Open a PR to awesome-remote-mcp-servers. It needs the Glama badge and a star on the repo.
- [ ] Wait for PulseMCP to resume submissions; it should ingest the official registry by itself.
- [ ] Do these after item 1 is live, so visitors land on the right page.

### 5. Other AI tools

Full analysis in `docs/submissions/other-clients-oauth.md`. Finding: `/oauth/register` rejects a whole request if any single redirect URI is off the allowlist, and Cursor and VS Code each register several at once, so both fail with a 400 today. Gemini CLI, Zed and the MCP Inspector already work (loopback).

- [ ] Registration keeps the allowed redirect URIs and rejects only if none remain (RFC 7591 permits this). This unblocks Cursor and VS Code desktop without trusting a new host.
- [ ] Add `_` to the loopback path characters so Goose works.
- [ ] Do not add `vscode.dev/redirect` (it forwards the code to any handler named in `state`) or `cursor://` (any local app can claim it).
- [ ] ChatGPT needs RFC 9207 `iss` support and a live test first.
- [ ] Tests for each change, mutation-checked, then an independent security review, because this is the list that stops code theft.

### 6. Announcements

- [x] Demo GIF recorded: `.scratch/demo/mdreview-demo.gif` (29 s, 1.9 MB, not committed). Known flaws: the Mermaid diagram is out of view, and click circles show.
- [ ] Owner reviews the GIF (re-record with the diagram if wanted).
- [ ] Draft posts for r/ClaudeAI, r/mcp, Show HN and X.
- [ ] Owner posts them, after items 1 and 3b are live.

## Decisions needed from the owner

1. Which Claude plan is the account on (Pro or Max is enough to submit)?
2. Replace the plugin's symlinks with real copies plus a sync test?
3. How should Anthropic's reviewers sign in to a populated account?
4. Implement the item 5 code change (registration filter and `_` in the loopback path)?
5. Publish the `.mcpb` as a release download, or drop it?
