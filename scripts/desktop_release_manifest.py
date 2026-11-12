"""Record installer provenance and SHA-256 checksums for internal Desktop builds."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_directory", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()
    directory = args.bundle_directory.resolve(strict=True)
    installers = sorted(path for path in directory.rglob("*") if path.is_file()
                        and not path.is_symlink() and not any(part.endswith(".app") for part in path.parts)
                        and path.name.endswith((".dmg", ".msi", ".exe", ".tar.gz", ".zip", ".sig")))
    if not installers:
        parser.error("No packaged installers found in the bundle directory")
    commit = args.commit or os.getenv("GITHUB_SHA") or subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    config = json.loads((ROOT / "dashboard/src-tauri/tauri.conf.json").read_text())
    files = [{"path": path.relative_to(directory).as_posix(), "bytes": path.stat().st_size,
              "sha256": digest(path)} for path in installers]
    manifest = directory / "release-manifest.json"
    manifest.write_text(json.dumps({"product": config["productName"], "version": config["version"],
        "target": args.target, "commit": commit, "channel": "internal-alpha",
        "signing_verified": False, "files": files}, indent=2) + "\n", encoding="utf-8", newline="\n")
    sums = [f"{item['sha256']}  {item['path']}" for item in files]
    sums.append(f"{digest(manifest)}  {manifest.name}")
    (directory / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    print(f"Recorded {len(files)} installer(s) and manifest checksums for {args.target}.")


if __name__ == "__main__":
    main()
