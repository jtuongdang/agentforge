#!/usr/bin/env python3
"""Preview or remove old, clean worktrees integrated into their selected base branch.

Usage:
    python scripts/cleanup_worktrees.py [--max-age-hours 24] [--apply]
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
    parser.add_argument("--apply", action="store_true",
                        help="Apply safe removals. Without this option the command only previews.")
    args = parser.parse_args()

    dry_run = not args.apply
    mode_label = "[PREVIEW] " if dry_run else "[APPLY] "
    print(f"{mode_label}Checking old worktrees (age > {args.max_age_hours}h)...")
    print("Policy: remove only clean worktrees integrated into their selected base; retain all others.\n")

    result = prune_worktrees(max_age_hours=args.max_age_hours, dry_run=dry_run, clean_unmerged_targets_only=True)

    print(f"{mode_label}Fully removed (base contains task commit): {result['fully_removed_count']}")
    print(f"{mode_label}Build folders cleaned                  : {result['target_cleaned_count']}")
    print(f"{mode_label}Total affected worktrees      : {result['total_cleaned_count']}")
    print(f"{mode_label}Freed disk space              : {result['freed_mb']} MB ({result['freed_bytes']:,} bytes)")
    print(f"{mode_label}Remaining physical worktrees  : {result['remaining_count']}")

    if result["total_cleaned_count"] > 0 and args.apply:
        print("\nSuccessfully optimized disk space while preserving 100% of unmerged source code.")


if __name__ == "__main__":
    main()
