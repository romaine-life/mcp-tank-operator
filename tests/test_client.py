"""Unit tests for TankClient HTTP client.

Every method authenticates with an auth.romaine.life service-principal
JWT (passed as the first arg). Tests patch httpx module-level
functions so we don't need a real orchestrator. See
romaine-life/tank-operator#486 for the rollout that retired the prior
IP-tail + SA-token auth path tested previously.
"""
from __future__ import annotations

import re
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


def test_get_session_run_options_sends_get_with_jwt_bearer(client: TankClient) -> None:
    body = {"create_modes": ["claude_gui", "codex_gui"], "models": {"codex": ["gpt-5.5"]}}
    with patch("httpx.get", return_value=_ok_response(body)) as mock_get:
        result = client.get_session_run_options("jwt")

    assert result == body
    call = mock_get.call_args
    assert call.args[0].endswith("/api/internal/session-run-options")
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    assert call.kwargs["timeout"] == 15.0


# ---------------------------------------------------------------------------
# read_transcript
# ---------------------------------------------------------------------------


def test_get_session_capabilities_sends_get_with_jwt(client: TankClient) -> None:
    body = {"session": {"id": "63"}, "mcp_servers": [], "mcp_tools": []}
    with patch("httpx.get", return_value=_ok_response(body)) as mock_get:
        result = client.get_session_capabilities("jwt", session_id="63")

    assert result == body
    call = mock_get.call_args
    assert call.args[0].endswith("/api/internal/sessions/63/capabilities")
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    assert call.kwargs["timeout"] == 20.0


def test_read_transcript_sends_get_with_jwt_and_no_params(client: TankClient) -> None:
    body = {"session_id": "63", "rows": [], "projection": "server_transcript_rows_v1"}
    with patch("httpx.get", return_value=_ok_response(body)) as mock_get:
        result = client.read_transcript("jwt", session_id="63")

    assert result == body
    call = mock_get.call_args
    assert call.args[0].endswith("/api/internal/sessions/63/timeline")
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    # No anchor/rows passed → no query params (None, not an empty dict).
    assert call.kwargs["params"] is None


def test_read_transcript_forwards_query_params(client: TankClient) -> None:
    with patch("httpx.get", return_value=_ok_response({"rows": []})) as mock_get:
        client.read_transcript(
            "jwt",
            session_id="63",
            anchor="oldest",
            rows=40,
            before_cursor="cur-abc",
        )
    params = mock_get.call_args.kwargs["params"]
    assert params == {"anchor": "oldest", "rows": "40", "before_cursor": "cur-abc"}


def test_read_transcript_raises_on_404(client: TankClient) -> None:
    with patch("httpx.get", return_value=_resp(404, '{"detail": "session not found"}', method="GET")):
        with pytest.raises(httpx.HTTPStatusError):
            client.read_transcript("jwt", session_id="nope")


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
        client.set_test_environment(
            "jwt",
            session_id="abc",
            slot_index=2,
            url="https://slot-2",
            pull_request_url="https://github.com/romaine-life/tank-operator/pull/123",
        )
    assert mock_post.call_args.kwargs["json"] == {
        "active": True,
        "slot_index": 2,
        "url": "https://slot-2",
        "pull_request_url": "https://github.com/romaine-life/tank-operator/pull/123",
    }
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}


def test_set_pull_request_link_sends_post(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"id": "abc"})) as mock_post:
        client.set_pull_request_link(
            "jwt",
            session_id="abc",
            url="https://github.com/romaine-life/tank-operator/pull/123",
        )
    assert mock_post.call_args.args[0].endswith("/api/internal/sessions/abc/pull-request-link")
    assert mock_post.call_args.kwargs["json"] == {
        "url": "https://github.com/romaine-life/tank-operator/pull/123",
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


def test_send_message_forwards_origin_session_avatar_header(client: TankClient) -> None:
    with patch("httpx.post", return_value=_ok_response({"status": "queued"})) as mock_post:
        client.send_message(
            "jwt",
            session_id="abc",
            prompt="hi",
            origin_session_id="42",
            origin_session_avatar_id="jp1-grant",
        )

    assert mock_post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer jwt",
        "X-Tank-Origin-Session-Id": "42",
        "X-Tank-Origin-Session-Avatar-Id": "jp1-grant",
    }


# ---------------------------------------------------------------------------
# spawn_run — single create that carries the first turn as initial_turn
# ---------------------------------------------------------------------------


