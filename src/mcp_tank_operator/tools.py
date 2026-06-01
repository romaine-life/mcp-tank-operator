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

_SPIRELENS_CAPABILITY = "spirelens_mcp"
_SPIRELENS_MCP_SERVER = "spire-lens-mcp"
_SPIRELENS_MCP_URL = "http://127.0.0.1:9997/mcp"
_SPIRELENS_REQUIRED_TOOLS = (
    "bridge_health",
    "get_host_status",
    "start_sts2",
    "stop_sts2",
    "restart_sts2",
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


def _require_supported_capability(capability: str) -> str:
    normalized = capability.strip().casefold()
    if normalized != _SPIRELENS_CAPABILITY:
        raise ValueError(
            f"unsupported capability {capability!r}; currently supported: {_SPIRELENS_CAPABILITY}"
        )
    return _SPIRELENS_CAPABILITY


def _target_session_id(client: TankClient, bearer: str, session_ref: str | None) -> str:
    ref = (session_ref or "").strip()
    if ref:
        session = _resolve_session_ref(client.list_sessions(bearer), ref)
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            raise ValueError(f"session {session_ref!r} resolved without an id")
        return session_id

    origin_session_id = (current_origin_session_id() or "").strip()
    if origin_session_id:
        return origin_session_id

    raise ValueError(
        "session_id is required because the calling pod did not provide "
        "X-Tank-Origin-Session-Id; pass the current session id or Tank display name"
    )


def _spirelens_static_context() -> dict[str, Any]:
    return {
        "capability": _SPIRELENS_CAPABILITY,
        "summary": (
            "Opt-in Tank session capability that joins the SpireLens tailnet, "
            "exposes the game-host MCP at 127.0.0.1:9997, and lets sessions "
            "mint short-lived SSH certificates directly from auth.romaine.life."
        ),
        "mcp": {
            "server_name": _SPIRELENS_MCP_SERVER,
            "local_url": _SPIRELENS_MCP_URL,
            "required_host_tools": list(_SPIRELENS_REQUIRED_TOOLS),
        },
        "tailnet": {
            "socket": "/tmp/tailscaled.sock",
            "session_tag": "tag:spirelens-orchestrator",
            "host_tag": "tag:spirelens-host",
            "host_name": "nelsonlaptop",
            "host_mcp_port": 15527,
            "bridge_port_on_host": 15526,
        },
        "ssh": {
            "tank_session_mode": "auth-romaine-direct",
            "glimmung_run_mode": "GLIMMUNG_SSH_CERT_URL callback URLs are only present in Glimmung run pods.",
            "token_path": "/var/run/secrets/auth.romaine.life/token",
            "cert_endpoint": "https://auth.romaine.life/api/auth/exchange/ssh-cert",
            "cert_principal": "spirelens-agent",
            "login_user": "nelsonlaptopuser",
            "proxy_command": "tailscale --socket=/tmp/tailscaled.sock nc %h %p",
            "notes": [
                "The SSH username is the Windows account, not the certificate principal.",
                "Raw SSH through tailscale nc proves network reachability but fails auth until the IdP-signed cert is supplied.",
            ],
        },
        "docs": [
            "/workspace/.tank/docs/spirelens-mcp-access.md",
            "/workspace/tank-operator/docs/tailnet-host-access.md",
            "/workspace/spirelens/docs/laptop-host-setup.md",
        ],
    }


def _tool_names(capabilities: dict[str, Any], server_name: str) -> list[str]:
    names = {
        str(tool.get("name") or "").strip()
        for tool in capabilities.get("mcp_tools", [])
        if isinstance(tool, dict) and tool.get("server") == server_name
    }
    return sorted(name for name in names if name)


def _server_entry(capabilities: dict[str, Any], server_name: str) -> dict[str, Any] | None:
    for server in capabilities.get("mcp_servers", []):
        if isinstance(server, dict) and server.get("name") == server_name:
            return server
    return None


def _server_errors(capabilities: dict[str, Any], server_name: str) -> list[str]:
    return [
        str(error.get("error") or "")
        for error in capabilities.get("mcp_tool_errors", [])
        if isinstance(error, dict) and error.get("server") == server_name
    ]


def _spirelens_session_context(capabilities: dict[str, Any]) -> dict[str, Any]:
    session = capabilities.get("session") if isinstance(capabilities.get("session"), dict) else {}
    selected = [
        str(item)
        for item in (session.get("capabilities") or [])
        if str(item).strip()
    ]
    server = _server_entry(capabilities, _SPIRELENS_MCP_SERVER)
    tools = _tool_names(capabilities, _SPIRELENS_MCP_SERVER)
    missing_tools = sorted(set(_SPIRELENS_REQUIRED_TOOLS) - set(tools))
    return {
        "inspected": True,
        "session_id": session.get("id"),
        "mode": session.get("mode"),
        "status": session.get("status"),
        "selected_capabilities": selected,
        "enabled": _SPIRELENS_CAPABILITY in selected,
        "mcp_server": {
            "present": server is not None,
            "target": server.get("target") if server else None,
            "expected_target": _SPIRELENS_MCP_URL,
            "target_matches_expected": (server or {}).get("target") == _SPIRELENS_MCP_URL,
        },
        "tools": {
            "present": tools,
            "required": list(_SPIRELENS_REQUIRED_TOOLS),
            "missing": missing_tools,
        },
        "tool_errors": _server_errors(capabilities, _SPIRELENS_MCP_SERVER),
    }


def _check_entry(name: str, ok: bool, evidence: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "evidence": evidence}


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
    def get_session_capability_context(
        capability: str = _SPIRELENS_CAPABILITY,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Return warm documentation for a Tank session capability.

        Use this when a user mentions a rare create-time capability and the
        access path is easy to forget. For `spirelens_mcp`, this explains the
        native MCP endpoint, the Tank-session SSH certificate path, how that
        differs from Glimmung run pods, and the session-local docs to read.

        If `session_id` is omitted, the tool inspects the calling session when
        the mcp-auth-proxy supplied `X-Tank-Origin-Session-Id`; otherwise it
        returns the static context with `session.inspected=false`.
        """
        _require_supported_capability(capability)
        context = _spirelens_static_context()
        origin_session_id = (current_origin_session_id() or "").strip()
        if not (session_id or origin_session_id):
            context["session"] = {
                "inspected": False,
                "reason": "No session_id argument and no X-Tank-Origin-Session-Id header.",
            }
            return context

        bearer = _service_bearer()
        target_id = (
            _target_session_id(client, bearer, session_id)
            if session_id
            else origin_session_id
        )
        capabilities = client.get_session_capabilities(bearer, target_id)
        context["session"] = _spirelens_session_context(capabilities)
        return context

    @mcp.tool()
    def verify_spirelens_session_access(session_id: str | None = None) -> dict[str, Any]:
        """Inspect whether a session has the SpireLens MCP capability wired.

        This is a safe read-only verifier. It asks Tank to inspect the target
        session pod's `/workspace/.mcp.json` and visible MCP tool inventory,
        then reports whether the `spirelens_mcp` capability, local
        `spire-lens-mcp` server entry, and expected host lifecycle tools are
        present. It does not return credentials or SSH into the laptop.

        Omit `session_id` to verify the calling session. Pass a Tank display
        name or id to inspect a caller-owned sibling session.
        """
        bearer = _service_bearer()
        target_id = _target_session_id(client, bearer, session_id)
        capabilities = client.get_session_capabilities(bearer, target_id)
        session_context = _spirelens_session_context(capabilities)
        checks = [
            _check_entry(
                "session_has_spirelens_mcp_capability",
                bool(session_context["enabled"]),
                session_context["selected_capabilities"],
            ),
            _check_entry(
                "workspace_mcp_config_has_spire_lens_mcp",
                bool(session_context["mcp_server"]["present"]),
                session_context["mcp_server"],
            ),
            _check_entry(
                "spire_lens_mcp_uses_expected_local_proxy",
                bool(session_context["mcp_server"]["target_matches_expected"]),
                session_context["mcp_server"],
            ),
            _check_entry(
                "required_host_lifecycle_tools_are_visible",
                len(session_context["tools"]["missing"]) == 0,
                session_context["tools"],
            ),
        ]

        if not session_context["enabled"]:
            status = "not_enabled"
        elif all(check["ok"] for check in checks):
            status = "ok"
        else:
            status = "degraded"

        next_actions: list[str] = []
        if not session_context["enabled"]:
            next_actions.append('Create or switch to a session with capabilities: ["spirelens_mcp"].')
        if not session_context["mcp_server"]["present"]:
            next_actions.append("Check that the session mounted mcp.spirelens.json over /workspace/.mcp.json.")
        if session_context["mcp_server"]["present"] and session_context["tools"]["missing"]:
            next_actions.append(
                "Call get_session_capability_context('spirelens_mcp') for the SSH/admin repair path, "
                "then refresh the host MCP checkout or scheduled task if the host tools are stale."
            )
        if session_context["tool_errors"]:
            next_actions.append("Review mcp_tool_errors from Tank's session capability probe.")

        return {
            "capability": _SPIRELENS_CAPABILITY,
            "session_id": target_id,
            "status": status,
            "checks": checks,
            "session": session_context,
            "next_actions": next_actions,
            "docs": _spirelens_static_context()["docs"],
        }

    @mcp.tool()
    def read_transcript(
        session_id: str,
        anchor: str | None = None,
        rows: int | None = None,
        before_cursor: str | None = None,
        timeline_id: str | None = None,
    ) -> dict[str, Any]:
        """Read another session's conversation transcript (the caller's own).

        Use this to inspect what a sibling session has been doing — for
        example, to triage a session that appears stuck before you decide
        whether to send_prompt it, delete it, or escalate. This reads the
        durable transcript projection from Postgres, so it works even after
        the target session's pod is gone (pod logs do not survive that).

        The caller may only read sessions it owns; an unknown or other-user
        session id returns an error (404 masked as "not found").

        Returns the projected transcript-row read model:
          - `rows`: projected transcript rows (messages, meta/status rows,
            compacted Turn-activity shells) for this page.
          - `next_cursor` / `prev_cursor`: paginate forward (newer) / backward
            (older) by passing `prev_cursor` back as `before_cursor`.
          - `found_oldest` / `found_newest`: whether this page reached an end.
          - `live_order_key`: the durable tail's order_key — compare with the
            last row to tell whether the session is still producing events.

        Args:
          - `session_id`: the target session id (use list_sessions /
            resolve_session to find it).
          - `anchor`: "newest" (default — the tail, best for "what is it doing
            now") or "oldest" (the start of the conversation).
          - `rows`: page size; the server clamps to its max.
          - `before_cursor`: a `prev_cursor` from an earlier call, to page
            backward through history.
          - `timeline_id`: center the page on a specific transcript row.

        `anchor`, `before_cursor`, and `timeline_id` are mutually exclusive.
        """
        return client.read_transcript(
            _service_bearer(),
            session_id=session_id,
            anchor=anchor,
            rows=rows,
            before_cursor=before_cursor,
            timeline_id=timeline_id,
        )

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
