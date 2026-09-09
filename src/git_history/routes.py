"""The git-http-backend proxy (#379): serves a real `git clone`/`git fetch` over HTTP by shelling
out to the system `git http-backend` CGI binary (`subprocess`), never reimplementing the smart-HTTP
protocol.

Talks to the host's request handler through the stdlib http.server surface ONLY (`.headers`,
`.rfile`, `.wfile`, `.command`, `.path`, `.send_response`/`.send_header`/`.end_headers`) — never a
host-specific helper — so this module has no mdreview import and no opinion on auth: the
composition root injects an `authorize(handler, doc_id) -> bool` callable, responsible for writing
its own 401/404 onto the handler when it returns False. This is exactly the seam interfaces.py
documents: git_history depends on the host only through what the host chooses to inject.

Two endpoints, matching what `git clone`/`git fetch` actually request:
  GET  /git/<doc_id>.git/info/refs?service=git-upload-pack
  POST /git/<doc_id>.git/git-upload-pack
No branching, no push (non-goal): the route table below never matches git-receive-pack, and
gitcache._init_bare additionally sets http.receivepack=false on every repo as defense in depth.
"""
import os
import re
import subprocess
from urllib.parse import urlparse

_PATH_RE = re.compile(r"^/git/([A-Za-z0-9]{4,40})\.git/(info/refs|git-upload-pack)$")
_HEADER_SEP_RE = re.compile(rb"\r\n\r\n|\n\n")
_HEADER_LINE_RE = re.compile(rb"\r\n|\n")


class GitHistoryRoutes:
    def __init__(self, cache, source, authorize):
        self._cache = cache
        self._source = source
        self._authorize = authorize

    def handle(self, h, m, path):
        """True once the request is fully handled (response written); False lets the host's own
        router try its own arms (an unmatched path, or a matched path on the wrong verb)."""
        mo = _PATH_RE.match(path)
        if not mo:
            return False
        doc_id, service = mo.group(1), mo.group(2)
        want_method = "GET" if service == "info/refs" else "POST"
        if m != want_method:
            return False
        if not self._authorize(h, doc_id):
            return True                        # authorize() already wrote 401/404
        repo = self._cache.ensure_repo(doc_id, self._source)
        self._run_cgi(h, repo, doc_id, service)
        return True

    def _run_cgi(self, h, repo_path, doc_id, service):
        cache_root = os.path.dirname(repo_path)
        length = int(h.headers.get("Content-Length", 0) or 0)
        body = h.rfile.read(length) if length else b""
        env = dict(os.environ)
        env.update({
            "GIT_PROJECT_ROOT": cache_root,
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": h.command,
            "PATH_INFO": "/%s.git/%s" % (doc_id, service),
            "QUERY_STRING": urlparse(h.path).query,
            "CONTENT_TYPE": h.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(length),
            "GATEWAY_INTERFACE": "CGI/1.1",
        })
        git_protocol = h.headers.get("Git-Protocol")
        if git_protocol:
            env["HTTP_GIT_PROTOCOL"] = git_protocol
        content_encoding = h.headers.get("Content-Encoding")
        if content_encoding:
            # git gzips large upload-pack POSTs; http-backend only inflates when it sees this.
            env["HTTP_CONTENT_ENCODING"] = content_encoding
        proc = subprocess.run(["git", "http-backend"], input=body, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _write_cgi_response(h, proc.stdout)


def _write_cgi_response(h, raw):
    sep_m = _HEADER_SEP_RE.search(raw)
    if sep_m:
        head, body = raw[:sep_m.start()], raw[sep_m.end():]
    else:
        head, body = raw, b""
    status = 200
    headers = []
    for line in _HEADER_LINE_RE.split(head):
        if not line:
            continue
        k, _, v = line.partition(b":")
        k = k.decode("latin-1").strip()
        v = v.decode("latin-1").strip()
        if k.lower() == "status":
            try:
                status = int(v.split()[0])
            except (ValueError, IndexError):
                status = 200
        else:
            headers.append((k, v))
    h.send_response(status)
    for k, v in headers:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(body)
