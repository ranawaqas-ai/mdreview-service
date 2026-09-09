"""Opt-in git-tracked review history (#379): a clonable git remote per document, materialized
lazily (Overleaf's model, not Gollum's — see the approved design, mdreview review 51d5884ed9) from
a host-supplied HistorySource, never from a live write path.

Self-contained module wired into a host only when that host's own opt-in flag is set (mdreview:
MDREVIEW_ENABLE_GIT_HISTORY, mirroring MDREVIEW_ENABLE_LATEX's src/latex_review precedent). This
package imports nothing from mdreview — the whole coupling to a host's document model is the
HistorySource protocol (interfaces.py) plus the `authorize` callable `build()` takes below; a host
adapter (e.g. mdreview/git_history_adapter.py) does the translating on its own side of the seam.

`build` is the composition entry point: it assembles the lazy materializer (gitcache.GitCache) and
the git-http-backend proxy route (routes.GitHistoryRoutes) and returns the route object, which the
caller appends to its module-dispatch list (mirrors latex_review.build's return contract).
"""
from git_history.gitcache import GitCache
from git_history.routes import GitHistoryRoutes


def build(cache_dir, max_rounds, source, authorize):
    """cache_dir: filesystem dir for the per-review bare repos.
    max_rounds: cold-materialize cap (see gitcache.py's module docstring).
    source: a HistorySource implementation — the ONLY coupling to a host's document model.
    authorize(handler, doc_id) -> bool: injected read-access gate; on False it has already written
        the HTTP error response onto `handler`. Keeps this package free of any auth opinion.
    """
    cache = GitCache(cache_dir, max_rounds)
    return GitHistoryRoutes(cache, source, authorize)
