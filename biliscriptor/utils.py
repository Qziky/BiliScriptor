from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .logging_config import log_event


BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")
SENSITIVE_COOKIE_NAMES = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5"}


def extract_bvid(text: str) -> str:
    match = BV_PATTERN.search(text)
    if not match:
        raise ValueError(f"Cannot find a BV id in: {text}")
    return match.group(1)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text)).strip("_") or "item"


def normalize_resource_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    log_event(
        "utils.write_json",
        "JSON file written.",
        level=logging.DEBUG,
        path=str(path),
        bytes=len(text.encode("utf-8")),
        top_level_type=type(data).__name__,
    )
    return path


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    byte_count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            f.write(line)
            row_count += 1
            byte_count += len(line.encode("utf-8"))
    log_event(
        "utils.write_jsonl",
        "JSONL file written.",
        level=logging.DEBUG,
        path=str(path),
        row_count=row_count,
        bytes=byte_count,
    )
    return path


def seconds_to_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    row_count = 0
    for index, row in enumerate(rows, 1):
        row_count += 1
        start_ms = int(row.get("start_ms") or 0)
        end_ms = int(row.get("end_ms") or start_ms)
        lines.append(str(index))
        lines.append(f"{seconds_to_srt_time(start_ms / 1000)} --> {seconds_to_srt_time(end_ms / 1000)}")
        lines.append(str(row.get("text") or ""))
        lines.append("")
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    log_event(
        "utils.write_srt",
        "SRT file written.",
        level=logging.DEBUG,
        path=str(path),
        row_count=row_count,
        bytes=len(text.encode("utf-8")),
    )
    return path


def write_txt(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    texts = [str(row.get("text") or "") for row in rows]
    text = "\n".join(texts)
    path.write_text(text, encoding="utf-8")
    log_event(
        "utils.write_txt",
        "Text file written.",
        level=logging.DEBUG,
        path=str(path),
        row_count=len(texts),
        bytes=len(text.encode("utf-8")),
    )
    return path


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def sanitize_for_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key in SENSITIVE_COOKIE_NAMES else sanitize_for_manifest(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_manifest(item) for item in value]
    return value
