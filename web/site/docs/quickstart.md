# Quickstart

## 1. Run an instance

```bash
make up         # serves on http://localhost:8137
# or, without the Makefile:
docker build -f infra/Dockerfile -t mdreview-service .
docker run -d -p 8137:8080 -v mdreview-data:/data mdreview-service
```

Health check: `curl localhost:8137/healthz` → `{"ok":true}`. Reviews persist in the `/data` volume
across restarts. See [Run](https://github.com/ranawaqas-ai/mdreview-service#run) for the full
options.

## 2. The review loop

The whole contract is five steps. `status` is cheap to poll; `feedback` returns the notes.

```bash
BASE=http://localhost:8137

# 1. Submit a draft. title/project/session/source_path are optional provenance shown on the dashboard.
resp=$(curl -s -X POST "$BASE/api/reviews" -H 'Content-Type: application/json' \
  -d '{"title":"My draft","markdown":"# My draft\n\nFirst paragraph...\n",
       "project":"my-repo","session":"run-42","source_path":"docs/my-draft.md"}')
# resp = {"id":"...", "review_url":"...", "status_url":"...", "feedback_url":"...", ...}

# 2. Hand review_url to the human. They open it and annotate.

# 3. Poll for feedback.
curl -s "$BASE/api/reviews/<id>/status"     # {"comments_updated":..., "turn":..., ...}
curl -s "$BASE/api/reviews/<id>/feedback"   # {"markdown":"...", "notes":[...], ...}

# 4. Apply the edits and push the new version. The human's page live-reloads; addressed notes
#    are struck through.
curl -s -X PUT "$BASE/api/reviews/<id>/source" -H 'Content-Type: application/json' \
  -d '{"markdown":"# My draft\n\nTighter first paragraph...\n"}'

# 5. (optional) clean up
curl -s -X DELETE "$BASE/api/reviews/<id>"
```

The POST response is your handle — persist the `id` and URLs on your side; the service keeps no
session for you. Treat `id` as opaque, and operate only on reviews you created.

## 3. Knowing when the human is done

There is no explicit "submit" — feedback streams as the human types. Two practical signals:

- **The turn baton (preferred).** When the human presses **"Send to agent"**, `turn` on
  `GET /status` flips to `"agent"` — an explicit "your move." See
  [the baton workflow](#/guide) in the Guide.
- **Quiet `comments_updated`.** The viewer bumps `comments_updated` on `/status` as the human
  comments; when it's been unchanged for a few minutes and is non-zero, treat the round as done.

## Next

- [Guide](#/guide) — comments, the turn baton, rich content, MCP, and self-hosting.
- [Troubleshooting](#/troubleshooting) — when something doesn't behave.
