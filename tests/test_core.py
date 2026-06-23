from __future__ import annotations

import json
import contextlib
import io
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from biliscriptor.client import BiliApiError, BiliClient, get_mixin_key, normalize_error_status, sign_wbi
from biliscriptor.cli import build_parser
from biliscriptor.danmaku_pb import decode_dm_seg_mobile_reply
from biliscriptor.extractors import fetch_comments, normalize_reply, normalize_subtitle_rows
import biliscriptor.pipeline as pipeline
from biliscriptor.pipeline import ParseOptions
from biliscriptor.report import build_report
from biliscriptor.utils import extract_bvid, write_json, write_jsonl, write_srt


class _FakeResponse:
    def __init__(self, body: bytes = b"{}") -> None:
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class _FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def open(self, request: object, timeout: int = 0) -> _FakeResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, bytes):
            return _FakeResponse(outcome)
        return _FakeResponse(json.dumps(outcome).encode("utf-8"))


class _SequenceClient:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = replies

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, **_: object) -> dict[str, Any]:
        pn = int((params or {}).get("pn") or 1)
        return self.replies[min(pn - 1, len(self.replies) - 1)]


class _CommentClient:
    def __init__(self, *, child_payload: dict[str, Any] | None = None) -> None:
        self.child_payload = child_payload or {"code": 0, "data": {"replies": []}}

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, **_: object) -> dict[str, Any]:
        if url.endswith("/reply/reply"):
            return self.child_payload
        return {
            "code": 0,
            "data": {
                "replies": [
                    {
                        "rpid": 1,
                        "root": 0,
                        "parent": 0,
                        "member": {"mid": 10, "uname": "user"},
                        "content": {"message": "root"},
                    }
                ],
                "page": {"count": 100, "size": 20},
            },
        }


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def test_extract_bvid_from_url() -> None:
    assert extract_bvid("https://www.bilibili.com/video/BV1QEVY6jEYv/?p=1") == "BV1QEVY6jEYv"


def test_wbi_signing_shape() -> None:
    img_key = "7cd084941338484aae1ad9425b84077c"
    sub_key = "4932caff0ff746eab6f01bf08b70ac45"
    signed = sign_wbi({"bvid": "BV1QEVY6jEYv", "cid": 123}, img_key, sub_key)
    assert get_mixin_key(img_key, sub_key)
    assert signed["bvid"] == "BV1QEVY6jEYv"
    assert signed["cid"] == "123"
    assert "wts" in signed
    assert len(signed["w_rid"]) == 32


def test_normalize_error_status() -> None:
    assert normalize_error_status(-101) == "not_logged_in"
    assert normalize_error_status(-412) == "risk_control"
    assert normalize_error_status(None, 429) == "rate_limited"
    assert normalize_error_status(12345) == "api_failed"


def test_client_parses_http_json_error_body() -> None:
    error = urllib.error.HTTPError(
        "https://example.test/api",
        403,
        "Forbidden",
        {},
        BytesIO(json.dumps({"code": -101, "message": "请先登录"}).encode("utf-8")),
    )
    client = BiliClient(rate_limit=0, max_retries=0)
    client.opener = _FakeOpener([error])  # type: ignore[assignment]

    try:
        client.get_json("https://example.test/api")
    except BiliApiError as exc:
        assert exc.status == "not_logged_in"
        assert exc.code == -101
        assert exc.message == "请先登录"
    else:
        raise AssertionError("Expected BiliApiError")


def test_client_wraps_invalid_json_response() -> None:
    client = BiliClient(rate_limit=0, max_retries=0)
    client.opener = _FakeOpener([b"not-json"])  # type: ignore[assignment]

    try:
        client.get_json("https://example.test/api")
    except BiliApiError as exc:
        assert exc.status == "invalid_json"
    else:
        raise AssertionError("Expected BiliApiError")


def test_client_retries_rate_limited_http_error() -> None:
    error = urllib.error.HTTPError(
        "https://example.test/api",
        429,
        "Too Many Requests",
        {},
        BytesIO(json.dumps({"code": -799, "message": "rate limited"}).encode("utf-8")),
    )
    opener = _FakeOpener([error, {"code": 0, "data": {"ok": True}}])
    client = BiliClient(rate_limit=0, max_retries=2)
    client.opener = opener  # type: ignore[assignment]

    with patch("biliscriptor.client.time.sleep", Mock()):
        payload = client.get_json("https://example.test/api")

    assert payload["data"]["ok"] is True
    assert opener.calls == 2


