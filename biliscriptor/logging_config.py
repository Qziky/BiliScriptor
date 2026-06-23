from __future__ import annotations

import argparse
import contextvars
import itertools
import json
import logging
import os
import re
import sys
import traceback
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIR = "logs"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMATS = ("both", "text", "jsonl")
REDACTED = "<redacted>"

_LOGGER_NAME = "biliscriptor"
logging.getLogger(_LOGGER_NAME).addHandler(logging.NullHandler())
logging.getLogger(_LOGGER_NAME).propagate = False
_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("biliscriptor_run_id", default="")
_command: contextvars.ContextVar[str] = contextvars.ContextVar("biliscriptor_command", default="")
_request_counter = itertools.count(1)

_SENSITIVE_EXACT_KEYS = {
    "cookie",
    "cookies",
    "set_cookie",
    "set-cookie",
    "cookie_value",
    "cookie_values",
    "sessdata",
    "bili_jct",
    "dedeuserid",
    "dedeuserid__ckmd5",
    "qrcode_key",
    "csrf",
    "csrf_token",
    "token",
    "access_token",
    "refresh_token",
    "w_rid",
    "password",
    "secret",
}
_SENSITIVE_KEY_PARTS = (
    "sessdata",
    "bili_jct",
    "dedeuserid",
    "qrcode_key",
    "csrf",
    "token",
    "password",
    "secret",
    "w_rid",
)
_SAFE_KEY_EXCEPTIONS = {
    "cookie_file",
    "cookie_names",
    "param_keys",
    "query_keys",
    "key_count",
    "keys",
}
_URL_KEYS = {"url", "endpoint", "referer", "login_url", "poll_url", "subtitle_url"}
_TEXT_SECRET_RE = re.compile(
    r"(?i)\b(SESSDATA|bili_jct|DedeUserID(?:__ckMd5)?|qrcode_key|csrf(?:_token)?|"
    r"access_token|refresh_token|token|w_rid|password|secret)=([^&\s;,\"]+)"
)
_TEXT_URL_RE = re.compile(r"https?://[^\s)\"']+")


@dataclass(frozen=True)
class LoggingConfig:
    run_id: str
    command: str
    text_log: Path | None = None
    jsonl_log: Path | None = None

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(path for path in (self.text_log, self.jsonl_log) if path is not None)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z")
        event = getattr(record, "event", "")
        run_id = getattr(record, "run_id", "")
        command = getattr(record, "command", "")
        fields = getattr(record, "structured", {}) or {}
        parts = [
            timestamp,
            record.levelname,
            record.name,
        ]
        if event:
            parts.append(f"event={event}")
        if run_id:
            parts.append(f"run_id={run_id}")
        if command:
            parts.append(f"command={command}")
        parts.append(record.getMessage())
        if fields:
            parts.append(json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str))
        return sanitize_text(" | ".join(parts))


class _JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "structured", {}) or {}
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", ""),
            "message": sanitize_text(record.getMessage()),
            "run_id": getattr(record, "run_id", ""),
            "command": getattr(record, "command", ""),
        }
        payload.update(sanitize_for_log(fields))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help="Directory for detailed run logs.")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="DEBUG", help="Minimum log level.")
    parser.add_argument("--log-format", choices=LOG_FORMATS, default="both", help="Detailed log file format.")
    parser.add_argument("--no-file-log", action="store_true", help="Disable default log file creation.")
    parser.add_argument("--log-to-stderr", action="store_true", help="Also stream detailed logs to stderr.")


def configure_logging(
    *,
    command: str,
    log_dir: Path | str = DEFAULT_LOG_DIR,
    log_level: str = "DEBUG",
    log_format: str = "both",
    no_file_log: bool = False,
    log_to_stderr: bool = False,
) -> LoggingConfig:
    level = getattr(logging, log_level.upper(), logging.DEBUG)
    safe_command = _safe_filename(command or "command")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{timestamp}-{safe_command}-{os.getpid()}"
    _run_id.set(run_id)
    _command.set(command)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    text_log: Path | None = None
    jsonl_log: Path | None = None
    if not no_file_log:
        root = Path(log_dir)
        root.mkdir(parents=True, exist_ok=True)
        if log_format in {"both", "text"}:
            text_log = root / f"{run_id}.log"
            text_handler = logging.FileHandler(text_log, encoding="utf-8")
            text_handler.setLevel(level)
            text_handler.setFormatter(_TextFormatter())
            logger.addHandler(text_handler)
        if log_format in {"both", "jsonl"}:
            jsonl_log = root / f"{run_id}.jsonl"
            jsonl_handler = logging.FileHandler(jsonl_log, encoding="utf-8")
            jsonl_handler.setLevel(level)
            jsonl_handler.setFormatter(_JsonlFormatter())
            logger.addHandler(jsonl_handler)

    if log_to_stderr:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(_TextFormatter())
        logger.addHandler(stream_handler)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    config = LoggingConfig(run_id=run_id, command=command, text_log=text_log, jsonl_log=jsonl_log)
    log_event(
        "logging.configured",
        "Detailed logging configured.",
        command=command,
        log_level=log_level.upper(),
        log_format=log_format,
        log_paths=[str(path) for path in config.paths],
        file_logging=not no_file_log,
        stderr_logging=log_to_stderr,
    )
    return config


def flush_logging() -> None:
    for handler in logging.getLogger(_LOGGER_NAME).handlers:
        handler.flush()


def shutdown_logging() -> None:
    logger = logging.getLogger(_LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
    logger.addHandler(logging.NullHandler())


def new_request_id() -> str:
    return f"req-{next(_request_counter):06d}"


def log_event(
    event: str,
    message: str = "",
    *,
    level: int = logging.INFO,
    logger_name: str = _LOGGER_NAME,
    **fields: Any,
) -> None:
    logger = logging.getLogger(logger_name)
    logger.log(
        level,
        sanitize_text(message),
        extra={
            "event": event,
            "structured": sanitize_for_log(fields),
            "run_id": _run_id.get(),
            "command": _command.get(),
        },
    )


def log_exception(
    event: str,
    exc: BaseException,
    message: str = "",
    *,
    logger_name: str = _LOGGER_NAME,
    level: int = logging.ERROR,
    **fields: Any,
) -> None:
    log_event(
        event,
        message or str(exc),
        level=level,
        logger_name=logger_name,
        error_type=exc.__class__.__name__,
        error_message=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        **fields,
    )


def sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_key(key_text):
                cleaned[key_text] = REDACTED
            elif key_text.lower() in _URL_KEYS:
                cleaned[key_text] = sanitize_url(str(item))
            else:
                cleaned[key_text] = sanitize_for_log(item)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, BaseException):
        return sanitize_text(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("http://", "https://", "//")):
            return sanitize_url(stripped)
        return sanitize_text(value)
    return value


def sanitize_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return sanitize_text(url)
    if not parsed.scheme and not parsed.netloc:
        return sanitize_text(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query_keys = ",".join(sorted(query))
    path = parsed.path or "/"
    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    if query_keys:
        return f"{base}?query_keys={query_keys}"
    return base


def sanitize_mapping_keys(mapping: dict[str, Any] | None) -> list[str]:
    return sorted(str(key) for key in (mapping or {}).keys())


def sanitize_text(text: str) -> str:
    cleaned = _TEXT_URL_RE.sub(lambda match: sanitize_url(match.group(0)), str(text))
    cleaned = _TEXT_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", cleaned)
    return cleaned


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SAFE_KEY_EXCEPTIONS:
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _safe_filename(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text)).strip("_") or "command"
