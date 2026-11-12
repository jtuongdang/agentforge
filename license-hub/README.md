# License Hub

The License Hub implements the device-bound 14-day trial and signed entitlement
service selected for Phase 7C. It uses .NET 8 Minimal API and MongoDB. It does
not accept source code, prompts, email addresses, OS details, or IP fields. API
DTOs reject unknown JSON fields and request bodies are capped at 4 KiB.

## Local build and tests

```sh
dotnet build license-hub/LicenseHub.csproj -m:1
dotnet test license-hub.tests/LicenseHub.Tests.csproj -m:1
```

The hub needs a MongoDB instance and secrets to start. Do not add them to this
repository or paste them into chat. The application has passed integration
tests against a temporary local MongoDB, but has not been deployed to or
exercised against an external/production MongoDB service.

For local tests, start a MongoDB process bound only to `127.0.0.1`, copy
`.env.example` to `.env`, replace its placeholders with disposable test values,
then load that ignored file into the process environment before `dotnet run`.
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
