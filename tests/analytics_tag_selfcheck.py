#!/usr/bin/env python3
"""analytics_tag_selfcheck.py: the optional Umami tag (src/mdreview/analytics.py).

WHAT THIS GUARDS. The app's HTML ships in a public image that other people run. So the tag must be
absent unless the operator sets both variables, and a review page must never report its real id or
its document title. The checks run against the real pages in web/app.

Mutation checks (each was applied by hand and run; the check named after it must FAIL):
  * drop the `website_id` half of the guard        -> "src alone leaves the page unchanged"
  * pass fixed_path=False for the review page      -> "server sends the review page with a fixed path"
  * drop the referrer rewrite                      -> "every page drops a same-origin referrer"
  * drop data-exclude-search                       -> "dashboard tag excludes the query string"
  * remove html.escape                             -> "attribute values are escaped"

Run: python3 tests/analytics_tag_selfcheck.py     (exit 0 = pass)
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# mdreview.config creates its data dir at import; keep that inside the project.
os.environ["MDREVIEW_DATA"] = str(ROOT / ".scratch" / "analytics-selfcheck-data")

from mdreview.analytics import REVIEW_PATH, with_analytics  # noqa: E402

SRC = "https://stats.example.com/script.js"
WID = "11111111-2222-3333-4444-555555555555"
BASE = "https://app.example.com"
failed = []


def check(name, cond):
    print(("ok   - " if cond else "FAIL - ") + name)
    if not cond:
        failed.append(name)


pages = {n: (ROOT / "web" / "app" / n).read_text() for n in
         ("dashboard.html", "account.html", "viewer.html")}

for name, page in pages.items():
    check(name + " has exactly one </head> to anchor on", page.count("</head>") == 1)
    check(name + ": nothing set leaves the page unchanged",
          with_analytics(page, src="", website_id="", public_base=BASE) == page)
    check(name + ": src alone leaves the page unchanged",
          with_analytics(page, src=SRC, website_id="", public_base=BASE) == page)
    check(name + ": website id alone leaves the page unchanged",
          with_analytics(page, src="", website_id=WID, public_base=BASE) == page)
    check(name + ": a non-https src leaves the page unchanged",
          with_analytics(page, src="http://stats.example.com/script.js", website_id=WID,
                         public_base=BASE) == page)

dash = with_analytics(pages["dashboard.html"], src=SRC, website_id=WID, public_base=BASE)
check("dashboard tag appears exactly once", dash.count(SRC) == 1 and dash.count(WID) == 1)
check("dashboard tag sits inside <head>", dash.index(SRC) < dash.index("</head>"))
check("dashboard tag is limited to the public hostname", 'data-domains="app.example.com"' in dash)
check("dashboard tag excludes the query string", 'data-exclude-search="true"' in dash)
check("dashboard tag excludes the hash", 'data-exclude-hash="true"' in dash)
check("removing the tag gives back the original page",
      re.sub(r'<script defer src="https://stats[^\n]*\n<script>window[^\n]*\n', "", dash) == pages["dashboard.html"])

review = with_analytics(pages["viewer.html"], fixed_path=True, src=SRC, website_id=WID,
                        public_base=BASE)
check("every page turns auto-tracking off",
      'data-auto-track="false"' in review and 'data-auto-track="false"' in dash)
check("every page drops a same-origin referrer",
      all('if((p.referrer||"").charAt(0)==="/")p.referrer="";' in x for x in (dash, review)))
check("dashboard keeps its own path", "p.url=" not in dash)
check("review page reports the literal path", 'p.url="%s"' % REVIEW_PATH in review)
check("review page reports a fixed title", "p.title=" in review)
check("review page tag appears exactly once", review.count(SRC) == 1)

no_base = with_analytics(pages["dashboard.html"], src=SRC, website_id=WID, public_base="")
check("no public base means no data-domains attribute", "data-domains" not in no_base)

odd = with_analytics("<head></head>", src='https://x/"><b>', website_id='a"b', public_base=BASE)
check("attribute values are escaped", '"><b>' not in odd and 'a"b' not in odd)

check("a page without </head> is returned unchanged",
      with_analytics("plain", src=SRC, website_id=WID, public_base=BASE) == "plain")

server_src = (ROOT / "src" / "mdreview" / "server.py").read_text()
for name in ("dashboard.html", "account.html"):
    check("server sends %s through with_analytics" % name,
          'with_analytics(app.store.read_text(os.path.join(WEB_DIR, "%s")))' % name in server_src)
check("server sends the review page with a fixed path",
      'with_analytics(app.store.read_text(os.path.join(WEB_DIR, "viewer.html")), fixed_path=True)'
      in server_src)

print("\n%d failed" % len(failed) if failed else "\nall passed")
sys.exit(1 if failed else 0)
