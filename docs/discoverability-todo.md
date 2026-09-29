# Discoverability todo

Goal: get mdreview onto more platforms and in front of more people. Working checklist, last updated 2026-09-29. Directory submissions and gate evidence stay on the GitHub epics (#393 plugin, #396 claude.ai connector directory, #397 registry and other directories).

Rules that shape the list: Anthropic's directory takes any paid Claude plan (the owner is on Max). It no longer takes desktop extensions. Signing in to third-party sites, creating accounts and paying are the owner's; posting and forms under the owner's name need the owner's go on each.

## Live today (v0.7.0, released 2026-09-29)

- [x] Remote connector at `https://app.mdreview.space/mcp`, OAuth, any email can sign in. Any claude.ai user can add it by URL.
- [x] Claude Code plugin 0.2.0 from GitHub, real files (no symlinks), CI drift check against `src/`.
- [x] Official MCP Registry entry `io.github.ranawaqas-ai/mdreview` 0.7.0. Glama already indexed it from there.
- [x] Site and README no longer say "invite-only"; new "Add mdreview to your agent" section; privacy policy at `https://mdreview.space/privacy/`.
- [x] Cursor and VS Code desktop can register (registration keeps the allowed redirect addresses). Not tested against the real clients.
- [x] Tool descriptions reworded for Anthropic's review criteria; 21 annotated tools.
- [x] GitHub repo has a description, homepage and 13 search topics. Repo links point at `ranawaqas-ai`.
- [x] Desktop extension bundle attached to the v0.7.0 release as an experimental download (never run in Claude Desktop).
- [x] Reviewer demo login merged (`/auth/demo`, off unless `MDREVIEW_DEMO_LOGIN_KEY` is set). Not yet on prod: it ships with the next release.
- [x] mcpservers.org form submitted (free plan, review within two weeks, email reply to rana.waqas.works@gmail.com).

## Waiting on the owner

- [ ] **Claude plugin directory.** Submit at https://claude.ai/directory/manage, "Plugin bundle", answers in `docs/submissions/plugin-directory.md`. The browser tool I use cannot draw claude.ai in its background window, so this one is done in your own Chrome.
- [ ] **Glama.** Sign in with GitHub, claim both listings (server and connector). This fixes the stale "invite-only" text and the "Unhealthy" badge, and the awesome-remote-mcp-servers PR depends on it. Steps in `docs/submissions/third-party-directories.md`.
- [ ] **Smithery** (smithery.ai/new). Needs a sign-in. Then I can fill the form.
- [ ] **awesome-remote-mcp-servers PR.** After the Glama claim, and the list wants you to star its repo.
- [ ] **mcp.so.** Only a paid $39 submission was visible. Skipped until you say.
- [ ] **PulseMCP.** Submissions paused; it should ingest the official registry by itself. Recheck in a few weeks.

## Connector directory listing (#396)

Everything for the portal is written in `docs/submissions/claude-connector-directory.md`. The connector must pass Anthropic's review: the wording fixes shipped, the error-message, input-validation and size items from the audit did not.

- [ ] Release the demo login (next `dev` to `main` release).
- [ ] Prod step, your go: put a generated key in the host `.env`, add the variable to the prod compose file, recreate, run `scripts/seed_demo.py` (runbook: `docs/operations/demo-login.md`).
- [ ] Run all 21 tools once through a custom connector on prod.
- [ ] Submit at https://claude.ai/directory/manage, "MCP connector", with the reviewer instructions and the access code.
- [ ] After approval, tear the demo login down in the runbook's order.
- [ ] Optional API polish from the audit (clear error messages, reject bad `status` and empty markdown, response size guard) as its own PR.

## Announcements

- [x] Drafts for Show HN, r/ClaudeAI, r/mcp and X: `docs/submissions/announcements.md`.
- [x] Demo GIF recorded: `.scratch/demo/mdreview-demo.gif` (29 s, 1.9 MB, not committed). Flaws: the Mermaid diagram is out of view and click circles show.
- [ ] Owner reviews the GIF; re-record with the diagram if wanted, and decide whether to commit it to `web/site/` for the README and site.
- [ ] Run the connector once against prod from claude.ai so the posts describe something you have seen work.
- [ ] Check each subreddit's self-promotion rules, then post. Best after the plugin and Glama steps land so the links are right.

## Other clients

Research and analysis: `docs/submissions/other-clients-oauth.md`.

- [x] Gemini CLI, Zed, MCP Inspector and Goose already work (loopback). Cursor and VS Code desktop can now register.
- [ ] Test against the real Cursor and VS Code, then add one-line install snippets to the site and README.
- [ ] ChatGPT needs RFC 9207 `iss` support and a live test first.
- [ ] Deliberately not added: `vscode.dev/redirect` (forwards the code to any handler named in `state`) and `cursor://` (any local app can claim it).

## Parked

- Claude Desktop extension directory: closed to new submissions. The bundle stays a release download.
- Team or Enterprise plan: not needed.
