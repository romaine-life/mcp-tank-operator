"""HTTP client wrapper for the tank-operator internal sessions API.

Auth: every call presents the calling pod's auth.romaine.life
service-principal JWT (forwarded by mcp-auth-proxy in
X-Auth-Romaine-Token; this server reads it into caller.SERVICE_BEARER
and threads it through). The orchestrator verifies the JWT, gates on
``role=service``, and treats the JWT's ``actor_email`` claim as the
owner identity. No SA token, no caller_pod_ip query param.

See nelsong6/tank-operator#486 for the cross-repo plan.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

ORCHESTRATOR_URL = os.environ.get(
    "ORCHESTRATOR_INTERNAL_URL",
    "http://tank-operator.tank-operator.svc:80",
)

_ERROR_BODY_CAP = 1200
_SPAWN_READY_TIMEOUT_SECONDS = 120.0
_SPAWN_READY_POLL_SECONDS = 2.0


def _check(r: httpx.Response) -> None:
    if r.is_success:
        return
    body = r.text or ""
    if len(body) > _ERROR_BODY_CAP:
        body = body[:_ERROR_BODY_CAP] + "...(truncated)"
    detail = f": {body}" if body else ""
    raise httpx.HTTPStatusError(
        f"{r.status_code} {r.reason_phrase} for "
        f"{r.request.method} {r.request.url}{detail}",
        request=r.request,
        response=r,
    )


class TankClient:
    """Wraps /api/internal/sessions/* calls with service-principal JWT auth.

    Every method takes the calling pod's service JWT and forwards it as
    the Authorization Bearer. The orchestrator resolves ``actor_email``
    from the JWT and acts on behalf of that human.
    """

    def __init__(self, orchestrator_url: str = ORCHESTRATOR_URL) -> None:
        self._url = orchestrator_url.rstrip("/")

    def _headers(
        self,
        service_jwt: str,
        *,
        origin_session_id: str | None = None,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {service_jwt}"}
        # Forward the originating tank-operator session id on handoff
        # calls (POST /api/internal/sessions/{id}/messages). Tank-operator
        # uses it to stamp the persisted user_message.created event so
        # the frontend renders the parent session's avatar on the user
        # bubble instead of the human owner's Gravatar. Header name is
        # shared with mcp-auth-proxy (stamping side) and
        # tank-operator/backend-go/cmd/tank-operator/handlers_internal.go
        # (reading side); a cross-repo coordinated deploy applies.
        if origin_session_id:
            headers["X-Tank-Origin-Session-Id"] = origin_session_id
        return headers

    def list_sessions(self, service_jwt: str) -> list[dict[str, Any]]:
        r = httpx.get(
            f"{self._url}/api/internal/sessions",
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def get_session_capabilities(self, service_jwt: str, session_id: str) -> dict[str, Any]:
        """Return the skills and MCP surface visible inside a session pod."""
        r = httpx.get(
            f"{self._url}/api/internal/sessions/{session_id}/capabilities",
            headers=self._headers(service_jwt),
            timeout=20.0,
        )
        _check(r)
        return r.json()

    def read_transcript(
        self,
        service_jwt: str,
        session_id: str,
        anchor: str | None = None,
        rows: int | None = None,
        before_cursor: str | None = None,
        timeline_id: str | None = None,
    ) -> dict[str, Any]:
        """Read the projected transcript-row read model for a session.

        GET /api/internal/sessions/{id}/timeline. The orchestrator scopes the
        read to the JWT's actor_email (same gate as the browser /timeline),
        so the caller can only read sessions it owns; a cross-user or missing
        session returns 404.

        Query params mirror the browser /timeline contract:
          - anchor: "newest" (default, tail) or "oldest" (head).
          - rows: page size (server clamps to its max).
          - before_cursor: page strictly older than a prev_cursor from an
            earlier response — the backward-pagination path through history.
          - timeline_id: center the page on a specific transcript row.

        before_cursor / timeline_id / anchor are mutually exclusive; the
        server rejects more than one anchor with 400.
        """
        params: dict[str, str] = {}
        if anchor:
            params["anchor"] = anchor
        if rows is not None:
            params["rows"] = str(rows)
        if before_cursor:
            params["before_cursor"] = before_cursor
        if timeline_id:
            params["timeline_id"] = timeline_id
        r = httpx.get(
            f"{self._url}/api/internal/sessions/{session_id}/timeline",
            params=params or None,
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def create_session(self, service_jwt: str, mode: str) -> dict[str, Any]:
        r = httpx.post(
            f"{self._url}/api/internal/sessions",
            json={"mode": mode},
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def delete_session(
        self, service_jwt: str, session_id: str,
    ) -> dict[str, Any]:
        r = httpx.delete(
            f"{self._url}/api/internal/sessions/{session_id}",
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def set_session_name(
        self, service_jwt: str, session_id: str, name: str | None,
    ) -> dict[str, Any]:
        r = httpx.patch(
            f"{self._url}/api/internal/sessions/{session_id}",
            json={"name": name},
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def set_test_environment(
        self,
        service_jwt: str,
        session_id: str,
        active: bool = True,
        slot_index: int | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"active": active}
        if slot_index is not None:
            body["slot_index"] = slot_index
        if url:
            body["url"] = url
        r = httpx.post(
            f"{self._url}/api/internal/sessions/{session_id}/test-state",
            json=body,
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def send_message(
        self,
        service_jwt: str,
        session_id: str,
        prompt: str,
        model: str | None = None,
        permission_mode: str | None = None,
        origin_session_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt}
        if model:
            body["model"] = model
        if permission_mode:
            body["permission_mode"] = permission_mode
        r = httpx.post(
            f"{self._url}/api/internal/sessions/{session_id}/messages",
            json=body,
            headers=self._headers(service_jwt, origin_session_id=origin_session_id),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def spawn_run(
        self,
        service_jwt: str,
        prompt: str,
        mode: str = "claude_gui",
        name: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        origin_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a session, wait for ready, then queue the first prompt.

        `origin_session_id` rides only on the inner send_message call,
        not on the create. Tank-operator stamps it onto the persisted
        user_message.created event so the frontend renders the parent
        session's avatar on the first user bubble in the new session —
        making it visually obvious that the prompt came from another
        agent rather than the human owner.
        """
        body: dict[str, Any] = {"mode": mode}
        if name:
            body["name"] = name
        # POST /api/internal/sessions — the canonical service-principal
        # session-create endpoint. Accepts inline `name` post-#486. The
        # legacy `/spawn` alias was retired in the API cleanup PR that
        # ships alongside this change.
        r = httpx.post(
            f"{self._url}/api/internal/sessions",
            json=body,
            headers=self._headers(service_jwt),
            timeout=15.0,
        )
        _check(r)
        session = r.json()
        session_id = str(session.get("id") or "")
        if not session_id:
            raise RuntimeError(f"spawn returned no id: {session!r}")
        session = self._wait_for_session_ready(service_jwt, session_id)
        message = self.send_message(
            service_jwt,
            session_id=session_id,
            prompt=prompt,
            model=model,
            permission_mode=permission_mode,
            origin_session_id=origin_session_id,
        )
        return {"status": "queued", "session": session, "message": message}

    def _wait_for_session_ready(
        self,
        service_jwt: str,
        session_id: str,
        timeout_seconds: float = _SPAWN_READY_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_session: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            for session in self.list_sessions(service_jwt):
                if str(session.get("id")) != session_id:
                    continue
                last_session = session
                if session.get("ready_at") or session.get("status") == "Active":
                    return session
            time.sleep(_SPAWN_READY_POLL_SECONDS)
        raise TimeoutError(
            f"session {session_id} was not ready after {timeout_seconds:.0f}s"
            + (f"; last state: {last_session!r}" if last_session else "")
        )
