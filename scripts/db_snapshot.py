#!/usr/bin/env python3
"""Create and verify safe SQLite snapshots for the local Orchestrator."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.settings import default_data_dir  # noqa: E402

DEFAULT_DB = Path(os.getenv("CLINIC_ORCHESTRATOR_DB", default_data_dir() / "orchestrator.sqlite3")).expanduser()


def integrity_check(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite file does not exist: {path}")
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as con:
        result = con.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise sqlite3.DatabaseError(f"SQLite integrity check failed: {result[0] if result else 'no result'}")


def backup_database(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if source == destination:
        raise ValueError("Backup destination must differ from the active database")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
    if not source.is_file():
        raise FileNotFoundError(f"Active database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source, timeout=20) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        integrity_check(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def restore_database(backup: Path, destination: Path) -> None:
    backup = backup.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if backup == destination:
        raise ValueError("Restore destination must differ from the backup")
    integrity_check(backup)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(backup) as src, sqlite3.connect(destination) as dst:
            src.backup(dst)
        integrity_check(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB,
                        help=f"active database path (default: {DEFAULT_DB})")
    commands = parser.add_subparsers(dest="action", required=True)
    backup = commands.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("destination", type=Path)
    verify = commands.add_parser("verify", help="check a snapshot's SQLite integrity")
    verify.add_argument("snapshot", type=Path)
    restore = commands.add_parser("restore", help="restore a snapshot into a new database file")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            backup_database(args.database, args.destination)
            print(f"Backup created and verified: {args.destination.expanduser().resolve()}")
        elif args.action == "verify":
            integrity_check(args.snapshot.expanduser().resolve())
            print(f"SQLite integrity check passed: {args.snapshot.expanduser().resolve()}")
        else:
            restore_database(args.snapshot, args.destination)
            print(f"Restored and verified new database: {args.destination.expanduser().resolve()}")
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
