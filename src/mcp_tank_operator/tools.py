"""MCP tool definitions for mcp-tank-operator.

All tools resolve the caller's identity from the pod IP ContextVar set by the
HTTP middleware. If the ContextVar is unset (stdio mode, healthz probe, missing
X-Forwarded-For) tools raise an actionable error rather than silently acting as
the server's own SA. This satisfies the spec: caller identity is always the
network-layer source-IP chain, never a caller-supplied email.
"""
from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from .caller import current_caller_pod_ip
from .client import TankClient

_CALLER_MISSING_MSG = (
    "could not identify caller from pod IP — make sure you're calling from "
    "inside a tank-operator session pod"
)


def _pod_ip() -> str:
    ip = current_caller_pod_ip()
    if not ip:
        raise ValueError(_CALLER_MISSING_MSG)
    return ip


def register_tools(mcp: FastMCP, client: TankClient) -> None:
    @mcp.tool()
    def list_sessions() -> list[dict[str, Any]]:
        """List tank-operator sessions owned by the calling session pod's owner.

        Returns id, pod_name, mode, status, name, requested_at, created_at,
        ready_at, and url for each session. Use to discover sibling sessions
        before sending them a prompt with send_prompt, or to check whether a
        session you spawned has started.
        """
        return client.list_sessions(_pod_ip())

    @mcp.tool()
    def create_session(mode: str = "subscription") -> dict[str, Any]:
        """Create a new tank-operator session pod owned by the calling user.

        `mode` must be one of the supported session modes (subscription,
        subscription_headless, codex_headless, etc.). Returns the new session's
        id, pod_name, status, mode, and url.

        Use create_session + send_prompt to hand work to a fresh agent with a
        specific prompt after the pod is ready. For a combined spawn-and-run in
        one call, use spawn_run_session instead.
        """
        return client.create_session(_pod_ip(), mode=mode)

    @mcp.tool()
    def delete_session(session_id: str) -> dict[str, Any]:
        """Delete a tank-operator session pod owned by the calling user.

        The calling user must own the session — attempting to delete another
        user's session raises 403. Returns {"id": ..., "status": "deleted"}.
        """
        return client.delete_session(_pod_ip(), session_id=session_id)

    @mcp.tool()
    def set_session_name(session_id: str, name: str | None) -> dict[str, Any]:
        """Set or clear the friendly display name on a session.

        `name` is stored as a Pod annotation and visible in the tank UI.
        Pass None or empty string to clear an existing name. Returns the
        updated session record.
        """
        return client.set_session_name(_pod_ip(), session_id=session_id, name=name)

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
            _pod_ip(),
            session_id=session_id,
            active=active,
            slot_index=slot_index,
            url=url,
        )

    @mcp.tool()
    def get_session_url(session_id: str) -> dict[str, str]:
        """Return the tank UI URL for a session.

        Opens the session in the tank web interface. The session must be owned
        by the calling user.
        """
        sessions = client.list_sessions(_pod_ip())
        for s in sessions:
            if s.get("id") == session_id:
                return {"session_id": session_id, "url": s.get("url", "")}
        raise ValueError(f"session {session_id} not found or not owned by caller")

    @mcp.tool()
    def send_prompt(
        session_id: str,
        prompt: str,
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send a follow-up prompt to an existing headless session.

        The target session must be in a headless mode (subscription_headless
        or codex_headless). The call is fire-and-forget: returns 202 once the
        run has been queued on the pod. The agent in the receiving session picks
        up with its prior conversation transcript (--continue semantics).

        `model` and `permission_mode` are forwarded to headless-run.sh;
        pre-validated server-side to [A-Za-z0-9._-]{1,64}.

        For a completely fresh agent with no prior context, spawn a new session
        with spawn_run_session instead.
        """
        return client.send_message(
            _pod_ip(),
            session_id=session_id,
            prompt=prompt,
            model=model,
            permission_mode=permission_mode,
        )

    @mcp.tool()
    def spawn_run_session(
        prompt: str,
        mode: str = "subscription_headless",
        name: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a fresh headless session and dispatch the first prompt to it.

        The new pod is owned by the same user as the calling session. Returns
        the new session record and a url the user can open in the tank UI to
        watch the run.

        - `prompt`: instructions for the agent (required, non-empty).
        - `mode`: subscription_headless (Claude, default) or codex_headless.
        - `name`: optional friendly label shown in the tank UI.
        - `model`, `permission_mode`: forwarded to headless-run.sh verbatim
          (validated server-side to [A-Za-z0-9._-]{1,64}).

        Fire-and-forget: returns once the run has been launched. Poll
        /api/sessions/{id}/run/history (via the stdio mcp-tank server's
        get_run_history tool) for transcript output.
        """
        return client.spawn_run(
            _pod_ip(),
            prompt=prompt,
            mode=mode,
            name=name,
            model=model,
            permission_mode=permission_mode,
        )
