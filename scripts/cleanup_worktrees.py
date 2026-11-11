#!/usr/bin/env python3
"""CLI utility to safely clean up old Git worktrees with Ultra-Safe policy:
1. Merged/pushed task branches: fully removed via git worktree remove.
2. Unmerged task branches: source code preserved 100%, compiler artifact target/ directories cleaned.

Usage:
    python scripts/cleanup_worktrees.py [--max-age-hours 24] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.worktrees import prune_worktrees  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old task worktrees with ultra-safe preservation.")
    parser.add_argument("--max-age-hours", type=float, default=24.0,
                        help="Prune worktrees older than this many hours (default: 24.0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report prunable worktrees without deleting anything")
    args = parser.parse_args()

    mode_label = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode_label}Executing Ultra-Safe Worktree Cleanup (age > {args.max_age_hours}h)...")
    print("Policy: Merged/pushed -> Full remove | Unmerged -> Clean target/ only (Keep source 100%)\n")

    result = prune_worktrees(max_age_hours=args.max_age_hours, dry_run=args.dry_run, clean_unmerged_targets_only=True)

    print(f"{mode_label}Fully removed (merged/pushed) : {result['fully_removed_count']}")
    print(f"{mode_label}Target/ cleaned (unmerged kept): {result['target_cleaned_count']}")
    print(f"{mode_label}Total affected worktrees      : {result['total_cleaned_count']}")
    print(f"{mode_label}Freed disk space              : {result['freed_mb']} MB ({result['freed_bytes']:,} bytes)")
    print(f"{mode_label}Remaining physical worktrees  : {result['remaining_count']}")

    if result["total_cleaned_count"] > 0 and not args.dry_run:
        print("\nSuccessfully optimized disk space while preserving 100% of unmerged source code.")


if __name__ == "__main__":
    main()
