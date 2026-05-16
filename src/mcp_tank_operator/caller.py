"""Per-request caller identity extraction.

Identity is the auth.romaine.life service-principal JWT forwarded by
the calling session pod's mcp-auth-proxy sidecar in the
X-Auth-Romaine-Token header. mcp-auth-proxy exchanges the pod's
projected ``audience=https://auth.romaine.life`` SA token at
``auth.romaine.life/api/auth/exchange/k8s`` and forwards the resulting
``role=service`` JWT; this server extracts it into a ContextVar and
tool handlers thread it through to TankClient.

The pre-#486 IP-tail identity path (X-Forwarded-For → caller_pod_ip
query param → orchestrator-side FindPodByIP) was retired in Stage 4 —
the orchestrator no longer accepts that shape on ``/api/internal/
sessions/*``. See nelsong6/tank-operator#486.
"""
from __future__ import annotations

from contextvars import ContextVar

# Inbound service-principal JWT. None when absent — tools surface a
# clean "service-principal authentication required" error rather than
# silently falling through.
SERVICE_BEARER: ContextVar[str | None] = ContextVar(
    "mcp_tank_operator_service_bearer", default=None,
)

# Header name shared with the upstream mcp-auth-proxy sidecar
# (in tank-operator's claude-container). Changing it requires a
# cross-repo coordinated deploy.
SERVICE_BEARER_HEADER = "x-auth-romaine-token"


def current_service_bearer() -> str | None:
    return SERVICE_BEARER.get()
