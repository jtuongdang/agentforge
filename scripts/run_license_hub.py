"""Start the local License Hub with an ignored env file; never evaluate shell code."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_environment(path: Path) -> dict[str, str]:
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid env assignment at line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed env quote at line {number}")
            value = value[1:-1]
        if "\x00" in value or key in values:
            raise ValueError(f"Duplicate or invalid env assignment at line {number}")
        values[key] = value
    # Explicit process environment takes precedence over local configuration.
    return {**values, **os.environ}


def validate_environment(environment: dict[str, str]) -> None:
    required = ("MONGODB_URI", "LICENSE_SIGNING_PRIVATE_KEY_PATH", "LICENSE_KEY_PEPPER", "LICENSE_ADMIN_TOKEN")
    missing = [key for key in required if not environment.get(key, "").strip()]
    if missing:
        raise ValueError("Missing settings: " + ", ".join(missing))
    if not environment["MONGODB_URI"].startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError("MONGODB_URI must use a MongoDB connection scheme")
    if not Path(environment["LICENSE_SIGNING_PRIVATE_KEY_PATH"]).is_file():
        raise ValueError("LICENSE_SIGNING_PRIVATE_KEY_PATH does not identify an existing file")
    try:
        pepper = base64.b64decode(environment["LICENSE_KEY_PEPPER"], validate=True)
    except ValueError:
        raise ValueError("LICENSE_KEY_PEPPER must be valid base64") from None
    if len(pepper) < 32 or len(environment["LICENSE_ADMIN_TOKEN"]) < 32:
        raise ValueError("License pepper and admin token must contain at least 32 bytes/characters")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "license-hub" / ".env")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    try:
        environment = load_environment(args.env_file)
        validate_environment(environment)
    except OSError:
        print("Could not read the env file or signing key configuration.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.check_config:
        print("License Hub configuration: complete (secret values hidden; no database connection made).")
        return 0
    dotnet = shutil.which("dotnet")
    if not dotnet:
        print("The .NET 8 SDK/runtime is required to start License Hub.", file=sys.stderr)
        return 1
    environment.setdefault("ASPNETCORE_URLS", "http://127.0.0.1:8080")
    if not args.no_build:
        result = subprocess.call([dotnet, "build", str(ROOT / "license-hub" / "LicenseHub.csproj"),
                                  "--nologo", "--verbosity", "minimal", "-m:1"], env=environment)
        if result:
            return result
    return subprocess.call([dotnet, str(ROOT / "license-hub" / "bin" / "Debug" / "net8.0" / "LicenseHub.dll")],
                           cwd=ROOT, env=environment)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
