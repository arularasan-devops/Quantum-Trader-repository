"""Minimal MCP stdio server — JSON-RPC 2.0 over stdin/stdout.

Implemented directly rather than by adding an MCP SDK dependency: the protocol
surface needed here is three methods, and a new package in the live dependency
tree is a supply-chain cost this does not justify.

Run:

    .venv/bin/python -m app.mcp.server

Methods: ``initialize``, ``tools/list``, ``tools/call``, ``ping``. Tools come from
the allowlist in :mod:`app.mcp.tools`, which contains no real-order tool.
Exceptions are returned as short error text — never a traceback, a file path or a
credential.
"""
from __future__ import annotations

import json
import sys
import traceback

from app.mcp import tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "quantum-trader", "version": "phase6"}
_INSTRUCTIONS = (
    "Quantum Trader research interface. Market data, AI analysis, research and "
    "PAPER trading only. Real-money execution is disabled in this build and no "
    "tool can place, modify or cancel a real order; broker access is read-only. "
    "Probability outputs are measured on underlying moves and exclude option "
    "theta, spread and slippage."
)


def _result(rid: object, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": payload}


def _error(rid: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(req: dict) -> dict | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method is None:
        return _error(rid, -32600, "invalid request")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(rid, {"protocolVersion": PROTOCOL_VERSION,
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": SERVER_INFO,
                             "instructions": _INSTRUCTIONS})
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": tools.descriptors()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(rid, -32602, "'name' must be a string")
        try:
            out = tools.call(name, params.get("arguments") or {})
            text = json.dumps(out, default=str)
            return _result(rid, {"content": [{"type": "text", "text": text}],
                                 "isError": False})
        except tools.ToolError as exc:
            return _result(rid, {"content": [{"type": "text", "text": str(exc)}],
                                 "isError": True})
        except Exception as exc:  # never leak internals to the client
            return _result(rid, {
                "content": [{"type": "text",
                             "text": f"tool '{name}' failed: {type(exc).__name__}"}],
                "isError": True})
    return _error(rid, -32601, f"unknown method '{method}'")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = handle(req)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            resp = _error(req.get("id"), -32603, "internal error")
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
