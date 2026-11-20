#!/usr/bin/env python3
"""Generate web/site/releases/index.html from the GitHub Releases API (#373).

The page used to be hand-authored HTML, one block per version, current only as of whoever last
remembered to edit it. It stalled at v0.2.0 while production ran v0.5.3 -- eight tagged releases
invisible to anyone reading the site. This script replaces the habit: the GitHub Release for a
tag (release.yml creates one after the images publish, see #373) is the single source of truth,
and every downstream artifact is generated from it.

Runs at Pages-deploy time (.github/workflows/pages.yml), on every push to web/site/** and on
every `release: published`/`edited` event. Nothing this script produces is committed back to the
repo -- `main` requires signed commits (branch protection, required_signatures), which would make
a bot commit here awkward for zero benefit: generating fresh on every deploy is simpler, can
never go stale between a release and the next unrelated web/site/** edit, and there is no
committed file for anyone to hand-edit and quietly re-diverge from the API (the exact failure
this issue is about). The generated file is gitignored-in-spirit: it exists in the runner's
checkout for one job, gets uploaded to Pages, and the checkout is discarded.

Usage:
    python3 scripts/gen_releases_page.py <output.html> --repo OWNER/NAME

Offline / test mode, no network call (used by tests/releases_page_tripwire_selfcheck.py so the
generator's real code path is exercised without depending on live GitHub state):

    python3 scripts/gen_releases_page.py <output.html> --releases-file releases.json

GH_TOKEN or GITHUB_TOKEN in the environment, if set, is sent as a bearer token -- this raises the
unauthenticated GitHub API rate limit (60/hour/IP) to 5000/hour. The fetch also works without one
since release data on a public repo needs no auth; pages.yml sets GH_TOKEN from secrets.GITHUB_TOKEN
anyway since it costs nothing to.
"""
import argparse
import html
import json
import os
import sys
import urllib.request
from datetime import datetime

API_ROOT = "https://api.github.com"
GITHUB_REPO_URL = "https://github.com/%s"


def _headers(token):
    headers = {
        "Accept": "application/vnd.github.html+json",  # adds body_html: GitHub's own markdown
        "X-GitHub-Api-Version": "2022-11-28",           # renderer, so this script needs none.
        "User-Agent": "mdreview-releases-page-generator",
    }
    if token:
        headers["Authorization"] = "Bearer %s" % token
    return headers


