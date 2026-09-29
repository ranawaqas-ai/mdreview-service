# Claude plugin directory submission

Prepared 2026-09-28 against the live docs (claude.com/docs/plugins/submit, plugins/pre-submission-checklist, plugins/build, directory/publish, directory/submission-status). Nothing has been submitted.

## What changed since the todo was written

`https://clau.de/plugin-directory-submission` now redirects (302) to https://claude.com/docs/directory/publish. There is no separate form. Plugins are submitted in the developer portal at https://claude.ai/directory/manage, which needs a claude.ai sign-in (not readable without one, so the portal's exact wording is taken from the docs, not from the portal). The earlier Claude Console form is no longer supported.

Eligibility: Pro, Max, Team or Enterprise. On Pro and Max you submit from your own account with no role check. So a Team plan is not needed, and a Pro or Max plan is. Free accounts cannot submit. Your GitHub account must be connected to claude.ai and have push access to `ranawaqas-ai/mdreview-service` (the portal checks this at create and submit time; Validate alone does not).

## Blocker (resolved): symlinks

The checklist says: "Commit regular files and folders for everything the plugin loads, not symbolic links, Git submodules, or Git LFS pointer files" (result: Blocks where the plugin loads the entry). `plugins/mdreview/mcp_server.py` was a symlink and is the MCP server entry, so Validate would have blocked. Also, "people who install the plugin get only the plugin folder".

What changed: the two symlinks are now real committed copies of `src/mcp_server.py` and `src/mcp/*.py` (6 files), rebuilt by `scripts/sync_plugin_wrapper.py`. `tests/plugin_wrapper_sync_selfcheck.py` runs in pr-checks and fails on any byte difference from `src/`, a missing or extra file, or any symlink under `plugins/`. Verified: the plugin copy passes `tests/mcp_smoke.py` (48 checks) against a throwaway server, reports the same `tools_hash` as `src/`, and a local marketplace install runs from the plugin cache.

Effect on the self-update scan finding: `update.py` and `bundle.py` are omitted from the plugin copy, so no self-update code ships in the plugin at all. `__main__` imports `update` inside try/except and the plugin sets `MDREVIEW_NO_AUTO_UPDATE=1`, so nothing changes at runtime. The finding below should no longer apply, though that is unconfirmed until the portal scan runs.

## Answers, by portal step

### Source

| Field | Answer |
|---|---|
| Repository | `ranawaqas-ai/mdreview-service` |
| Plugin path (optional) | `plugins/mdreview` |
| Branch or tag (optional) | `main` (the default branch; the marketplace add also reads it) |

Press Validate on the exact commit you intend to ship, and again after any push.

### Listing details (read from plugin.json and the README, not typed)

| Field | Value |
|---|---|
| Name | `mdreview` |
| Short description | Human-in-the-loop document review for agents: push a markdown or LaTeX draft, a person comments in the browser, the agent reads the comments and revises. |
| Long description | The README at `plugins/mdreview/README.md`. It explains the review loop, install, what the plugin runs, and what data leaves the machine. |
| Author | Rana Waqas, https://mdreview.space |
| Homepage | https://mdreview.space |
| Repository URL | https://github.com/ranawaqas-ai/mdreview-service |
| License | Apache-2.0 (`license` in plugin.json; the root `LICENSE` is not inside the plugin folder, so the field is what counts) |
| Keywords | review, markdown, latex, human-in-the-loop, comments |
| Category | The docs list no category field for plugins. Unconfirmed. If the portal asks, use productivity or developer tools. |
| Version | 0.1.0. Raise it with every release. |

Install commands (shown in the README):

```
/plugin marketplace add ranawaqas-ai/mdreview-service
/plugin install mdreview@mdreview
```

### Data handling (four questions in the docs)

