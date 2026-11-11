# Remote Pairing Threat Model

Status: design baseline for Phase 8; remote transport and client trust policy remain unselected. This document does not enable a remote listener.

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
| Cross-site request or DNS rebinding | Validate Host and Origin on every remote REST and WebSocket route; CSRF protection for any future write grant | Wrong Host/Origin tests fail for HTTP and WebSocket handshake |
| Source disclosure beyond granted view | Explicit field and payload allowlists; default remote view excludes credentials, environment, raw prompt, and arbitrary filesystem paths | Snapshot fixture checks exact permitted fields and redacts secrets |
| HTML/log injection | Treat logs, diffs, titles, and device names as text; cap page sizes and serialized payloads | Browser rendering test plus max-payload tests |
| Resource exhaustion | Pairing, session, REST, and WebSocket rate/connection limits; bounded log/diff pages | Concurrent and oversized-request tests stay within configured limits |
| Privilege escalation | Read-only default; publication/review action permission is separate, explicit, revision-pinned, and auditable | Scope-denial tests for every mutation and stale SHA |
| Lost device | Owner can list paired devices and revoke one immediately | Revoke test cuts REST and WebSocket access without restarting the backend |

## Transport recommendation for the first remote milestone

Prefer a private VPN path with authenticated HTTPS ingress. Do not bind the existing owner API to `0.0.0.0`, publish it directly to a LAN, or use a public relay for the first milestone. Keep relay and internet-facing access out of scope until a separate relay threat model, abuse controls, and operational owner are defined. The selected VPN implementation must support identity verification at the application boundary; network membership alone is not an application permission.

## Implementation gates

1. Select the supported ingress and remote-client identity source.
2. Keep the existing loopback API and local token behavior unchanged; introduce a separate remote route surface.
3. Implement invite creation/consumption, owner-visible device pairing, and read-only REST/WS.
4. Add expiry, per-device revoke, payload limits, rate limits, and audit events.
5. Add any action grant only after the read-only route and revoke tests pass.
6. Validate on two devices over the selected VPN and verify the owner API is not reachable from the second device.

## Explicitly unresolved

- Which VPN/ingress product or operator-managed gateway is supported.
- Which remote client is in scope (native companion, browser, or both).
- Whether the remote client may see full diffs or only review summaries.
- Whether review approval is ever granted remotely; publication remains excluded by default.
- Who owns certificate issuance, device recovery, and VPN support.