def test_spawn_run_sends_initial_turn_on_create(client: TankClient) -> None:
    # The prompt rides the CREATE as initial_turn — there is no second
    # /messages call and no ready-wait. The orchestrator requires the first
    # turn at create time for GUI chat sessions, so a promptless create that
    # could leave an empty, unusable session is never issued.
    create_resp = _ok_response(
        {"id": "child-1", "status": "Pending", "initial_turn": {"status": "queued"}},
        status=201,
    )
    with patch("httpx.post", return_value=create_resp) as mock_post:
        result = client.spawn_run("jwt", prompt="hi", mode="claude_gui", name="child-1")

    # Exactly one POST: the create. No /messages follow-up, no list-poll.
    assert mock_post.call_count == 1
    call = mock_post.call_args
    assert call.args[0].endswith("/api/internal/sessions")
    body = call.kwargs["json"]
    assert body["mode"] == "claude_gui"
    assert body["name"] == "child-1"
    assert body["initial_turn"]["prompt"] == "hi"
    # client_nonce matches the orchestrator turn-id syntax ^[A-Za-z0-9._-]{1,80}$.
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,80}", body["initial_turn"]["client_nonce"])
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}
    assert result["status"] == "queued"
    assert result["session"]["id"] == "child-1"


def test_spawn_run_requires_non_empty_prompt(client: TankClient) -> None:
    # The client refuses to create a promptless GUI session — the empty,
    # unusable-session footgun is closed on the client as well as the server.
    with patch("httpx.post") as mock_post:
        for bad in ("", "   "):
            with pytest.raises(ValueError, match="prompt is required"):
                client.spawn_run("jwt", prompt=bad)
    mock_post.assert_not_called()


def test_spawn_run_forwards_model_effort_repos_to_create(client: TankClient) -> None:
    # Explicit run config rides the CREATE so the row records the model/effort
    # before the runner starts; the orchestrator applies it to the initial turn.
    create_resp = _ok_response({"id": "cdx-1", "status": "Pending"}, status=201)
    with patch("httpx.post", return_value=create_resp) as mock_post:
        client.spawn_run(
            "jwt",
            prompt="hi",
            mode="codex_gui",
            name="cdx-1",
            model="gpt-5.5",
            effort="high",
            repos=["romaine-life/tank-operator"],
        )

    assert mock_post.call_count == 1
    body = mock_post.call_args.kwargs["json"]
    assert body["mode"] == "codex_gui"
    assert body["name"] == "cdx-1"
    assert body["model"] == "gpt-5.5"
    assert body["effort"] == "high"
    assert body["repos"] == ["romaine-life/tank-operator"]
    assert body["initial_turn"]["prompt"] == "hi"


def test_spawn_run_forwards_permission_mode_on_initial_turn(client: TankClient) -> None:
    # permission_mode is turn-scoped, so it rides initial_turn, not the create body.
    create_resp = _ok_response({"id": "c"}, status=201)
    with patch("httpx.post", return_value=create_resp) as mock_post:
        client.spawn_run("jwt", prompt="hi", permission_mode="acceptEdits")
    initial_turn = mock_post.call_args.kwargs["json"]["initial_turn"]
    assert initial_turn["permission_mode"] == "acceptEdits"


def test_spawn_run_forwards_origin_session_avatar_on_create(client: TankClient) -> None:
    # The origin headers ride the CREATE now (the prompt is part of it), so the
    # first user bubble still renders the parent session's avatar.
    create_resp = _ok_response({"id": "child-1"}, status=201)
    with patch("httpx.post", return_value=create_resp) as mock_post:
        client.spawn_run(
            "jwt",
            prompt="hi",
            origin_session_id="42",
            origin_session_avatar_id="jp1-grant",
        )

    assert mock_post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer jwt",
        "X-Tank-Origin-Session-Id": "42",
        "X-Tank-Origin-Session-Avatar-Id": "jp1-grant",
    }


# ---------------------------------------------------------------------------
# spawn_test_slot_session — single create against a SLOT orchestrator
# ---------------------------------------------------------------------------


def test_spawn_test_slot_session_uses_slot_orchestrator(client: TankClient) -> None:
    create_resp = _ok_response({"id": "slot-child", "status": "Pending"}, status=201)
    with patch("httpx.post", return_value=create_resp) as mock_post:
        result = client.spawn_test_slot_session(
            "jwt",
            slot_name="tank-operator-slot-2",
            prompt="validate",
            mode="claude_gui",
            name="slot validation",
        )

    assert mock_post.call_count == 1
    call = mock_post.call_args
    assert call.args[0] == (
        "http://tank-operator.tank-operator-slot-2.svc:80/api/internal/sessions"
    )
    body = call.kwargs["json"]
    assert body["mode"] == "claude_gui"
    assert body["name"] == "slot validation"
    assert body["initial_turn"]["prompt"] == "validate"
    assert result["session"]["id"] == "slot-child"