def test_client_retries_http_5xx_even_with_zero_api_code() -> None:
    error = urllib.error.HTTPError(
        "https://example.test/api",
        503,
        "Service Unavailable",
        {},
        BytesIO(json.dumps({"code": 0, "message": "temporary"}).encode("utf-8")),
    )
    opener = _FakeOpener([error, {"code": 0, "data": {"ok": True}}])
    client = BiliClient(rate_limit=0, max_retries=2)
    client.opener = opener  # type: ignore[assignment]

    with patch("biliscriptor.client.time.sleep", Mock()):
        payload = client.get_json("https://example.test/api")

    assert payload["data"]["ok"] is True
    assert opener.calls == 2


def test_get_wbi_json_refreshes_keys_on_voucher() -> None:
    client = BiliClient(rate_limit=0, max_retries=0)
    client.get_wbi_keys = Mock(side_effect=[("old_img_key_12345678901234567890", "old_sub_key_12345678901234567890"), ("new_img_key_12345678901234567890", "new_sub_key_12345678901234567890")])  # type: ignore[method-assign]
    client.get_json = Mock(side_effect=[{"code": 0, "data": {"v_voucher": "risk"}}, {"code": 0, "data": {"ok": True}}])  # type: ignore[method-assign]

    payload = client.get_wbi_json("https://example.test/wbi", {"bvid": "BV1QEVY6jEYv"})

    assert payload["data"]["ok"] is True
    assert client.get_wbi_keys.call_args_list[0].kwargs == {"refresh": False}
    assert client.get_wbi_keys.call_args_list[1].kwargs == {"refresh": True}
    assert client.get_json.call_count == 2


def test_default_comment_depth_is_conservative() -> None:
    options = ParseOptions(url_or_bvid="BV1QEVY6jEYv")
    assert options.comment_pages == 1
    assert options.reply_pages == 1

    args = build_parser().parse_args(["parse", "BV1QEVY6jEYv"])
    assert args.comment_pages == 1
    assert args.reply_pages == 1


def test_runtime_artifact_defaults_use_runtime_directory() -> None:
    options = ParseOptions(url_or_bvid="BV1QEVY6jEYv")
    assert options.cookie_file == Path("runtime/bilibili_cookies.txt")

    login_args = build_parser().parse_args(["login"])
    assert login_args.cookie_file == "runtime/bilibili_cookies.txt"
    assert login_args.qr_file == "runtime/bilibili_login_qr.svg"

    parse_args = build_parser().parse_args(["parse", "BV1QEVY6jEYv"])
    assert parse_args.cookie_file == "runtime/bilibili_cookies.txt"

    subtitle_args = build_parser().parse_args(["subtitles", "BV1QEVY6jEYv"])
    assert subtitle_args.cookie_file == "runtime/bilibili_cookies.txt"


def test_subtitles_command_exists() -> None:
    args = build_parser().parse_args(["subtitles", "BV1QEVY6jEYv", "--page", "1"])
    assert args.command == "subtitles"
    assert args.url_or_bvid == "BV1QEVY6jEYv"
    assert args.page == 1


def test_cli_rejects_invalid_numeric_options() -> None:
    parser = build_parser()
    invalid_cases = [
        ["parse", "BV1QEVY6jEYv", "--comment-pages", "0"],
        ["parse", "BV1QEVY6jEYv", "--reply-pages", "-1"],
        ["parse", "BV1QEVY6jEYv", "--rate-limit", "-0.1"],
        ["parse", "BV1QEVY6jEYv", "--page", "0"],
        ["subtitles", "BV1QEVY6jEYv", "--page", "-1"],
    ]
    for argv in invalid_cases:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                parser.parse_args(argv)
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError(f"Expected argparse failure for {argv}")


def test_cli_accepts_zero_rate_limit_and_positive_pages() -> None:
    args = build_parser().parse_args(
        [
            "parse",
            "BV1QEVY6jEYv",
            "--comment-pages",
            "2",
            "--reply-pages",
            "3",
            "--rate-limit",
            "0",
            "--page",
            "1",
        ]
    )
    assert args.comment_pages == 2
    assert args.reply_pages == 3
    assert args.rate_limit == 0
    assert args.page == 1


