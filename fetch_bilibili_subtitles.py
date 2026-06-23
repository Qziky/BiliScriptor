# -*- coding: utf-8 -*-
"""Backward-compatible subtitle-only entrypoint.

Prefer the new CLI:
    python -m bili_md.cli parse <BV-or-url>
    bili-md parse <BV-or-url>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bili_md.pipeline import parse_subtitles_only


DEFAULT_URL = "https://www.bilibili.com/video/BV1QEVY6jEYv/"
DEFAULT_COOKIE_FILE = "bilibili_cookies.txt"
DEFAULT_OUTPUT_DIR = "output"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Bilibili subtitles.")
    parser.add_argument("url_or_bvid", nargs="?", default=DEFAULT_URL, help="Bilibili video URL or BV id.")
    parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help="Cookie file from login_bilibili_qr.py.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output root directory.")
    parser.add_argument("--page", type=int, default=None, help="Only fetch one 1-based page index.")
    args = parser.parse_args()

    result = parse_subtitles_only(
        args.url_or_bvid,
        cookie_file=Path(args.cookie_file),
        output_dir=Path(args.output_dir),
        page=args.page,
    )
    stage = (result.manifest.get("stages") or {}).get("subtitles") or {}
    failures = result.manifest.get("failures") or []
    print(f"Video: {result.bvid}")
    print(f"Output: {result.output_dir}")
    print(f"Subtitles: {stage.get('status')} {stage.get('message') or ''}")
    print(f"Failures: {len(failures)}")
    return 1 if (result.manifest.get("stages") or {}).get("metadata", {}).get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
