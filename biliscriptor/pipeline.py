from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import BiliApiError, BiliClient
from .extractors import (
    fetch_comments,
    fetch_danmaku,
    fetch_player_v2,
    fetch_streams,
    fetch_subtitle,
    fetch_video_info,
    get_subtitle_items,
    normalize_subtitle_rows,
    subtitle_stem,
)
from .report import write_report
from .utils import extract_bvid, sanitize_for_manifest, utc_now_iso, write_json, write_jsonl, write_srt, write_txt


TOOL_VERSION = "0.1.0"


@dataclass
class ParseOptions:
    url_or_bvid: str
    cookie_file: Path = Path("runtime/bilibili_cookies.txt")
    output_dir: Path = Path("output")
    comment_pages: int = 1
    reply_pages: int = 1
    rate_limit: float = 1.0
    all_comments: bool = False
    page: int | None = None
    skip_subtitles: bool = False
    skip_danmaku: bool = False
    skip_comments: bool = False
    skip_streams: bool = False
    write_report: bool = True


@dataclass
class ParseResult:
    bvid: str
    output_dir: Path
    manifest: dict[str, Any]


def _stage_ok(manifest: dict[str, Any], name: str, message: str = "", **extra: Any) -> None:
    manifest["stages"][name] = {"status": "ok", "message": message, **extra}


def _stage_missing(manifest: dict[str, Any], name: str, message: str = "", **extra: Any) -> None:
    manifest["stages"][name] = {"status": "missing", "message": message, **extra}


def _stage_skipped(manifest: dict[str, Any], name: str, message: str = "", **extra: Any) -> None:
    manifest["stages"][name] = {"status": "skipped", "message": message, **extra}


def _record_failure(
    manifest: dict[str, Any],
    *,
    stage: str,
    status: str,
    error_message: str,
    error_code: int | str | None = None,
    page_index: int | None = None,
    cid: int | None = None,
) -> None:
    manifest["failures"].append(
        {
            "stage": stage,
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "page_index": page_index,
            "cid": cid,
            "fetched_at": utc_now_iso(),
        }
    )


def _write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    return write_json(path, sanitize_for_manifest(manifest))


def _error_parts(exc: Exception) -> tuple[str, int | str | None, str]:
    if isinstance(exc, BiliApiError):
        return exc.status, exc.code, exc.message
    return exc.__class__.__name__, None, str(exc)


def _page_index(page: dict[str, Any], fallback: int) -> int:
    return int(page.get("page") or fallback)


