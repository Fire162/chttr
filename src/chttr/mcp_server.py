"""`chttr mcp`: your meetings as an MCP server (stdio), for Claude Code, Claude
Desktop or any MCP client.

    claude mcp add chttr -- chttr mcp

Tools: search_meetings, read_transcript, get_notes, list_meetings,
list_action_items (the same ones `chttr ask` gives Claude). Read-only; it never
records, changes or deletes anything. Speaks JSON-RPC 2.0, one message per line.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from typing import Any

from . import __version__
from .ask import tool_functions

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _doc_parts(fn) -> tuple[str, dict[str, str]]:
    doc = inspect.getdoc(fn) or ""
    summary, _, rest = doc.partition("\n\n")
    args = {}
    if "Args:" in rest:
        for m in re.finditer(r"^\s{4}(\w+): (.+(?:\n\s{8}.+)*)", rest.split("Args:", 1)[1], re.MULTILINE):
            args[m.group(1)] = " ".join(m.group(2).split())
    return " ".join(summary.split()), args


def tool_schema(fn) -> dict:
    summary, arg_docs = _doc_parts(fn)
    props, required = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        kind = {"int": "integer", "float": "number", "bool": "boolean"}.get(str(p.annotation), "string")
        props[name] = {"type": kind, "description": arg_docs.get(name, "")}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"name": fn.__name__, "description": summary,
            "inputSchema": {"type": "object", "properties": props, "required": required},
            "annotations": {"readOnlyHint": True}}


class Server:
    def __init__(self) -> None:
        self.tools = {fn.__name__: fn for fn in tool_functions()}

    def handle(self, msg: dict) -> dict | None:
        method, mid = str(msg.get("method") or ""), msg.get("id")
        if mid is None:                       # a notification (initialized, cancelled, ...): no reply
            return None
        try:
            result = self._dispatch(method, msg.get("params") or {})
        except KeyError as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {exc}"}}
        except Exception as exc:  # noqa: BLE001 - report, keep serving
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            asked = params.get("protocolVersion", PROTOCOL_VERSIONS[0])
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "chttr", "version": __version__},
                    "instructions": "Search and read the user's recorded meetings (transcripts with speaker "
                                    "names, notes, action items). Cite meeting title, date and time."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool_schema(fn) for fn in self.tools.values()]}
        if method == "tools/call":
            fn = self.tools.get(params.get("name", ""))
            if fn is None:
                return {"content": [{"type": "text", "text": f"unknown tool {params.get('name')!r}"}], "isError": True}
            try:
                text = fn(**(params.get("arguments") or {}))
            except (TypeError, ValueError) as exc:
                return {"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True}
            return {"content": [{"type": "text", "text": text}]}
        raise KeyError(method)


def run(stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = server.handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
