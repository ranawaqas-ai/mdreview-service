"""JSON-RPC dispatch shared by both transports: the stdio wrapper (__main__.py) and the service's
own /mcp endpoint (#395). No I/O of its own: handle() takes one parsed message and a ServiceClient
and returns the response object, or None for a notification.
"""
import json

from .tools import PROTOCOL_VERSION, SERVER_INFO, INSTRUCTIONS, TOOLS, TOOL_NAMES, _server_info
from .client import ToolError

# Revisions whose tools-only surface is identical to ours. A client asking for one of them gets it
# echoed back; anything else is offered PROTOCOL_VERSION (the spec's negotiation rule).
SUPPORTED_VERSIONS = ("2025-03-26", "2025-06-18", "2025-11-25")


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _text(rid, text, is_error):
    return _result(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})


def _tools_call(rid, params, client):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in TOOL_NAMES:
        return error(rid, -32602, "Unknown tool: %s" % name)   # protocol error
    if name == "server_info":
        # reports the wrapper's own identity, no HTTP: must work with no service behind it
        return _text(rid, json.dumps(_server_info()), False)
    try:
        return _text(rid, client.call_tool(name, args), False)
    except KeyError as e:
        return error(rid, -32602, "Missing required argument: %s" % e)
    except ToolError as e:
        return _text(rid, str(e), True)


def _initialize(rid, params):
    asked = params.get("protocolVersion")
    return _result(rid, {
        "protocolVersion": asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
        "capabilities": {"tools": {}},      # tools only: no resources, no prompts
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,       # the end-to-end workflow, surfaced to the agent
    })


def handle(msg, client):
    """The response to one JSON-RPC message, or None when it is a notification (no id)."""
    rid = msg.get("id")
    if rid is None:
        return None
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return _initialize(rid, params)
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        return _tools_call(rid, params, client)
    if method == "ping":
        return _result(rid, {})
    return error(rid, -32601, "Method not found: %s" % method)
