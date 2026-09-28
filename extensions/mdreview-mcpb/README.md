# mdreview Claude Desktop extension

An MCPB bundle that runs the mdreview stdio MCP server (`src/mcp_server.py` and `src/mcp/`) inside Claude Desktop. It talks to the hosted service at `https://app.mdreview.space` with a per-user agent token. The server is stdlib-only Python, so the bundle contains no third-party dependencies.

## Read this before submitting

Anthropic's current docs say desktop extension listings in the directory are deprecated and the directory no longer accepts MCPB submissions (https://claude.com/docs/connectors/building/submission and https://claude.com/docs/connectors/building/mcpb, both checked 2026-09-28). Local servers are meant to go in through a plugin instead, and mdreview already has one in `plugins/mdreview`. The short link https://clau.de/desktop-extention-submission redirects to a Google Form that I could not open (HTTP 401), so I could not confirm whether it still takes submissions. The owner should open it first and check for a deprecation notice.

Whatever the form does, the bundle is a valid MCPB and can be installed by hand or attached to a release for people who want it.

## What is in the bundle

- `manifest.json`, manifest_version 0.3, with `privacy_policies`, a sensitive `token` user setting, the 21 tools and three example prompts.
- `icon.png`, 512x512 with transparent corners, rendered from the site's "md" favicon.
- `server/mcp_server.py` and `server/mcp/`, copied from `src/` as real files.
- `LICENSE`.

The server starts with `python3`, `MDREVIEW_BASE=https://app.mdreview.space`, `MDREVIEW_TOKEN` from the user setting and `MDREVIEW_NO_AUTO_UPDATE=1`, the same settings the Claude Code plugin uses.

## Build

```sh
extensions/mdreview-mcpb/build.sh
```

This stages a clean copy under `.scratch/mcpb/stage`, runs `mcpb validate`, and packs `.scratch/mcpb/mdreview-<version>.mcpb`. It calls the official CLI through `npx --yes @anthropic-ai/mcpb`, so it needs Node and network access once and installs nothing globally. `.scratch/` is gitignored, so the built file is never committed.

Bump `version` in `manifest.json` together with the server release.

## Install for a local test

Double-click the `.mcpb` file, or drag it into the Claude Desktop window, or open Settings, Extensions, Advanced settings, Install Extension and pick the file. Paste an agent token from https://app.mdreview.space/account when asked.

The bundle needs `python3` (3.8 or newer) on the PATH. Claude Desktop does not ship Python, so a Mac without it will fail to start the server. The manifest lists only `darwin` because the command name `python3` is not reliable on Windows; I did not test Windows.

## What was tested, and what was not

Tested: `mcpb validate` and `mcpb pack` pass. The packed bundle was unpacked and its server was run over stdio with no token. `initialize` and `tools/list` work (21 tools, all with annotations, names equal to the manifest list), and a tool call returns `isError: true` with `HTTP 401 ... authentication required`.

Not tested: installation and use inside Claude Desktop itself. I cannot drive that app, so the install UI, the token prompt, the way Desktop resolves `python3`, and a real tool call with a valid token are unverified.

## Submit

1. Open https://clau.de/desktop-extention-submission and confirm it still accepts desktop extensions.
2. Build the bundle and install it in Claude Desktop yourself first.
3. Look at `icon.png` and approve it.
4. Fill the form from `manifest.json`: name, description, homepage, repository, privacy policy `https://mdreview.space/privacy/`, and the README "Privacy Policy" section at the end of the repository README.
5. Give reviewers a token for a test account. Tokens come from the account page after signing in with an email link.

## Privacy

See the "Privacy Policy" section at the end of the repository README and https://mdreview.space/privacy/. A separate `PRIVACY.md` is not required by the docs I could read, so none is included.
