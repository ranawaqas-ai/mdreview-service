# Reviewer demo sign-in

## What it is

Directory reviewers must connect to mdreview and run its tools against a populated account. Normal
sign-in is a magic link sent to an inbox only the account holder can read, so reviewers cannot use
it. `/auth/demo` gives them one URL and one access code that lands them in a demo account holding
sample reviews.

The route exists only while the environment variable `MDREVIEW_DEMO_LOGIN_KEY` is set on the
server. With it unset or empty, `/auth/demo` answers 404 like any unknown path. A value shorter
than 32 characters makes the server refuse to start.

The demo account is `mdreview-demo@mdreview.space`, an ordinary account. It is not an admin and
has no super-read right. A session from `/auth/demo` is a normal session with the normal
30-day lifetime.

The code is read from the form body only, so it never appears in a URL, a log line or an audit
row. Five failed attempts from one client IP inside ten minutes lock that IP out of the route for
the rest of the window, even for the right code. There is no global lockout, so nobody can lock
the reviewers out from another address. Failures and successes are recorded in the auth audit as
`demo_login_fail` and `demo_login`, with the IP and user agent.

## Generate and store the key

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep it in `~/.mdreview-demo-login-key` on the operator's machine, mode 600. Never commit it or
paste it into an issue or a chat. The compose files only declare the variable name. The value
lives in the host `.env` next to the compose file.

## Turn it on (owner-gated, prod)

These steps change the live host, so the owner runs them or gives an explicit go-ahead.

1. Back up the host compose file and `.env`.
2. Sync the compose file from the repo if the host copy does not yet declare
   `MDREVIEW_DEMO_LOGIN_KEY`. The repo version does.
3. Add `MDREVIEW_DEMO_LOGIN_KEY=<the key>` to the host `.env`.
4. Recreate the container so it reads the new environment (`docker compose up -d --force-recreate`).
5. Seed the sample reviews from the operator's machine, with the key in the environment:

   ```
   MDREVIEW_DEMO_LOGIN_KEY="$(cat ~/.mdreview-demo-login-key)" \
     python3 scripts/seed_demo.py --base https://app.mdreview.space
   ```

   The script signs in through `/auth/demo`, mints itself a short-lived token, creates four
   reviews titled `[Demo] ...` through the API, then revokes its token. Rerunning replaces the
   samples. It writes nothing to the server's files.

The samples are a spec with a Mermaid diagram and one resolved thread, a short checklist with one
open comment to reply to, a review with an attached image, and a short notes review. There is no
LaTeX sample because LaTeX may be off on prod.

## What to tell reviewers

1. Open `https://app.mdreview.space/auth/demo` in a browser and enter the access code.
2. In the same browser, add the claude.ai custom connector `https://app.mdreview.space/mcp` and
   choose Allow on the consent screen. The connector's sign-in reuses the demo session.
3. Ask Claude to list reviews. The four `[Demo]` reviews should appear.

Send the code over a private channel, separate from the URL.

## Teardown (in this order)

The order matters. The wipe needs the route, and the 30-day session outlives the route.

1. While the route is still on, empty the account:

   ```
   MDREVIEW_DEMO_LOGIN_KEY="$(cat ~/.mdreview-demo-login-key)" \
     python3 scripts/seed_demo.py --base https://app.mdreview.space --wipe
   ```

2. Remove `MDREVIEW_DEMO_LOGIN_KEY` from the host `.env` and recreate the container. `/auth/demo`
   now returns 404.
3. Revoke what the demo account still holds, as an admin on `/admin`. Look up the demo uid on
   `/admin/users`, then:

   - `POST /admin/users/{uid}/revoke-sessions` ends every browser session.
   - `POST /admin/users/{uid}/revoke-tokens` deletes every API token, including the OAuth
     connector grants the reviewers created.

4. Delete `~/.mdreview-demo-login-key` if the key will not be reused.

If the code may have leaked, do steps 2 and 3 first and skip the wipe.
