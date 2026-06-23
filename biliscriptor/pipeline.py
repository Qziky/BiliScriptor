from __future__ import annotations

import logging
import time
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
from .logging_config import log_event, log_exception
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
    elapsed_ms = extra.pop("_elapsed_ms", None)
    manifest["stages"][name] = {"status": "ok", "message": message, **extra}
    _log_stage_status("ok", name, message, elapsed_ms=elapsed_ms, **extra)


def _stage_missing(manifest: dict[str, Any], name: str, message: str = "", **extra: Any) -> None:
    elapsed_ms = extra.pop("_elapsed_ms", None)
    manifest["stages"][name] = {"status": "missing", "message": message, **extra}
    _log_stage_status("missing", name, message, elapsed_ms=elapsed_ms, **extra)


def _stage_skipped(manifest: dict[str, Any], name: str, message: str = "", **extra: Any) -> None:
    elapsed_ms = extra.pop("_elapsed_ms", None)
    manifest["stages"][name] = {"status": "skipped", "message": message, **extra}
    _log_stage_status("skipped", name, message, elapsed_ms=elapsed_ms, **extra)


def _start_stage(name: str, **fields: Any) -> float:
    log_event(
        "pipeline.stage_start",
        f"Stage started: {name}.",
        level=logging.INFO,
        stage=name,
        **fields,
    )
    return time.perf_counter()


def _elapsed_ms(started: float | None) -> int | None:
    if started is None:
        return None
    return round((time.perf_counter() - started) * 1000)


def _log_stage_status(status: str, name: str, message: str = "", *, elapsed_ms: int | None = None, **extra: Any) -> None:
    log_event(
        "pipeline.stage_status",
        message or f"Stage {name} finished with status {status}.",
        level=logging.INFO if status in {"ok", "skipped"} else logging.WARNING,
        stage=name,
        status=status,
        elapsed_ms=elapsed_ms,
        **extra,
    )


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
    log_event(
        "pipeline.stage_failure",
        error_message,
        level=logging.WARNING,
        stage=stage,
        status=status,
        error_code=error_code,
        page_index=page_index,
        cid=cid,
    )
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
    result = write_json(path, sanitize_for_manifest(manifest))
    log_event(
        "pipeline.manifest_written",
        "Manifest written.",
        level=logging.INFO,
        path=str(result),
        failure_count=len(manifest.get("failures") or []),
        stage_count=len(manifest.get("stages") or {}),
    )
    return result


def _error_parts(exc: Exception) -> tuple[str, int | str | None, str]:
    if isinstance(exc, BiliApiError):
        return exc.status, exc.code, exc.message
    return exc.__class__.__name__, None, str(exc)


def _page_index(page: dict[str, Any], fallback: int) -> int:
    return int(page.get("page") or fallback)


