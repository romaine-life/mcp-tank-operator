"""HTTP client wrapper for the tank-operator internal sessions API.

Auth: every call presents this pod's projected SA token in Authorization.
The orchestrator validates it via TokenReview and checks the SA subject
(mcp-tank-operator/mcp-tank-operator) against INTERNAL_API_ALLOWED_SUBJECTS.

Caller identity: every call includes ?caller_pod_ip=<ip>. The orchestrator
resolves that IP to the session pod's owner email via find_pod_by_ip +
tank-operator/owner-email annotation and acts on behalf of that email. This
keeps identity locked to the network-layer source-IP chain and prevents any
caller from claiming an arbitrary email.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

ORCHESTRATOR_URL = os.environ.get(
    "ORCHESTRATOR_INTERNAL_URL",
    "http://tank-operator.tank-operator.svc:80",
)
SA_TOKEN_PATH = os.environ.get(
    "SA_TOKEN_PATH",
    "/var/run/secrets/kubernetes.io/serviceaccount/token",
)

# Cap on streamed prompt output returned in send_prompt results.
MAX_OUTPUT_CHARS = 40_000

_ERROR_BODY_CAP = 1200


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
    """Wraps /api/internal/sessions/* calls with SA-token auth + caller IP."""

    def __init__(
        self,
        orchestrator_url: str = ORCHESTRATOR_URL,
        sa_token_path: str = SA_TOKEN_PATH,
    ) -> None:
        self._url = orchestrator_url.rstrip("/")
        self._sa_token_path = Path(sa_token_path)

    def _sa_token(self) -> str:
        try:
            return self._sa_token_path.read_text().strip()
        except OSError as exc:
            raise RuntimeError(
                f"could not read SA token at {self._sa_token_path}: {exc}"
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._sa_token()}"}

    def list_sessions(self, caller_pod_ip: str) -> list[dict[str, Any]]:
        r = httpx.get(
            f"{self._url}/api/internal/sessions",
            params={"caller_pod_ip": caller_pod_ip},
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def create_session(self, caller_pod_ip: str, mode: str) -> dict[str, Any]:
        r = httpx.post(
            f"{self._url}/api/internal/sessions",
            params={"caller_pod_ip": caller_pod_ip},
            json={"mode": mode},
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def delete_session(self, caller_pod_ip: str, session_id: str) -> dict[str, Any]:
        r = httpx.delete(
            f"{self._url}/api/internal/sessions/{session_id}",
            params={"caller_pod_ip": caller_pod_ip},
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def set_session_name(
        self, caller_pod_ip: str, session_id: str, name: str | None
    ) -> dict[str, Any]:
        r = httpx.patch(
            f"{self._url}/api/internal/sessions/{session_id}",
            params={"caller_pod_ip": caller_pod_ip},
            json={"name": name},
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def set_test_environment(
        self,
        caller_pod_ip: str,
        session_id: str,
        active: bool = True,
        slot_index: int | None = None,
        url: str | None = None,
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"active": active}
        if slot_index is not None:
            body["slot_index"] = slot_index
        if url:
            body["url"] = url
        if lease_id:
            body["lease_id"] = lease_id
        r = httpx.post(
            f"{self._url}/api/internal/sessions/{session_id}/test-state",
            params={"caller_pod_ip": caller_pod_ip},
            json=body,
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def send_message(
        self,
        caller_pod_ip: str,
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
            params={"caller_pod_ip": caller_pod_ip},
            json=body,
            headers=self._headers(),
            timeout=15.0,
        )
        _check(r)
        return r.json()

    def spawn_run(
        self,
        caller_pod_ip: str,
        prompt: str,
        mode: str,
        name: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt, "mode": mode}
        if name:
            body["name"] = name
        if model:
            body["model"] = model
        if permission_mode:
            body["permission_mode"] = permission_mode
        r = httpx.post(
            f"{self._url}/api/internal/sessions/run",
            params={"caller_pod_ip": caller_pod_ip},
            json=body,
            headers=self._headers(),
            timeout=30.0,
        )
        _check(r)
        return r.json()