def test_subtitle_rows_and_srt(tmp_path: Path) -> None:
    rows = normalize_subtitle_rows(
        [{"from": 1.0, "to": 2.5, "content": "你好"}],
        item={"lan": "zh-CN", "lan_doc": "中文", "type": 0, "ai_type": 0, "ai_status": 0},
        bvid="BV1QEVY6jEYv",
        aid=1,
        page_index=1,
        cid=2,
    )
    assert rows[0]["source"] == "official"
    assert rows[0]["start_ms"] == 1000
    assert rows[0]["end_ms"] == 2500
    assert rows[0]["cid"] == 2
    srt = write_srt(tmp_path / "sub.srt", rows).read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,500" in srt
    assert "你好" in srt


def test_decode_dm_seg_mobile_reply() -> None:
    elem = b"".join(
        [
            _field_varint(1, 99),
            _field_varint(2, 1500),
            _field_varint(3, 1),
            _field_varint(4, 25),
            _field_varint(5, 16777215),
            _field_bytes(6, b"abcdef12"),
            _field_bytes(7, "弹幕".encode("utf-8")),
            _field_varint(8, 1710000000),
            _field_varint(9, 3),
            _field_bytes(12, b"99"),
        ]
    )
    payload = _field_bytes(1, elem)
    rows = decode_dm_seg_mobile_reply(payload)
    assert rows == [
        {
            "id": 99,
            "progress": 1500,
            "mode": 1,
            "fontsize": 25,
            "color": 16777215,
            "user_hash": "abcdef12",
            "content": "弹幕",
            "ctime": 1710000000,
            "weight": 3,
            "id_str": "99",
        }
    ]


def test_normalize_reply() -> None:
    row = normalize_reply(
        {
            "rpid": 1,
            "root": 0,
            "parent": 0,
            "member": {"mid": 10, "uname": "用户"},
            "content": {"message": "评论"},
            "like": 3,
            "ctime": 4,
            "floor": 5,
            "rcount": 6,
            "state": 0,
        },
        bvid="BV1QEVY6jEYv",
        aid=7,
        page_index=None,
        cid=None,
        fetched_at="2026-06-23T00:00:00Z",
        source_api="reply",
    )
    assert row["message"] == "评论"
    assert row["start_ms"] is None
    assert row["cid"] is None
    assert row["status"] == "ok"


def test_fetch_comments_records_comment_safety_cap() -> None:
    with patch("biliscriptor.extractors.COMMENT_PAGE_SAFETY_CAP", 1):
        data = fetch_comments(
            _CommentClient(),  # type: ignore[arg-type]
            bvid="BV1QEVY6jEYv",
            aid=1,
            comment_pages=1,
            reply_pages=1,
            all_comments=True,
        )

    assert data["truncations"] == [{"cap_kind": "comments", "cap_pages": 1, "next_page": 2}]


def test_fetch_comments_records_reply_safety_cap() -> None:
    child_payload = {
        "code": 0,
        "data": {
            "replies": [
                {
                    "rpid": 2,
                    "root": 1,
                    "parent": 1,
                    "member": {"mid": 11, "uname": "child"},
                    "content": {"message": "child"},
                }
            ],
            "page": {"count": 100, "size": 20},
        },
    }
    with patch("biliscriptor.extractors.REPLY_PAGE_SAFETY_CAP", 1):
        data = fetch_comments(
            _CommentClient(child_payload=child_payload),  # type: ignore[arg-type]
            bvid="BV1QEVY6jEYv",
            aid=1,
            comment_pages=1,
            reply_pages=1,
            all_comments=True,
        )

    assert {"cap_kind": "replies", "cap_pages": 1, "next_page": 2, "root_rpid": 1} in data["truncations"]


