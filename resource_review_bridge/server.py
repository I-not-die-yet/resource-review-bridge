"""Dependency-free stdio MCP server exposing only review_resource."""

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from . import __version__
from .reviewer import ResourceReviewer, tool_error
from .state import JobStore


PROTOCOL_VERSION = "2025-06-18"
TOOL = {
    "name": "review_resource",
    "title": "Review public resource",
    "description": "Read one public URL in read-only mode and return a structured Evidence Packet. Instagram and Threads login reminders are treated as dismissible overlays before access is considered blocked.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http or https URL to review."},
            "request_id": {"type": "string", "description": "Optional idempotency key for this logical review."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
}


def default_state_path() -> str:
    return os.environ.get(
        "RESOURCE_REVIEW_STATE_PATH",
        os.path.expanduser("~/.local/state/resource-review-bridge/state.sqlite"),
    )


def response(request_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result
    return value


def handle(message: Dict[str, Any], reviewer: ResourceReviewer) -> Optional[Dict[str, Any]]:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        return response(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "resource-review-bridge", "version": __version__},
        })
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": [TOOL]})
    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "review_resource":
            return response(request_id, error={"code": -32602, "message": "Unknown tool"})
        arguments = params.get("arguments") or {}
        try:
            result = reviewer.review(arguments.get("url"), arguments.get("request_id"))
            return response(request_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": result.get("status") == "failed",
            })
        except Exception as exc:
            result = tool_error(exc)
            return response(request_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": True,
            })
    return response(request_id, error={"code": -32601, "message": "Method not found"})


def serve_stdio(state_path: str) -> int:
    store = JobStore(state_path)
    reviewer = ResourceReviewer(store)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                outgoing = handle(message, reviewer)
            except json.JSONDecodeError:
                outgoing = response(None, error={"code": -32700, "message": "Parse error"})
            except Exception:
                outgoing = response(message.get("id") if isinstance(message, dict) else None, error={
                    "code": -32603, "message": "Internal error"
                })
            if outgoing is not None:
                sys.stdout.write(json.dumps(outgoing, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        store.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdio", action="store_true", help="Run the MCP server over stdin/stdout.")
    mode.add_argument("--status", action="store_true", help="Print the most recent terminal job metadata.")
    parser.add_argument("--state", default=default_state_path(), help="SQLite state path.")
    args = parser.parse_args(argv)
    if args.status:
        store = JobStore(args.state)
        try:
            print(json.dumps({"latest_terminal": store.latest_terminal()}, ensure_ascii=False))
        finally:
            store.close()
        return 0
    return serve_stdio(args.state)


if __name__ == "__main__":
    raise SystemExit(main())
