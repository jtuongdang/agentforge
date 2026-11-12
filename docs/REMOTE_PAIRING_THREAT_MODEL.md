# Remote Pairing Threat Model

Status: Phase 8 implementation baseline selected by the project owner. The application opens no remote gateway unless the operator configures an exact HTTPS origin and trusted ingress peer.

## Selected first milestone

- Transport: private VPN with an operator-managed HTTPS ingress on the same host as the desktop sidecar.
- Client: browser at the configured HTTPS origin.
- Permissions: read-only task summary by default; no task creation, review decision, local owner API, logs, prompts, arbitrary source, provider settings, cleanup, or publication.
- Full diff: separate owner grant for one device + one task + the exact validated Git SHA. The grant stops matching when that task revision changes and can be revoked separately.
- The desktop backend keeps its normal ephemeral loopback API port. When enabled, it additionally binds a gateway to `127.0.0.1:8766` (configurable) for the local HTTPS ingress. The gateway is not bound to a LAN/VPN interface. The ingress must expose only `/remote`, `/remote/api/*`, and `/remote/ws/*` and must only accept VPN clients.
- Required environment: `CLINIC_ORCHESTRATOR_REMOTE_ORIGIN=https://<exact-host>`, `CLINIC_ORCHESTRATOR_REMOTE_PROXY_IPS=127.0.0.1` (or the actual local proxy peer), and optional `CLINIC_ORCHESTRATOR_REMOTE_GATEWAY_PORT=8766`. Missing or invalid values leave the gateway disabled. The ingress must overwrite `Host` and `X-Forwarded-Proto`, set `X-Forwarded-Proto: https`, pass the exact public `Host`, upgrade WebSocket, and avoid logging pairing/session cookies or headers.
- A desktop process restart expires browser sessions and pending pairings; the user must pair again. No remote credential is included in checkpoint archives.

## Security objective

Allow an explicitly paired remote device to inspect a bounded Orchestrator view without exposing the local owner API, starting workers, changing repositories, or publishing code. Read-only is the initial authorization level. Any later action permission must be a separate grant tied to an exact task revision and recorded in the audit log.

## Assets and trust boundaries

| Asset | Boundary / impact |
|---|---|
| Repository contents, diffs, prompts, handoffs | Private source and business data; disclosure can be high impact |
| Local SQLite database and worktrees | Task state, run history, review state, and recoverable user changes |
| Local API token and CLI/provider credentials | Owner-level control or external account access |
| Remote invite and session credentials | Remote-device access; must not become an owner credential |
| REST/WebSocket payloads | Logs and diffs may contain untrusted text, secrets, or large data |
| Desktop process and loopback API | Trusted local boundary; current server restricts Host/origin and uses a local API token |
| Remote transport/proxy | Not trusted to grant application permissions; it may provide confidentiality and peer identity only after verification |

Threat actors include an unintended device on the same LAN, a compromised or lost paired device, an attacker replaying an invite/session, a malicious page attempting browser-based local API access, and a network observer or active intermediary.

## Required controls

| Threat | Required control | Verification evidence |
|---|---|---|
| Raw owner token leaked to a remote client | Separate remote credential namespace, audience, and routes; never return or forward the local API token | Route matrix proves remote token cannot access owner API, fleet settings, task creation, exec, cleanup, or publish |
| Invite theft or replay | High-entropy one-time invite, short expiry, hash at rest, owner approval showing device identity, consume atomically | Expired, reused, concurrent-use, and wrong-device tests fail |
| Session theft | Scoped session, bounded idle and absolute expiry, explicit revoke, no credential in URLs or logs | REST denies after revoke; active WebSocket closes; token absent from logs |
| Network interception | TLS from remote client to a supported VPN ingress; reject cleartext and invalid peer identity | Certificate/identity negative tests; no insecure transport option in production config |
| Proxy bypass / owner API exposure | Keep loopback owner API private; separate remote listener/gateway with an explicit route allowlist | Bind-address and port scan on LAN; direct requests to owner listener fail |
| Cross-site request or DNS rebinding | Validate the exact configured Host and Origin (same-origin Fetch Metadata fallback for browser GET), forwarded HTTPS, and ingress peer IP on every remote REST and WebSocket route; CSRF protection for any future write grant | Wrong Host/Origin/protocol/proxy tests fail; no owner route is exposed on the remote gateway |
| Source disclosure beyond granted view | Explicit field and payload allowlists; default remote view excludes credentials, environment, raw prompt, logs, changed paths, and arbitrary filesystem paths; only task title/status/repository display/validation boolean are shown | Snapshot fixture checks exact permitted fields and redacts secret-like values and local paths |
| HTML/log injection | Treat logs, diffs, titles, and device names as text; cap page sizes and serialized payloads | Browser rendering test plus max-payload tests |
| Resource exhaustion | Pairing is one-use and five minutes; pairing poll is five minutes; sessions expire in 12 hours; REST budget is 120 requests/minute/device; WebSocket caps are 2/device and 32 total; request body cap 128 KiB; diff pages cap at 24,000 characters and total offset 1,000,000 | Unit/API tests cover body boundary, one-use invitation, rate/connection limits, and page bounds |
| Privilege escalation | Read-only default; publication/review action permission is separate, explicit, revision-pinned, and auditable | Scope-denial tests for every mutation and stale SHA |
| Lost device | Owner can list paired devices and revoke one immediately | Revoke test denies REST and closes the status-only WebSocket within two seconds |

## Transport recommendation for the first remote milestone

Use a private VPN path with HTTPS ingress on the same desktop host, reverse-proxying to the optional loopback gateway. Do not bind either application listener to `0.0.0.0`, publish the owner API to a LAN, or use a public relay. Keep relay and internet-facing access out of scope until a separate threat model, abuse controls, and operational owner are defined. VPN membership only provides network reachability; the app still requires its own paired device session.

## Implementation gates

1. Keep the existing ephemeral loopback owner API and local token behavior unchanged; the optional second gateway also binds to loopback only.
2. Implement one-time invitation, pending browser request, local owner approval, hash-only device credential, and read-only REST/status WebSocket.
3. Add expiry, per-device revoke, exact host/origin/proxy checks, payload/rate/connection limits, and audit events.
4. Allow full diff only after local owner confirmation pins the task's current validated SHA; do not offer remote action grants in this milestone.
5. Validate on two devices over the selected VPN, verify owner API isolation and WebSocket revoke, and inspect ingress TLS/header/logging configuration before enabling a production route.

## Implementation acceptance still pending

- The operator must configure a concrete VPN/HTTPS ingress origin, certificate, route allowlist, and local proxy peer IP before the optional gateway can start.
- Validate the browser flow on two devices through that VPN and verify the owner API is unreachable from the second device.
- Review remote UI behavior, cert renewal, lost-device recovery, and reverse-proxy access-log settings in the deployed ingress.
- Remote review approval and publication remain excluded; only a separately confirmed full diff for one pinned task revision is supported.