def test_spawn_test_slot_session_uses_tank_test_slot_defaults(client: TankClient) -> None:
    opts_resp = _ok_response(
        {
            "test_slot_defaults": {
                "mode": "codex_gui",
                "model": "gpt-5.4-mini",
                "effort": "low",
            }
        }
    )
    create_resp = _ok_response({"id": "slot-child", "status": "Pending"}, status=201)

    with (
        patch("httpx.get", return_value=opts_resp),
        patch("httpx.post", return_value=create_resp) as mock_post,
    ):
        result = client.spawn_test_slot_session(
            "jwt",
            slot_name="tank-operator-slot-2",
            prompt="validate",
            name="slot validation",
        )

    assert mock_post.call_count == 1
    call = mock_post.call_args
    assert call.args[0] == (
        "http://tank-operator.tank-operator-slot-2.svc:80/api/internal/sessions"
    )
    body = call.kwargs["json"]
    assert body["mode"] == "codex_gui"
    assert body["model"] == "gpt-5.4-mini"
    assert body["effort"] == "low"
    assert body["name"] == "slot validation"
    assert body["initial_turn"]["prompt"] == "validate"
    assert result["session"]["id"] == "slot-child"


def test_spawn_test_slot_session_refuses_production_targets(client: TankClient) -> None:
    for bad in ("default", "tank-operator", "https://tank.romaine.life", "slot/../../x"):
        with pytest.raises(ValueError):
            client.spawn_test_slot_session("jwt", slot_name=bad, prompt="validate")


# ---------------------------------------------------------------------------
# session-image override (test-slot repoint) — targets the SLOT orchestrator
# ---------------------------------------------------------------------------


def test_set_session_image_override_puts_to_slot_orchestrator(client: TankClient) -> None:
    body = {"session_scope": "tank-operator-slot-2", "codex_image": "img"}
    with patch("httpx.put", return_value=_ok_response(body)) as mock_put:
        result = client.set_session_image_override(
            "jwt", "tank-operator-slot-2", codex_image="img", git_ref="feat/x",
        )
    assert result == body
    call = mock_put.call_args
    # Targets the slot's own orchestrator (namespace == slot name) and the
    # slot-scoped override path — NOT the configured prod orchestrator URL.
    assert call.args[0] == (
        "http://tank-operator.tank-operator-slot-2.svc:80"
        "/api/internal/session-scopes/tank-operator-slot-2/image-override"
    )
    assert call.kwargs["json"] == {"codex_image": "img", "git_ref": "feat/x"}
    assert call.kwargs["headers"] == {"Authorization": "Bearer jwt"}


def test_set_session_image_override_forwards_antigravity_image(client: TankClient) -> None:
    body = {"session_scope": "tank-operator-slot-2", "antigravity_image": "agy-img"}
    with patch("httpx.put", return_value=_ok_response(body)) as mock_put:
        result = client.set_session_image_override(
            "jwt", "tank-operator-slot-2", antigravity_image="agy-img", git_ref="feat/agy",
        )
    assert result == body
    call = mock_put.call_args
    assert call.args[0] == (
        "http://tank-operator.tank-operator-slot-2.svc:80"
        "/api/internal/session-scopes/tank-operator-slot-2/image-override"
    )
    # Only the antigravity image (+ git_ref) ride the body — unset providers omitted.
    assert call.kwargs["json"] == {"antigravity_image": "agy-img", "git_ref": "feat/agy"}


def test_get_session_image_override_returns_unset_on_404(client: TankClient) -> None:
    with patch("httpx.get", return_value=_resp(404, '{"detail": "no override"}')):
        result = client.get_session_image_override("jwt", "tank-operator-slot-2")
    assert result == {"session_scope": "tank-operator-slot-2", "override_set": False}


def test_get_session_image_override_returns_value(client: TankClient) -> None:
    body = {"session_scope": "tank-operator-slot-2", "codex_image": "img"}
    with patch("httpx.get", return_value=_ok_response(body)):
        result = client.get_session_image_override("jwt", "tank-operator-slot-2")
    assert result["codex_image"] == "img"
    assert result["override_set"] is True


def test_clear_session_image_override_deletes(client: TankClient) -> None:
    with patch("httpx.delete", return_value=_ok_response({"status": "ok", "removed": True})) as mock_del:
        client.clear_session_image_override("jwt", "tank-operator-slot-2")
    assert mock_del.call_args.args[0].endswith(
        "/api/internal/session-scopes/tank-operator-slot-2/image-override"
    )
    assert mock_del.call_args.kwargs["headers"] == {"Authorization": "Bearer jwt"}


def test_slot_orchestrator_url_refuses_production_targets(client: TankClient) -> None:
    for bad in ("default", "tank-operator", "   ", "", "https://tank.romaine.life", "slot/../../x"):
        with pytest.raises(ValueError):
            client._slot_orchestrator_url(bad)
