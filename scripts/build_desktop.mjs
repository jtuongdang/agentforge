import { spawnSync } from "node:child_process";
import { chmodSync, copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const platform = process.platform;
const architecture = process.arch;
const targets = {
  "darwin:arm64": "aarch64-apple-darwin",
  "darwin:x64": "x86_64-apple-darwin",
  "win32:x64": "x86_64-pc-windows-msvc",
};
const target = targets[`${platform}:${architecture}`];
if (!target) {
  throw new Error(`Unsupported Desktop build host: ${platform}/${architecture}`);
}

const configuredJobs = process.env.NUITKA_BUILD_JOBS ?? "1";
if (!/^[1-9]\d*$/.test(configuredJobs)) {
  throw new Error("NUITKA_BUILD_JOBS must be a positive integer.");
}
const jobs = String(Math.min(Number(configuredJobs), 2));
const windows = platform === "win32";
const venvPython = join(root, ".venv", windows ? "Scripts" : "bin", windows ? "python.exe" : "python");
const pythonCandidates = windows ? ["python", "py"] : ["python3", "python"];

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    env: process.env,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status ?? "a signal"}`);
}

if (!existsSync(venvPython)) {
  const python = pythonCandidates.find((candidate) => {
    const check = spawnSync(candidate, ["--version"], { cwd: root, stdio: "ignore" });
    return !check.error && check.status === 0;
  });
  if (!python) throw new Error("Python 3 is required to build the packaged backend sidecar.");
  run(python, ["-m", "venv", join(root, ".venv")]);
}

const outputDir = join(root, ".desktop-build", "backend");
const binaryDir = join(root, "dashboard", "src-tauri", "binaries");
const extension = windows ? ".exe" : "";
const filename = `devagent-backend-${target}${extension}`;
mkdirSync(outputDir, { recursive: true });
mkdirSync(binaryDir, { recursive: true });

run(venvPython, ["-m", "pip", "install", "-q", "-r", "backend/requirements.txt", "-r", "backend/requirements-desktop.txt"]);
run(venvPython, [
  "-m", "nuitka",
  "--mode=onefile",
  `--jobs=${jobs}`,
  "--assume-yes-for-downloads",
  `--output-dir=${outputDir}`,
  `--output-filename=${filename}`,
  `--include-data-file=${join(root, "docs", "PROJECT_CONVENTIONS.md")}=${"docs/PROJECT_CONVENTIONS.md"}`,
  "backend/desktop_entry.py",
]);

const builtBinary = join(outputDir, filename);
if (!existsSync(builtBinary)) throw new Error(`Nuitka did not produce ${builtBinary}`);
const packagedBinary = join(binaryDir, filename);
copyFileSync(builtBinary, packagedBinary);
if (!windows) chmodSync(packagedBinary, 0o755);
console.log(`Built backend sidecar for ${target}: ${packagedBinary}`);
