# DevAgent Orchestrator

Local, SQLite-backed multi-agent orchestration for registered Git repositories. Clinic Management Desktop was the initial example project; it is not required to use the Orchestrator.

## Environment verified on 2026-11-04

| Tool | Observed | Integration |
|---|---|---|
| JDK / javac | 27.0.1; project targets Java 17 | Maven validation, when Maven is installed |
| Maven | 3.10.0 installed after initial survey; no Maven Wrapper in the repo | `mvn clean verify` |
| Python | 3.14.7 | FastAPI API and orchestration engine |
| Git | 2.39.5 | Isolated worktrees and branches |
| GitHub CLI | 2.97.0; authenticated as `jtuongdang`, `repo`/`workflow` scopes | Push and Draft PR after explicit approval |
| Codex CLI | 0.166.0; logged in with ChatGPT | Headless `codex exec` adapter |
| GitHub Copilot CLI | 1.0.105; installed via npm, authenticated prompt smoke check passed | Headless `copilot --prompt` adapter; one concurrent task |
| Antigravity CLI | 1.5.0; installed using Google's installer; headless prompt auth succeeded | Headless `agy --print` adapter; sandboxed `accept-edits` mode; one concurrent task |

The initially surveyed Java repository had 13 local modifications at survey time. This project does not modify, stage, reset, or clean that working tree. Its worker uses separate Git worktrees from committed refs.

Codex, GitHub Copilot and Antigravity are registered for automatic execution and can run independent tasks concurrently in separate worktrees. Copilot's command sandbox is enabled in the user's global Copilot settings. Antigravity runs with its per-session terminal sandbox and `accept-edits` mode; task-scoped permission rules allow Maven commands but deny Git publishing, MCP, external URLs, and destructive commands. The account reported `auto` mode unavailable, so the adapter does not request it or bypass permissions.

## Run locally

```sh
cd ~/Documents/ClinicOrchestrator
./scripts/dev.sh
```

The launcher creates `.venv`, installs the pinned Python requirements, starts FastAPI, and serves the Vite dashboard. The API binds to loopback (`127.0.0.1:8765` by default; override with `CLINIC_ORCHESTRATOR_HOST` and `CLINIC_ORCHESTRATOR_PORT`). Development data is under `data/`; packaged builds use the platform application data directory. Override it with `CLINIC_ORCHESTRATOR_DATA_DIR` or point SQLite at a specific file with `CLINIC_ORCHESTRATOR_DB`. Register any local Git checkout from the dashboard before creating tasks; startup does not assume a particular target project.

## Desktop alpha (macOS)

Install Node.js and Rust/Cargo on the build machine, then run `npm run desktop:dev` from `dashboard/` for the Tauri shell with a source Python backend. `npm run tauri build` runs the frontend build and `scripts/build_desktop_macos.sh`; that script compiles the backend into a self-contained Nuitka sidecar for the current macOS architecture. The packaged app uses a random loopback port and platform application data directory; the installed machine does not need Python, Node.js, or npm. Cross-architecture packages must be built separately on their target architecture.

To limit build CPU usage, set `NUITKA_BUILD_JOBS=1 CARGO_BUILD_JOBS=1` before `npm run tauri build`; the Nuitka script defaults to two jobs when unset.

## Safety boundaries

- Each task selects a base branch (defaulting to the repository's configured default); the Agent gets a dedicated worktree and a meaningful task branch such as `orchestrator/fix-phone-validation`. The checked-out project directory remains untouched, and uncommitted changes are never copied into the worktree.
- A task records acceptance criteria, priority and allowed file globs. Urgent/high tasks are dispatched first; out-of-scope changes are rejected before commit.
- The dashboard shows the selected base branch and committed patch before external publication. After validation, an operator can keep the commit local or separately approve a GitHub Draft PR against that selected base. The base must already exist on GitHub; a local-only base can be completed locally, or pushed separately before PR approval.
- Maven projects with a root `pom.xml` run `mvn clean verify` (or the Maven Wrapper). Other repositories can configure a custom argv validator; unsupported projects are blocked rather than incorrectly run through Maven.
- Build/test runs only in the worktree. Maven is detected at runtime; missing Maven is reported explicitly.
- Push and Draft PR are separate approval-gated actions. Merge and force push are not implemented.
- IDE-only agents receive an exported handoff package; they are never invoked as if they had a CLI.
- The Agents dashboard can check credential status and available quota where the CLI exposes it. It stores only status, timestamp, and safe quota fields; it never stores or displays token values. Copilot's CLI has no auth-only check, so its explicit check sends one short prompt and consumes one Copilot request.
- Removing an Agent disables future scheduling while preserving task/run history and the local CLI login. The Agent can be restored from the dashboard. Removal is blocked while it owns active tasks.
- Agent commands use configured adapters and capabilities. There are no hardcoded business roles.
- Parallel tasks must have independent file scopes or a pre-approved contract; the scheduler applies global run/build limits, shared executable credential limits and disk reservations, then creates a separate worktree for each admitted task. Lowering capacity drains active work without killing it.
- The Fleet page persists local run/build limits, shows active owners and disk headroom, and does not guess RAM per worker.
- On backend restart, queued tasks remain queued; interrupted execution is marked failed and an interrupted publish is blocked for manual remote review. Worktrees are retained.
- `/api/health` checks SQLite integrity and reports scheduler, task, repository, and Agent availability counts. `scripts/db_snapshot.py` creates consistent SQLite backups and restores only to a new file, refusing to overwrite existing data.
- Local API routes require a bearer token. The dashboard obtains it from the loopback-only `/api/session` endpoint; by default the token is stored as `api-token` in the app data directory with owner-only permissions on POSIX. WebSocket events use short-lived, one-time tickets. Do not include the token in task prompts or logs.
- External Tech Lead sessions use a separate one-time owner pairing code, limited scopes, a 90-second renewable lease and an eight-hour maximum session. The session cannot publish, push, merge or approve a Draft PR; review decisions are stored with an audit record. `/api/health` does not imply a Tech Lead is connected.
- Worktree cleanup is preview-only by default. `python scripts/cleanup_worktrees.py --apply` removes only aged, clean task worktrees whose commits are already ancestors of the selected base; dirty, ignored, untracked, handoff, and unmerged worktrees remain for review. Build folders are not deleted automatically.
- The dashboard's **Storage** page shows SQLite/WAL size, worktree/build usage, managed data totals, record counts and free disk space. It refreshes when opened and every 30 seconds; it is read-only and does not clean up data automatically.
- Before each Agent run, the Orchestrator injects [PROJECT_CONVENTIONS.md](docs/PROJECT_CONVENTIONS.md) and reads repository `AGENTS.md`/guidance files plus scoped ancestor instructions; the task package lists them and requires the Agent to report them.

See the [completion roadmap](docs/COMPLETION_ROADMAP.md), the [Desktop upgrade plan](docs/DESKTOP_UPGRADE_PLAN.md), and the [project conventions](docs/PROJECT_CONVENTIONS.md).
