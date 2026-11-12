# Desktop internal alpha installation

The internal alpha packages are version 0.1.0. They contain the Tauri shell,
dashboard, and standalone backend. Python, Node.js and npm are not required on
the installed machine. Git and a supported coding CLI/session are required for
CLI tasks; provider workers require a configured provider/model instead.

These builds have not completed publisher signing/notarization or clean-machine
GUI acceptance. Automatic updates are not configured. Use them for internal
evaluation and preserve a checkpoint before replacing an existing installation.

## Select and verify the package

Download an artifact from a successful target job in the **Desktop build matrix**
workflow. A different target failing or lacking a runner does not invalidate a
successful job's artifact. Packages are retained on GitHub Actions for 14 days.

| Computer | Target | Installer |
|---|---|---|
| Mac with Apple silicon | `aarch64-apple-darwin` | ARM64 `.dmg` |
| Mac with Intel processor | `x86_64-apple-darwin` | x64 `.dmg` |
| Windows x64 | `x86_64-pc-windows-msvc` | NSIS `.exe` or `.msi` |

Extract the downloaded artifact first. New builds contain `release-manifest.json`
with the exact source commit, target and installer hashes, plus `SHA256SUMS`.
On macOS, run `shasum -a 256 -c SHA256SUMS` inside the extracted directory.
On Windows, use PowerShell `Get-FileHash -Algorithm SHA256 <installer-path>`
and compare the result with the entry in `SHA256SUMS`. Checksums verify file
integrity; these alpha artifacts do not provide verified publisher signatures.

## Install and open

On macOS, open the DMG and copy **DevAgent Orchestrator.app** into Applications.
Choose the package matching **About This Mac**. If macOS blocks the unsigned
alpha, review its source and checksum before deciding whether to allow it in
system settings. This alpha is not a notarized public release.

On Windows, run one installer and follow its prompts. Windows may display an
unknown publisher message for the unsigned alpha.

After opening, add a local Git repository, select an existing base branch and
review the validator argv. Register or detect a coding CLI, or configure a
provider from Agents. Start with a small fixture task, inspect its validation
and complete it locally. Publication of a Draft PR requires separate approval.

## Data, upgrades and recovery

Default application data locations:

- macOS: `~/Library/Application Support/DevAgent Orchestrator/`
- Windows: `%LOCALAPPDATA%\DevAgent Orchestrator\`

Use **Storage → Export checkpoint** before an upgrade or moving machines.
Checkpoint export excludes credentials and entitlement/device consent. Restoring
requires reconfiguring those credentials and reviewing interrupted work. SQLite
migrations also create a local database snapshot before schema upgrades.

Close the app before replacing its bundle or running an installer upgrade. If
a run was interrupted, inspect the retained worktree and confirm the previous
worker stopped before using **Review checkpoint & requeue**. The app scans the
checkpoint again before it can dispatch; it does not resume automatically.

## Services configured separately

`license-hub/.env` belongs to the server-side License Hub and is excluded from
Desktop packages. Its Atlas credential, private signing key, pepper and admin
token must remain on the server. Starting that service is described in
[License Hub README](../license-hub/README.md). A licensed Desktop release still
needs an HTTPS hub URL and its pinned public key.

Remote browser review needs the operator-managed VPN/HTTPS ingress described in
[REMOTE_INGRESS.md](REMOTE_INGRESS.md). Installing the Desktop package alone does
not configure that infrastructure.
