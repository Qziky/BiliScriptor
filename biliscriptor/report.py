from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import write_jsonl


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def _count_jsonl(path: Path | None) -> int:
    if not path or not path.is_file():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _format_count(value: Any) -> str:
    if value is None or value == "":
        return "未知"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_duration(seconds: Any) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "未知"
    hours, rem = divmod(max(0, total), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _format_epoch(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return "未知"
    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _format_ms(value: Any) -> str:
    if value is None:
        return "无时间点"
    try:
        total_ms = int(value)
    except (TypeError, ValueError):
        return "无时间点"
    total_ms = max(0, total_ms)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"
    return f"{minutes:02}:{seconds:02}.{ms:03}"


def _clean_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _status_label(status: Any) -> str:
    labels = {
        "ok": "成功",
        "missing": "未抓到",
        "skipped": "已跳过",
        "failed": "失败",
    }
    return labels.get(str(status or ""), str(status or "未知"))


def _stage_label(name: str) -> str:
    labels = {
        "metadata": "视频元数据",
        "player": "播放器信息",
        "streams": "播放流候选",
        "subtitles": "字幕",
        "danmaku": "当前弹幕",
        "comments": "评论",
        "report": "阅读报告",
    }
    return labels.get(name, name)


def _subtitle_entries(output_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in ((manifest.get("files") or {}).get("subtitles") or []):
        if not isinstance(item, dict):
            continue
        files = item.get("files") or {}
        jsonl = Path(files.get("jsonl") or "")
        if not jsonl.is_absolute():
            jsonl = output_dir / jsonl
        entries.append(
            {
                "language": item.get("language") or "",
                "language_doc": item.get("language_doc") or "",
                "event_count": item.get("event_count"),
                "path": jsonl,
                "page_index": item.get("page_index"),
                "cid": item.get("cid"),
            }
        )
    if entries:
        return entries
    for path in sorted((output_dir / "subtitles").glob("*.jsonl")):
        entries.append(
            {
                "language": path.stem.split("_")[-1],
                "language_doc": "",
                "event_count": _count_jsonl(path),
                "path": path,
                "page_index": None,
                "cid": None,
            }
        )
    return entries


def _preferred_subtitle_path(entries: list[dict[str, Any]]) -> Path | None:
    if not entries:
        return None

    def score(entry: dict[str, Any]) -> int:
        language = str(entry.get("language") or "").lower()
        language_doc = str(entry.get("language_doc") or "").lower()
        path = str(entry.get("path") or "").lower()
        haystack = f"{language} {language_doc} {path}"
        if "ai-zh" in haystack:
            return 0
        if "zh" in haystack or "中文" in haystack:
            return 1
        return 2

    preferred = sorted(entries, key=score)[0]
    path = preferred.get("path")
    return path if isinstance(path, Path) and path.is_file() else None


def _jsonl_total(paths: list[Path]) -> int:
    return sum(_count_jsonl(path) for path in paths)


def _stream_summary(output_dir: Path) -> str:
    stream_files = sorted((output_dir / "streams").glob("*.json"))
    if not stream_files:
        return "未生成播放流候选文件"
    video_count = 0
    audio_count = 0
    qualities: set[str] = set()
    for path in stream_files:
        data = _load_json(path, {})
        summary = data.get("summary") or {}
        video_items = summary.get("video") or summary.get("videos") or []
        audio_items = summary.get("audio") or summary.get("audios") or []
        if isinstance(video_items, list):
            video_count += len(video_items)
            for item in video_items:
                if isinstance(item, dict):
                    quality = item.get("quality") or item.get("quality_desc") or item.get("id")
                    if quality:
                        qualities.add(str(quality))
        if isinstance(audio_items, list):
            audio_count += len(audio_items)
    detail = f"{len(stream_files)} 个分 P 文件"
    if video_count or audio_count:
        detail += f"，视频候选 {_format_count(video_count)} 条，音频候选 {_format_count(audio_count)} 条"
    if qualities:
        detail += f"，清晰度/编码标识：{', '.join(sorted(qualities)[:8])}"
    return detail


def _stat_summary(stat: dict[str, Any]) -> str:
    if not stat:
        return "未知"
    fields = [
        ("播放", "view"),
        ("弹幕", "danmaku"),
        ("评论", "reply"),
        ("点赞", "like"),
        ("投币", "coin"),
        ("收藏", "favorite"),
        ("分享", "share"),
    ]
    parts = []
    for label, key in fields:
        value = stat.get(key)
        if value is not None:
            parts.append(f"{label} {_format_count(value)}")
    return "，".join(parts) if parts else "未知"


def build_report(output_dir: Path) -> str:
    video = _load_json(output_dir / "video.json", {})
    pages = _load_json(output_dir / "pages.json", [])
    manifest = _load_json(output_dir / "manifest.json", {})
    config = manifest.get("config") or {}
    stat = video.get("stat") or {}
    owner = video.get("owner") or {}
    subtitle_entries = _subtitle_entries(output_dir, manifest)
    subtitle_count = 0
    for entry in subtitle_entries:
        try:
            subtitle_count += int(entry.get("event_count") or _count_jsonl(entry.get("path")))
        except (TypeError, ValueError):
            continue
    subtitle_languages = [
        str(entry.get("language_doc") or entry.get("language") or "未知语言")
        for entry in subtitle_entries
    ]
    danmaku_files = sorted((output_dir / "danmaku").glob("*.jsonl"))
    danmaku_count = _jsonl_total(danmaku_files)
    comments_path = output_dir / "comments" / "comments.jsonl"
    comment_count = _count_jsonl(comments_path)
    comments = _load_jsonl(comments_path, limit=10)
    subtitle_sample_path = _preferred_subtitle_path(subtitle_entries)
    subtitle_samples = _load_jsonl(subtitle_sample_path, limit=10) if subtitle_sample_path else []
    danmaku_samples = _load_jsonl(danmaku_files[0], limit=10) if danmaku_files else []
    title = video.get("title") or manifest.get("bvid") or output_dir.name
    bvid = video.get("bvid") or manifest.get("bvid") or output_dir.name
    aid = video.get("aid") or manifest.get("aid") or ""
    cookie_names = ", ".join(str(name) for name in (config.get("cookie_names") or [])) or "未记录"
    comment_scope = (
        "全量评论"
        if config.get("all_comments")
        else f"{config.get('comment_pages', 1)} 页主评论 + 每条 {config.get('reply_pages', 1)} 页楼中楼"
    )
    lines = [
        f"# {title}",
        "",
        "## 快速概览",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| BV / AV | `{bvid}` / `{aid}` |",
        f"| UP 主 | {owner.get('name') or '未知'} (`mid={owner.get('mid') or '未知'}`) |",
        f"| 时长 | {_format_duration(video.get('duration'))} |",
        f"| 分区 | {video.get('tname') or video.get('tname_v2') or '未知'} |",
        f"| 发布时间 | {_format_epoch(video.get('pubdate'))} |",
        f"| 页面统计 | {_stat_summary(stat)} |",
        f"| 抓取完成 | {manifest.get('finished_at') or '未知'} |",
        f"| 封面 | {video.get('pic') or '未知'} |",
        "",
        "## 视频简介",
        "",
        _clean_text(video.get("desc") or "无简介。", limit=600) or "无简介。",
        "",
        "## 数据概览",
        "",
        "| 数据 | 本次结果 |",
        "| --- | --- |",
        f"| 分 P | {_format_count(len(pages))} 个 |",
        f"| 字幕 | {_format_count(subtitle_count)} 条，{_format_count(len(subtitle_entries))} 种语言 |",
        f"| 当前弹幕 | {_format_count(danmaku_count)} 条 |",
        f"| 评论 | {_format_count(comment_count)} 行（{comment_scope}） |",
        f"| 播放流 | {_stream_summary(output_dir)} |",
        f"| 媒体下载 | {'已启用' if config.get('download_media') else '未启用，未下载音视频文件'} |",
        f"| Cookie | 文件 `{config.get('cookie_file') or '未记录'}`；名称：{cookie_names} |",
        "",
        "## 分 P 列表",
        "",
    ]
    if pages:
        for page in pages:
            lines.append(
                f"- P{page.get('page')}: {page.get('part') or '未命名'}，"
                f"`cid={page.get('cid')}`，时长 {_format_duration(page.get('duration'))}"
            )
    else:
        lines.append("- 未解析到分 P。")
    lines.extend(["", "## 解析状态", ""])
    for name, stage in (manifest.get("stages") or {}).items():
        status = stage.get("status")
        message = stage.get("message") or "无补充说明"
        lines.append(f"- {_stage_label(name)}：{_status_label(status)}（`{status or 'unknown'}`）- {message}")
    failures = manifest.get("failures") or []
    if failures:
        lines.extend(["", "## 失败项", ""])
        for item in failures:
            lines.append(
                f"- {_stage_label(str(item.get('stage') or item.get('status') or 'unknown'))}："
                f"{item.get('status') or 'failed'}，"
                f"{item.get('error_message') or item.get('reason') or '无错误信息'}"
            )
    lines.extend(["", "## 内容预览", ""])
    lines.extend(["### 字幕片段", ""])
    if subtitle_samples:
        sample_name = _rel(subtitle_sample_path, output_dir) if subtitle_sample_path else "未知字幕"
        lines.append(f"优先展示 `{sample_name}`：")
        lines.append("")
        for row in subtitle_samples:
            lines.append(f"- [{_format_ms(row.get('start_ms'))}] {_clean_text(row.get('text'), 180)}")
    else:
        lines.append("- 未生成字幕，或字幕文件为空。")
    lines.extend(["", "### 当前弹幕样例", ""])
    if danmaku_samples:
        for row in danmaku_samples:
            lines.append(f"- [{_format_ms(row.get('start_ms'))}] {_clean_text(row.get('text'), 120)}")
    else:
        lines.append("- 未生成当前弹幕，或弹幕文件为空。")
    lines.extend(["", "### 评论样例", ""])
    if comments:
        for row in comments:
            uname = row.get("uname") or ""
            message = _clean_text(row.get("message"), 180)
            level = "主评论" if not row.get("root") else "楼中楼"
            like = row.get("like")
            like_text = f"，赞 {_format_count(like)}" if like is not None else ""
            lines.append(f"- [{level}{like_text}] {uname}: {message}")
    else:
        lines.append("- 未生成评论或评论为空。")
    lines.extend(["", "## 字幕语言", ""])
    if subtitle_entries:
        for entry in subtitle_entries:
            path = entry.get("path")
            path_text = _rel(path, output_dir) if isinstance(path, Path) else "未知路径"
            language = entry.get("language_doc") or entry.get("language") or "未知语言"
            lines.append(
                f"- {language}：{_format_count(entry.get('event_count') or _count_jsonl(path))} 条，"
                f"`{path_text}`"
            )
    else:
        lines.append("- 未生成字幕 JSONL。")
    lines.extend(["", "## 文件索引", ""])
    file_rows = [
        ("manifest.json", "总清单：输入、配置、阶段状态、失败项和输出文件列表。"),
        ("video.json", "视频元数据：标题、UP 主、简介、统计数据、封面和原始详情字段。"),
        ("pages.json", "分 P 信息：每个分 P 的标题、时长、尺寸和 cid。"),
        ("player/", "播放器信息：字幕列表、播放器配置和每个分 P 的 player 数据。"),
        ("streams/", "播放流候选：清晰度、编码、带宽、DASH/MP4 候选状态；默认不下载媒体。"),
        ("subtitles/", "字幕数据：raw JSON、JSONL、SRT、TXT；每条带 bvid/page_index/cid/source/status。"),
        ("danmaku/", "当前弹幕：JSONL，一行一条，带时间点、cid、来源和状态。"),
        ("comments/", "评论数据：扁平 comments.jsonl 和树形 tree.json。"),
    ]
    for path, description in file_rows:
        exists = (output_dir / path).exists()
        marker = "已生成" if exists else "未生成"
        lines.append(f"- `{path}`：{marker}。{description}")
    lines.extend(["", "## 追溯说明", ""])
    lines.append(
        "字幕、弹幕、评论等事件行都保留 `bvid`、`aid`、`page_index`、`cid`、"
        "`source`、`source_api`、`fetched_at`、`start_ms`、`end_ms`、`status`、"
        "`error_code`、`error_message`，方便回溯来源和失败状态。"
    )
    if subtitle_languages:
        lines.extend(["", f"本次字幕语言：{', '.join(subtitle_languages)}。"])
    lines.append("")
    return "\n".join(lines)


def write_report(output_dir: Path) -> Path:
    report = build_report(output_dir)
    path = output_dir / "report.md"
    path.write_text(report, encoding="utf-8")
    return path


def ensure_empty_jsonl(path: Path) -> Path:
    return write_jsonl(path, [])