def parse_video(options: ParseOptions) -> ParseResult:
    bvid = extract_bvid(options.url_or_bvid)
    out_dir = options.output_dir.resolve() / bvid
    cookie_file = options.cookie_file.resolve()
    client = BiliClient(cookie_file if cookie_file.exists() else None, rate_limit=options.rate_limit)
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "tool": "BiliScriptor",
        "tool_version": TOOL_VERSION,
        "bvid": bvid,
        "input": options.url_or_bvid,
        "started_at": utc_now_iso(),
        "finished_at": None,
        "config": {
            "cookie_file": str(cookie_file) if cookie_file.exists() else None,
            "cookie_names": client.cookie_names,
            "comment_pages": options.comment_pages,
            "reply_pages": options.reply_pages,
            "rate_limit": options.rate_limit,
            "all_comments": options.all_comments,
            "page": options.page,
            "download_media": False,
        },
        "stages": {},
        "files": {},
        "failures": [],
    }
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_info = fetch_video_info(client, bvid)
        aid = int(video_info["aid"])
        pages = list(video_info.get("pages") or [])
        if options.page is not None:
            if options.page < 1 or options.page > len(pages):
                raise ValueError(f"--page must be between 1 and {len(pages)}.")
            pages_to_fetch = [pages[options.page - 1]]
        else:
            pages_to_fetch = pages
        manifest["aid"] = aid
        manifest["files"]["video"] = str(write_json(out_dir / "video.json", video_info))
        manifest["files"]["pages"] = str(write_json(out_dir / "pages.json", pages))
        _stage_ok(manifest, "metadata", f"Fetched {len(pages)} page(s).", page_count=len(pages))
    except Exception as exc:
        status, code, message = _error_parts(exc)
        _record_failure(manifest, stage="metadata", status=status, error_code=code, error_message=message)
        manifest["stages"]["metadata"] = {"status": "failed", "message": message}
        manifest["finished_at"] = utc_now_iso()
        manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))
        return ParseResult(bvid=bvid, output_dir=out_dir, manifest=manifest)

    page_outputs = {
        "player": [],
        "streams": [],
        "subtitles": [],
        "danmaku": [],
    }
    subtitle_count = 0
    danmaku_count = 0

    player_cache: dict[int, dict[str, Any]] = {}
    for fallback, page in enumerate(pages_to_fetch, 1):
        page_index = _page_index(page, fallback)
        cid = int(page["cid"])
        try:
            player_data = fetch_player_v2(client, bvid=bvid, aid=aid, cid=cid)
            player_cache[cid] = player_data
            path = write_json(out_dir / "player" / f"page_{page_index:03}.json", player_data)
            page_outputs["player"].append(str(path))
        except Exception as exc:
            status, code, message = _error_parts(exc)
            _record_failure(
                manifest,
                stage="player",
                status=status,
                error_code=code,
                error_message=message,
                page_index=page_index,
                cid=cid,
            )

        if not options.skip_streams:
            try:
                stream_data = fetch_streams(client, bvid=bvid, cid=cid)
                path = write_json(out_dir / "streams" / f"page_{page_index:03}.json", stream_data)
                page_outputs["streams"].append(str(path))
            except Exception as exc:
                status, code, message = _error_parts(exc)
                _record_failure(
                    manifest,
                    stage="streams",
                    status=status,
                    error_code=code,
                    error_message=message,
                    page_index=page_index,
                    cid=cid,
                )

        player_data = player_cache.get(cid)
        if not options.skip_subtitles:
            if not player_data:
                _record_failure(
                    manifest,
                    stage="subtitles",
                    status="player_missing",
                    error_code=None,
                    error_message="Player data was not available for subtitle discovery.",
                    page_index=page_index,
                    cid=cid,
                )
            else:
                subtitle_items = get_subtitle_items(player_data)
                if not subtitle_items:
                    _record_failure(
                        manifest,
                        stage="subtitles",
                        status="subtitle_missing",
                        error_code=None,
                        error_message="The player API returned no subtitle items.",
                        page_index=page_index,
                        cid=cid,
                    )
                for item_index, item in enumerate(subtitle_items, 1):
                    try:
                        raw_url = item.get("subtitle_url") or item.get("url") or ""
                        if not raw_url:
                            raise ValueError("empty subtitle_url")
                        raw = fetch_subtitle(client, subtitle_url=raw_url, bvid=bvid)
                        body = list(raw.get("body") or [])
                        rows = normalize_subtitle_rows(
                            body,
                            item=item,
                            bvid=bvid,
                            aid=aid,
                            page_index=page_index,
                            cid=cid,
                        )
                        stem = subtitle_stem(page_index, item_index, item)
                        raw_path = write_json(out_dir / "subtitles" / f"{stem}.raw.json", raw)
                        jsonl_path = write_jsonl(out_dir / "subtitles" / f"{stem}.jsonl", rows)
                        srt_path = write_srt(out_dir / "subtitles" / f"{stem}.srt", rows)
                        txt_path = write_txt(out_dir / "subtitles" / f"{stem}.txt", rows)
                        page_outputs["subtitles"].append(
                            {
                                "page_index": page_index,
                                "cid": cid,
                                "language": item.get("lan"),
                                "language_doc": item.get("lan_doc"),
                                "event_count": len(rows),
                                "files": {
                                    "raw": str(raw_path),
                                    "jsonl": str(jsonl_path),
                                    "srt": str(srt_path),
                                    "txt": str(txt_path),
                                },
                            }
                        )
                        subtitle_count += len(rows)
                    except Exception as exc:
                        status, code, message = _error_parts(exc)
                        _record_failure(
                            manifest,
                            stage="subtitles",
                            status=status,
                            error_code=code,
                            error_message=message,
                            page_index=page_index,
                            cid=cid,
                        )

        if options.skip_danmaku:
            continue
        try:
            rows = fetch_danmaku(
                client,
                bvid=bvid,
                aid=aid,
                cid=cid,
                page_index=page_index,
                duration=int(page.get("duration") or 0),
            )
            path = write_jsonl(out_dir / "danmaku" / f"page_{page_index:03}.current.jsonl", rows)
            page_outputs["danmaku"].append(str(path))
            danmaku_count += len(rows)
        except Exception as exc:
            status, code, message = _error_parts(exc)
            _record_failure(
                manifest,
                stage="danmaku",
                status=status,
                error_code=code,
                error_message=message,
                page_index=page_index,
                cid=cid,
            )

    if page_outputs["player"]:
        _stage_ok(manifest, "player", f"Fetched player data for {len(page_outputs['player'])} page(s).")
    else:
        _stage_missing(manifest, "player", "No player data was fetched.")
    if options.skip_streams:
        _stage_skipped(manifest, "streams", "Skipped by option.")
    elif page_outputs["streams"]:
        _stage_ok(manifest, "streams", f"Fetched stream data for {len(page_outputs['streams'])} page(s).")
    else:
        _stage_missing(manifest, "streams", "No stream data was fetched.")
    if options.skip_subtitles:
        _stage_skipped(manifest, "subtitles", "Skipped by option.")
    elif page_outputs["subtitles"]:
        _stage_ok(manifest, "subtitles", f"Fetched {subtitle_count} subtitle event(s).")
    else:
        _stage_missing(manifest, "subtitles", "No subtitles were fetched.")
    if options.skip_danmaku:
        _stage_skipped(manifest, "danmaku", "Skipped by option.")
    elif page_outputs["danmaku"]:
        _stage_ok(manifest, "danmaku", f"Fetched {danmaku_count} danmaku event(s).")
    else:
        _stage_missing(manifest, "danmaku", "No danmaku was fetched.")

    manifest["files"].update(page_outputs)

    if options.skip_comments:
        _stage_skipped(manifest, "comments", "Skipped by option.")
    else:
        try:
            comment_data = fetch_comments(
                client,
                bvid=bvid,
                aid=aid,
                comment_pages=options.comment_pages,
                reply_pages=options.reply_pages,
                all_comments=options.all_comments,
            )
            comments_path = write_jsonl(out_dir / "comments" / "comments.jsonl", comment_data["comments"])
            tree_path = write_json(out_dir / "comments" / "tree.json", comment_data["tree"])
            manifest["files"]["comments"] = {"jsonl": str(comments_path), "tree": str(tree_path)}
            truncations = list(comment_data.get("truncations") or [])
            if truncations:
                first = truncations[0]
                _stage_ok(
                    manifest,
                    "comments",
                    f"Fetched {len(comment_data['comments'])} comment row(s), truncated by safety cap.",
                    truncated=True,
                    truncations=truncations,
                    cap_kind=first.get("cap_kind"),
                    cap_pages=first.get("cap_pages"),
                )
                for item in truncations:
                    _record_failure(
                        manifest,
                        stage="comments",
                        status="truncated",
                        error_code=item.get("cap_kind"),
                        error_message=(
                            f"Comment fetching stopped at safety cap "
                            f"{item.get('cap_pages')} page(s)."
                        ),
                    )
            else:
                _stage_ok(manifest, "comments", f"Fetched {len(comment_data['comments'])} comment row(s).")
        except Exception as exc:
            status, code, message = _error_parts(exc)
            _record_failure(manifest, stage="comments", status=status, error_code=code, error_message=message)
            manifest["stages"]["comments"] = {"status": "failed", "message": message}

    manifest["finished_at"] = utc_now_iso()
    manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))

    if options.write_report:
        try:
            report_path = write_report(out_dir)
            manifest["files"]["report"] = str(report_path)
            _stage_ok(manifest, "report", "Generated report.md.")
        except Exception as exc:
            status, code, message = _error_parts(exc)
            _record_failure(manifest, stage="report", status=status, error_code=code, error_message=message)
            manifest["stages"]["report"] = {"status": "failed", "message": message}

    manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))
    return ParseResult(bvid=bvid, output_dir=out_dir, manifest=manifest)


def parse_subtitles_only(
    url_or_bvid: str,
    *,
    cookie_file: Path = Path("runtime/bilibili_cookies.txt"),
    output_dir: Path = Path("output"),
    page: int | None = None,
) -> ParseResult:
    return parse_video(
        ParseOptions(
            url_or_bvid=url_or_bvid,
            cookie_file=cookie_file,
            output_dir=output_dir,
            page=page,
            skip_danmaku=True,
            skip_comments=True,
            skip_streams=True,
            write_report=False,
        )
    )
