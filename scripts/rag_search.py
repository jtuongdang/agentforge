#!/usr/bin/env python3
"""Search the indexed project docs: scripts/rag_search.py "khóa đăng nhập" [--repo clinic-desktop] [--limit 5]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import rag  # noqa: E402
from app.git_ops import repository  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--repo", default="clinic-desktop")
parser.add_argument("--limit", type=int, default=5)
args = parser.parse_args()
rag.index_repository(args.repo, repository(args.repo)["path"])
for hit in rag.search(args.repo, args.query, args.limit):
    print(f"\n=== {hit['path']} › {hit['heading']}  (score {hit['score']})\n{hit['text'][:1200]}")
