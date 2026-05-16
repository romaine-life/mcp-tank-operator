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

    def _headers(self, service_jwt: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {service_jwt}"}

    def list_sessions(self, service_jwt: str) -> list[dict[str, Any]]:
        r = httpx.get(
            f"{self._url}/api/internal/sessions",
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
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt}
        if model:
            body["model"] = model
        if permission_mode:
            body["permission_mode"] = permission_mode
        r = httpx.post(
            f"{self._url}/api/internal/sessions/{session_id}/messages",
            json=body,
            headers=self._headers(service_jwt),
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
    ) -> dict[str, Any]:
        """Create a session, wait for ready, then queue the first prompt."""
        body: dict[str, Any] = {"mode": mode}
        if name:
            body["name"] = name
        # Use /spawn for new-session creation — semantically clearer than
        # the bare POST /api/internal/sessions and lets the orchestrator
        # accept an inline `name` field. Both endpoints have identical
        # auth + behavior post-#486.
        r = httpx.post(
            f"{self._url}/api/internal/sessions/spawn",
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
