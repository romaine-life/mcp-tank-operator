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
- `get_session_run_options()` — read Tank-owned create modes, SDK chat modes,
  provider model/effort lists, retired create modes, and defaults. Use this
  before choosing a non-default `mode`, `model`, or `effort`; Tank remains the
  validator and returns actionable errors for unsupported values.
- `delete_session(session_id)` — delete one of the caller's sessions.
- `set_session_name(session_id, name)` — set or clear the friendly display name.
- `set_pull_request_link(session_id, url)` — update the Tank UI PR link for the active test workflow.
- `get_session_url(session_id)` — tank UI URL for an existing session; accepts either an id or display name.
- `send_prompt(session_id, prompt, ...)` — fire-and-forget follow-up prompt to an SDK chat session.
- `spawn_run_session(prompt, mode, ...)` — create a fresh SDK chat session with `prompt` as its first turn. The prompt is required and rides the create request as `initial_turn`; the orchestrator enqueues it before the pod is ready and returns the session row plus the queued turn. There is no promptless "create empty, prompt later" path — that is why the old `create_session` tool was removed. This is the canonical way to start a new chat session.
- `spawn_test_slot_session(slot_name, prompt, mode, ...)` — create a fresh SDK chat session through a Glimmung test slot's own Tank orchestrator, then queue the first prompt. Use this for test-slot validation; it requires a slot name such as `tank-operator-slot-2` and refuses production-ish targets. When `mode`/`model`/`effort` are omitted, the tool reads Tank's admin-configured `test_slot_defaults` from run options.
- `point_slot_session_image(slot, codex_image, claude_image, antigravity_image, git_ref)` — point a Glimmung **test slot** at a branch-built session image so NEWLY-created sessions in that slot boot it (the same image lever production uses, no runtime overlay). Covers all three session-runner providers (claude / codex / antigravity); set one or several at once. Because a session pod's image is fixed at creation, this repoint plus a fresh slot session is the supported way to validate a session-container branch (including antigravity) on a slot — there is no in-place patch of a running session pod. Deploying a branch's CI-built image to the slot's own app/orchestrator surface is handled deterministically server-side by Tank's Test button/endpoint (`POST /api/sessions/{id}/test-workflow/start`), which drives Glimmung's `/v1/test-slots/deploy-image` HTTP API. The image must already exist in ACR (build it via the tank-operator `session-images-build.yml` workflow first); the production scope is refused server-side, so this can only repoint test slots. Targets the slot's own orchestrator (`tank-operator.<slot>.svc`), where the test-env gate is on.
- `get_slot_session_image(slot)` — report what session image NEW sessions in a test slot will boot (the current override, or `override_set: false`). Read-only.
- `clear_slot_session_image(slot)` — clear a slot's session-image override; new sessions revert to the chart-pinned image.

## Auth

Authorization is the **auth.romaine.life service-principal JWT**, not Kubernetes RBAC. Every tool authenticates to the orchestrator with the calling pod's JWT, forwarded by the pod's mcp-auth-proxy sidecar in the `X-Auth-Romaine-Token` header. mcp-auth-proxy exchanges `/var/run/secrets/auth.romaine.life/token` at `auth.romaine.life/api/auth/exchange/k8s` for a `role=service` JWT and forwards it; this server extracts it into the `SERVICE_BEARER` ContextVar and passes it as `Authorization: Bearer` on the outbound `/api/internal/sessions/*` call. The orchestrator verifies the JWT, gates on `role=service`, and treats its `actor_email` claim as the owner — so a caller can only ever act as itself, and a request without a valid JWT is refused (tools raise `service-principal authentication required`; the orchestrator rejects an invalid JWT). The MCP server never sees or accepts an owner email.

There is **no kube-rbac-proxy sidecar and no per-caller RBAC allowlist.** That gate was removed because it enumerated individual service accounts (`claude-session`, the long-deleted `hermes`) and could never cover dynamic Glimmung slot SAs (`tank-operator-slot-N-session`), all while the JWT path above was already the real authorization boundary. The server binds `0.0.0.0` and is reached directly via the `mcp-tank-operator` Service.

> Network posture: with no sidecar gate and no NetworkPolicy in the namespace, any in-cluster pod that can route to the Service reaches the server, but unauthenticated/invalid-JWT calls are refused at the tool/orchestrator layer. A label-selector NetworkPolicy scoped to session/orchestrator namespaces is the optional next hardening step (it needs tank-operator to label the dynamic slot namespaces).

The pre-#486 IP-tail identity path (`X-Forwarded-For` → `caller_pod_ip` query param) was retired in Stage 4. See [romaine-life/tank-operator#486](https://github.com/romaine-life/tank-operator/issues/486).