def fetch_releases(repo, token=None):
    """All releases for `repo` ("OWNER/NAME"), GitHub's rendered body_html included. Drafts are
    dropped here, at the source: nothing unpublished belongs on a public page, and every caller
    (render_page, the tripwire) can then assume the list it receives is public-safe."""
    req = urllib.request.Request(
        "%s/repos/%s/releases?per_page=100" % (API_ROOT, repo),
        headers=_headers(token),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [r for r in data if not r.get("draft")]


def _fmt_date(iso_ts):
    """"2026-07-24T14:36:38Z" -> "Jul 24, 2026" (the hand-authored page's date format)."""
    if not iso_ts:
        return ""
    dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
    return "%s %d, %d" % (dt.strftime("%b"), dt.day, dt.year)


HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>mdreview: releases</title>
<meta name="description" content="What's shipped in mdreview, newest first: release notes for the human-in-the-loop markdown review service.">
<link rel="canonical" href="https://mdreview.space/releases/">
<meta property="og:type" content="website">
<meta property="og:url" content="https://mdreview.space/releases/">
<meta property="og:title" content="mdreview releases">
<meta property="og:description" content="Release notes for mdreview, newest first.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%237c6cff'/%3E%3Ctext x='16' y='23' font-size='17' font-family='sans-serif' font-weight='700' fill='%23fff' text-anchor='middle'%3Emd%3C/text%3E%3C/svg%3E">
<style>
  :root{
    --bg:#fafafc; --panel:#ffffff; --panel2:#f6f6fb; --text:#17181d; --muted:#6b7280; --muted2:#8a90a0;
    --rule:#e7e8ee; --accent:#7c6cff; --accent2:#9d7bff; --accent-weak:#f1efff; --code:#f4f4f8;
    --glow:rgba(124,108,255,.14);
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#0e0f13; --panel:#171922; --panel2:#14161d; --text:#e6e8ee; --muted:#9aa0ac; --muted2:#7b8290;
      --rule:#262a33; --accent:#9184ff; --accent2:#b39bff; --accent-weak:#221f38; --code:#1b1d24;
      --glow:rgba(145,132,255,.16);
    }
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{background:var(--bg);color:var(--text);line-height:1.6;
       font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased;min-height:100vh;display:flex;flex-direction:column}
  a{text-decoration:none;color:var(--accent)}
  code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:2px 6px;border-radius:5px}
  .eyebrow{font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
  .card{background:var(--panel);border:1px solid var(--rule);border-radius:16px;padding:28px 28px 26px}
  .pill{font-size:12px;font-weight:600;letter-spacing:.02em;padding:4px 11px;border-radius:20px;
        background:var(--accent-weak);color:var(--accent);white-space:nowrap}
  .pill.ghost{background:var(--panel2);color:var(--muted);border:1px solid var(--rule)}
  .ver{font:600 15px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent)}
  .flink{display:inline-flex;align-items:center;gap:6px;font-size:14px;font-weight:500;color:var(--muted);
         padding:7px 12px;border:1px solid var(--rule);border-radius:9px}
  .flink:hover{border-color:var(--accent);color:var(--accent)}
  /* timeline: releases are a real sequence, so the rail encodes chronology */
  .timeline{position:relative;padding-left:30px}
  .timeline::before{content:"";position:absolute;left:8px;top:6px;bottom:6px;width:2px;background:var(--rule)}
  .entry{position:relative;margin-bottom:26px}
  .entry::before{content:"";position:absolute;left:-30px;top:8px;width:14px;height:14px;border-radius:50%;
                 background:var(--panel);border:2px solid var(--rule)}
  .entry.latest::before{border-color:var(--accent);box-shadow:0 0 0 4px var(--glow)}
  /* release notes: rendered from the Release body (GitHub's own markdown->html, see body_html in
     fetch_releases), so unlike the rest of this page these tags are not ours to lay out by hand */
  .relnotes{color:var(--muted);font-size:15px;margin-top:14px}
  .relnotes>*+*{margin-top:12px}
  .relnotes h1,.relnotes h2,.relnotes h3{color:var(--text);font-weight:660;letter-spacing:-.01em;margin-top:20px}
  .relnotes h1{font-size:19px}.relnotes h2{font-size:17px}.relnotes h3{font-size:15.5px}
  .relnotes ul,.relnotes ol{padding-left:22px}
  .relnotes li+li{margin-top:4px}
  .relnotes code{font-size:12.5px}
  .relnotes pre{background:var(--code);padding:12px 14px;border-radius:8px;overflow-x:auto}
  .relnotes pre code{background:none;padding:0}
  .relnotes blockquote{border-left:3px solid var(--rule);padding-left:14px;color:var(--muted2)}
  .relnotes a{color:var(--accent)}
  @media(max-width:640px){
    [data-grid2]{grid-template-columns:1fr!important}
    [data-h1]{font-size:32px!important}
    .card{padding:22px 20px}
  }
</style>
<script defer src="https://stats.waqasrana.space/script.js" data-website-id="10879eaf-0785-4f0a-b6db-3a303993f931" data-domains="mdreview.space"></script>
</head>
<body>
"""

HEADER = """
  <header style="border-bottom:1px solid var(--rule);background:var(--bg);position:sticky;top:0;z-index:20;backdrop-filter:saturate(120%%)">
    <div style="max-width:900px;margin:0 auto;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:64px">
      <a href="/" style="display:flex;align-items:center;gap:10px;color:var(--text);font-weight:650;font-size:16px">
        <span style="display:inline-grid;place-items:center;width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-size:12.5px;font-weight:700;box-shadow:0 3px 10px var(--glow)">md</span>
        mdreview
      </a>
      <nav style="display:flex;gap:8px;font-size:14.5px;align-items:center">
        <a href="/docs/" style="color:var(--muted);padding:8px 12px;border-radius:8px">Docs</a>
        <a href="/releases/" style="color:var(--text);padding:8px 12px;border-radius:8px;font-weight:600">Releases</a>
        <a href="https://github.com/%(repo)s" style="color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));padding:8px 15px;border-radius:9px;font-weight:600;box-shadow:0 4px 14px var(--glow)">GitHub</a>
      </nav>
    </div>
  </header>

  <main style="flex:1 0 auto;max-width:900px;width:100%%;margin:0 auto;padding:56px 24px 12px">

    <div style="max-width:640px;margin-bottom:40px">
      <p class="eyebrow">Changelog</p>
      <h1 data-h1 style="font-size:40px;line-height:1.12;letter-spacing:-.025em;font-weight:730;margin-top:8px">Releases</h1>
      <p style="color:var(--muted);font-size:17px;margin-top:14px">What's shipped in mdreview, newest first. Docker images publish to
        <a href="https://github.com/%(repo)s/pkgs/container/mdreview-service">GHCR</a> on every tagged release.</p>
    </div>

    <div class="timeline">
