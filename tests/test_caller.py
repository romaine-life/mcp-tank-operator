"""Service-principal JWT extraction.

The auth.romaine.life service JWT is forwarded by the calling session
pod's mcp-auth-proxy sidecar in the X-Auth-Romaine-Token header. We
bind it into the SERVICE_BEARER ContextVar for the lifetime of the
request; tools read it via current_service_bearer().

See nelsong6/tank-operator#486 for the rollout that retired the prior
IP-tail identity path.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_tank_operator.caller import (  # noqa: E402
    SERVICE_BEARER,
    SERVICE_BEARER_HEADER,
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