def parse_video(options: ParseOptions) -> ParseResult:
    parse_started = time.perf_counter()
    bvid = extract_bvid(options.url_or_bvid)
    out_dir = options.output_dir.resolve() / bvid
    cookie_file = options.cookie_file.resolve()
    client = BiliClient(cookie_file if cookie_file.exists() else None, rate_limit=options.rate_limit)
    log_event(
        "pipeline.parse_start",
        "Video parse started.",
        level=logging.INFO,
        bvid=bvid,
        output_dir=str(out_dir),
        cookie_file=str(cookie_file) if cookie_file.exists() else None,
        comment_pages=options.comment_pages,
        reply_pages=options.reply_pages,
        rate_limit=options.rate_limit,
        all_comments=options.all_comments,
        page=options.page,
        skip_subtitles=options.skip_subtitles,
        skip_danmaku=options.skip_danmaku,
        skip_comments=options.skip_comments,
        skip_streams=options.skip_streams,
        write_report=options.write_report,
    )
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
    log_event("pipeline.output_dir_ready", "Output directory ready.", level=logging.DEBUG, path=str(out_dir))

    metadata_started = _start_stage("metadata", bvid=bvid)
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
        _stage_ok(
            manifest,
            "metadata",
            f"Fetched {len(pages)} page(s).",
            page_count=len(pages),
            _elapsed_ms=_elapsed_ms(metadata_started),
        )
    except Exception as exc:
        status, code, message = _error_parts(exc)
        log_exception(
            "pipeline.metadata_failed",
            exc,
            "Metadata stage failed.",
            stage="metadata",
            status=status,
            error_code=code,
            bvid=bvid,
            elapsed_ms=_elapsed_ms(metadata_started),
        )
        _record_failure(manifest, stage="metadata", status=status, error_code=code, error_message=message)
        manifest["stages"]["metadata"] = {"status": "failed", "message": message}
        _log_stage_status("failed", "metadata", message, elapsed_ms=_elapsed_ms(metadata_started))
        manifest["finished_at"] = utc_now_iso()
        manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))
        log_event(
            "pipeline.parse_end",
            "Video parse ended after metadata failure.",
            level=logging.INFO,
            bvid=bvid,
            output_dir=str(out_dir),
            elapsed_ms=_elapsed_ms(parse_started),
            failure_count=len(manifest["failures"]),
        )
        return ParseResult(bvid=bvid, output_dir=out_dir, manifest=manifest)

    page_outputs = {
        "player": [],
        "streams": [],
        "subtitles": [],
        "danmaku": [],
    }
    subtitle_count = 0
    danmaku_count = 0
    stage_started = {
        "player": _start_stage("player", bvid=bvid, page_count=len(pages_to_fetch)),
        "streams": None if options.skip_streams else _start_stage("streams", bvid=bvid, page_count=len(pages_to_fetch)),
        "subtitles": None
        if options.skip_subtitles
        else _start_stage("subtitles", bvid=bvid, page_count=len(pages_to_fetch)),
        "danmaku": None if options.skip_danmaku else _start_stage("danmaku", bvid=bvid, page_count=len(pages_to_fetch)),
    }

    player_cache: dict[int, dict[str, Any]] = {}
    for fallback, page in enumerate(pages_to_fetch, 1):
        page_index = _page_index(page, fallback)
        cid = int(page["cid"])
        log_event(
            "pipeline.page_start",
            "Page processing started.",
            level=logging.INFO,
            bvid=bvid,
            aid=aid,
            page_index=page_index,
            cid=cid,
            duration=page.get("duration"),
        )
        try:
            player_data = fetch_player_v2(client, bvid=bvid, aid=aid, cid=cid)
            player_cache[cid] = player_data
            path = write_json(out_dir / "player" / f"page_{page_index:03}.json", player_data)
            page_outputs["player"].append(str(path))
            log_event(
                "pipeline.player_page_success",
                "Player data fetched for page.",
                level=logging.DEBUG,
                bvid=bvid,
                aid=aid,
                page_index=page_index,
                cid=cid,
                path=str(path),
            )
        except Exception as exc:
            status, code, message = _error_parts(exc)
            log_exception(
                "pipeline.player_page_failed",
                exc,
                "Player data fetch failed for page.",
                stage="player",
                status=status,
                error_code=code,
                bvid=bvid,
                page_index=page_index,
                cid=cid,
            )
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
                summary = stream_data.get("summary") or {}
                log_event(
                    "pipeline.streams_page_success",
                    "Stream candidates fetched for page.",
                    level=logging.DEBUG,
                    bvid=bvid,
                    page_index=page_index,
                    cid=cid,
                    path=str(path),
                    dash_video_count=summary.get("dash_video_count"),
                    dash_audio_count=summary.get("dash_audio_count"),
                    durl_count=summary.get("durl_count"),
                )
            except Exception as exc:
                status, code, message = _error_parts(exc)
                log_exception(
                    "pipeline.streams_page_failed",
                    exc,
                    "Stream fetch failed for page.",
                    stage="streams",
                    status=status,
                    error_code=code,
                    bvid=bvid,
                    page_index=page_index,
                    cid=cid,
                )
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
                log_event(
                    "pipeline.subtitle_items",
                    "Subtitle items discovered.",
                    level=logging.DEBUG,
                    bvid=bvid,
                    page_index=page_index,
                    cid=cid,
                    count=len(subtitle_items),
                    languages=[item.get("lan") or item.get("lan_doc") for item in subtitle_items],
                )
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
                        log_event(
                            "pipeline.subtitle_item_success",
                            "Subtitle item fetched and normalized.",
                            level=logging.DEBUG,
                            bvid=bvid,
                            aid=aid,
                            page_index=page_index,
                            cid=cid,
                            item_index=item_index,
                            language=item.get("lan"),
                            language_doc=item.get("lan_doc"),
                            event_count=len(rows),
                            files={
                                "raw": str(raw_path),
                                "jsonl": str(jsonl_path),
                                "srt": str(srt_path),
                                "txt": str(txt_path),
                            },
                        )
                    except Exception as exc:
                        status, code, message = _error_parts(exc)
                        log_exception(
                            "pipeline.subtitle_item_failed",
                            exc,
                            "Subtitle item fetch or normalization failed.",
                            stage="subtitles",
                            status=status,
                            error_code=code,
                            bvid=bvid,
                            page_index=page_index,
                            cid=cid,
                            item_index=item_index,
                        )
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
            log_event(
                "pipeline.danmaku_page_success",
                "Danmaku fetched for page.",
                level=logging.DEBUG,
                bvid=bvid,
                aid=aid,
                page_index=page_index,
                cid=cid,
                event_count=len(rows),
                path=str(path),
            )
        except Exception as exc:
            status, code, message = _error_parts(exc)
            log_exception(
                "pipeline.danmaku_page_failed",
                exc,
                "Danmaku fetch failed for page.",
                stage="danmaku",
                status=status,
                error_code=code,
                bvid=bvid,
                page_index=page_index,
                cid=cid,
            )
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
        _stage_ok(
            manifest,
            "player",
            f"Fetched player data for {len(page_outputs['player'])} page(s).",
            _elapsed_ms=_elapsed_ms(stage_started["player"]),
        )
    else:
        _stage_missing(
            manifest,
            "player",
            "No player data was fetched.",
            _elapsed_ms=_elapsed_ms(stage_started["player"]),
        )
    if options.skip_streams:
        _stage_skipped(manifest, "streams", "Skipped by option.")
    elif page_outputs["streams"]:
        _stage_ok(
            manifest,
            "streams",
            f"Fetched stream data for {len(page_outputs['streams'])} page(s).",
            _elapsed_ms=_elapsed_ms(stage_started["streams"]),
        )
    else:
        _stage_missing(
            manifest,
            "streams",
            "No stream data was fetched.",
            _elapsed_ms=_elapsed_ms(stage_started["streams"]),
        )
    if options.skip_subtitles:
        _stage_skipped(manifest, "subtitles", "Skipped by option.")
    elif page_outputs["subtitles"]:
        _stage_ok(
            manifest,
            "subtitles",
            f"Fetched {subtitle_count} subtitle event(s).",
            event_count=subtitle_count,
            _elapsed_ms=_elapsed_ms(stage_started["subtitles"]),
        )
    else:
        _stage_missing(
            manifest,
            "subtitles",
            "No subtitles were fetched.",
            _elapsed_ms=_elapsed_ms(stage_started["subtitles"]),
        )
    if options.skip_danmaku:
        _stage_skipped(manifest, "danmaku", "Skipped by option.")
    elif page_outputs["danmaku"]:
        _stage_ok(
            manifest,
            "danmaku",
            f"Fetched {danmaku_count} danmaku event(s).",
            event_count=danmaku_count,
            _elapsed_ms=_elapsed_ms(stage_started["danmaku"]),
        )
    else:
        _stage_missing(
            manifest,
            "danmaku",
            "No danmaku was fetched.",
            _elapsed_ms=_elapsed_ms(stage_started["danmaku"]),
        )

    manifest["files"].update(page_outputs)

    if options.skip_comments:
        _stage_skipped(manifest, "comments", "Skipped by option.")
    else:
        comments_started = _start_stage(
            "comments",
            bvid=bvid,
            aid=aid,
            comment_pages=options.comment_pages,
            reply_pages=options.reply_pages,
            all_comments=options.all_comments,
        )
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
            log_event(
                "pipeline.comments_written",
                "Comment files written.",
                level=logging.DEBUG,
                bvid=bvid,
                comment_count=len(comment_data["comments"]),
                tree_count=len(comment_data["tree"]),
                truncation_count=len(truncations),
                files={"jsonl": str(comments_path), "tree": str(tree_path)},
            )
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
                    _elapsed_ms=_elapsed_ms(comments_started),
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
                _stage_ok(
                    manifest,
                    "comments",
                    f"Fetched {len(comment_data['comments'])} comment row(s).",
                    comment_count=len(comment_data["comments"]),
                    tree_count=len(comment_data["tree"]),
                    _elapsed_ms=_elapsed_ms(comments_started),
                )
        except Exception as exc:
            status, code, message = _error_parts(exc)
            log_exception(
                "pipeline.comments_failed",
                exc,
                "Comments stage failed.",
                stage="comments",
                status=status,
                error_code=code,
                bvid=bvid,
                elapsed_ms=_elapsed_ms(comments_started),
            )
            _record_failure(manifest, stage="comments", status=status, error_code=code, error_message=message)
            manifest["stages"]["comments"] = {"status": "failed", "message": message}
            _log_stage_status("failed", "comments", message, elapsed_ms=_elapsed_ms(comments_started))

    manifest["finished_at"] = utc_now_iso()
    manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))

    if options.write_report:
        report_started = _start_stage("report", bvid=bvid, output_dir=str(out_dir))
        try:
            report_path = write_report(out_dir)
            manifest["files"]["report"] = str(report_path)
            _stage_ok(
                manifest,
                "report",
                "Generated report.md.",
                path=str(report_path),
                _elapsed_ms=_elapsed_ms(report_started),
            )
        except Exception as exc:
            status, code, message = _error_parts(exc)
            log_exception(
                "pipeline.report_failed",
                exc,
                "Report stage failed.",
                stage="report",
                status=status,
                error_code=code,
                bvid=bvid,
                elapsed_ms=_elapsed_ms(report_started),
            )
            _record_failure(manifest, stage="report", status=status, error_code=code, error_message=message)
            manifest["stages"]["report"] = {"status": "failed", "message": message}
            _log_stage_status("failed", "report", message, elapsed_ms=_elapsed_ms(report_started))

    manifest["files"]["manifest"] = str(_write_manifest(out_dir / "manifest.json", manifest))
    log_event(
        "pipeline.parse_end",
        "Video parse finished.",
        level=logging.INFO,
        bvid=bvid,
        output_dir=str(out_dir),
        elapsed_ms=_elapsed_ms(parse_started),
        failure_count=len(manifest["failures"]),
        stage_count=len(manifest["stages"]),
    )
    return ParseResult(bvid=bvid, output_dir=out_dir, manifest=manifest)


def parse_subtitles_only(
    url_or_bvid: str,
    *,
    cookie_file: Path = Path("runtime/bilibili_cookies.txt"),
    output_dir: Path = Path("output"),
    page: int | None = None,
) -> ParseResult:
    log_event(
        "pipeline.subtitles_only_start",
        "Subtitles-only parse requested.",
        level=logging.INFO,
        url_or_bvid=url_or_bvid,
        cookie_file=str(cookie_file),
        output_dir=str(output_dir),
        page=page,
    )
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
