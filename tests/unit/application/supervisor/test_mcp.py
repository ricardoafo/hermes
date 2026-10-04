"""Tests for the ``hermes-mcp`` JSON-RPC protocol handler (#34).

Invariants protected here:

- only initialize, ping, tools/list and tools/call exist; any other method
  (resources, prompts, sampling) is "method not found";
- tools/list shows exactly the granted tools, each marked read-only;
- tools/call returns the projection as structured content, a refusal as
  ``isError``, and an ungranted tool as an invalid-params error;
- notifications get no reply, and malformed input gets the JSON-RPC error
  codes clients expect.

The dispatcher is real, over the in-memory fakes of ``test_tools.py``; the
stdio loop is covered by ``tests/unit/entrypoints/test_supervisor.py``.
"""

from __future__ import annotations

import json

import pytest

from hermes.application.supervisor.mcp import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpServer,
)
from tests.unit.application.supervisor.test_tools import _dispatcher


def _server(grants: list[str] | None = None) -> McpServer:
    dispatcher, _ = _dispatcher(grants if grants is not None else ["hermes:read:health"])
    return McpServer(dispatcher, version="9.9.9")


def _request(method: str, params: object = None, request_id: int = 1) -> dict[str, object]:
    message: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_initialize_echoes_a_supported_protocol_version() -> None:
    """A client asking for a supported version gets that version back."""
    reply = _server().handle(_request("initialize", {"protocolVersion": "2025-03-26"}))

    assert reply is not None
    result = reply["result"]
    assert isinstance(result, dict)
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"] == {"name": "hermes-mcp", "version": "9.9.9"}


def test_initialize_offers_the_newest_version_for_an_unknown_one() -> None:
    """An unsupported requested version is answered with the newest supported one."""
    reply = _server().handle(_request("initialize", {"protocolVersion": "1999-01-01"}))

    assert reply is not None
    assert reply["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]  # type: ignore[index]


def test_a_notification_gets_no_reply() -> None:
    """notifications/initialized (no id) is acknowledged silently."""
    server = _server()
    notifications = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
    ]
    assert [server.handle(n) for n in notifications] == [None, None]


def test_ping_returns_an_empty_result() -> None:
    """A ping request answers {} so clients can check liveness."""
    assert _server().handle(_request("ping", request_id=7)) == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {},
    }


def test_tools_list_shows_only_granted_tools_marked_read_only() -> None:
    """The listing is the grant set, each tool annotated readOnlyHint."""
    reply = _server(["hermes:read:signals", "hermes:read:health"]).handle(_request("tools/list"))

    assert reply is not None
    tools = reply["result"]["tools"]  # type: ignore[index]
    assert [t["name"] for t in tools] == ["health", "signals"]
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    assert tools[1]["inputSchema"]["required"] == ["since"]


def test_tools_list_without_grants_is_empty() -> None:
    """Deny-by-default reaches the protocol: no grant, no tools."""
    reply = _server([]).handle(_request("tools/list"))

    assert reply is not None
    assert reply["result"] == {"tools": []}


def test_tools_call_returns_structured_content_and_matching_text() -> None:
    """A successful call carries the projection as structuredContent and as JSON text."""
    reply = _server().handle(_request("tools/call", {"name": "health", "arguments": {}}))

    assert reply is not None
    result = reply["result"]
    assert isinstance(result, dict)
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["verdict"] in {"ok", "late", "down"}


def test_a_refused_call_is_a_tool_error_not_a_protocol_error() -> None:
    """A guard refusal comes back as isError with the reason, so the model can read it."""
    reply = _server(["hermes:read:signals"]).handle(
        _request("tools/call", {"name": "signals", "arguments": {"since": "2025-06-01T00:00Z"}})
    )

    assert reply is not None
    result = reply["result"]
    assert result["isError"] is True  # type: ignore[index]
    assert "memorization guard" in result["content"][0]["text"]  # type: ignore[index]


def test_an_ungranted_tool_is_an_invalid_params_error() -> None:
    """Calling a tool outside the grants is refused at the protocol level."""
    reply = _server().handle(_request("tools/call", {"name": "positions"}))

    assert reply is not None
    assert reply["error"] == {"code": INVALID_PARAMS, "message": "unknown tool: positions"}


@pytest.mark.parametrize("method", ["resources/list", "prompts/list", "sampling/createMessage"])
def test_other_mcp_methods_do_not_exist(method: str) -> None:
    """Only the four read-only methods exist; everything else is method-not-found."""
    reply = _server().handle(_request(method))

    assert reply is not None
    assert reply["error"]["code"] == METHOD_NOT_FOUND  # type: ignore[index]


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("{not json", PARSE_ERROR),
        ('{"jsonrpc": "1.0", "id": 1, "method": "ping"}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]}', INVALID_PARAMS),
        ('{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}', INVALID_PARAMS),
    ],
)
def test_malformed_input_gets_the_standard_error_code(line: str, code: int) -> None:
    """Each malformed message maps to its JSON-RPC 2.0 error code."""
    reply = _server().handle_line(line)

    assert reply is not None
    assert reply["error"]["code"] == code  # type: ignore[index]


def test_a_blank_line_gets_no_reply() -> None:
    """Blank keep-alive lines are ignored."""
    server = _server()
    assert [server.handle_line(line) for line in ("", "  \n")] == [None, None]
