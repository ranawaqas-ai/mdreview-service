# Other MCP clients and the OAuth redirect allowlist

Actions 1 and 2 shipped (see "What shipped" below); the rest is research only. Facts were gathered on 2026-09-28. "Verified" means read in the vendor's own docs or open-source code, or observed directly in a public HTTP response. Anything else is marked unverified.

## Summary

The allowlist itself blocks fewer clients than registration does. `_register` in `src/mdreview/hosted/oauth.py` rejects the whole request if any one submitted redirect URI fails `redirect_allowed`. Cursor and VS Code each register several URIs in one request, and at least one of each set is off our list, so both get a 400 before authorize is reached. Filtering the submitted set down to the allowed subset unblocks both without adding an allowlist entry. RFC 7591 section 3.2.1 says the server "MAY reject or replace any of the client's requested metadata values submitted during the registration and substitute them with suitable values".

Recommended actions, in order:

1. Filter at registration instead of rejecting (design change, no new trusted host).
2. Add `_` to the loopback path character class so Goose works.
3. Implement RFC 9207 `iss` in authorize responses, then consider one ChatGPT entry after a live test.
4. Add nothing else now. `vscode.dev/redirect`, `cursor://` and the Cursor web callback stay out.

The set of URIs to add to `ALLOWED_REDIRECTS` right now is empty. That is a deliberate answer.

| Client | Callback it sends | Works today? | Action | Risk |
|---|---|---|---|---|
| Claude Desktop and Code | `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`, Claude Code loopback `http://localhost:PORT/callback` | Yes | None | Existing |
| Cursor desktop | DCR sends three URIs (`cursor://anysphere.cursor-mcp/oauth/callback`, `https://www.cursor.com/agents/mcp/oauth/callback`, `http://localhost:8787/callback`); desktop authorizes with `http://localhost:8787/callback` | No, DCR returns 400 | Filter at registration | None added |
| VS Code desktop | DCR sends `https://insiders.vscode.dev/redirect`, `https://vscode.dev/redirect`, `http://127.0.0.1/`, `http://127.0.0.1:33418/`; desktop authorizes with `http://127.0.0.1:PORT/` | No, DCR returns 400 | Filter at registration | None added |
| VS Code web, remote, Codespaces | `https://vscode.dev/redirect` | No | Leave out | High if added (relay chosen by `state`) |
| ChatGPT | `https://chatgpt.com/connector_platform_oauth_redirect`, or `https://chatgpt.com/connector/oauth/{callback_id}` | No | Implement `iss`, then add the stable URI after a live test | Medium until tested |
| Gemini CLI | `http://localhost:<random>/oauth/callback` | Yes | None | Existing |
| Zed | `http://127.0.0.1:<random>/callback` (CIMD first, DCR fallback) | Yes | None | Existing |
| MCP Inspector | `http://localhost:6274/oauth/callback` (web), `http://127.0.0.1:6276/oauth/callback` (CLI, TUI) | Yes | None | Existing |
| Goose | `http://127.0.0.1:<port>/oauth_callback` | No, the underscore fails the path class | Add `_` to the path class | Negligible (loopback only) |
| Cline | Unverified | Unknown | None | n/a |
| Windsurf | Unverified | Unknown | None | n/a |

## What shipped

`_register` now drops well-formed redirect URIs that fail `redirect_allowed`, stores only the kept ones, and echoes only those in the 201 response. If none remain it returns the existing 400 `invalid_redirect_uri`. Malformed input is still a whole-request 400: a non-list, an empty list, a non-string element, an element over 2048 characters, or more than 10 URIs. Order does not matter, and authorize still checks `redirect_allowed` and `redirect_matches` on the requested URI, so a dropped URI is a 400 page with no redirect.

`_` is now in the loopback path class, so Goose's `/oauth_callback` registers. While checking the regex against the rejection list, two loosenesses turned up and were fixed in the same expression: the port used `\d`, which matches non-ASCII digits, and it accepted ports above 65535. The port is now `[0-9]` and at most 65535 (`_PORT`).