"""

FOOTER = """
    </div>
  </main>

  <footer style="border-top:1px solid var(--rule);color:var(--muted);font-size:13.5px;margin-top:40px">
    <div style="max-width:900px;margin:0 auto;display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;padding:20px 24px">
      <a href="https://github.com/%(repo)s" style="color:var(--muted)">github.com/%(repo)s</a>
      <span style="flex:1"></span>
      <span>mdreview.space</span>
      <a href="https://github.com/%(owner)s" style="color:var(--muted)">built by waqas</a>
      <a href="https://github.com/%(repo)s/blob/main/LICENSE" style="color:var(--muted)">Apache 2.0</a>
    </div>
  </footer>

</body>
</html>
"""

EMPTY_STATE = """
      <div class="card">
        <p style="color:var(--muted)">No releases published yet.</p>
      </div>
"""

ENTRY = """
      <section class="entry%(latest_class)s">
        <div class="card">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
            <span class="ver">%(tag)s</span>
            %(pill)s
            <span style="flex:1"></span>
            <span style="color:var(--muted2);font-size:13.5px">%(date)s</span>
          </div>
          <h2 style="font-size:24px;line-height:1.2;letter-spacing:-.02em;font-weight:700">%(title)s</h2>
          <div class="relnotes">%(body_html)s</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">
            <a class="flink" href="%(html_url)s">Release notes on GitHub →</a>
            <a class="flink" href="/docs/">Docs</a>
          </div>
        </div>
      </section>
"""


def render_page(releases, repo):
    """The full page, releases ordered newest-first. This sort is this function's OWN,
    independent of any other module's idea of "newest" (see releases_page_tripwire.py's
    docstring for why that independence matters: a generator and its check sharing one sort/
    comparator can both go wrong together and neither would notice)."""
    owner = repo.split("/", 1)[0]
    # Sort and date by created_at, not published_at. GitHub sets created_at from the tagged
    # commit/tag object regardless of when the Release object itself was created, so it survives
    # a backfill: the 8 Releases #373 backfilled long after their tags shipped all got
    # published_at values clustered in the minute the backfill ran (today), which would have
    # sorted correctly here only by the accident of running the backfill in version order.
    # created_at instead reflects when each version actually shipped, both for those backfilled
    # releases and for every future one created immediately after its tag.
    ordered = sorted(releases, key=lambda r: r.get("created_at") or "", reverse=True)

    if not ordered:
        body = EMPTY_STATE
    else:
        parts = []
        for i, r in enumerate(ordered):
            tag = r["tag_name"]
            title = html.escape(r.get("name") or tag)
            body_html = r.get("body_html") or "<p>No release notes yet.</p>"
            parts.append(ENTRY % {
                "latest_class": " latest" if i == 0 else "",
                "tag": html.escape(tag),
                "pill": '<span class="pill">Latest</span>' if i == 0 else "",
                "date": _fmt_date(r.get("created_at")),
                "title": title,
                "body_html": body_html,
                "html_url": html.escape(r.get("html_url") or
                                         (GITHUB_REPO_URL % repo) + "/releases/tag/" + tag),
            })
        body = "".join(parts)

    return (HEAD
            + (HEADER % {"repo": repo})
            + body
            + (FOOTER % {"repo": repo, "owner": owner}))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", help="path to write the generated releases/index.html to")
    p.add_argument("--repo", help='"OWNER/NAME", required unless --releases-file is given')
    p.add_argument("--releases-file", help="skip the network call; load releases JSON from this "
                                            "path instead (offline / test mode)")
    args = p.parse_args(argv)

    if args.releases_file:
        with open(args.releases_file, encoding="utf-8") as f:
            releases = json.load(f)
    else:
        if not args.repo:
            p.error("--repo is required unless --releases-file is given")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        releases = fetch_releases(args.repo, token)

    repo = args.repo or "ranawaqas-ai/mdreview-service"
    page = render_page(releases, repo)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote %s (%d release%s)" % (args.output, len(releases), "" if len(releases) == 1 else "s"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
