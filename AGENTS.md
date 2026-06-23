# Repository Guidelines

## Project Structure & Module Organization

BiliScriptor is a Python 3.10+ CLI package. Source code lives in `biliscriptor/`: `cli.py` defines commands, `pipeline.py` coordinates parsing, `client.py` handles Bilibili API requests, `extractors.py` normalizes API data, `report.py` renders Markdown output, and `logging_config.py` configures detailed text/JSONL run logs with redaction helpers. `biliscriptor/__main__.py` supports `python -m biliscriptor`. Tests are in `tests/`, with pytest-style functions in `test_core.py` and a unittest compatibility wrapper in `test_unittest.py`. Reference notes are kept in `B站视频解析百科.md`.

## Build, Test, and Development Commands

Use a virtual environment for local work:

```bash
python -m venv .venv
python -m pip install -e .
```

Run the CLI directly during development:

```bash
python -m biliscriptor parse BV1QEVY6jEYv
python -m biliscriptor report output/BV1QEVY6jEYv
python -m biliscriptor login
```

For a manual live parse smoke test, use a real Bilibili URL and verify the command summary, output package, and generated logs:

```bash
python -m biliscriptor parse "https://www.bilibili.com/video/BV11kVt6sEWA?"
```

Run tests with either supported runner:

```bash
python -m pytest
python -m unittest discover -s tests
```

## Coding Style & Naming Conventions

Follow the existing Python style: 4-space indentation, type annotations where practical, small functions with explicit return values, and `from __future__ import annotations` in new modules. Use `snake_case` for functions, variables, and module names; use `PascalCase` for classes and dataclasses. Prefer `pathlib.Path` for filesystem paths and JSON/JSONL helpers from `biliscriptor.utils` for output files. Keep network-facing logic in `client.py` and transformation logic in `extractors.py`.

## Testing Guidelines

Add tests for parsing, normalization, report generation, logging, and CLI argument behavior. Name pytest tests `test_<behavior>` and place them in `tests/test_core.py` unless a new area grows large enough for its own file. Use `tmp_path` or `tempfile.TemporaryDirectory()` for generated output. Automated tests should avoid live network calls; use mocks, fake openers, or representative API payloads instead. Treat real network parsing as a manual smoke test only.

## Commit & Pull Request Guidelines

The current history uses short Chinese commit subjects, for example `初始化`. Keep commits concise and imperative, such as `修复字幕时间格式` or `Add report failure tests`. Pull requests should include a clear summary, test results, affected CLI commands, and sample output or screenshots when report formatting changes. Link related issues when available and note any Bilibili API behavior assumptions.

## Security & Configuration Tips

Do not commit `bilibili_cookies.txt`, generated `output/` data, `runtime/`, `logs/`, or secrets from manifests/logs. Logs may record cookie names, URL paths, parameter keys, statuses, timings, counts, file paths, and sanitized errors, but must not record cookie values, QR code keys, tokens, csrf values, WBI signatures, full response bodies, subtitle text, comment text, or danmaku text. Preserve the existing behavior that avoids writing cookie values into reports, manifests, or logs.
