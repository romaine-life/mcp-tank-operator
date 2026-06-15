"""HTTP entrypoint — streamable-http transport.

Authorization is the auth.romaine.life service-principal JWT, not
Kubernetes RBAC. The calling pod's mcp-auth-proxy sidecar forwards the
JWT in ``X-Auth-Romaine-Token``; a Starlette middleware extracts it into
the ``SERVICE_BEARER`` ContextVar and tool handlers thread it through to
TankClient, which presents it as ``Authorization: Bearer`` on the
outbound ``/api/internal/sessions/*`` call. The orchestrator verifies
the JWT, gates on ``role=service``, and scopes every action to the
JWT's ``actor_email`` — so a caller can only ever act as itself, and a
request without a valid JWT is refused (tools raise a clean
"service-principal authentication required"; the orchestrator rejects an
invalid JWT outright).

There is no kube-rbac-proxy sidecar and no per-caller RBAC allowlist:
that gate was removed because it enumerated individual service accounts
(claude-session, the deleted hermes, and it could never cover dynamic
Glimmung slot SAs) while the JWT path above is the real boundary. The
server binds 0.0.0.0 and is reached directly via the ``mcp-tank-operator``
Service. See romaine-life/tank-operator#486 for the rollout that retired
the prior IP-tail identity path.
"""

import logging
import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from .caller import (
    CALLER_KIND_HEADER,
    CALLER_SESSION_ID,
    CALLER_SESSION_ID_HEADER,
    CALLER_SESSION_SCOPE,
    CALLER_SESSION_SCOPE_HEADER,
    CALLER_SYSTEM_HEADER,
    ORIGIN_SESSION_HEADER,
    ORIGIN_SESSION_AVATAR_HEADER,
    ORIGIN_SESSION_ID,
    ORIGIN_SESSION_AVATAR_ID,
    SERVICE_BEARER,
    SERVICE_BEARER_HEADER,
)
from .client import TankClient
from .tools import register_tools

log = logging.getLogger(__name__)


class CallerIdentityMiddleware(BaseHTTPMiddleware):
    """Bind the inbound service-principal JWT into the SERVICE_BEARER
    ContextVar for the duration of the request. Absent header → None,
    which tool handlers surface as a domain-specific error."""

    async def dispatch(self, request: Request, call_next):
        bearer = request.headers.get(SERVICE_BEARER_HEADER)
        if bearer is not None:
            bearer = bearer.strip() or None
        origin = request.headers.get(ORIGIN_SESSION_HEADER)
        if origin is not None:
            origin = origin.strip() or None
        origin_avatar = request.headers.get(ORIGIN_SESSION_AVATAR_HEADER)
        if origin_avatar is not None:
            origin_avatar = origin_avatar.strip() or None
        caller_system = (request.headers.get(CALLER_SYSTEM_HEADER) or "").strip()
        caller_kind = (request.headers.get(CALLER_KIND_HEADER) or "").strip()
        caller_session_id = None
        caller_session_scope = None
        if caller_system == "tank-operator" and caller_kind == "session":
            caller_session_id = (
                request.headers.get(CALLER_SESSION_ID_HEADER) or ""
            ).strip() or None
            caller_session_scope = (
                request.headers.get(CALLER_SESSION_SCOPE_HEADER) or ""
            ).strip() or None
        token = SERVICE_BEARER.set(bearer)
        origin_token = ORIGIN_SESSION_ID.set(origin)
        origin_avatar_token = ORIGIN_SESSION_AVATAR_ID.set(origin_avatar)
        caller_session_token = CALLER_SESSION_ID.set(caller_session_id)
        caller_scope_token = CALLER_SESSION_SCOPE.set(caller_session_scope)
        try:
            return await call_next(request)
        finally:
            CALLER_SESSION_SCOPE.reset(caller_scope_token)
            CALLER_SESSION_ID.reset(caller_session_token)
            ORIGIN_SESSION_AVATAR_ID.reset(origin_avatar_token)
            ORIGIN_SESSION_ID.reset(origin_token)
            SERVICE_BEARER.reset(token)


def build_app() -> Starlette:
    mcp = FastMCP(
        "tank-operator-mcp",
        stateless_http=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    client = TankClient()
    register_tools(mcp, client)

    async def healthz(_: Request) -> Response:
        return Response("ok", media_type="text/plain")

    async def delete_session(_: Request) -> Response:
        # Return 200 so Claude Code's MCP client can reconnect cleanly
        # after a pod restart (405 is treated as fatal, not as "no session").
        return Response(status_code=200)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/", delete_session, methods=["DELETE"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[
            Middleware(CallerIdentityMiddleware),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    # Bind the pod network (not loopback): requests arrive directly on the
    # Service now that no kube-rbac-proxy sidecar fronts this process.
    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
