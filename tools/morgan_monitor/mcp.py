"""Read-only stdio MCP metadata surface. No listener, mutations or render tools."""
from __future__ import annotations

import json
from typing import TextIO

from .store import StoreError, read_snapshot

MAX_REQUEST = 65536
TOOLS = [{"name": "monitor_metadata", "description": "Read safe local monitor source health and coverage; historical records do not establish execution.",
          "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]


def handle(message, state_path):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    # Notifications (including initialized) never produce a response.
    if "id" not in message:
        return None
    request_id = message["id"]
    if (type(request_id) not in (str, int) and request_id is not None) or (isinstance(request_id, str) and len(request_id) > 128):
        request_id = None
    response = {"jsonrpc": "2.0", "id": request_id}
    method, params = message["method"], message.get("params", {})
    if method == "initialize":
        response["result"] = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "morgan-monitor-metadata", "version": "1.0.0"}}
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": TOOLS}
    elif method == "tools/call" and isinstance(params, dict) and set(params) <= {"name", "arguments", "_meta"} and params.get("name") == "monitor_metadata" and params.get("arguments", {}) == {}:
        try:
            data = read_snapshot(state_path)
            response["result"] = {"content": [{"type": "text", "text": json.dumps(data, sort_keys=True, allow_nan=False)}], "isError": False}
        except StoreError:
            response["result"] = {"content": [{"type": "text", "text": '{"error":"state_unavailable"}'}], "isError": True}
    else:
        response["error"] = {"code": -32601, "message": "Method unavailable"}
    return response


def serve(state_path, incoming: TextIO, outgoing: TextIO):
    for _ in range(1_000_000):
        line = incoming.readline(MAX_REQUEST + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST:
            # Terminate instead of interpreting the remainder as another call.
            outgoing.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request too large"}}) + "\n")
            outgoing.flush()
            return
        try:
            response = handle(json.loads(line), state_path)
        except (ValueError, TypeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            outgoing.write(json.dumps(response, sort_keys=True, allow_nan=False) + "\n")
            outgoing.flush()
