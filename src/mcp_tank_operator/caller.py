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

# Originating tank-operator session id for handoff calls
# (send_prompt / spawn_run_session). The calling pod's mcp-auth-proxy
# sidecar stamps this from its SESSION_ID env var on the way out; we
# forward it to the tank-operator orchestrator so the persisted
# user_message.created event carries it and the frontend can render the
# parent session's avatar on the user bubble. Absent for non-handoff
# tools and for clients that don't set it — the orchestrator treats
# missing/empty as "human-typed" and falls back to the human Gravatar.
ORIGIN_SESSION_ID: ContextVar[str | None] = ContextVar(
    "mcp_tank_operator_origin_session_id", default=None,
)

# Header name shared with the upstream mcp-auth-proxy sidecar
# (in tank-operator's claude-container). Changing it requires a
# cross-repo coordinated deploy.
SERVICE_BEARER_HEADER = "x-auth-romaine-token"

# Header carrying the originating tank-operator session id on handoff
# calls. Shared with mcp-auth-proxy (which stamps it) and with
# tank-operator's handlers_internal.go (which reads it). Cross-repo
# coordinated deploy applies — the orchestrator silently ignores
# unknown header values, so the worst case during rollout is the
# avatar falls back to the human Gravatar.
ORIGIN_SESSION_HEADER = "x-tank-origin-session-id"


def current_service_bearer() -> str | None:
    return SERVICE_BEARER.get()


def current_origin_session_id() -> str | None:
    return ORIGIN_SESSION_ID.get()
