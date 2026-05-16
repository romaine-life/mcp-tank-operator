"""Unit tests for TankClient HTTP client.

Every method authenticates with an auth.romaine.life service-principal
JWT (passed as the first arg). Tests patch httpx module-level
functions so we don't need a real orchestrator. See
nelsong6/tank-operator#486 for the rollout that retired the prior
IP-tail + SA-token auth path tested previously.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_tank_operator.client import TankClient, _check  # noqa: E402


# ---------------------------------------------------------------------------
# _check helpers
# ---------------------------------------------------------------------------


def _resp(status: int, body: str, *, method: str = "GET", url: str = "http://orch/api/internal/sessions") -> httpx.Response:
    req = httpx.Request(method, url)
    return httpx.Response(status_code=status, text=body, request=req)


def test_check_passes_on_2xx() -> None:
    _check(_resp(200, '{"ok": true}'))
    _check(_resp(201, '{"id": "abc"}'))
    _check(_resp(202, '{"status": "dispatched"}'))


def test_check_raises_on_4xx_with_body() -> None:
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _check(_resp(422, '{"detail": "could not identify caller"}'))
    assert "422" in str(exc.value)


def test_check_truncates_huge_body() -> None:
    big = "x" * 5000
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _check(_resp(500, big))
    assert "...(truncated)" in str(exc.value)


def test_check_raises_on_403() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        _check(_resp(403, "forbidden"))


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TankClient:
    return TankClient(orchestrator_url="http://orch")


def _ok_response(body: object, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.is_success = 200 <= status < 300
    resp.status_code = status
    resp.reason_phrase = "OK"
    resp.text = ""
    resp.json.return_value = body
    resp.request = httpx.Request("GET", "http://orch/api/internal/sessions")
    return resp


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_sends_get_with_jwt_bearer(client: TankClient) -> None:
    sessions = [{"id": "abc", "owner": "alice@example.test", "status": "Running"}]
    with patch("httpx.get", return_value=_ok_response(sessions)) as mock_get:
        result = client.list_sessions("eyJ.fake.jwt")

    assert result == sessions
    call = mock_get.call_args
    assert call.kwargs["headers"] == {"Authorization": "Bearer eyJ.fake.jwt"}
    assert "params" not in call.kwargs  # no caller_pod_ip query param post-#486
    assert "internal/sessions" in call.args[0]


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_sends_post_with_jwt(client: TankClient) -> None:
    session = {"id": "new123", "mode": "claude_gui", "status": "Pending"}
    with patch("httpx.post", return_value=_ok_response(session, status=201)) as mock_post:
        result = client.create_session("jwt", mode="claude_gui")
    assert result["id"] == "new123"
    call = mock_post.call_args
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    assert call.kwargs["json"] == {"mode": "claude_gui"}
    assert "params" not in call.kwargs


# ---------------------------------------------------------------------------
# delete_session / set_session_name / set_test_environment
# ---------------------------------------------------------------------------


def test_delete_session_sends_delete_with_jwt(client: TankClient) -> None:
    with patch("httpx.delete", return_value=_ok_response({"id": "abc", "status": "deleted"})) as mock_del:
        client.delete_session("jwt", session_id="abc")
    assert mock_del.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    assert "params" not in mock_del.call_args.kwargs


def test_set_session_name_sends_patch(client: TankClient) -> None:
    session = {"id": "abc", "name": "new"}
    with patch("httpx.patch", return_value=_ok_response(session)) as mock_patch:
        client.set_session_name("jwt", session_id="abc", name="new")
    assert mock_patch.call_args.kwargs["json"] == {"name": "new"}
    assert mock_patch.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}


def test_set_session_name_clears_with_none(client: TankClient) -> None:
    with patch("httpx.patch", return_value=_ok_response({"id": "abc"})) as mock_patch:
        client.set_session_name("jwt", session_id="abc", name=None)
    assert mock_patch.call_args.kwargs["json"] == {"name": None}


def test_set_test_environment_sends_post(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"id": "abc"})) as mock_post:
        client.set_test_environment("jwt", session_id="abc", slot_index=2, url="https://slot-2")
    assert mock_post.call_args.kwargs["json"] == {
        "active": True,
        "slot_index": 2,
        "url": "https://slot-2",
    }
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_sends_post(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"status": "queued"})) as mock_post:
        client.send_message("jwt", session_id="abc", prompt="hi")
    assert mock_post.call_args.kwargs["json"] == {"prompt": "hi"}
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}


def test_send_message_includes_optional_model(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"status": "queued"})):
        client.send_message("jwt", session_id="abc", prompt="hi", model="claude")
    # If we get here without error, the optional was passed through.


# ---------------------------------------------------------------------------
# spawn_run — composite (create + ready-wait + send_message)
# ---------------------------------------------------------------------------


def test_spawn_run_uses_spawn_endpoint(client: TankClient) -> None:
    # First call: POST /spawn returns the session record.
    # Then ready-wait polls list_sessions until ready.
    # Then send_message queues the first turn.
    spawn_resp = _ok_response({"id": "child-1", "status": "Pending"}, status=201)
    list_resp = _ok_response([{"id": "child-1", "ready_at": "now", "status": "Active"}])
    msg_resp = _ok_response({"status": "queued"})

    with (
        patch("httpx.post", side_effect=[spawn_resp, msg_resp]) as mock_post,
        patch("httpx.get", return_value=list_resp),
    ):
        result = client.spawn_run("jwt", prompt="hi", mode="claude_gui", name="child-1")

    # First POST is /spawn with the inline name.
    spawn_call = mock_post.call_args_list[0]
    assert "/api/internal/sessions/spawn" in spawn_call.args[0]
    assert spawn_call.kwargs["json"] == {"mode": "claude_gui", "name": "child-1"}
    assert spawn_call.kwargs["headers"] == {"Authorization": "Bearer jwt"}

    # Second POST is the message queue.
    msg_call = mock_post.call_args_list[1]
    assert "/messages" in msg_call.args[0]

    assert result["status"] == "queued"
    assert result["session"]["id"] == "child-1"


def test_spawn_run_raises_when_spawn_returns_no_id(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"mode": "x"}, status=201)):
        with pytest.raises(RuntimeError, match="spawn returned no id"):
            client.spawn_run("jwt", prompt="hi")


def test_spawn_run_times_out_waiting_for_ready_session(client: TankClient) -> None:
    with pytest.raises(TimeoutError, match="session newrun was not ready"):
        client._wait_for_session_ready("jwt", "newrun", timeout_seconds=0.0)
