# License Hub

The License Hub implements the device-bound 14-day trial and signed entitlement
service selected for Phase 7C. It uses .NET 8 Minimal API and MongoDB. It does
not accept source code, prompts, email addresses, OS details, or IP fields in
public device/license requests. Authenticated admin endpoints can store a user's
display name and email to assign licenses; these records are not Desktop login
credentials. API DTOs reject unknown JSON fields and request bodies are capped
at 4 KiB.

## Admin console and self deployment

The same service serves `/admin/` with an admin-token login. It lists users,
licenses and device statuses, creates licenses, revokes licenses, and creates,
edits or disables user profiles. Lists are authenticated, paginated, and omit raw
license keys and key hashes. New raw keys are displayed only after creation.
The browser keeps its admin token in memory and clears it on logout/reload.

Licenses may link to `user_id`; existing unlinked licenses remain supported.
Disabling a user denies activation and returns a signed `banned` entitlement at
the next online verification. It does not instantly invalidate an offline cached
entitlement. Re-enabling the user restores verification for a still-valid license.

See [Docker Compose deployment](../deploy/license-hub/README.md) for the image,
secret mounts, non-root file permissions, HTTPS/tunnel setup and update steps.
The image never contains database credentials, admin tokens or signing keys.

## Local build and tests

```sh
dotnet build license-hub/LicenseHub.csproj -m:1
dotnet test license-hub.tests/LicenseHub.Tests.csproj -m:1
```

The hub needs a MongoDB instance and secrets to start. Do not add them to this
repository or paste them into chat. The application has passed integration
tests against a temporary local MongoDB and an isolated synthetic database on
MongoDB Atlas. A configured local staging hub has also passed startup/health
checks. Production HTTPS deployment and the desktop release public-key pin
remain unaccepted; the Atlas test does not establish production readiness.

For local tests, start a MongoDB process bound only to `127.0.0.1`, copy
`.env.example` to `.env`, replace its placeholders with disposable test values,
then use the launcher from the repository root:

```sh
python3 scripts/run_license_hub.py --check-config
python3 scripts/run_license_hub.py
```

The launcher reads `license-hub/.env` without evaluating shell commands, checks
required settings without printing their values, builds with one MSBuild worker,
and starts the hub. Existing process environment variables take precedence over
the file. Use `--no-build` after a successful build or `--env-file` for a separate
staging configuration. Keep the env file readable only by its owner (`chmod 600`).
The template uses a local MongoDB URI and is not a production deployment file.

## Runtime configuration

Provide these environment variables through the deployment secret manager:

| Name | Purpose |
|---|---|
| `MONGODB_URI` | MongoDB connection string with least-privilege access to the license database |
| `LICENSE_DATABASE` | Database name; defaults to `LicenseHubDb` |
| `LICENSE_SIGNING_PRIVATE_KEY_PATH` | Mounted RSA private PEM, at least 2048 bits; never ship to clients |
| `LICENSE_KEY_PEPPER` | Base64-encoded random secret of at least 32 bytes for HMAC hashing license keys |
| `LICENSE_ADMIN_TOKEN` | Random admin bearer secret of at least 32 bytes |

## Container deployment

Build from the License Hub directory so the desktop application and local files
are not sent as part of the build context:

```sh
docker build -f license-hub/Dockerfile -t clinic-license-hub:local license-hub
```

Run the container on a private network and provide the environment values from
the host's secret manager. Mount the signing key read-only at the path provided
by `LICENSE_SIGNING_PRIVATE_KEY_PATH`; do not copy it into the image or build
context. Port 8080 is plaintext inside the private container network and must
only be reachable through the operator-managed HTTPS ingress. Do not publish
the container port directly to the Internet. Configure the ingress to suppress
request-body logs and restrict `/api/v1/admin/*` to the operator's management
network.

Create signing material outside the repository and distribute only the public
key to desktop builds:

```sh
umask 077
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out license-private.pem
openssl pkey -in license-private.pem -pubout -out license-public.pem
openssl rand -base64 32
openssl rand -hex 32
```

The hub is intended to sit behind an operator-managed HTTPS ingress. Restrict
the MongoDB network allowlist to the hub, protect `/api/v1/admin/*` at the
ingress as well as with its bearer token, disable request-body logging, and
back up the database and signing key under separate access controls. Do not
expose a plaintext listener publicly.

## API contract

- `POST /api/v1/devices/verify` accepts only `{"device_hash":"<sha256>"}`;
  the first request atomically starts the 14-day trial. Reinstalling the app
  does not reset the MongoDB record.
- `POST /api/v1/licenses/activate` accepts the device hash and license key over
  HTTPS. The hub stores only an HMAC-SHA256 hash of the normalized key. Device
  capacity is enforced atomically; the generated entitlement is RSA-PSS/SHA-256.
- `POST /api/v1/licenses/deactivate` releases the current device slot when the
  matching license key is presented. It does not restart the device trial.
- `POST /api/v1/admin/licenses` requires `Authorization: Bearer …`, creates a
  high-entropy key, and returns the raw key once. MongoDB stores only its hash.
- `POST /api/v1/admin/licenses/{id}/revoke` disables the license. A device
  receives a signed revoked entitlement at its next online verification.
- `GET /api/v1/admin/summary`, `/users`, `/licenses`, and `/devices` require
  the admin bearer token. Lists accept bounded `offset` and `limit` (1–100).
- `POST /api/v1/admin/users` and `PATCH /api/v1/admin/users/{id}` manage
  `display_name`, normalized unique `email`, and `is_enabled`.
- `POST /api/v1/admin/licenses` additionally accepts an optional `user_id`.
- `/health` is liveness; `/ready` checks database connectivity. The image's
  healthcheck uses `/ready` through its internal loopback listener.

Entitlements bind to the one-way device hash and contain only status, validity
times, feature names, tier, and opaque license ID. Desktop cache is local and
excluded from portable checkpoints. The client keeps project reading/export
available if AI entitlement is denied.

## Not deployed yet

This repository contains the service implementation, unit/integration tests,
and a non-root container image definition, but no MongoDB credentials, hosted
URL, TLS ingress, production RSA key, signing-key rotation policy, CI
deployment, or production license records. Validate trial creation/expiry,
concurrent activation limits, deactivation, revocation, MongoDB failure
recovery, and the desktop public-key pin against an isolated staging database
before enabling it for users.

## Google broker source upgrade (G2)

The source now includes an opt-in Google OIDC broker, PKCE one-time Desktop
handoff, hashed app sessions with refresh rotation/revocation, and explicit
administrator approval for existing profiles. It is disabled by default and
is not included in the published `0.1.0-admin.1` image. Read
[Google setup and remaining acceptance gates](../docs/GOOGLE_AUTH_SETUP.md).
Desktop Account/profile and license-binding enforcement (G3) remain open.

Tests use a fake OIDC issuer/backchannel and isolated synthetic MongoDB databases.
With a dedicated local MongoDB running, enable the two optional integration tests:

```sh
LICENSE_HUB_TEST_MONGO_URI='mongodb://127.0.0.1:27018/?directConnection=true' \
  nice -n 10 dotnet test license-hub.tests/LicenseHub.Tests.csproj -m:1 -p:UseSharedCompilation=false
```

No Google credential or external request is needed for these tests. Each test
creates and removes only its own randomly named synthetic database. Without
the test MongoDB setting, these integration tests are reported as skipped.
