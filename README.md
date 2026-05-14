# mcp-tank-operator

In-cluster MCP server that lets a tank-operator session pod manage other sessions on behalf of its owner.

## Layout

- `src/` — Python MCP server package.
- `Dockerfile` — image build for `romainecr.azurecr.io/mcp-tank-operator`.
- `chart/` — Helm chart synced by ArgoCD.

Images are SHA-tagged from `main`; `.github/workflows/build.yml` pushes the image and commits the matching chart tag.

## Tools

- `list_sessions()` — sessions owned by the calling user.
- `list_session_refs()` — low-noise list of session ids and Tank UI display names.
- `resolve_session(session_ref)` — resolve a Tank UI display name or session id to the full session record.
- `create_session(mode)` — spawn a new session pod. Current chat modes are `claude_gui` and `codex_gui`; default is `claude_gui`.
- `delete_session(session_id)` — delete one of the caller's sessions.
- `set_session_name(session_id, name)` — set or clear the friendly display name.
- `get_session_url(session_id)` — tank UI URL for an existing session; accepts either an id or display name.
- `send_prompt(session_id, prompt, ...)` — fire-and-forget follow-up prompt to an SDK chat session.
- `spawn_run_session(prompt, mode, ...)` — create a fresh SDK chat session, wait for its pod to become ready, then queue the first prompt.

## Auth

Inbound: kube-rbac-proxy validates the session pod's projected SA token via `TokenReview` + `SubjectAccessReview` against the synthetic `mcp.tank-operator.io/servers/tank-operator` resource.

Outbound: the server presents its own pod's projected SA token (`mcp-tank-operator/mcp-tank-operator`) minted for the `tank-operator` audience to the orchestrator, plus a `caller_pod_ip` query param recovered from the inbound `X-Forwarded-For` chain. The orchestrator resolves the IP to an owner email via the same `find_pod_by_ip` path that backs `/api/internal/resolve-caller`. The MCP server never sees or accepts an owner email — identity is locked to the network-layer source-IP chain.
