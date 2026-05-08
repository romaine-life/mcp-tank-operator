"""HTTP entrypoint — streamable-http transport.

Auth is handled by kube-rbac-proxy in front of this process: session pods
present their projected K8s SA token, the proxy validates it via TokenReview +
SubjectAccessReview against mcp.tank-operator.io/servers/tank-operator, and
only authorized requests reach this server. We bind loopback so direct
pod-IP:8080 access bypasses nothing — only the proxy can reach us.

Per-caller identity: a Starlette middleware reads the source session pod's IP
off X-Forwarded-For (kube-rbac-proxy appends it) and stashes it in a
ContextVar. Each tool call reads that ContextVar and passes it as
caller_pod_ip to the orchestrator's /api/internal/sessions/* endpoints. The
orchestrator resolves IP → owner email server-side.

Fail-open: if pod IP is missing (probe, unknown caller) the ContextVar is None
and tools surface a clean "could not identify caller" error rather than 500.
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

from .caller import CALLER_POD_IP, extract_source_pod_ip
from .client import TankClient
from .tools import register_tools

log = logging.getLogger(__name__)


class CallerPodIPMiddleware(BaseHTTPMiddleware):
    """Extract caller pod IP from X-Forwarded-For and bind to ContextVar."""

    async def dispatch(self, request: Request, call_next):
        forwarded_for = request.headers.get("x-forwarded-for")
        peer_ip = request.client.host if request.client else None
        pod_ip = extract_source_pod_ip(forwarded_for, peer_ip)
        token = CALLER_POD_IP.set(pod_ip)
        try:
            return await call_next(request)
        finally:
            CALLER_POD_IP.reset(token)


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
            Middleware(CallerPodIPMiddleware),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(build_app(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
