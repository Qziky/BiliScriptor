from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .login import run_login
from .pipeline import ParseOptions, parse_subtitles_only, parse_video
from .report import write_report


DEFAULT_COOKIE_FILE = "bilibili_cookies.txt"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_QR_FILE = "bilibili_login_qr.svg"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biliscriptor",
        description="BiliScriptor / 哔稿匠: parse Bilibili videos into local data packages.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Scan a Bilibili QR code and save local cookies.")
    login.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    login.add_argument("--qr-file", default=DEFAULT_QR_FILE)
    login.add_argument("--no-open", action="store_true")
    login.add_argument("--generate-only", action="store_true")
    login.add_argument("--poll-interval", type=float, default=2.0)

    parse = subparsers.add_parser("parse", help="Parse one Bilibili video URL or BV id.")
    parse.add_argument("url_or_bvid")
    parse.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    parse.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parse.add_argument("--comment-pages", type=int, default=1)
    parse.add_argument("--reply-pages", type=int, default=1)
    parse.add_argument("--rate-limit", type=float, default=1.0)
    parse.add_argument("--all-comments", action="store_true")
    parse.add_argument("--page", type=int, default=None, help="Only parse one 1-based page index.")
    parse.add_argument("--skip-subtitles", action="store_true")
    parse.add_argument("--skip-danmaku", action="store_true")
    parse.add_argument("--skip-comments", action="store_true")
    parse.add_argument("--skip-streams", action="store_true")
    parse.add_argument("--no-report", action="store_true")

    report = subparsers.add_parser("report", help="Generate report.md from an existing output/BVxxxx package.")
    report.add_argument("package_dir")

    subtitles = subparsers.add_parser("subtitles", help="Fetch subtitles only for one Bilibili video URL or BV id.")
    subtitles.add_argument("url_or_bvid")
    subtitles.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    subtitles.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    subtitles.add_argument("--page", type=int, default=None, help="Only fetch one 1-based page index.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "login":
        return run_login(
            cookie_file=Path(args.cookie_file),
            qr_file=Path(args.qr_file),
            no_open=args.no_open,
            generate_only=args.generate_only,
            poll_interval=args.poll_interval,
        )

    if args.command == "parse":
        result = parse_video(
            ParseOptions(
                url_or_bvid=args.url_or_bvid,
                cookie_file=Path(args.cookie_file),
                output_dir=Path(args.output_dir),
                comment_pages=args.comment_pages,
                reply_pages=args.reply_pages,
                rate_limit=args.rate_limit,
                all_comments=args.all_comments,
                page=args.page,
                skip_subtitles=args.skip_subtitles,
                skip_danmaku=args.skip_danmaku,
                skip_comments=args.skip_comments,
                skip_streams=args.skip_streams,
                write_report=not args.no_report,
            )
        )
        failures = result.manifest.get("failures") or []
        print(f"Video: {result.bvid}")
        print(f"Output: {result.output_dir}")
        print(f"Failures: {len(failures)}")
        failed_stages = [
            name for name, stage in (result.manifest.get("stages") or {}).items() if stage.get("status") == "failed"
        ]
        return 1 if failed_stages and "metadata" in failed_stages else 0

    if args.command == "report":
        path = write_report(Path(args.package_dir).resolve())
        print(f"Report: {path}")
        return 0

    if args.command == "subtitles":
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

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
