"""MCP tool definitions for mcp-tank-operator.

All tools authenticate via the calling pod's auth.romaine.life
service-principal JWT, extracted from the inbound X-Auth-Romaine-Token
header into the SERVICE_BEARER ContextVar by the HTTP middleware.
If absent (stdio mode, healthz probe, older mcp-auth-proxy sidecar
without the injection support), tools raise a clean error rather than
silently acting as the server's own SA.

See nelsong6/tank-operator#486 for the rollout that retired the prior
IP-tail identity path.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .caller import current_origin_session_id, current_service_bearer
from .client import TankClient

_SERVICE_BEARER_MISSING_MSG = (
    "service-principal authentication required — this tool needs the calling "
    "session pod's mcp-auth-proxy sidecar to forward an auth.romaine.life "
    "service JWT in the X-Auth-Romaine-Token header. See "
    "https://github.com/nelsong6/tank-operator/issues/486."
)


def _service_bearer() -> str:
    jwt = current_service_bearer()
    if not jwt:
        raise ValueError(_SERVICE_BEARER_MISSING_MSG)
    return jwt


def _default_session_name(session: dict[str, Any]) -> str:
    raw = str(session.get("pod_name") or session.get("id") or "")
    return raw.removeprefix("session-")[:8]


def _session_display_name(session: dict[str, Any]) -> str:
    name = session.get("name")
    if isinstance(name, str) and name:
        return name
    return _default_session_name(session)


def _resolve_session_ref(sessions: list[dict[str, Any]], session_ref: str) -> dict[str, Any]:
    ref = session_ref.strip()
    if not ref:
        raise ValueError("session_ref must not be blank")

    for session in sessions:
        if session.get("id") == ref:
            return session

    exact = [session for session in sessions if _session_display_name(session) == ref]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        choices = ", ".join(str(session.get("id")) for session in exact)
        raise ValueError(f"session name {ref!r} is ambiguous; matching ids: {choices}")

    folded = ref.casefold()
    insensitive = [
        session
        for session in sessions
        if _session_display_name(session).casefold() == folded
    ]
    if len(insensitive) == 1:
        return insensitive[0]
    if len(insensitive) > 1:
        choices = ", ".join(str(session.get("id")) for session in insensitive)
        raise ValueError(f"session name {ref!r} is ambiguous; matching ids: {choices}")

    raise ValueError(f"session {session_ref!r} not found or not owned by caller")


def _session_ref_summary(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session.get("id"),
        "name": session.get("name"),
        "display_name": _session_display_name(session),
        "pod_name": session.get("pod_name"),
        "mode": session.get("mode"),
        "status": session.get("status"),
        "url": session.get("url"),
    }


def register_tools(mcp: FastMCP, client: TankClient) -> None:
    @mcp.tool()
    def list_sessions() -> list[dict[str, Any]]:
        """List tank-operator sessions owned by the calling session pod's owner.

        Returns id, pod_name, mode, status, name, requested_at, created_at,
        ready_at, and url for each session. Use to discover sibling sessions
        before sending them a prompt with send_prompt, or to check whether a
        session you spawned has started.
        """
        return client.list_sessions(_service_bearer())

    @mcp.tool()
    def list_session_refs() -> list[dict[str, Any]]:
        """List the session ids and Tank UI names owned by the caller.

        Low-noise discovery for when the user refers to a session by its
        Tank-sidebar name. `display_name` is the effective label: the
        friendly `name` when set, otherwise the default short id derived
        from the pod/session id. Pass either `id` or `display_name` to
        resolve_session.
        """
        return [_session_ref_summary(session) for session in client.list_sessions(_service_bearer())]

    @mcp.tool()
    def resolve_session(session_ref: str) -> dict[str, Any]:
        """Resolve a tank UI session name or session id to its session record.

        Use this when the user gives the friendly name shown in the Tank UI
        (for example "tank test") and you need the underlying session id or
        pod name. Matching is exact first, then case-insensitive. If more
        than one caller-owned session has the same display name, raises an
        ambiguity error listing the matching ids.
        """
        return _resolve_session_ref(client.list_sessions(_service_bearer()), session_ref)

    @mcp.tool()
    def create_session(mode: str = "claude_gui") -> dict[str, Any]:
        """Create a new tank-operator session pod owned by the calling user.

        `mode` must be one of the supported Tank session modes. Current chat
        modes are claude_gui (default) and codex_gui. Returns the new session's
        id, pod_name, status, mode, and url.

        Use create_session + send_prompt to hand work to a fresh SDK session
        after the pod is ready. For a combined create-and-queue flow, use
        spawn_run_session instead.
        """
        return client.create_session(_service_bearer(), mode=mode)

    @mcp.tool()
    def delete_session(session_id: str) -> dict[str, Any]:
        """Delete a tank-operator session pod owned by the calling user.

        The calling user must own the session — attempting to delete another
        user's session raises 403. Returns {"id": ..., "status": "deleted"}.
        """
        return client.delete_session(_service_bearer(), session_id=session_id)

    @mcp.tool()
    def set_session_name(session_id: str, name: str | None) -> dict[str, Any]:
        """Set or clear the friendly display name on a session.

        `name` is stored as a Pod annotation and visible in the tank UI.
        Pass None or empty string to clear an existing name. Returns the
        updated session record.
        """
        return client.set_session_name(_service_bearer(), session_id=session_id, name=name)

    @mcp.tool()
    def set_test_environment(
        session_id: str,
        slot_index: int | None = None,
        url: str | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        """Update Tank's GUI test pill for a caller-owned session.

        Call this after reserving a Glimmung test slot so the Tank UI can show
        the beaker pill as active, display the slot number, and link to the
        test environment. Pass active=False to clear the state.
        """
        return client.set_test_environment(
            _service_bearer(),
            session_id=session_id,
            active=active,
            slot_index=slot_index,
            url=url,
        )

    @mcp.tool()
    def get_session_url(session_id: str) -> dict[str, str]:
        """Return the tank UI URL for a session.

        Opens the session in the tank web interface. `session_id` may be either
        the real session id or the friendly name shown in the Tank UI. The
        session must be owned by the calling user.
        """
        session = _resolve_session_ref(client.list_sessions(_service_bearer()), session_id)
        return {"session_id": str(session.get("id", "")), "url": session.get("url", "")}

    @mcp.tool()
    def send_prompt(
        session_id: str,
        prompt: str,
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send a follow-up prompt to an existing SDK chat session.

        The target session must be in a chat-capable mode such as claude_gui or
        codex_gui. The call is fire-and-forget: returns 202 once the turn has
        been queued for the pod-side SDK runner.

        `model` and `permission_mode` are forwarded to the SDK turn queue;
        pre-validated server-side to [A-Za-z0-9._-]{1,64}.

        For a completely fresh session, use spawn_run_session instead.
        """
        return client.send_message(
            _service_bearer(),
            session_id=session_id,
            prompt=prompt,
            model=model,
            permission_mode=permission_mode,
            # Originating session id stamped by the calling pod's
            # mcp-auth-proxy sidecar. Tank-operator persists it on the
            # user_message.created event so the SPA renders the parent
            # session's avatar on the user bubble in the target session
            # — the handoff reads as agent-authored rather than as the
            # human owner typing it themselves.
            origin_session_id=current_origin_session_id(),
        )

    @mcp.tool()
    def spawn_run_session(
        prompt: str,
        mode: str = "claude_gui",
        name: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a fresh SDK chat session and queue the first prompt to it.

        The new pod is owned by the same user as the calling session
        (resolved server-side from the service JWT's actor_email claim).
        Returns the new session record plus the queued turn response.

        - `prompt`: instructions for the agent (required, non-empty).
        - `mode`: claude_gui (default) or codex_gui.
        - `name`: optional friendly label shown in the tank UI.
        - `model`, `permission_mode`: forwarded to the SDK turn queue
          (validated server-side to [A-Za-z0-9._-]{1,64}).

        Waits for the new session pod to become ready, then queues the
        first turn. Open the returned session URL in Tank to watch progress.
        """
        return client.spawn_run(
            _service_bearer(),
            prompt=prompt,
            mode=mode,
            name=name,
            model=model,
            permission_mode=permission_mode,
            # See send_prompt — same flow, only the first turn in the
            # freshly spawned session needs the parent-session avatar.
            origin_session_id=current_origin_session_id(),
        )
