"""Service-principal JWT extraction.

The auth.romaine.life service JWT is forwarded by the calling session
pod's mcp-auth-proxy sidecar in the X-Auth-Romaine-Token header. We
bind it into the SERVICE_BEARER ContextVar for the lifetime of the
request; tools read it via current_service_bearer().

See romaine-life/tank-operator#486 for the rollout that retired the prior
IP-tail identity path.
"""
from __future__ import annotations

import sys
import base64
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_tank_operator.caller import (  # noqa: E402
    CALLER_SESSION_ID,
    CALLER_SESSION_SCOPE,
    SERVICE_BEARER,
    SERVICE_BEARER_HEADER,
    current_caller_session_id,
    current_caller_session_scope,
    current_service_bearer,
)


def test_service_bearer_header_constant_matches_upstream_contract() -> None:
    # mcp-auth-proxy (in tank-operator's claude-container) writes the JWT
    # to this exact header. Changing the constant requires a cross-repo
    # coordinated deploy.
    assert SERVICE_BEARER_HEADER == "x-auth-romaine-token"


def test_current_service_bearer_default_is_none() -> None:
    """Absence is the unauthenticated signal — tools surface a clean error
    rather than silently proceeding without a caller identity."""
    token = SERVICE_BEARER.set(None)
    try:
        assert current_service_bearer() is None
    finally:
        SERVICE_BEARER.reset(token)


def test_current_service_bearer_round_trips() -> None:
    token = SERVICE_BEARER.set("eyJ.fake.jwt")
    try:
        assert current_service_bearer() == "eyJ.fake.jwt"
    finally:
        SERVICE_BEARER.reset(token)


def test_current_caller_session_prefers_infrastructure_header() -> None:
    id_token = CALLER_SESSION_ID.set("session-709")
    scope_token = CALLER_SESSION_SCOPE.set("default")
    bearer_token = SERVICE_BEARER.set(_unsigned_jwt({"sub": "svc:tank:708"}))
    try:
        assert current_caller_session_id() == "709"
        assert current_caller_session_scope() == "default"
    finally:
        SERVICE_BEARER.reset(bearer_token)
        CALLER_SESSION_SCOPE.reset(scope_token)
        CALLER_SESSION_ID.reset(id_token)


def test_current_caller_session_falls_back_to_service_jwt_sub() -> None:
    token = SERVICE_BEARER.set(_unsigned_jwt({"sub": "svc:tank:709"}))
    try:
        assert current_caller_session_id() == "709"
        assert current_caller_session_scope() == "default"
    finally:
        SERVICE_BEARER.reset(token)


def _unsigned_jwt(claims: dict[str, str]) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def enc(value: dict[str, str]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{enc(header)}.{enc(claims)}."