Still accepted, and not changed because fixing them is not a one-line change and the host is fixed to loopback: `..` path segments, `%2f` and `%5c` in the path, port 0 and leading-zero ports. None can change the host or split a header, and the path must match a registered path exactly.

Tests are in `tests/oauth_selfcheck.py` (`registration_filter`): Cursor-shaped and VS Code-shaped sets in both orders, all-disallowed, mixed attacker lists in both orders, malformed input, Goose, and a list of URIs that must stay refused. Mutation checks: storing and echoing the unfiltered list fails the "echoing only the allowed URIs" and "only the allowed URI is echoed" cases, and a backslash in the path class fails "'http://localhost/cb\\x' alone is refused".

Deliberately left out is unchanged: no new trusted host, no custom scheme, no `vscode.dev`, no ChatGPT entry, no `iss` (action 3).

## Findings by client

### Cursor

Verified facts.

- Cursor documents fixed callbacks "https://www.cursor.com/agents/mcp/oauth/callback" for web and agents and "http://localhost:8787/callback" for the desktop app, plus static `CLIENT_ID` config and scope discovery when `scopes` is omitted. https://cursor.com/docs/context/mcp
- A Cursor staff reply on the forum says MCP OAuth moved from `cursor://anysphere.cursor-mcp/oauth/callback` to the loopback URI because "a lot of OAuth providers won't accept a custom-scheme (cursor://) URI", and that with DCR Cursor registers all three URIs at once. Loopback is the default from 3.10.17. https://forum.cursor.com/t/oauth-redirect-uri-changed-from-cursor-to-http-localhost-for-streamable-http-mcp/165019
- A second forum thread (Cursor 3.13.25) has staff saying "reducing what we register during DCR is on us", so the multi-URI registration may change. https://forum.cursor.com/t/mcp-oauth-dcr-still-uses-custom-scheme-callback-on-3-13-25-breaks-standards-compliant-providers/167372
- An open-source server fixed the same rejection by allowing the exact `https://www.cursor.com/agents/mcp/oauth/callback` (third-party, corroborating only). https://github.com/browserless/browserless-mcp/pull/309
- `https://www.cursor.com/agents/mcp/oauth/callback` answers 308 to `https://cursor.com/agents/mcp/oauth/callback` (observed with a plain GET).

Unverified. Which of the three URIs Cursor sends in the authorize request on each surface (the docs and the staff reply say desktop uses loopback). Whether Cursor sends `resource` (its client is closed source).

### VS Code (GitHub Copilot agent mode)

Verified from the source of `microsoft/vscode` on `main`.

