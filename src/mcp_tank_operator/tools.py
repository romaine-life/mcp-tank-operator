"""MCP tool definitions for mcp-tank-operator.

All tools authenticate via the calling pod's auth.romaine.life
service-principal JWT, extracted from the inbound X-Auth-Romaine-Token
header into the SERVICE_BEARER ContextVar by the HTTP middleware.
If absent (stdio mode, healthz probe, older mcp-auth-proxy sidecar
without the injection support), tools raise a clean error rather than
silently acting as the server's own SA.

See romaine-life/tank-operator#486 for the rollout that retired the prior
IP-tail identity path.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .caller import (
    current_caller_session_id,
    current_origin_session_avatar_id,
    current_origin_session_id,
    current_service_bearer,
)
from .client import TankClient

_SERVICE_BEARER_MISSING_MSG = (
    "service-principal authentication required — this tool needs the calling "
    "session pod's mcp-auth-proxy sidecar to forward an auth.romaine.life "
    "service JWT in the X-Auth-Romaine-Token header. See "
    "https://github.com/romaine-life/tank-operator/issues/486."
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


def _client_for_slot(client: TankClient, slot_name: str | None) -> TankClient:
    """Route to a test slot's own orchestrator when slot_name is set, else prod.

    ``spawn_test_slot_session`` creates sessions in a slot orchestrator's own
    registry, so a session-by-id tool (list/read/send/delete/url) must target
    that slot — otherwise the id resolves against production, where the same id
    is a different or missing session. Passing the slot name routes the call to
    that slot's registry; omitting it keeps the production behavior.
    """
    name = (slot_name or "").strip()
    return client.for_slot(name) if name else client


def _origin_session_context(client: TankClient, service_bearer: str) -> tuple[str | None, str | None]:
    origin_session_id = current_origin_session_id()
    origin_avatar_id = current_origin_session_avatar_id()
    if origin_avatar_id or not origin_session_id:
        return origin_session_id, origin_avatar_id
    try:
        sessions = client.list_sessions(service_bearer)
    except Exception:
        return origin_session_id, None
    for session in sessions:
        if str(session.get("id") or "").strip() == origin_session_id:
            avatar_id = str(session.get("agent_avatar_id") or "").strip()
            return origin_session_id, avatar_id or None
    return origin_session_id, None


def _session_display_name(session: dict[str, Any]) -> str:
    # The orchestrator ships a non-null `name` on every session record — the
    # single canonical title (the user's name, else a server-assigned id slug).
    # Read it directly; the MCP read model never re-derives a label. (The
    # transitional `display_name` alias was removed once `name` became non-null.)
    return str(session.get("name") or "")


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

    caller_session_id = (current_caller_session_id() or "").strip()
    if caller_session_id:
        return caller_session_id

    raise ValueError(
        "session_id is required because the calling pod did not provide "
        "trusted current-session identity; pass a Tank display name or id"
    )


def _current_session_id() -> str:
    session_id = (current_caller_session_id() or "").strip()
    if not session_id:
        raise ValueError(
            "current session identity is required; mcp-auth-proxy must forward "
            "caller context or an auth.romaine.life service token with sub=svc:tank:<id>"
        )
    return session_id


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
    def list_sessions(slot_name: str | None = None) -> list[dict[str, Any]]:
        """List tank-operator sessions owned by the calling session pod's owner.

        Returns id, pod_name, mode, status, name, requested_at, created_at,
        ready_at, and url for each session. Use to discover sibling sessions
        before sending them a prompt with send_prompt, or to check whether a
        session you spawned has started.

        Pass `slot_name` (e.g. "tank-operator-slot-2") to list sessions in that
        test slot's own orchestrator — the ones created by
        spawn_test_slot_session, which do NOT appear in the production list.
        Omit it for production sessions.
        """
        return _client_for_slot(client, slot_name).list_sessions(_service_bearer())

    @mcp.tool()
    def get_session_run_options() -> dict[str, Any]:
        """Return Tank's current session create/run options.

        Use this before choosing a non-default `mode`, `model`, or `effort`
        for create_session/spawn_run_session. The data comes from Tank's
        backend validation contract, not from this MCP server. Tank still
        validates the eventual create/turn request and returns an actionable
        error if a value is not accepted.

        Returns create modes, SDK chat modes with providers, retired create
        modes, provider model lists, provider effort lists, and defaults.
        """
        return client.get_session_run_options(_service_bearer())

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
        slot_name: str | None = None,
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
          - `slot_name`: if the target session was created by
            spawn_test_slot_session, the slot name (e.g. "tank-operator-slot-2")
            so the read targets that slot's own registry instead of production.

        `anchor`, `before_cursor`, and `timeline_id` are mutually exclusive.
        """
        return _client_for_slot(client, slot_name).read_transcript(
            _service_bearer(),
            session_id=session_id,
            anchor=anchor,
            rows=rows,
            before_cursor=before_cursor,
            timeline_id=timeline_id,
        )

    @mcp.tool()
    def delete_session(session_id: str, slot_name: str | None = None) -> dict[str, Any]:
        """Delete a tank-operator session pod owned by the calling user.

        The calling user must own the session — attempting to delete another
        user's session raises 403. Returns {"id": ..., "status": "deleted"}.

        Pass `slot_name` to delete a session created in that test slot's own
        orchestrator (via spawn_test_slot_session); omit it for production
        sessions.
        """
        return _client_for_slot(client, slot_name).delete_session(
            _service_bearer(), session_id=session_id
        )

    @mcp.tool()
    def set_session_name(session_id: str, name: str | None) -> dict[str, Any]:
        """Set or clear the friendly display name on a session.

        `name` is stored as a Pod annotation and visible in the tank UI.
        Pass None or empty string to clear an existing name. Returns the
        updated session record.
        """
        return client.set_session_name(_service_bearer(), session_id=session_id, name=name)

    @mcp.tool()
    def set_pull_request_link(url: str | None) -> dict[str, Any]:
        """Update the calling session's GUI pull request link.

        Call this after opening the draft PR for a test workflow. The Tank UI
        shows a pull-request icon linking to the PR without changing the
        already-posted test environment link. Pass None or empty string to clear
        the PR link.
        """
        return client.set_pull_request_link(
            _service_bearer(),
            session_id=_current_session_id(),
            url=url,
        )

    @mcp.tool()
    def get_session_url(session_id: str, slot_name: str | None = None) -> dict[str, str]:
        """Return the tank UI URL for a session.

        Opens the session in the tank web interface. `session_id` may be either
        the real session id or the friendly name shown in the Tank UI. The
        session must be owned by the calling user.

        Pass `slot_name` to resolve a session created in that test slot's own
        orchestrator (via spawn_test_slot_session); omit it for production.
        """
        target = _client_for_slot(client, slot_name)
        session = _resolve_session_ref(target.list_sessions(_service_bearer()), session_id)
        return {"session_id": str(session.get("id", "")), "url": session.get("url", "")}

    @mcp.tool()
    def send_prompt(
        session_id: str,
        prompt: str,
        model: str | None = None,
        permission_mode: str | None = None,
        slot_name: str | None = None,
    ) -> dict[str, Any]:
        """Send a follow-up prompt to an existing SDK chat session.

        The target session must be in a chat-capable mode such as claude_gui or
        codex_gui. The call is fire-and-forget: returns 202 once the turn has
        been queued for the pod-side SDK runner.

        `model` and `permission_mode` are forwarded to the SDK turn queue.
        Tank validates `model` server-side and returns an actionable error when
        it is not accepted; omit it unless the user explicitly asked for a
        model override.

        Pass `slot_name` (e.g. "tank-operator-slot-2") to prompt a session
        created in that test slot's own orchestrator via spawn_test_slot_session;
        omit it for production sessions.

        For a completely fresh session, use spawn_run_session instead.
        """
        service_bearer = _service_bearer()
        origin_session_id, origin_session_avatar_id = _origin_session_context(client, service_bearer)
        return _client_for_slot(client, slot_name).send_message(
            service_bearer,
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
            origin_session_id=origin_session_id,
            origin_session_avatar_id=origin_session_avatar_id,
        )

    @mcp.tool()
    def spawn_run_session(
        prompt: str,
        mode: str = "claude_gui",
        name: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        repos: list[str] | None = None,
        capabilities: list[str] | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a fresh SDK chat session and queue the first prompt to it.

        The new pod is owned by the same user as the calling session
        (resolved server-side from the service JWT's actor_email claim).
        Returns the new session record plus the queued turn response.

        - `prompt`: instructions for the agent (required, non-empty).
        - `mode`: claude_gui (default) or codex_gui.
        - `name`: optional friendly label shown in the tank UI.
        - `model`/`effort`: optional session run config, forwarded to BOTH
          session-create and the first turn. Tank validates these server-side
          and returns an actionable error when a value is not accepted. Omit
          them unless the user explicitly asked for a model/effort.
        - `repos`: up to 5 "owner/name" GitHub slugs cloned into /workspace
          before the agent starts, so the spawned session boots with its repos
          already present.
        - `capabilities`: optional create-time session capabilities such as
          ["restricted_git"] (requires a repo-capable mode). Tank validates
          them server-side. Use this to spawn a session that exercises the
          governed Git flow.
        - `permission_mode`: forwarded to the SDK turn queue.

        Waits for the new session pod to become ready, then queues the
        first turn. Open the returned session URL in Tank to watch progress.
        """
        service_bearer = _service_bearer()
        origin_session_id, origin_session_avatar_id = _origin_session_context(client, service_bearer)
        return client.spawn_run(
            service_bearer,
            prompt=prompt,
            mode=mode,
            name=name,
            model=model,
            effort=effort,
            repos=repos,
            capabilities=capabilities,
            permission_mode=permission_mode,
            # See send_prompt — same flow, only the first turn in the
            # freshly spawned session needs the parent-session avatar.
            origin_session_id=origin_session_id,
            origin_session_avatar_id=origin_session_avatar_id,
        )

    @mcp.tool()
    def spawn_test_slot_session(
        slot_name: str,
        prompt: str,
        mode: str | None = None,
        name: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        repos: list[str] | None = None,
        capabilities: list[str] | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a fresh SDK chat session inside a Glimmung test slot.

        Use this during Tank test-slot validation when the new session must be
        created by the slot's own orchestrator, not production/default Tank.
        `slot_name` is the Glimmung slot namespace, for example
        "tank-operator-slot-2"; production-ish targets such as "default" are
        refused by the client before any HTTP request is made.

        - `prompt`: instructions for the agent (required, non-empty).
        - `mode`: optional explicit mode. When omitted, the slot uses Tank's
          admin-configured test-slot default from `get_session_run_options()`.
        - `name`: optional friendly label shown in the slot UI.
        - `model`/`effort`: optional session run config, forwarded to BOTH
          session-create and the first turn. Tank validates these server-side
          and returns an actionable error when a value is not accepted. Omit
          them unless the user explicitly asked for a model/effort.
        - `repos`: up to 5 "owner/name" GitHub slugs cloned into /workspace
          before the agent starts.
        - `capabilities`: optional create-time session capabilities such as
          ["restricted_git"] (requires a repo-capable mode). This is the
          supported way to validate a restricted-git feature on a slot: the
          slot orchestrator opts the new pod into the governed Git flow so its
          mcp-auth-proxy sidecar exposes the Tank governed tools.
        - `permission_mode`: forwarded to the SDK turn queue.

        Returns the slot-created session record plus the queued turn response.
        Open the returned slot URL to validate the run in the test environment.
        """
        service_bearer = _service_bearer()
        origin_session_id, origin_session_avatar_id = _origin_session_context(client, service_bearer)
        return client.spawn_test_slot_session(
            service_bearer,
            slot_name=slot_name,
            prompt=prompt,
            mode=mode,
            name=name,
            model=model,
            effort=effort,
            repos=repos,
            capabilities=capabilities,
            permission_mode=permission_mode,
            # Same cross-session handoff stamp as spawn_run_session, but scoped
            # to the test slot's orchestrator.
            origin_session_id=origin_session_id,
            origin_session_avatar_id=origin_session_avatar_id,
        )

    @mcp.tool()
    def point_slot_session_image(
        slot: str,
        codex_image: str | None = None,
        claude_image: str | None = None,
        antigravity_image: str | None = None,
        git_ref: str | None = None,
    ) -> dict[str, Any]:
        """Point a Glimmung test slot's session image at a branch-built image so
        NEWLY-created sessions in that slot boot it.

        This is the "make new sessions inherit my branch" repoint. The slot's
        orchestrator stamps the override onto every session pod it creates from
        now on — the same image lever production uses, no runtime overlay. It
        only governs pods created after the repoint; a session pod already
        running keeps the image it booted (there is no in-place patch of a live
        session pod). Glimmung's `deploy_image_to_test_slot` is the separate
        lever that deploys a branch's CI-built image to the slot's own
        app/orchestrator surface.

        Covers all three session-runner providers (codex / claude / antigravity).
        Because a session pod's image is fixed at creation time, this repoint
        plus a fresh slot session is the supported way to validate a
        session-container branch — including an antigravity-container branch —
        on a slot.

        Prerequisites:
          - The image must already exist in ACR. This tool does NOT build images:
            dispatch the `session-images-build.yml` workflow at your branch first
            (it pushes a content-fingerprint tag), then pass that tag here.
          - `slot` is the Glimmung slot name from `checkout_test_slot`, e.g.
            "tank-operator-slot-2". (That name is also the namespace and the
            session scope, so one value targets everything.)

        Args:
          - `slot`: the test-slot name.
          - `codex_image`: full image ref for codex sessions, e.g.
            "romainecr.azurecr.io/codex-container:codex-<fingerprint>".
          - `claude_image`: full image ref for claude sessions, e.g.
            "romainecr.azurecr.io/claude-container:claude-<fingerprint>".
          - `antigravity_image`: full image ref for antigravity sessions, e.g.
            "romainecr.azurecr.io/antigravity-container:antigravity-<fingerprint>".
          - `git_ref`: optional provenance label stored with the override.

        At least one of `codex_image` / `claude_image` / `antigravity_image` is
        required; you may set several at once to repoint multiple providers in
        one call. The production scope is refused server-side and only test-env
        orchestrators honor the override, so this cannot repoint production
        sessions. Use `get_slot_session_image` to see what new sessions will
        inherit and `clear_slot_session_image` to revert to the chart-pinned
        image.
        """
        has_codex = bool(codex_image and codex_image.strip())
        has_claude = bool(claude_image and claude_image.strip())
        has_antigravity = bool(antigravity_image and antigravity_image.strip())
        if not (has_codex or has_claude or has_antigravity):
            raise ValueError(
                "at least one of codex_image / claude_image / antigravity_image "
                "is required"
            )
        return client.set_session_image_override(
            _service_bearer(),
            slot=slot,
            codex_image=codex_image,
            claude_image=claude_image,
            antigravity_image=antigravity_image,
            git_ref=git_ref,
        )

    @mcp.tool()
    def get_slot_session_image(slot: str) -> dict[str, Any]:
        """Report a test slot's current session-image override — the authoritative
        answer to "what image will NEW sessions in this slot boot?".

        Returns the stored override (`session_scope`, `claude_image`,
        `codex_image`, `antigravity_image`, `git_ref`, `set_by`, `set_at`) or
        `{"override_set": false}` when none is set (new sessions then boot the
        slot's chart-pinned image). Read-only.
        """
        return client.get_session_image_override(_service_bearer(), slot=slot)

    @mcp.tool()
    def clear_slot_session_image(slot: str) -> dict[str, Any]:
        """Clear a test slot's session-image override so new sessions revert to the
        chart-pinned image. Call this when you finish validating a branch on the
        slot (or before pointing it somewhere else).
        """
        return client.clear_session_image_override(_service_bearer(), slot=slot)
