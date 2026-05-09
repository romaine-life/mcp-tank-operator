"""Unit tests for TankClient HTTP client.

Uses unittest.mock to patch httpx module-level functions so we don't need
a real server or network. SA token is injected via a temp file.
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
    assert "could not identify caller" in str(exc.value)


def test_check_truncates_huge_body() -> None:
    huge = "x" * 5000
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _check(_resp(500, huge))
    assert "...(truncated)" in str(exc.value)
    assert len(str(exc.value)) < 2500


def test_check_raises_on_403() -> None:
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _check(_resp(403, '{"detail": "not owned by caller"}', method="DELETE"))
    assert "403" in str(exc.value)


# ---------------------------------------------------------------------------
# TankClient helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def sa_token_file(tmp_path: Path) -> Path:
    token_file = tmp_path / "token"
    token_file.write_text("test-sa-token")
    return token_file


@pytest.fixture()
def client(sa_token_file: Path) -> TankClient:
    return TankClient(
        orchestrator_url="http://orch",
        sa_token_path=str(sa_token_file),
    )


def _ok_response(body: object, status: int = 200) -> MagicMock:
    import json

    mock = MagicMock(spec=httpx.Response)
    mock.is_success = True
    mock.status_code = status
    mock.json.return_value = body
    mock.text = json.dumps(body)
    return mock


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_sends_get_with_caller_ip(client: TankClient) -> None:
    sessions = [{"id": "abc", "owner": "alice@example.test", "status": "Running"}]
    with patch("httpx.get", return_value=_ok_response(sessions)) as mock_get:
        result = client.list_sessions("10.0.0.1")

    assert result == sessions
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args
    assert call_kwargs.kwargs["params"] == {"caller_pod_ip": "10.0.0.1"}
    assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-sa-token"
    assert "internal/sessions" in call_kwargs.args[0]


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_sends_post_with_mode(client: TankClient) -> None:
    session = {"id": "new123", "mode": "subscription", "status": "Pending"}
    with patch("httpx.post", return_value=_ok_response(session, status=201)) as mock_post:
        result = client.create_session("10.0.0.1", mode="subscription")

    assert result["id"] == "new123"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"] == {"mode": "subscription"}
    assert mock_post.call_args.kwargs["params"] == {"caller_pod_ip": "10.0.0.1"}


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


def test_delete_session_sends_delete_to_session_url(client: TankClient) -> None:
    with patch("httpx.delete", return_value=_ok_response({"id": "abc", "status": "deleted"})) as mock_del:
        result = client.delete_session("10.0.0.1", session_id="abc")

    assert result["status"] == "deleted"
    mock_del.assert_called_once()
    assert "sessions/abc" in mock_del.call_args.args[0]


# ---------------------------------------------------------------------------
# set_session_name
# ---------------------------------------------------------------------------


def test_set_session_name_sends_patch(client: TankClient) -> None:
    session = {"id": "abc", "name": "rollout watcher", "status": "Running"}
    with patch("httpx.patch", return_value=_ok_response(session)) as mock_patch:
        result = client.set_session_name("10.0.0.1", session_id="abc", name="rollout watcher")

    assert result["name"] == "rollout watcher"
    assert mock_patch.call_args.kwargs["json"] == {"name": "rollout watcher"}


def test_set_session_name_clears_name_with_none(client: TankClient) -> None:
    session = {"id": "abc", "name": None, "status": "Running"}
    with patch("httpx.patch", return_value=_ok_response(session)) as mock_patch:
        client.set_session_name("10.0.0.1", session_id="abc", name=None)

    assert mock_patch.call_args.kwargs["json"] == {"name": None}


def test_set_test_environment_sends_post(client: TankClient) -> None:
    session = {
        "id": "abc",
        "test_state": {
            "active": True,
            "slot_index": 2,
            "url": "https://tank-slot-2.tank.dev.romaine.life",
        },
    }
    with patch("httpx.post", return_value=_ok_response(session)) as mock_post:
        result = client.set_test_environment(
            "10.0.0.1",
            session_id="abc",
            slot_index=2,
            url="https://tank-slot-2.tank.dev.romaine.life",
        )

    assert result["test_state"]["slot_index"] == 2
    assert "sessions/abc/test-state" in mock_post.call_args.args[0]
    assert mock_post.call_args.kwargs["json"] == {
        "active": True,
        "slot_index": 2,
        "url": "https://tank-slot-2.tank.dev.romaine.life",
    }


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_sends_post_to_messages_endpoint(client: TankClient) -> None:
    resp_body = {"status": "queued"}
    with patch("httpx.post", return_value=_ok_response(resp_body, status=202)) as mock_post:
        client.send_message("10.0.0.1", session_id="abc", prompt="keep going")

    assert "sessions/abc/messages" in mock_post.call_args.args[0]
    assert mock_post.call_args.kwargs["json"]["prompt"] == "keep going"


def test_send_message_includes_optional_model(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"status": "queued"}, status=202)) as mock_post:
        client.send_message("10.0.0.1", session_id="abc", prompt="hi", model="claude-opus-4-7")

    assert mock_post.call_args.kwargs["json"]["model"] == "claude-opus-4-7"


def test_send_message_omits_none_optionals(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"status": "queued"}, status=202)) as mock_post:
        client.send_message("10.0.0.1", session_id="abc", prompt="hi")

    body = mock_post.call_args.kwargs["json"]
    assert "model" not in body
    assert "permission_mode" not in body


# ---------------------------------------------------------------------------
# spawn_run
# ---------------------------------------------------------------------------


def test_spawn_run_sends_post_to_run_endpoint(client: TankClient) -> None:
    resp_body = {"session": {"id": "newrun"}, "status": "dispatched"}
    with patch("httpx.post", return_value=_ok_response(resp_body, status=202)) as mock_post:
        result = client.spawn_run("10.0.0.1", prompt="fix the bug", mode="subscription_headless")

    assert "sessions/run" in mock_post.call_args.args[0]
    assert result["status"] == "dispatched"
    body = mock_post.call_args.kwargs["json"]
    assert body["prompt"] == "fix the bug"
    assert body["mode"] == "subscription_headless"


def test_spawn_run_includes_optional_name(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"session": {}, "status": "dispatched"}, status=202)) as mock_post:
        client.spawn_run("10.0.0.1", prompt="hi", mode="subscription_headless", name="my-run")

    assert mock_post.call_args.kwargs["json"]["name"] == "my-run"


# ---------------------------------------------------------------------------
# SA token error
# ---------------------------------------------------------------------------


def test_sa_token_read_error_raises_runtime_error() -> None:
    bad_client = TankClient(orchestrator_url="http://orch", sa_token_path="/nonexistent/token")
    with pytest.raises(RuntimeError, match="could not read SA token"):
        bad_client.list_sessions("10.0.0.1")
