"""Unit tests for MCP tool registrations.

Every tool reads the SERVICE_BEARER ContextVar (set by the HTTP
middleware from X-Auth-Romaine-Token) and forwards the JWT to a
TankClient method. Tests verify:
  - _service_bearer() raises the right error when the ContextVar is unset.
  - Each tool delegates to the correct TankClient method with the JWT.
  - resolve_session / get_session_url walk the list and raise on missing.

See nelsong6/tank-operator#486 for the rollout that retired the prior
IP-tail identity path tested here.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from mcp_tank_operator.caller import ORIGIN_SESSION_ID, SERVICE_BEARER  # noqa: E402
from mcp_tank_operator.tools import register_tools  # noqa: E402


@pytest.fixture()
def mcp_client_pair():
    mcp = FastMCP("test-tank-operator-mcp")
    client = MagicMock()
    register_tools(mcp, client)
    return mcp, client


@contextlib.contextmanager
def _bearer(jwt: str | None):
    """Bind SERVICE_BEARER for the duration of a tool call."""
    token = SERVICE_BEARER.set(jwt)
    try:
        yield
    finally:
        SERVICE_BEARER.reset(token)


@contextlib.contextmanager
def _origin(session_id: str | None):
    """Bind ORIGIN_SESSION_ID for the duration of a tool call.

    Mirrors what the HTTP middleware does when an inbound request
    carries the X-Tank-Origin-Session-Id header — namely, lets the
    tool's call to TankClient forward it as the origin_session_id kwarg
    so tank-operator stamps it on the persisted user_message.created
    event.
    """
    token = ORIGIN_SESSION_ID.set(session_id)
    try:
        yield
    finally:
        ORIGIN_SESSION_ID.reset(token)


def _get_tool(mcp: FastMCP, name: str):
    """Retrieve the raw callable for a registered tool by name."""
    for tool in mcp._tool_manager._tools.values():
        if tool.name == name:
            return tool.fn
    raise KeyError(f"tool {name!r} not registered")


# ---------------------------------------------------------------------------
# _service_bearer guard — every tool refuses without the inbound JWT.
# ---------------------------------------------------------------------------


def test_list_sessions_raises_when_no_service_bearer(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "list_sessions")
    with _bearer(None):
        with pytest.raises(ValueError, match="service-principal authentication required"):
            fn()


def test_create_session_raises_when_no_service_bearer(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "create_session")
    with _bearer(None):
        with pytest.raises(ValueError, match="service-principal authentication required"):
            fn()


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [{"id": "abc"}]
    fn = _get_tool(mcp, "list_sessions")
    with _bearer("eyJ.fake.jwt"):
        result = fn()
    client.list_sessions.assert_called_once_with("eyJ.fake.jwt")
    assert result == [{"id": "abc"}]


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.create_session.return_value = {"id": "new123"}
    fn = _get_tool(mcp, "create_session")
    with _bearer("eyJ.fake.jwt"):
        result = fn(mode="claude_gui")
    client.create_session.assert_called_once_with("eyJ.fake.jwt", mode="claude_gui")
    assert result["id"] == "new123"


# ---------------------------------------------------------------------------
# read_transcript
# ---------------------------------------------------------------------------


def test_read_transcript_raises_when_no_service_bearer(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "read_transcript")
    with _bearer(None):
        with pytest.raises(ValueError, match="service-principal authentication required"):
            fn(session_id="63")


def test_read_transcript_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.read_transcript.return_value = {"session_id": "63", "rows": []}
    fn = _get_tool(mcp, "read_transcript")
    with _bearer("eyJ.fake.jwt"):
        result = fn(session_id="63")
    client.read_transcript.assert_called_once_with(
        "eyJ.fake.jwt",
        session_id="63",
        anchor=None,
        rows=None,
        before_cursor=None,
        timeline_id=None,
    )
    assert result["session_id"] == "63"


def test_read_transcript_forwards_pagination_args(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.read_transcript.return_value = {"rows": []}
    fn = _get_tool(mcp, "read_transcript")
    with _bearer("jwt"):
        fn(session_id="63", anchor="oldest", rows=40, before_cursor="cur-abc")
    kwargs = client.read_transcript.call_args.kwargs
    assert kwargs["anchor"] == "oldest"
    assert kwargs["rows"] == 40
    assert kwargs["before_cursor"] == "cur-abc"


# ---------------------------------------------------------------------------
# list_session_refs / resolve_session — friendly-name pathway
# ---------------------------------------------------------------------------


def test_list_session_refs_returns_names_and_ids(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [
        {"id": "abc", "name": "tank test", "pod_name": "session-abc"},
        {"id": "xyz", "pod_name": "session-xyz"},
    ]
    fn = _get_tool(mcp, "list_session_refs")
    with _bearer("jwt"):
        result = fn()
    assert result[0]["display_name"] == "tank test"
    assert result[1]["display_name"] == "xyz"


def test_resolve_session_finds_matching_id(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [{"id": "abc"}, {"id": "xyz"}]
    fn = _get_tool(mcp, "resolve_session")
    with _bearer("jwt"):
        result = fn("xyz")
    assert result == {"id": "xyz"}


def test_resolve_session_finds_friendly_name(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [
        {"id": "abc", "name": "tank test"},
        {"id": "xyz", "name": "other"},
    ]
    fn = _get_tool(mcp, "resolve_session")
    with _bearer("jwt"):
        result = fn("tank test")
    assert result["id"] == "abc"


def test_resolve_session_raises_on_ambiguous_name(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [
        {"id": "abc", "name": "shared"},
        {"id": "xyz", "name": "shared"},
    ]
    fn = _get_tool(mcp, "resolve_session")
    with _bearer("jwt"):
        with pytest.raises(ValueError, match="ambiguous"):
            fn("shared")


# ---------------------------------------------------------------------------
# SpireLens capability context / verifier
# ---------------------------------------------------------------------------


def test_get_session_capability_context_returns_static_docs_without_origin(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    fn = _get_tool(mcp, "get_session_capability_context")

    result = fn()

    assert result["capability"] == "spirelens_mcp"
    assert result["session"]["inspected"] is False
    assert result["ssh"]["cert_endpoint"].endswith("/api/auth/exchange/ssh-cert")
    assert result["ssh"]["cert_principal"] == "spirelens-agent"
    assert result["ssh"]["login_user"] == "nelsonlaptopuser"
    client.get_session_capabilities.assert_not_called()


def test_get_session_capability_context_inspects_origin_session(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.get_session_capabilities.return_value = {
        "session": {
            "id": "42",
            "mode": "codex_gui",
            "status": "Active",
            "capabilities": ["spirelens_mcp"],
        },
        "mcp_servers": [
            {
                "name": "spire-lens-mcp",
                "target": "http://127.0.0.1:9997/mcp",
            }
        ],
        "mcp_tools": [
            {"server": "spire-lens-mcp", "name": "bridge_health"},
            {"server": "spire-lens-mcp", "name": "get_host_status"},
            {"server": "spire-lens-mcp", "name": "start_sts2"},
            {"server": "spire-lens-mcp", "name": "stop_sts2"},
            {"server": "spire-lens-mcp", "name": "restart_sts2"},
        ],
    }
    fn = _get_tool(mcp, "get_session_capability_context")

    with _bearer("jwt"), _origin("42"):
        result = fn()

    client.get_session_capabilities.assert_called_once_with("jwt", "42")
    assert result["session"]["enabled"] is True
    assert result["session"]["tools"]["missing"] == []


def test_get_session_capability_context_rejects_unknown_capability(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "get_session_capability_context")

    with pytest.raises(ValueError, match="unsupported capability"):
        fn(capability="unknown")


def test_verify_spirelens_session_access_reports_ok(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.get_session_capabilities.return_value = {
        "session": {
            "id": "42",
            "mode": "codex_gui",
            "status": "Active",
            "capabilities": ["spirelens_mcp"],
        },
        "mcp_servers": [
            {
                "name": "spire-lens-mcp",
                "target": "http://127.0.0.1:9997/mcp",
            }
        ],
        "mcp_tools": [
            {"server": "spire-lens-mcp", "name": "bridge_health"},
            {"server": "spire-lens-mcp", "name": "get_host_status"},
            {"server": "spire-lens-mcp", "name": "start_sts2"},
            {"server": "spire-lens-mcp", "name": "stop_sts2"},
            {"server": "spire-lens-mcp", "name": "restart_sts2"},
        ],
    }
    fn = _get_tool(mcp, "verify_spirelens_session_access")

    with _bearer("jwt"), _origin("42"):
        result = fn()

    assert result["status"] == "ok"
    assert all(check["ok"] for check in result["checks"])


def test_verify_spirelens_session_access_reports_degraded_missing_tools(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.get_session_capabilities.return_value = {
        "session": {"id": "42", "capabilities": ["spirelens_mcp"]},
        "mcp_servers": [
            {
                "name": "spire-lens-mcp",
                "target": "http://127.0.0.1:9997/mcp",
            }
        ],
        "mcp_tools": [{"server": "spire-lens-mcp", "name": "bridge_health"}],
        "mcp_tool_errors": [{"server": "spire-lens-mcp", "error": "Missing session ID"}],
    }
    fn = _get_tool(mcp, "verify_spirelens_session_access")

    with _bearer("jwt"), _origin("42"):
        result = fn()

    assert result["status"] == "degraded"
    assert "restart_sts2" in result["session"]["tools"]["missing"]
    assert any("mcp_tool_errors" in action for action in result["next_actions"])


def test_verify_spirelens_session_access_requires_session_target(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "verify_spirelens_session_access")

    with _bearer("jwt"), _origin(None):
        with pytest.raises(ValueError, match="session_id is required"):
            fn()


# ---------------------------------------------------------------------------
# delete_session / set_session_name / set_test_environment
# ---------------------------------------------------------------------------


def test_delete_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.delete_session.return_value = {"id": "abc", "status": "deleted"}
    fn = _get_tool(mcp, "delete_session")
    with _bearer("jwt"):
        result = fn(session_id="abc")
    client.delete_session.assert_called_once_with("jwt", session_id="abc")
    assert result["status"] == "deleted"


def test_set_session_name_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.set_session_name.return_value = {"id": "abc", "name": "new"}
    fn = _get_tool(mcp, "set_session_name")
    with _bearer("jwt"):
        fn(session_id="abc", name="new")
    client.set_session_name.assert_called_once_with("jwt", session_id="abc", name="new")


def test_set_test_environment_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.set_test_environment.return_value = {"id": "abc"}
    fn = _get_tool(mcp, "set_test_environment")
    with _bearer("jwt"):
        fn(session_id="abc", slot_index=2, url="https://slot-2")
    client.set_test_environment.assert_called_once_with(
        "jwt",
        session_id="abc",
        active=True,
        slot_index=2,
        url="https://slot-2",
    )


# ---------------------------------------------------------------------------
# get_session_url
# ---------------------------------------------------------------------------


def test_get_session_url_finds_matching_session(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [{"id": "abc", "url": "https://tank/abc"}]
    fn = _get_tool(mcp, "get_session_url")
    with _bearer("jwt"):
        result = fn(session_id="abc")
    assert result == {"session_id": "abc", "url": "https://tank/abc"}


def test_get_session_url_raises_when_not_found(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = []
    fn = _get_tool(mcp, "get_session_url")
    with _bearer("jwt"):
        with pytest.raises(ValueError, match="not found"):
            fn(session_id="nope")


# ---------------------------------------------------------------------------
# send_prompt
# ---------------------------------------------------------------------------


def test_send_prompt_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.send_message.return_value = {"status": "queued"}
    fn = _get_tool(mcp, "send_prompt")
    with _bearer("jwt"):
        result = fn(session_id="abc", prompt="hi")
    client.send_message.assert_called_once_with(
        "jwt",
        session_id="abc",
        prompt="hi",
        model=None,
        permission_mode=None,
        origin_session_id=None,
    )
    assert result["status"] == "queued"


def test_send_prompt_forwards_optional_model(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.send_message.return_value = {"status": "queued"}
    fn = _get_tool(mcp, "send_prompt")
    with _bearer("jwt"):
        fn(session_id="abc", prompt="hi", model="claude-opus-4-7")
    assert client.send_message.call_args.kwargs["model"] == "claude-opus-4-7"


def test_send_prompt_forwards_origin_session_id(mcp_client_pair) -> None:
    """Cross-session handoff path: when the calling pod's mcp-auth-proxy
    set X-Tank-Origin-Session-Id, the middleware binds it into
    ORIGIN_SESSION_ID and the tool forwards it as origin_session_id so
    tank-operator stamps it on the user_message.created event and the
    frontend renders the parent session's avatar.
    """
    mcp, client = mcp_client_pair
    client.send_message.return_value = {"status": "queued"}
    fn = _get_tool(mcp, "send_prompt")
    with _bearer("jwt"), _origin("42"):
        fn(session_id="abc", prompt="hi")
    assert client.send_message.call_args.kwargs["origin_session_id"] == "42"


def test_spawn_run_session_forwards_origin_session_id(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.spawn_run.return_value = {"session": {"id": "new"}, "status": "queued"}
    fn = _get_tool(mcp, "spawn_run_session")
    with _bearer("jwt"), _origin("42"):
        fn(prompt="investigate issue")
    assert client.spawn_run.call_args.kwargs["origin_session_id"] == "42"


# ---------------------------------------------------------------------------
# spawn_run_session
# ---------------------------------------------------------------------------


def test_spawn_run_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.spawn_run.return_value = {"session": {"id": "new"}, "status": "queued"}
    fn = _get_tool(mcp, "spawn_run_session")
    with _bearer("eyJ.fake.jwt"):
        result = fn(prompt="investigate issue", name="child-1")
    client.spawn_run.assert_called_once_with(
        "eyJ.fake.jwt",
        prompt="investigate issue",
        mode="claude_gui",
        name="child-1",
        model=None,
        permission_mode=None,
        origin_session_id=None,
    )
    assert result["status"] == "queued"


def test_spawn_run_session_raises_without_service_bearer(mcp_client_pair) -> None:
    mcp, _ = mcp_client_pair
    fn = _get_tool(mcp, "spawn_run_session")
    with _bearer(None):
        with pytest.raises(ValueError, match="service-principal authentication required"):
            fn(prompt="anything")
