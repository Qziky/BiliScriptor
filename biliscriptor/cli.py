from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .login import run_login
from .logging_config import add_logging_arguments, configure_logging, log_event, log_exception, shutdown_logging
from .pipeline import ParseOptions, parse_subtitles_only, parse_video
from .report import write_report


DEFAULT_COOKIE_FILE = "runtime/bilibili_cookies.txt"
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_QR_FILE = "runtime/bilibili_login_qr.svg"


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


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
    add_logging_arguments(login)

    parse = subparsers.add_parser("parse", help="Parse one Bilibili video URL or BV id.")
    parse.add_argument("url_or_bvid")
    parse.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    parse.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parse.add_argument("--comment-pages", type=positive_int, default=1)
    parse.add_argument("--reply-pages", type=positive_int, default=1)
    parse.add_argument("--rate-limit", type=non_negative_float, default=1.0)
    parse.add_argument("--all-comments", action="store_true")
    parse.add_argument("--page", type=positive_int, default=None, help="Only parse one 1-based page index.")
    parse.add_argument("--skip-subtitles", action="store_true")
    parse.add_argument("--skip-danmaku", action="store_true")
    parse.add_argument("--skip-comments", action="store_true")
    parse.add_argument("--skip-streams", action="store_true")
    parse.add_argument("--no-report", action="store_true")
    add_logging_arguments(parse)

    report = subparsers.add_parser("report", help="Generate report.md from an existing output/BVxxxx package.")
    report.add_argument("package_dir")
    add_logging_arguments(report)

    subtitles = subparsers.add_parser("subtitles", help="Fetch subtitles only for one Bilibili video URL or BV id.")
    subtitles.add_argument("url_or_bvid")
    subtitles.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    subtitles.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    subtitles.add_argument("--page", type=positive_int, default=None, help="Only fetch one 1-based page index.")
    add_logging_arguments(subtitles)
    return parser


def _args_for_log(args: argparse.Namespace) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(args).items()
        if key not in {"log_dir", "log_level", "log_format", "no_file_log", "log_to_stderr"}
    }


def _print_log_paths(paths: tuple[Path, ...]) -> None:
    if not paths:
        return
    joined = ", ".join(str(path) for path in paths)
    print(f"Logs: {joined}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging_config = configure_logging(
        command=args.command,
        log_dir=Path(args.log_dir),
        log_level=args.log_level,
        log_format=args.log_format,
        no_file_log=args.no_file_log,
        log_to_stderr=args.log_to_stderr,
    )
    exit_code = 0
    log_event(
        "cli.command_start",
        "CLI command started.",
        level=logging.INFO,
        args=_args_for_log(args),
    )

    try:
        if args.command == "login":
            exit_code = run_login(
                cookie_file=Path(args.cookie_file),
                qr_file=Path(args.qr_file),
                no_open=args.no_open,
                generate_only=args.generate_only,
                poll_interval=args.poll_interval,
            )
            return exit_code

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
                name
                for name, stage in (result.manifest.get("stages") or {}).items()
                if stage.get("status") == "failed"
            ]
            exit_code = 1 if failed_stages and "metadata" in failed_stages else 0
            return exit_code

        if args.command == "report":
            path = write_report(Path(args.package_dir).resolve())
            print(f"Report: {path}")
            exit_code = 0
            return exit_code

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
            exit_code = 1 if (result.manifest.get("stages") or {}).get("metadata", {}).get("status") == "failed" else 0
            return exit_code

        parser.print_help(sys.stderr)
        exit_code = 2
        return exit_code
    except Exception as exc:
        log_exception("cli.command_exception", exc, "CLI command failed with an unhandled exception.")
        raise
    finally:
        log_event("cli.command_end", "CLI command finished.", level=logging.INFO, exit_code=exit_code)
        shutdown_logging()
        _print_log_paths(logging_config.paths)


if __name__ == "__main__":
    raise SystemExit(main())
