"""Per-request caller pod IP extraction.

kube-rbac-proxy fronts our Python upstream on loopback. The proxy is a Go
reverse proxy that appends the immediate peer IP to X-Forwarded-For before
forwarding. The *last* entry in that chain is the session pod's IP — the IP
that reached kube-rbac-proxy from outside the mcp-tank-operator pod. We stash
that IP in a ContextVar so tool handlers can pass it to the orchestrator's
/api/internal/sessions/* endpoints without threading a request object through.

Unlike mcp-github, we don't resolve the IP to an email here — the orchestrator
does that atomically in each internal endpoint (caller_pod_ip → owner email via
find_pod_by_ip + owner-email annotation). This keeps the MCP server stateless.

Fail-open posture: if pod IP is absent (healthz probes, local testing) the
ContextVar stays None. Tools surface a clean error: "could not identify caller
from pod IP — make sure you're calling from inside a tank-operator session pod".
"""
from __future__ import annotations

from contextvars import ContextVar

CALLER_POD_IP: ContextVar[str | None] = ContextVar("mcp_tank_operator_caller_pod_ip", default=None)


def current_caller_pod_ip() -> str | None:
    return CALLER_POD_IP.get()


def extract_source_pod_ip(forwarded_for: str | None, peer_ip: str | None) -> str | None:
    """Pick the session pod's IP off the X-Forwarded-For chain.

    kube-rbac-proxy fronts our Python upstream on loopback; it appends the
    immediate peer to X-Forwarded-For before forwarding. The *last* hop is
    the IP that reached the proxy from outside the pod — i.e. the session
    pod's cluster IP.
    """
    if forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer_ip
