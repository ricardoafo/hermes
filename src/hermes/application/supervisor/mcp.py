"""The ``hermes-mcp`` protocol: MCP over JSON-RPC 2.0, read-only tools only.

Handles one decoded JSON-RPC message at a time and returns the reply (or
``None`` for a notification); the stdio loop lives in
:mod:`hermes.entrypoints.supervisor`. Only the methods a read-only tool server
needs exist: ``initialize``, ``ping``, ``tools/list`` and ``tools/call``.
There are no resources, prompts, sampling or write methods, so a client can
reach nothing but the granted tools of :class:`ToolDispatcher`.

Written by hand rather than with an MCP SDK: the server needs four methods,
and a hand-written handler keeps the VPS process free of an HTTP stack and
its supply chain (ADR-010). Reachable only over stdio, which the operator
runs over SSH on the private network (ADR-012 D-07).

Reference: openspec/changes/34-supervisor-read-tools/change.md; issue #34.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from hermes.application.supervisor.tools import ToolDispatcher, UnknownToolError

__all__ = [
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "McpServer",
]

PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602

SUPPORTED_PROTOCOL_VERSIONS: Final = ("2025-06-18", "2025-03-26", "2024-11-05")
"""Newest first; the first is offered when the client asks for another."""

_SERVER_NAME: Final = "hermes-mcp"
_INSTRUCTIONS: Final = (
    "Read-only, outcome-blind view of hermes. Tools return times, states, "
    "pairs and counts; never prices, values or P&L. Nothing here can place, "
    "cancel or change anything."
)

type JsonObject = dict[str, object]


class McpServer:
    """Answer MCP requests from the granted read-only tools."""

    def __init__(self, dispatcher: ToolDispatcher, *, version: str) -> None:
        """Wrap a dispatcher.

        Args:
            dispatcher: Serves and audits the granted tools.
            version: Server version reported on ``initialize``.
        """
        self._dispatcher = dispatcher
        self._version = version

    def handle_line(self, line: str) -> JsonObject | None:
        """Decode one line of input and return the reply, if any.

        Args:
            line: One newline-delimited JSON-RPC message.

        Returns:
            The reply, or ``None`` for a notification or a blank line.
        """
        if not line.strip():
            return None
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return _error(None, PARSE_ERROR, "Parse error")
        return self.handle(message)

    def handle(self, message: object) -> JsonObject | None:
        """Return the reply to one decoded JSON-RPC message.

        Args:
            message: The decoded message.

        Returns:
            The reply, or ``None`` when ``message`` is a notification.
        """
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(message.get("id"), INVALID_REQUEST, "Invalid Request")
        if "id" not in message:
            return None
        request_id = message["id"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        return self._route(method, request_id, params)

    def _route(self, method: str, request_id: object, params: Mapping[str, object]) -> JsonObject:
        match method:
            case "initialize":
                return _result(request_id, self._initialize(params))
            case "ping":
                return _result(request_id, {})
            case "tools/list":
                return _result(request_id, self._list())
            case "tools/call":
                return self._call(request_id, params)
            case _:
                return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _initialize(self, params: Mapping[str, object]) -> JsonObject:
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": _SERVER_NAME, "version": self._version},
            "instructions": _INSTRUCTIONS,
        }

    def _list(self) -> JsonObject:
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": f"{tool.summary} Scope: {tool.scope}.",
                    "inputSchema": dict(tool.input_schema),
                    "annotations": {"readOnlyHint": True, "openWorldHint": False},
                }
                for tool in self._dispatcher.available()
            ]
        }

    def _call(self, request_id: object, params: Mapping[str, object]) -> JsonObject:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "tools/call needs a name and arguments")
        try:
            result = self._dispatcher.call(name, arguments)
        except UnknownToolError as exc:
            return _error(request_id, INVALID_PARAMS, str(exc))
        if result.payload is None:
            text = result.error or "refused"
            return _result(
                request_id, {"content": [{"type": "text", "text": text}], "isError": True}
            )
        text = json.dumps(result.payload, sort_keys=True)
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": result.payload,
                "isError": False,
            },
        )


def _result(request_id: object, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
