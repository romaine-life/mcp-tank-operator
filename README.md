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
- `get_session_capability_context(capability, session_id)` — warm documentation for rare Tank session capabilities. Currently covers `spirelens_mcp`, including the native MCP endpoint, direct auth.romaine.life SSH certificate path, and the difference from Glimmung run callback URLs.
- `verify_spirelens_session_access(session_id)` — read-only inspection for a caller-owned session's SpireLens MCP wiring: selected capability, `/workspace/.mcp.json` server entry, local proxy target, and expected host lifecycle tools.
- `read_transcript(session_id, ...)` — read a caller-owned session's conversation transcript (projected rows + pagination cursors). Reads the durable Postgres projection, so it works even after the target session's pod is gone — useful for triaging a stuck sibling session before deciding to prompt, delete, or escalate.
- `create_session(mode)` — spawn a new session pod. Current chat modes are `claude_gui` and `codex_gui`; default is `claude_gui`.
- `delete_session(session_id)` — delete one of the caller's sessions.
- `set_session_name(session_id, name)` — set or clear the friendly display name.
- `get_session_url(session_id)` — tank UI URL for an existing session; accepts either an id or display name.
- `send_prompt(session_id, prompt, ...)` — fire-and-forget follow-up prompt to an SDK chat session.
- `spawn_run_session(prompt, mode, ...)` — create a fresh SDK chat session, wait for its pod to become ready, then queue the first prompt.

## Auth

Inbound: kube-rbac-proxy validates the calling session pod's projected SA token via `TokenReview` + `SubjectAccessReview` against the synthetic `mcp.tank-operator.io/servers/tank-operator` resource. This gates *whether* the pod can reach this MCP server at all; per-call identity is a separate layer below.

Outbound: every tool authenticates to the orchestrator with the calling pod's auth.romaine.life **service-principal JWT**, forwarded by the pod's mcp-auth-proxy sidecar in the `X-Auth-Romaine-Token` header. mcp-auth-proxy exchanges `/var/run/secrets/auth.romaine.life/token` at `auth.romaine.life/api/auth/exchange/k8s` for a `role=service` JWT and forwards it; this server extracts it into the `SERVICE_BEARER` ContextVar and passes it as `Authorization: Bearer` on the outbound `/api/internal/sessions/*` call. The orchestrator verifies the JWT and treats its `actor_email` claim as the owner — the MCP server never sees or accepts an owner email.

The pre-#486 IP-tail identity path (`X-Forwarded-For` → `caller_pod_ip` query param) was retired in Stage 4. See [nelsong6/tank-operator#486](https://github.com/nelsong6/tank-operator/issues/486).
