"""Unit tests for MCP tool registrations.

Tools are thin wrappers: they read the CALLER_POD_IP ContextVar, call
TankClient methods, and return results. Tests verify:
  - _pod_ip() raises the right error when ContextVar is unset.
  - Each tool delegates to the correct TankClient method.
  - get_session_url walks the list and raises on missing session.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from mcp_tank_operator.caller import CALLER_POD_IP  # noqa: E402
from mcp_tank_operator.tools import register_tools  # noqa: E402


@pytest.fixture()
def mcp_client_pair():
    mcp = FastMCP("test-tank-operator-mcp")
    client = MagicMock()
    register_tools(mcp, client)
    return mcp, client


def _set_ip(ip: str | None):
    """Context manager that sets CALLER_POD_IP for the duration of a call."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        token = CALLER_POD_IP.set(ip)
        try:
            yield
        finally:
            CALLER_POD_IP.reset(token)

    return _ctx()


def _get_tool(mcp: FastMCP, name: str):
    """Retrieve the raw callable for a registered tool by name."""
    for tool in mcp._tool_manager._tools.values():
        if tool.name == name:
            return tool.fn
    raise KeyError(f"tool {name!r} not registered")


# ---------------------------------------------------------------------------
# _pod_ip guard
# ---------------------------------------------------------------------------


def test_list_sessions_raises_when_no_caller_ip(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    fn = _get_tool(mcp, "list_sessions")
    with _set_ip(None):
        with pytest.raises(ValueError, match="could not identify caller"):
            fn()


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [{"id": "abc"}]
    fn = _get_tool(mcp, "list_sessions")
    with _set_ip("10.0.0.5"):
        result = fn()
    client.list_sessions.assert_called_once_with("10.0.0.5")
    assert result == [{"id": "abc"}]


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.create_session.return_value = {"id": "new", "mode": "subscription"}
    fn = _get_tool(mcp, "create_session")
    with _set_ip("10.0.0.5"):
        result = fn(mode="subscription")
    client.create_session.assert_called_once_with("10.0.0.5", mode="subscription")
    assert result["id"] == "new"


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


def test_delete_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.delete_session.return_value = {"id": "abc", "status": "deleted"}
    fn = _get_tool(mcp, "delete_session")
    with _set_ip("10.0.0.5"):
        result = fn(session_id="abc")
    client.delete_session.assert_called_once_with("10.0.0.5", session_id="abc")
    assert result["status"] == "deleted"


# ---------------------------------------------------------------------------
# set_session_name
# ---------------------------------------------------------------------------


def test_set_session_name_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.set_session_name.return_value = {"id": "abc", "name": "watcher"}
    fn = _get_tool(mcp, "set_session_name")
    with _set_ip("10.0.0.5"):
        result = fn(session_id="abc", name="watcher")
    client.set_session_name.assert_called_once_with("10.0.0.5", session_id="abc", name="watcher")
    assert result["name"] == "watcher"


def test_set_test_environment_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.set_test_environment.return_value = {"id": "abc", "test_state": {"slot_index": 2}}
    fn = _get_tool(mcp, "set_test_environment")
    with _set_ip("10.0.0.5"):
        result = fn(
            session_id="abc",
            slot_index=2,
            url="https://tank-slot-2.tank.dev.romaine.life",
        )
    client.set_test_environment.assert_called_once_with(
        "10.0.0.5",
        session_id="abc",
        active=True,
        slot_index=2,
        url="https://tank-slot-2.tank.dev.romaine.life",
    )
    assert result["test_state"]["slot_index"] == 2


# ---------------------------------------------------------------------------
# get_session_url
# ---------------------------------------------------------------------------


def test_get_session_url_finds_matching_session(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [
        {"id": "abc", "url": "https://tank.romaine.life/?session=abc"},
        {"id": "xyz", "url": "https://tank.romaine.life/?session=xyz"},
    ]
    fn = _get_tool(mcp, "get_session_url")
    with _set_ip("10.0.0.5"):
        result = fn(session_id="abc")
    assert result["session_id"] == "abc"
    assert result["url"].endswith("?session=abc")


def test_get_session_url_raises_when_not_found(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.list_sessions.return_value = [{"id": "xyz", "url": "https://tank.romaine.life/?session=xyz"}]
    fn = _get_tool(mcp, "get_session_url")
    with _set_ip("10.0.0.5"):
        with pytest.raises(ValueError, match="not found or not owned"):
            fn(session_id="abc")


# ---------------------------------------------------------------------------
# send_prompt
# ---------------------------------------------------------------------------


def test_send_prompt_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.send_message.return_value = {"status": "queued"}
    fn = _get_tool(mcp, "send_prompt")
    with _set_ip("10.0.0.5"):
        result = fn(session_id="abc", prompt="keep going")
    client.send_message.assert_called_once_with(
        "10.0.0.5",
        session_id="abc",
        prompt="keep going",
        model=None,
        permission_mode=None,
    )
    assert result["status"] == "queued"


def test_send_prompt_forwards_optional_model(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.send_message.return_value = {"status": "queued"}
    fn = _get_tool(mcp, "send_prompt")
    with _set_ip("10.0.0.5"):
        fn(session_id="abc", prompt="hi", model="claude-opus-4-7")
    assert client.send_message.call_args.kwargs["model"] == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# spawn_run_session
# ---------------------------------------------------------------------------


def test_spawn_run_session_delegates_to_client(mcp_client_pair) -> None:
    mcp, client = mcp_client_pair
    client.spawn_run.return_value = {"session": {"id": "new"}, "status": "dispatched"}
    fn = _get_tool(mcp, "spawn_run_session")
    with _set_ip("10.0.0.5"):
        result = fn(prompt="investigate issue")
    client.spawn_run.assert_called_once_with(
        "10.0.0.5",
        prompt="investigate issue",
        mode="subscription_headless",
        name=None,
        model=None,
        permission_mode=None,
    )
    assert result["status"] == "dispatched"
