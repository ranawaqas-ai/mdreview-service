# Claude connector directory submission

Prepared 2026-09-29 against the live docs (claude.com/docs/connectors/building/submission, review-criteria, authentication, testing, managing-your-listing, connectors/verification, directory/submission-status, directory/publish, and the Software Directory Policy summary). Nothing has been submitted. This is the remote server `https://app.mdreview.space/mcp` submitted as an "MCP connector" (issue #396). The plugin is a separate submission, see `docs/submissions/plugin-directory.md`.

## What the docs say now

Anyone on Pro, Max, Team or Enterprise can submit at https://claude.ai/directory/manage (Submit new, then MCP connector). Free accounts cannot. On Pro and Max there is no role check. Every submission is scanned automatically and, by default, listed as a Community connector. Anthropic may escalate a listing it finds highly useful to Verified review, where a reviewer runs each tool. There is no application for Verified. Review time is not fixed. Escalations go to mcp-review@anthropic.com. Portal statuses for a connector are Draft, In review, Changes requested, Not approved, Approved (you press Publish) and Published.

The docs do not quote the seven compliance acknowledgements. They only list their subjects (directory guidelines, first-party API usage, financial transactions, AI media generation, prompt injection, conversation data collection, public documentation) and say all seven are required. The portal's own wording is behind sign-in and was not read. The answers below are keyed to those subjects. The review-criteria page also does not state a 240 s tool timeout or a 150k-character response cap; those two numbers come from the brief and are unverified against the docs. The docs do say Claude allows 10 s for discovery, registration and token endpoints and 30 s for refresh.

## Audit of the live server

Method. A throwaway local hosted instance on a free port (the same boot as `tests/oauth_selfcheck.py`, stub email, public base set to a test host), driven over `POST /mcp` with a token obtained through the real DCR, PKCE consent, token and refresh flow. Nothing was sent to app.mdreview.space. The audit script stayed in `.scratch/` and is not committed. Local latencies were 1 to 10 ms, so they prove nothing about production network time.

Counts. 17 PASS (row 20 only locally), 6 RISK, 1 FAIL, 4 unverified.

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Tool names 64 characters or fewer | PASS | 21 tools, longest is 16 characters. |
| 2 | Read and write tools are separate | PASS | No catch-all request tool. 11 read-only, 7 non-destructive writes, 3 destructive (`update_source`, `delete_review`, `delete_comment`). |
| 3 | `title` and a hint on every tool | PASS | No tool lacks `title`, `annotations.title` or `readOnlyHint`. Destructive tools carry `destructiveHint: true`. `openWorldHint: false` throughout. |
| 4 | Custom query tools name their API | PASS | No tool takes a freeform path or body. |
| 5 | Descriptions free of prompt-injection patterns | RISK | See the wording table below. None asks Claude to call software the user did not request, but several use capitalised MUST, NEVER and FIRST directives. |
| 6 | Descriptions match behaviour | FAIL | `attach_asset` says "PREFER `path`: pass a local file path and this server reads + encodes the bytes". Over `/mcp` a `path` call always errors ("attach_asset over the remote endpoint needs `content_b64`"). A reviewer following the description gets a failure. |
| 7 | Descriptions match behaviour, remote context | RISK | `server_info` talks about `~/.mdreview`, `mcp_server.py --print-version` and reconnecting a managed wrapper. None of that applies to a remote connector. `get_git_url` says clone with an `Authorization: Bearer <your token>` header, which a scanner may read as steering Claude to handle credentials. |
| 8 | Errors are actionable | RISK | Review-level errors are terse: `HTTP 404 from GET /api/reviews/nope: {"error": "not found"}`. Comment-level errors are worse: `HTTP 404 from GET /api/reviews/<id>/comments/cnope: {"error": "no route", "method": "GET", "path": "..."}`. "no route" says nothing about a bad comment id. |
| 9 | Inputs are validated, not silently accepted | RISK | `list_comments` with `status: "weird"` returns `{"comments": []}`. `create_review` with `markdown: ""` succeeds. `create_comment` with a `quoted_text` that is not in the document succeeds. Missing required arguments return a JSON-RPC `-32602` ("Missing required argument: 'markdown'") rather than a tool result, which is clear enough. |
| 10 | Responses reasonably sized | RISK | Nothing is truncated. A 271,007-character draft comes back whole from `get_source` (with revision, 272,038) and from `get_history` with `round`. `list_reviews` with 62 reviews is 19,686 characters, which grows linearly and is unpaginated. `list_comments` with 50 long comments is 39,734. `tools/list` is 19,926 characters and `initialize` instructions are 886. Only very large drafts cross roughly 150k. |
| 11 | Tool calls finish inside the limit | PASS | All calls returned in under 10 ms locally. The backend hop has a 30 s client timeout, well under 240 s. LaTeX compile time on `create_review` and `update_source` was not exercised (LaTeX is off locally), so unverified. |
| 12 | Unauthenticated request gets 401 with `resource_metadata` | PASS | `POST /mcp` with no token and with a bogus token both return 401 with `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/oauth-protected-resource/mcp"`. `GET /mcp` returns 405 with `Allow: POST`, which the transport spec permits. |
| 13 | Protected resource metadata | PASS | Both `/.well-known/oauth-protected-resource` and `.../mcp` return 200. `resource` equals `<base>/mcp` exactly and `authorization_servers` has one entry, the issuer. |
| 14 | Authorization server metadata | PASS | RFC 8414 document at `/.well-known/oauth-authorization-server` has `registration_endpoint`, `code_challenge_methods_supported: ["S256"]` and `token_endpoint_auth_methods_supported: ["none"]`. `/.well-known/openid-configuration` returns 404, which Claude tolerates because it tries RFC 8414 first. |
| 15 | Dynamic client registration | PASS | Registering `https://claude.ai/api/mcp/auth_callback` returns 201 with a `client_id`. Claude Code loopback URIs (`http://localhost:3118/callback` and `http://127.0.0.1:3118/callback`) also register. |
| 16 | PKCE S256 and consent | PASS | Consent page shows the app name, the account and the redirect host ("You will be sent back to claude.ai"). Code exchange with the right verifier gives `access_token`, `refresh_token`, `expires_in: 3600`. |
| 17 | Token endpoint content type | PASS | Accepts `application/x-www-form-urlencoded`. A JSON body gets `unsupported_grant_type`, which is a valid RFC 6749 code. |
| 18 | Refresh rotation and error codes | PASS | Refresh returns a new access and refresh token. A bad or spent refresh token returns 400 `invalid_grant`. |
| 19 | CIMD not advertised | PASS | `client_id_metadata_document_supported` is absent, so Claude uses DCR and the server never fetches a client URL. |
| 20 | Endpoint latency under 10 s (30 s refresh) | PASS locally, unverified on prod | Discovery, registration, token and refresh took 1 to 2 ms locally. Production network time and the magic-link consent path were not measured. |
| 21 | Refresh race | RISK | A refresh revokes the previous access token at once (old token gets 401 after refresh, confirmed). If Claude ever fires two refreshes with the same refresh token in parallel, the second gets `invalid_grant` and the user must reconnect. Not reproduced, only inferred from the code. |
| 22 | No conversation data collection, no memory or file access | PASS | No tool reads Claude's memory, history or user files. `attach_asset` `path` is refused on the remote endpoint, so the server never reads its own disk. |
| 23 | Own first-party API, domain matches | PASS | Every tool calls this service on loopback. The MCP domain is app.mdreview.space. |
| 24 | Unsupported use cases | PASS | No money movement and no AI media generation. |
| 25 | Redirect from the registered URL | Unverified | Production must answer `https://app.mdreview.space/mcp` directly with no 3xx to another host (Claude drops the Authorization header on a cross-host redirect). Not tested because the brief forbids requests to production. Owner check, see the checklist. |
| 26 | WAF or CDN lets Anthropic through | Unverified | Anthropic egress is `160.79.104.0/21`. The origin check on `/mcp` allows requests with no `Origin` or with claude.ai and claude.com. Whether nginx or Hostinger filters block that range was not tested. |
| 27 | `get_git_url` works on prod | Unverified | Locally it returns `HTTP 404 ... no route` because git history is off. If prod has it off too, a reviewer running every tool sees an error. |
| 28 | Public documentation live | Unverified | `https://mdreview.space/privacy/` returns 200. The docs page is a hash-routed single page, and whether the deployed copy already has the connector section (merged on dev, ships with the next release to main) was not confirmed. |

### Proposed minimal wording changes (for the coordinator to apply in `src/`)

These are suggestions only. The goal is to describe what each tool does and drop capitalised commands aimed at the model. The behavioural rule can stay in a plain sentence.

| Tool | Current text | Proposed text |
|---|---|---|
| `attach_asset` | "PREFER `path`: pass a local file path and this server reads + encodes the bytes itself ... Use `content_b64` only if the file isn't on this machine." | Serve a remote-specific description over `/mcp`. "Attaches an image to a review so the viewer renders it. Pass the file bytes as `content_b64` and `name` (the exact src the draft uses). `path` works only on the local stdio server." Also make the `path` property description say the same. |
| `update_source` | "on a 409 you MUST re-read the source, re-apply your change onto the new text ... NEVER resend a buffered draft after a 409" | "A stale `expected_revision` returns HTTP 409 and writes nothing. Re-read with `get_source` (with_revision=true) and re-apply the change before saving again, so the reviewer's edit is not overwritten." |
| `create_review` | "you MUST pass kind=\"latex\"; a latex-enabled server REJECTS such a create when kind is omitted" and "AUTHOR TO THE VIEWER'S RENDERER, don't dumb the markdown down" and "Warn the human first and offer to link the two." | "If the content is LaTeX (a .tex source_path or a \\documentclass body), set kind=\"latex\"; a latex-enabled server rejects it otherwise." Shorten the authoring note to "The viewer renders GFM, Mermaid diagrams, LaTeX math, footnotes and highlighted code." Drop the warn-the-human sentence and keep the immutability fact. |
| `list_comments` | "Call this FIRST to see what the reviewer raised ... only address what the reviewer actually flagged." | "Lists comments on a document, filtered by `status` (open by default, resolved, reopened, all)." |
| `delete_comment` | "HARD-delete ... Use it only on a comment you created by mistake, never to dismiss the reviewer's feedback (resolve that)." | "Permanently deletes a comment and its thread. This cannot be undone; `resolve_comment` hides a comment without deleting it." |
| `hand_back` | "Call it when you're done ... or when blocked." | "Sets the turn to the reviewer and shows `message` in their banner. `state` is done (default) or blocked." |
| `server_info` | Mentions `~/.mdreview`, `--print-version`, "RECONNECT the MCP client". | "Reports the server's name, version, protocol version, tool count and tools hash." |
| `get_git_url` | Includes the `git -c http.extraheader="Authorization: Bearer <your token>" clone` recipe. | "Returns a git clone URL for the review's history (one commit per round). Available only when git-tracked history is enabled; otherwise the call returns 404." Move the token recipe to the docs page. |
| Instructions block | "read it before first use" | Drop that clause. |

Error messages. Have the API say what was wrong and what to do: for a missing review, `no review with id "nope"; list ids with list_reviews`. For a missing comment, `no comment "cnope" on this review; list ids with list_comments`. Reject an unknown `status` with `status must be one of open, resolved, reopened, all`. Reject empty `markdown` on `create_review`.

Response size. Add a length note to `get_source` and `get_history` and, if it is cheap, a `max_chars` guard that returns the first N characters with a clear "truncated, N of M characters" line. Without it, a draft above roughly 150k characters is at risk when Claude reads it.

## Portal answers

### 1. Connection

| Field | Answer |
|---|---|
| Server URL | `https://app.mdreview.space/mcp` |
| URL option | Universal URL (every user connects to the same URL) |
| Transport | Streamable HTTP (JSON responses over POST, no SSE stream, no session id; `GET` returns 405) |

Do not enter a URL that redirects. Confirm with `curl -sI https://app.mdreview.space/mcp` that the answer is 401 or 405 from the same host (owner check).

### 2. Tools

Nothing to type. The portal syncs the 21 tools from the server. All have a title and an annotation. Read-only (11) are `get_comment`, `get_feedback`, `get_git_url`, `get_history`, `get_review`, `get_source`, `get_status`, `list_assets`, `list_comments`, `list_reviews`, `server_info`. Write (7) are `attach_asset`, `create_comment`, `create_review`, `hand_back`, `ping_working`, `reply_to_comment`, `resolve_comment`. Destructive (3) are `delete_comment`, `delete_review`, `update_source`. The server exposes tools only (no prompts or resources).

### 3. Listing

| Field | Answer |
|---|---|
| Name (max 100) | `mdreview` (8 characters) |
| One-liner (max 200) | see below (164 characters) |
| Description (max 2,000) | see below (1,305 characters) |
| Categories (1 to 5) | The docs give no category list, so choose the closest in the portal. Suggested: Productivity, Developer tools, Writing and documents. Unverified. |
| Documentation URL | `https://mdreview.space/docs/#/mcp` |
| Privacy policy URL | `https://mdreview.space/privacy/` (returns 200, last updated 25 September 2026) |
| Support contact | rana.waqas.works@gmail.com |
| Icon | `extensions/mdreview-mcpb/icon.png`, 512 x 512 PNG with alpha. The docs state no icon size, so 512 x 512 is unverified as accepted. |
| URL slug | `mdreview` (permanent once published; check it is free in the portal) |

One-liner (164 characters):

```text
Let a person review what your AI agent writes. The agent posts a markdown or LaTeX draft, you comment in your browser, and the agent reads the comments and revises.
```

Description (1,305 characters):

```text
mdreview adds a human review step to work that Claude drafts. Claude creates a review from a markdown document (or a LaTeX paper) and gives you a link. You open it in your browser, read the rendered draft, and leave comments anchored to the exact passages you mean. Claude then reads your open comments, edits the draft, replies to or resolves each thread, and hands the turn back to you. The page updates live while Claude works.

With this connector Claude can create and update reviews, read the current draft and its past rounds, read and write comment threads, attach images the draft uses, and give the turn back to you. Diagrams (Mermaid), math, footnotes and syntax-highlighted code render in the viewer. Every review keeps its history, so you can see what changed between rounds.

Everything runs against your own mdreview account at app.mdreview.space. Reviews are private to you unless you share them. You approve the connection on a consent screen when you add it, and you can revoke it at any time on the Account page of mdreview. Some tools delete or replace data (update_source, delete_review, delete_comment), and Claude asks before it runs them.

Sign in to mdreview with any email address (a one-time link is emailed to you). No paid plan is needed. mdreview is open source (Apache-2.0).
```

### 4. Use cases

Primary use cases. Review a draft that Claude wrote (a spec, plan, design doc, README, blog post or LaTeX paper) with a person who comments in the browser. Iterate until the reviewer is satisfied. Keep a history of each round.

What users need before connecting. An mdreview account, which anyone can create at https://app.mdreview.space with any email address (a one-time sign-in link is sent to that address, so they need to be able to receive email). No paid mdreview plan, no API key and no other setup.

Reads or writes. Both. Read tools list and fetch reviews, drafts, history, comments, assets and status. Write tools create reviews and comments, replace a draft, attach images, reply to and resolve comments, and pass the turn. Three tools delete or overwrite data and are annotated destructive.

### 5. Company

| Field | Answer |
|---|---|
| Company name | Rana Waqas (individual open-source developer, no company entity) |
| Website | https://mdreview.space |
| Primary contact for review updates | rana.waqas.works@gmail.com |

The portal may require an organization name. If it does, use the claude.ai account's own organization name. Unverified.

### 6. Authentication

Choose OAuth with dynamic client registration (`oauth_dcr`). Do not choose CIMD (not advertised), Anthropic-held credentials, custom connection or none. Endpoints are on the same host as the server. Sign-in is app-owned (an emailed link), then a consent page. Access tokens last 1 hour, refresh tokens 30 days and rotate. Users can revoke a connection on the mdreview Account page, where it is listed as "OAuth: Claude". The server does not start unauthenticated and does not use lazy authentication, so do not tick that option.

### 7. Data handling

| Question | Answer |
|---|---|
| Whose API is behind the connector | Our own. mdreview is the developer's own first-party service. No partner API and no third-party API is proxied. |
| Personal health data | No. The service does not target or collect it. Users could type anything into a document, which the privacy policy covers as user content. |
| Sponsored content | No. No ads, no promotion, no analytics. |

### 8. Test and launch

Reviewer instructions to paste (fill the placeholder before submitting):

```text
[DEMO LOGIN LINK AND STEPS: to be filled when the reviewer demo login ships]

Sign-in is by emailed one-time link, which a reviewer cannot read, so the demo login above stands in for it. It opens an account that already holds sample reviews with comments, history and an attached image.

Add the server in Claude at Customize > Connectors > Add custom connector with URL https://app.mdreview.space/mcp. Claude registers itself; sign in and press Allow on the consent page.

To exercise every tool, ask Claude to (1) list reviews, (2) create a review from a short markdown document with a Mermaid block, (3) open the returned review_url in a browser, add a comment on a phrase, (4) list comments, get one comment, reply to it, resolve it, (5) update the source, read the source and its history, (6) attach a small PNG (pass content_b64; local file paths are not readable by the remote server), (7) hand the turn back and ping working, (8) get status, feedback and the git URL, (9) delete the comment and the review, (10) call server_info.
```

Confirmation to tick. "I have run every tool myself, either through MCP Inspector or as a custom connector in Claude." This is true only after the owner does it. Nobody has run all 21 tools through a claude.ai custom connector yet (the todo says prod has so far been tested on staging only). The 21 tools were run through `/mcp` on a local instance by direct JSON-RPC in this audit, which is not the same thing and does not satisfy the acknowledgement.

### 9. Compliance (seven acknowledgements)

The portal text was not readable, so each line gives the subject named in the docs and the honest answer. Tick all seven only after the wording fixes in the audit are applied, because the prompt-injection line depends on them.

| # | Subject | Honest answer for mdreview |
|---|---|---|
| 1 | Directory guidelines (Software Directory Terms and Policy) | Read the Terms (support.claude.com article 13145338) and Policy (13145358) first. The Policy summary was read here; the Terms were not. mdreview meets the listed server standards (tool names 16 characters or fewer, OAuth 2.0, Streamable HTTP, annotations). |
| 2 | First-party API usage | Yes. The connector calls only mdreview's own service, on the same domain. No third-party or partner API. |
| 3 | Financial transactions | No. It does not move money, crypto or other assets. |
| 4 | AI media generation | No. It does not generate images, video or audio. It renders diagrams the agent writes as Mermaid text, which the docs allow. |
| 5 | Prompt injection | Today the honest answer is not clean. Several descriptions carry capitalised MUST, NEVER and FIRST directives (see rows 5 to 7 of the audit). They concern the tool's own function and the reviewer's data, none tells Claude to call other software or fetch instructions, but a scanner could flag them. Apply the wording changes, then tick. |
| 6 | Conversation data collection | The connector collects only what the tool call carries (the document, comments and files Claude sends). It does not read Claude's memory, chat history or user files, and `attach_asset` `path` is refused on the remote server. Data use is described in the privacy policy. |
| 7 | Public documentation | Docs at https://mdreview.space/docs/#/mcp and the privacy policy are public. Confirm the deployed docs already include the connector section (merged on dev, ships in the next release to main) before ticking. |

## Before you submit

- [ ] Confirm the claude.ai account is on Pro, Max or higher.
- [x] Wording fixes from the audit are applied in `src/mcp/tools.py` (and mirrored into the plugin copy). No capitalised MUST/NEVER/FIRST/PREFER directives remain in the tool descriptions except one `NOT` in `ping_working`.
- [ ] Error messages, input validation (`status`, empty `markdown`) and a size guard from the audit are not done; they change API behaviour and need their own PR.
- [ ] Release dev to main and confirm tools/list on prod shows the new wording.
- [ ] Ship the reviewer demo login and fill the placeholder in the Test & launch text. Reviewers cannot read the emailed sign-in link.
- [ ] `curl -sI https://app.mdreview.space/mcp` returns 401 or 405 from the same host with no 3xx redirect.
- [ ] `curl -si -X POST https://app.mdreview.space/mcp` (no token) returns 401 with a `resource_metadata` header, and both `.well-known` documents return 200 from a public network.
- [ ] Nothing at the edge blocks `160.79.104.0/21` on `/mcp`, `/oauth/*` or `/.well-known/*`.
- [ ] Add the connector to your own claude.ai as a custom connector on prod and run every tool from a chat. Note any tool that errors (start with `get_git_url`). Then tick the "I ran every tool" box.
- [ ] Confirm https://mdreview.space/docs/#/mcp shows the connector section and https://mdreview.space/privacy/ loads.
- [ ] Read the Directory Terms and Policy, then tick the seven acknowledgements.
- [ ] Open https://claude.ai/directory/manage, Submit new, MCP connector, and paste the answers above. Read the warnings on Review and submit; they go to the reviewers.

## After you submit

Anthropic scans the submission automatically for policy compliance and lists it as a Community connector by default, with no action from you. Some submissions also get a person's review; times vary. Watch the status in the portal. If it shows Changes requested or Not approved, press View feedback, fix, and resubmit. When it shows Approved, press Publish. Verified status is Anthropic's call. There is no application, and a Community listing works the same as a Verified one for users. Once published, the portal shows a health badge (Healthy is 2% or fewer request errors over 30 days) and usage by tool. Edits to name, description or icon go through review again, and the slug cannot change. Email mcp-review@anthropic.com for stuck submissions.

## Not confirmed

The portal's own screens and the exact wording of the seven acknowledgements, the category list, the icon size rule, whether an organization name is required for an individual, the Software Directory Terms article, the 240 s tool timeout and 150k-character cap (not in the docs read), anything about production network behaviour (redirects, WAF, latency), LaTeX-mode tool behaviour, and whether `get_git_url` works on prod.
