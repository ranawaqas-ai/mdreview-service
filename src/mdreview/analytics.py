"""Optional page-view analytics tag (Umami), added to app pages at send time.

The app's HTML ships inside a public image that other people self-host, so the tag is never
written into web/app/*.html: an install that sets nothing sends the pages unchanged and makes
no request to anyone's analytics server. The hosted instance sets both variables.

Review pages carry the review id in their URL and the document title in <title>, so they report
a literal path and title. No page reports a real review id, in its own path or as a referrer.
"""
import html
from urllib.parse import urlparse

from mdreview.config import PUBLIC_BASE, UMAMI_SRC, UMAMI_WEBSITE_ID

REVIEW_PATH = "/review/:id"
REVIEW_TITLE = "mdreview review"


def _tag(src, website_id, public_base, fixed_path):
    # Auto-tracking is off on every page so the one page view can be cleaned first. Umami keeps a
    # same-origin referrer as a path, so a visit that arrives from a review would otherwise carry
    # that review's id. Same-origin referrers are dropped; referrers from other sites are kept.
    attrs = [("src", src), ("data-website-id", website_id), ("data-auto-track", "false"),
             ("data-exclude-search", "true"), ("data-exclude-hash", "true")]
    host = urlparse(public_base).hostname
    if host:
        # Keeps a copy of this config on another hostname (staging, a restore test) silent.
        attrs.append(("data-domains", host))
    fixed = 'p.url="%s";p.title="%s";' % (REVIEW_PATH, REVIEW_TITLE) if fixed_path else ""
    return ("<script defer " + " ".join(
        '%s="%s"' % (k, html.escape(v, quote=True)) for k, v in attrs) + "></script>\n"
        '<script>window.addEventListener("load",function(){if(window.umami)'
        "umami.track(function(p){" + fixed +
        'if((p.referrer||"").charAt(0)==="/")p.referrer="";return p;});});</script>\n')


def with_analytics(page, fixed_path=False, src=UMAMI_SRC, website_id=UMAMI_WEBSITE_ID,
                   public_base=PUBLIC_BASE):
    """Return `page` with the tag inserted before </head>, or unchanged when not configured."""
    if not (src.startswith("https://") and website_id) or "</head>" not in page:
        return page
    return page.replace("</head>", _tag(src, website_id, public_base, fixed_path) + "</head>", 1)
