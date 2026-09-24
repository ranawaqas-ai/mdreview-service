"""POST /mcp: the MCP server over Streamable HTTP, for remote clients such as a claude.ai custom
connector (#395). Dispatched through the core feature-module seam, like the auth and admin modules.

It is the stdio wrapper's dispatch (mcp.rpc) behind an HTTP front: JSON in, JSON out, no SSE, no
session id. Each tool call becomes a loopback request to this same server carrying the caller's own
Bearer, so every existing access check applies unchanged and the endpoint can do nothing the
caller's token could not already do against /api.

Transport rules (MCP 2025-11-25, basic/transports): POST carries one JSON-RPC message; a
notification or response gets 202 with no body; GET and DELETE get 405; an unsupported
MCP-Protocol-Version gets 400; an Origin header from a foreign site gets 403. On a require-auth tier
a caller without a Bearer gets 401 pointing at the protected-resource metadata, which is where an
OAuth client starts.
"""
import json
from urllib.parse import urlparse

from mcp.client import ServiceClient
from mcp import rpc

PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource" + PATH

# Browsers send Origin; a claude.ai connector's server-side calls normally do not. Anything else
# presenting one is a cross-site page trying to drive the endpoint (DNS-rebinding class).
_TRUSTED_ORIGINS = {"https://claude.ai", "https://claude.com"}


class McpModule:
    def handle(self, h, m, path):
        if path != PATH:
            return False
        if not self._origin_ok(h):
            h._json(403, {"error": "origin not allowed"})
            return True
        if m != "POST":
            h._send(405, b"", "text/plain", [("Allow", "POST")])
            return True
        version = h.headers.get("MCP-Protocol-Version")
        if version and version not in rpc.SUPPORTED_VERSIONS:
            h._json(400, {"error": "unsupported MCP-Protocol-Version %s" % version})
            return True

        p = h._principal()
        bearer = self._bearer(h)
        if p.plane != "local" and (p.is_anonymous or not bearer):
            h._send(401, json.dumps({"error": "a Bearer token is required"}), "application/json",
                    [("WWW-Authenticate", 'Bearer resource_metadata="%s%s"' % (h._base(), METADATA_PATH))])
            return True

        msg = self._message(h)
        if msg is None:
            h._json(400, rpc.error(None, -32700, "expected one JSON-RPC message object"))
            return True
        if msg.get("id") is None:          # notification or response: accepted, nothing to say
            h._send(202, b"")
            return True

        client = ServiceClient("http://127.0.0.1:%d" % h.server.server_address[1], bearer,
                               local_files=False, host=h.headers.get("Host"),
                               client_ip=h.headers.get("X-Real-IP") or h.client_address[0])
        try:
            resp = rpc.handle(msg, client)
        except Exception as e:             # one bad message never becomes a 500 page
            resp = rpc.error(msg.get("id"), -32603, "Internal error: %s" % e)
        h._json(200, resp)
        return True

    @staticmethod
    def _origin_ok(h):
        origin = h.headers.get("Origin")
        if not origin:
            return True
        own = urlparse(h._base())
        return origin in _TRUSTED_ORIGINS or origin == "%s://%s" % (own.scheme, own.netloc)

    @staticmethod
    def _bearer(h):
        auth = h.headers.get("Authorization", "")
        return auth[7:].strip() if auth[:7].lower() == "bearer " else ""

    @staticmethod
    def _message(h):
        n = int(h.headers.get("Content-Length", 0) or 0)
        try:
            msg = json.loads(h.rfile.read(n) if n else b"")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return msg if isinstance(msg, dict) else None      # a JSON array = batching, removed in 2025-06-18