- The registration body (`fetchDynamicRegistration` in `src/vs/base/common/oauth.ts`) has `redirect_uris` `https://insiders.vscode.dev/redirect`, `https://vscode.dev/redirect`, `http://127.0.0.1/` and `http://127.0.0.1:33418/`, plus `token_endpoint_auth_method: 'none'`, `application_type: 'native'`, and `scope` when the resource metadata lists scopes. https://github.com/microsoft/vscode/blob/main/src/vs/base/common/oauth.ts
- On desktop without a remote, the first flow is a loopback server whose redirect URI is `http://127.0.0.1:<port>/` (`src/vs/workbench/api/node/extHostAuthentication.ts`, `loopbackServer.ts`). It prefers port 33418 and falls back to a random port (see https://github.com/microsoft/vscode/issues/278512).
- The URL-handler flow used for web and remote hardcodes `redirect_uri=https://vscode.dev/redirect` and puts a `vscode://...` app URI in `state` (`src/vs/workbench/api/common/extHostAuthentication.ts`).
- Both flows send `resource` (from the protected-resource metadata) on authorize and token requests, and send `scope` only when non-empty.

Direct observation of `vscode.dev/redirect` on 2026-09-28, with `state` set to various targets and a dummy `code`.

- It forwarded the `code` to any `http://127.0.0.1:<port>/...` or `http://localhost:<port>/...`, to any `vscode://<authority>/...` and `vscode-insiders://<authority>/...` (including `vscode://a.b@example.com/x`), and to any `https://vscode.dev/<path>`. `insiders.vscode.dev/redirect` behaved the same.
- It refused other hosts, including `localhost.example.com`, `localhost@example.com` and `//example.com` (it sends the browser to its own page that reports an unsupported target), and returned 403 for `javascript:`.
- This is current behavior of a Microsoft service, and no contract promises it stays.

Consequence. The relay target is chosen by `state`, which is set by whoever builds the authorize URL. Allowlisting `https://vscode.dev/redirect` would allowlist every `vscode://` handler on a victim's machine (any extension or app that claims the scheme), every local port, and every `https://vscode.dev/` path. Desktop VS Code does not need it because it uses loopback. Only web, remote and Codespaces sessions would benefit.

### ChatGPT (connectors, developer mode)

Verified from OpenAI's docs. https://developers.openai.com/apps-sdk/build/auth

- With RFC 9207 issuer identification (`authorization_response_iss_parameter_supported: true` in the metadata, and `iss` on every authorization response, success and error, equal to the metadata `issuer`), ChatGPT uses the stable redirect `https://chatgpt.com/connector_platform_oauth_redirect`. Without it, ChatGPT uses `https://chatgpt.com/connector/oauth/{callback_id}`. Servers published before the change also keep the stable URI.
- Registration is CIMD (preferred, with a `client_id` of `https://chatgpt.com/oauth/client.json`) or DCR when `registration_endpoint` is present. We advertise DCR only, which the docs allow.
- ChatGPT appends `resource=<mcp url>` to the authorize and token requests, requires `S256` in `code_challenge_methods_supported`, and requests advertised OIDC scopes by default. Our metadata advertises none, and `oauth.py` does not read `scope`.

Unverified. Whether ChatGPT's DCR body lists one redirect URI or both forms (if both, registration fails today for the same all-or-nothing reason). What the page at `chatgpt.com/connector_platform_oauth_redirect` does with a code. A plain GET returns 403 from here, so its behavior could not be observed, and it might forward the code to other origins or leak it through a Referer header or an analytics beacon.

`oauth.py` sends no `iss` today, so ChatGPT would select the `{callback_id}` form, which cannot be matched safely as one fixed string.

### Gemini CLI

Verified. The default redirect is `http://localhost:<random-port>/oauth/callback`, DCR is performed when supported, the `resource` parameter is built from the server URL, and scopes come from config. https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md and `packages/core/src/mcp/oauth-provider.ts` in the same repo (registers `redirect_uris: [redirectUri]`, one URI). Works today.

### Zed

Verified from `crates/context_server/src/oauth.rs` in `zed-industries/zed`. It tries CIMD (`https://zed.dev/oauth/client-metadata.json`) and falls back to DCR with `client_name: "Zed"` and a single loopback `redirect_uris: [http://127.0.0.1:<port>/callback]`. It sends `resource` on authorize and token requests, and picks scopes from the `WWW-Authenticate` `scope`, else `scopes_supported`. We do not advertise CIMD, so Zed falls back to DCR and works today. The port is random per https://github.com/zed-industries/zed/pull/51768.

### MCP Inspector

Verified. Web uses `http://localhost:6274/oauth/callback`, CLI and TUI use `http://127.0.0.1:6276/oauth/callback`, loopback hosts only, with DCR, static client id or CIMD. https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector/authorization Older versions registered `.../oauth/callback/debug` and then sent `.../oauth/callback` (https://github.com/modelcontextprotocol/inspector/issues/930, closed). `redirect_matches` compares the path exactly, so that old mismatch would still fail against us. Works today.

### Goose

Verified from `crates/goose/src/oauth/mod.rs` in `block/goose` (line 453). It binds `127.0.0.1` on a port and uses `http://127.0.0.1:{port}/oauth_callback`, registers via DCR with `client_name` "goose", and passes `iss` through. GitHub issue 5107, which says remote OAuth is unsupported, is out of date. `_LOOPBACK` allows `[A-Za-z0-9._~%/-]` in the path and `_` is not in that set, so `/oauth_callback` is refused today.

### Cline and Windsurf

Unverified. No primary source was found for either. Third-party pages suggest Cline uses `http://127.0.0.1:1456-1458/mcp/oauth/callback`, and a Cline issue quotes a `vscode://` redirect, so the extension and Cline Desktop may differ. `McpOAuthManager.ts` was not found at its old path. The Windsurf docs (moved to docs.devin.ai) say only that OAuth is supported for each transport. Neither gets a recommendation. Both are probably loopback (already accepted) or a custom scheme (left out below).

## Proposed changes

### 1. Filter at registration (recommended, design change)

In `_register`, keep the submitted URIs that pass `redirect_allowed`, store and return that subset as `redirect_uris`, and return `invalid_redirect_uri` only when the subset is empty. Authorize still calls `redirect_allowed` on the requested URI and `redirect_matches` against the stored set, so the trust boundary stays where it is.

Security analysis.

- No new URI becomes deliverable. The set of places that can ever receive a code is exactly what it is today.
- The client sees the trimmed list in the 201 response, which RFC 7591 permits.
- A client that registers `[evil, loopback]` gets a client with only the loopback URI instead of a 400. An attacker gains nothing, since they could register the loopback URI alone.
- The residual risk is compatibility. A client that ignores the returned `redirect_uris` and authorizes with a dropped URI gets a 400 page at authorize, the same outcome as today.
- Cursor desktop authorizes with `http://localhost:8787/callback`, which matches exactly. VS Code desktop authorizes with `http://127.0.0.1:<port>/`, and `redirect_matches` compares host and path for loopback, so a random fallback port matches the registered `http://127.0.0.1:33418/`.
- Cursor Agents (web) and VS Code web or remote stay unsupported until a callback host is trusted.

Test cases for `tests/oauth_selfcheck.py` (not written).

- Register `[https://claude.ai/api/mcp/auth_callback, https://evil.example/cb]` returns 201 and the response lists only the claude.ai URI.
- Register the exact Cursor set returns 201 with one URI, and authorize with `http://localhost:8787/callback` reaches consent.
- Register the exact VS Code set returns 201 with the two loopback URIs, and authorize with `http://127.0.0.1:59656/` reaches consent.
- After registering the Cursor set, authorize with `cursor://anysphere.cursor-mcp/oauth/callback` returns a 400 page with no `Location` header.
- After registering the VS Code set, authorize with `https://vscode.dev/redirect` returns a 400 page with no `Location` header.
- Register `[https://evil.example/cb]` alone returns 400 `invalid_redirect_uri`.
- Register `[http://evil.example\@localhost/cb, http://localhost/cb]` returns 201 with only `http://localhost/cb`.
- Register `[http://localhost/cb\r\nSet-Cookie: x=1, http://localhost/cb]` returns 201 with only `http://localhost/cb`, and no response header contains `Set-Cookie`.
- Mutation check. Make the filter keep everything and the foreign-URI cases fail.

### 2. Allow `_` in the loopback path (recommended, tiny)

Change the path class from `[A-Za-z0-9._~%/-]` to `[A-Za-z0-9._~%/_-]`. The pattern stays one whole-string match, still `http` with host `localhost` or `127.0.0.1` only, so the receiver is still a process on the user's own machine. `_` has no meaning that could change the host or split a header.

Test cases.

- Register `http://127.0.0.1:41000/oauth_callback` returns 201.
- Authorize with `http://127.0.0.1:41001/oauth_callback` on that client reaches consent (port-agnostic).
- Authorize with `http://127.0.0.1:41001/oauth-callback` on that client is refused (the path must match).
- `http://127.0.0.1:41000/oauth_callback\r\nX: y` and `http://127.0.0.1:41000/oauth_callback?x=1` are refused (no CR/LF, no query).

### 3. ChatGPT, conditional

Step one is an authorize change. Add `authorization_response_iss_parameter_supported: true` to the RFC 8414 metadata and append `iss` (equal to the issuer) to every redirect from `/oauth/authorize`, success and error. This makes ChatGPT select the stable URI, and it also gives every client a defense against mix-up attacks.

Step two is one whole-string entry, added only after the owner has run one connector flow in ChatGPT developer mode against staging and confirmed both the DCR body and the callback behavior.

```
https://chatgpt.com/connector_platform_oauth_redirect
```

Security analysis. An attacker who registers this URI can build an authorize link with their own PKCE challenge and `state`, and the code lands on a chatgpt.com page. The risk is what that page does with the URL. It runs OpenAI's code on an origin that also hosts user-generated content. If the route forwards, logs or leaks query strings to a party the attacker controls, the code is stolen. OpenAI documents this URI as the place to send codes, which is the same kind of trust we already give claude.ai, but its behavior could not be observed (403 to a plain GET), so this stays conditional. The `{callback_id}` form is never added because it needs a wildcard segment, and the `iss` route removes the need for it.

Test cases if added.

- Accept. Register and authorize with exactly `https://chatgpt.com/connector_platform_oauth_redirect`.
- Reject. A trailing slash, `?x=1`, `#x`, `https://chatgpt.com/connector_platform_oauth_redirect/../x`, a `%2e%2e` segment, `https://chatgpt.com.evil.example/connector_platform_oauth_redirect`, `https://chatgpt.com@evil.example/connector_platform_oauth_redirect`, `https://evil.example\@chatgpt.com/connector_platform_oauth_redirect`, `http://chatgpt.com/connector_platform_oauth_redirect`, `https://CHATGPT.com/connector_platform_oauth_redirect`, `https://chatgpt.com/connector/oauth/abc123`, and a CR/LF suffix.
- Success and error redirects both carry `iss=<issuer>`, and the metadata document advertises the flag.

## Left out, and why

| Candidate | Reason |
|---|---|
| `https://vscode.dev/redirect` and `https://insiders.vscode.dev/redirect` | The relay forwards the code to whatever `state` names, including any `vscode://` handler and any local port (observed). Allowing it widens the receiver set from "processes on this machine listening on loopback" to "any app that claims `vscode://`, and any `https://vscode.dev/<path>`", chosen by whoever builds the link. Desktop VS Code does not need it. |
| `cursor://anysphere.cursor-mcp/oauth/callback` | Any local app can claim a custom scheme, and Cursor's own staff moved off it. Cursor desktop no longer authorizes with it. |
| `https://www.cursor.com/agents/mcp/oauth/callback` | It serves cloud agents, and it redirects to bare `cursor.com`, so a client registering the `www.` form depends on the vendor keeping it. Revisit only with evidence of demand, and only for the exact form Cursor sends. |
| `https://chatgpt.com/connector/oauth/{callback_id}` | It needs a pattern, and the `iss` route removes the need. |
| Any custom scheme (`goose://`, `windsurf://`, `cline://`) | No client was shown to require one, and each carries the same local-claim risk. |
| Cline, Windsurf | Nothing verified. |

## What this research could not verify

- The authorize-time redirect URI of Cursor on every surface, and whether Cursor will change its DCR body soon.
- ChatGPT's DCR body, and how its callback page handles the code.
- Cline's and Windsurf's redirect URIs.
- Anything by live test. No client was registered against any server, and no request was sent to `app.mdreview.space` or staging. The only requests to third-party hosts were plain GETs of public pages, source files and `vscode.dev/redirect`.
