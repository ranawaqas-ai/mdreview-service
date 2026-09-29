#!/usr/bin/env python3
"""Thin entry point for the mdreview MCP server — the implementation lives in the `mcp` package.

Kept at the src/ root so the documented `python3 src/mcp_server.py` invocation (and external
.mcp.json client configs that point at this path) keep working unchanged; running this script puts
src/ on sys.path, so `import mcp` resolves to the sibling package. Canonical form: `python -m mcp`.
"""
from mcp.__main__ import main

if __name__ == "__main__":
    main()