def test_pipeline_comment_truncation_manifest_fields(tmp_path: Path) -> None:
    fake_client = Mock()
    fake_client.cookie_names = []

    with (
        patch.object(pipeline, "BiliClient", Mock(return_value=fake_client)),
        patch.object(
            pipeline,
            "fetch_video_info",
            Mock(return_value={"aid": 1, "bvid": "BV1QEVY6jEYv", "pages": []}),
        ),
        patch.object(
            pipeline,
            "fetch_comments",
            Mock(
                return_value={
                    "comments": [],
                    "tree": [],
                    "truncations": [{"cap_kind": "comments", "cap_pages": 1, "next_page": 2}],
                }
            ),
        ),
    ):
        result = pipeline.parse_video(
            ParseOptions(
                url_or_bvid="BV1QEVY6jEYv",
                output_dir=tmp_path,
                skip_subtitles=True,
                skip_danmaku=True,
                skip_streams=True,
                write_report=False,
                all_comments=True,
            )
        )

    comments_stage = result.manifest["stages"]["comments"]
    assert comments_stage["status"] == "ok"
    assert comments_stage["truncated"] is True
    assert comments_stage["cap_kind"] == "comments"
    assert comments_stage["cap_pages"] == 1
    assert result.manifest["failures"][-1]["status"] == "truncated"

    manifest = json.loads((tmp_path / "BV1QEVY6jEYv" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"]["comments"]["truncated"] is True


def test_build_report(tmp_path: Path) -> None:
    write_json(
        tmp_path / "video.json",
        {
            "title": "标题",
            "bvid": "BV1QEVY6jEYv",
            "aid": 1,
            "duration": 125,
            "desc": "简介",
            "tname": "科技",
            "pubdate": 1710000000,
            "owner": {"mid": 10, "name": "测试UP"},
            "stat": {"view": 123, "danmaku": 4, "reply": 5, "like": 6, "coin": 7, "favorite": 8, "share": 9},
        },
    )
    write_json(
        tmp_path / "pages.json",
        [
            {"page": 1, "cid": 2, "part": "正片", "duration": 120},
            {"page": 2, "cid": 3, "part": "花絮", "duration": 5},
        ],
    )
    write_jsonl(
        tmp_path / "subtitles" / "page_001_1_ai-en.jsonl",
        [{"start_ms": 1000, "text": "English sample"}],
    )
    write_jsonl(
        tmp_path / "subtitles" / "page_001_2_ai-zh.jsonl",
        [
            {"start_ms": 1500, "text": "中文样例一"},
            {"start_ms": 2500, "text": "中文样例二"},
        ],
    )
    write_jsonl(tmp_path / "danmaku" / "page_001.current.jsonl", [{"start_ms": 3000, "text": "弹幕"}])
    write_jsonl(
        tmp_path / "comments" / "comments.jsonl",
        [
            {"uname": "用户A", "message": "主评论", "like": 2, "root": 0},
            {"uname": "用户B", "message": "楼中楼", "like": 1, "root": 1},
        ],
    )
    write_json(
        tmp_path / "streams" / "page_001.json",
        {"summary": {"videos": [{"id": 80, "codecid": 7}], "audios": [{"id": 30280}]}},
    )
    write_json(
        tmp_path / "manifest.json",
        {
            "bvid": "BV1QEVY6jEYv",
            "finished_at": "2026-06-23T00:00:00Z",
            "config": {
                "cookie_file": "bilibili_cookies.txt",
                "cookie_names": ["SESSDATA", "bili_jct", "DedeUserID"],
                "cookie_values": {"SESSDATA": "secret-sessdata-value"},
                "comment_pages": 1,
                "reply_pages": 1,
                "all_comments": False,
                "download_media": False,
            },
            "stages": {
                "metadata": {"status": "ok", "message": "Fetched 2 page(s)."},
                "subtitles": {"status": "ok", "message": "Fetched 3 subtitle event(s)."},
                "danmaku": {"status": "ok", "message": "Fetched 1 danmaku event(s)."},
                "comments": {"status": "ok", "message": "Fetched 2 comment row(s)."},
            },
            "files": {
                "subtitles": [
                    {
                        "language": "ai-en",
                        "language_doc": "English",
                        "event_count": 1,
                        "files": {"jsonl": str(tmp_path / "subtitles" / "page_001_1_ai-en.jsonl")},
                    },
                    {
                        "language": "ai-zh",
                        "language_doc": "中文",
                        "event_count": 2,
                        "files": {"jsonl": str(tmp_path / "subtitles" / "page_001_2_ai-zh.jsonl")},
                    },
                ]
            },
        },
    )
    report = build_report(tmp_path)
    assert "# 标题" in report
    assert "## 快速概览" in report
    assert "## 数据概览" in report
    assert "测试UP" in report
    assert "播放 123" in report
    assert "P1: 正片，`cid=2`" in report
    assert "P2: 花絮，`cid=3`" in report
    assert "字幕 | 3 条，2 种语言" in report
    assert "当前弹幕 | 1 条" in report
    assert "评论 | 2 行（1 页主评论 + 每条 1 页楼中楼）" in report
    assert "视频候选 1 条，音频候选 1 条" in report
    assert "中文样例一" in report
    assert "English sample" not in report
    assert "[00:03.000] 弹幕" in report
    assert "[主评论，赞 2] 用户A: 主评论" in report
    assert "[楼中楼，赞 1] 用户B: 楼中楼" in report
    assert "`manifest.json`：已生成" in report
    assert "`subtitles/`：已生成" in report
    assert "secret-sessdata-value" not in report
    assert json.loads((tmp_path / "video.json").read_text(encoding="utf-8"))["title"] == "标题"


def test_build_report_empty_and_failed_sections(tmp_path: Path) -> None:
    write_json(tmp_path / "video.json", {"title": "空视频", "bvid": "BV1empty0000", "aid": 1})
    write_json(tmp_path / "pages.json", [])
    write_json(
        tmp_path / "manifest.json",
        {
            "stages": {
                "metadata": {"status": "ok", "message": "Fetched 0 page(s)."},
                "subtitles": {"status": "missing", "message": "No subtitles were fetched."},
                "danmaku": {"status": "skipped", "message": "Skipped by option."},
                "comments": {"status": "failed", "message": "接口失败"},
            },
            "failures": [
                {
                    "stage": "comments",
                    "status": "api_failed",
                    "error_message": "接口失败",
                }
            ],
        },
    )
    report = build_report(tmp_path)
    assert "未解析到分 P" in report
    assert "字幕：未抓到（`missing`）" in report
    assert "当前弹幕：已跳过（`skipped`）" in report
    assert "评论：失败（`failed`）" in report
    assert "## 失败项" in report
    assert "评论：api_failed，接口失败" in report
    assert "未生成字幕，或字幕文件为空" in report
    assert "未生成当前弹幕，或弹幕文件为空" in report
    assert "未生成评论或评论为空" in report
    assert "`subtitles/`：未生成" in report


def test_build_report_snapshot_structure_and_privacy(tmp_path: Path) -> None:
    write_json(
        tmp_path / "video.json",
        {
            "title": "快照标题",
            "bvid": "BV1QEVY6jEYv",
            "aid": 1,
            "duration": 61,
            "desc": "报告快照描述",
            "owner": {"mid": 10, "name": "测试UP"},
            "stat": {"view": 123},
        },
    )
    write_json(tmp_path / "pages.json", [{"page": 1, "cid": 2, "part": "正片", "duration": 61}])
    write_jsonl(tmp_path / "comments" / "comments.jsonl", [{"uname": "用户", "message": "评论", "like": 1}])
    write_json(
        tmp_path / "manifest.json",
        {
            "bvid": "BV1QEVY6jEYv",
            "finished_at": "2026-06-23T00:00:00Z",
            "config": {
                "cookie_file": "bilibili_cookies.txt",
                "cookie_names": ["SESSDATA"],
                "cookie_values": {"SESSDATA": "secret-sessdata-value"},
                "comment_pages": 1,
                "reply_pages": 1,
                "all_comments": False,
                "download_media": False,
            },
            "stages": {
                "metadata": {"status": "ok", "message": "Fetched 1 page(s)."},
                "comments": {"status": "ok", "message": "Fetched 1 comment row(s)."},
            },
            "failures": [],
        },
    )

    report = build_report(tmp_path)
    expected_sections = [
        "# 快照标题",
        "## 快速概览",
        "## 视频简介",
        "## 数据概览",
        "## 分 P 列表",
        "## 解析状态",
        "## 内容预览",
        "## 文件索引",
        "## 追溯说明",
    ]
    for section in expected_sections:
        assert section in report
    assert "| BV / AV | `BV1QEVY6jEYv` / `1` |" in report
    assert "- P1: 正片" in report
    assert "secret-sessdata-value" not in report