1. Does the plugin read or store personal data? It stores nothing locally. It sends the text of documents and comments the user and Claude write, and any file the user asks Claude to attach, to the mdreview service. The service stores them under the user's account until the user deletes them (the service also keeps the account email, session IP and user agent, and a sign-in security log; see the privacy policy).
2. Does it send data to services other than its declared connectors? No. The local server contacts only https://app.mdreview.space, with the user's agent token in the request. The sign-in email is sent by the service through Azure Communication Services, which the plugin does not contact.
3. How long is data kept? Reviews, comments and attachments until the user deletes them (`delete_review` or the dashboard). Account deletion on request within 30 days. Sign-in email send records are deleted after two days.
4. Intended for people under 18? No. Not aimed at minors.

Privacy URL: https://mdreview.space/privacy/

### Compliance

Contact email: rana.waqas.works@gmail.com. Select all four acknowledgements after reading the Anthropic Software Directory Terms and Policy (support.claude.com articles 13145338 and 13145358, not read here).

### Review and submit

Choose GitHub push webhook (default) or Scheduled check only. The webhook needs admin on the repository. The auto-publish toggle defaults to a reviewer publishing each version, which is fine for a first listing.

## Reviewer notes

Sign in at https://app.mdreview.space with any email address (a one-time link is emailed), open Account, and create an agent token under Agent tokens. Paste it when Claude Code prompts for "mdreview agent token". Then ask Claude to create a review of a short markdown document and open the link. No paid account is involved. If a reviewer cannot receive email, the owner should mint a token and give it to them privately, not in the repository.

## What the automated checks will report

| Finding | Expected result | Why |
|---|---|---|
| Symlinks | Passes | Real files now; enforced by the sync selfcheck. |
| README of 40+ words | Passes | `plugins/mdreview/README.md`, added in this change. |
| License | Passes | `license` field. |
| Local MCP server command | Passes | `python3 ${CLAUDE_PLUGIN_ROOT}/mcp_server.py`, plain arguments, no shell, no package launcher. |
| Scripts the validator couldn't follow | Held for a reviewer | The plugin folder is a subfolder and the server is a non-shell Python file. Avoiding it needs the plugin at a repository root. Held is not a rejection. |
| Credentials | Passes | The token comes from a `sensitive` userConfig option, not a file or the environment. |
| Security scan: undisclosed behavior | Lower risk | `src/mcp/update.py` (downloads wrapper files and overwrites the installed copy) is no longer in the plugin. It still reads a local file for `attach_asset` and can open a browser (opt-in). Both are disclosed in the README. |
| Files | Passes | 6 Python files, all under 256 KiB, no binaries. |

## Also required by the docs, not done

The docs say that if you run a remote MCP server you should submit it as an MCP connector too. `https://app.mdreview.space/mcp` is that server (issue #396). The docs also say anyone on Pro or Max can submit, which removes the reason #396 was parked ("no Team plan"). The plugin itself uses the local stdio server, so it does not depend on the connector being listed.

## Before you submit

- [x] Symlink fix (real copies plus sync script); it still has to reach `main` so Validate passes there.
- [ ] Confirm the claude.ai account is Pro or Max (or higher) and that GitHub is connected with push access.
- [ ] Merge this branch to `main` (the tracked branch) through the normal dev to main release.
- [ ] `claude plugin validate . --strict` and `claude plugin validate plugins/mdreview --strict` pass.
- [ ] `python3 tests/plugin_manifest_selfcheck.py` passes.
- [ ] Open https://claude.ai/directory/manage, Submit new, Plugin bundle, fill Source, Validate, clear every Blocks finding.
- [ ] Read the Directory Terms and Policy, then tick the four acknowledgements.
- [ ] After submitting, note the portal status (Scanning, In review, Approved, Published) and press Publish when it says Approved. Help is at directory@anthropic.com.
- [ ] Bump `version` in plugin.json for every later change users should receive.

## Not confirmed

The portal's own screens (behind sign-in), the category field, the exact wording of the four acknowledgements, the two Anthropic policy articles, and whether the security scanner still flags anything now that `update.py` is gone from the plugin.
