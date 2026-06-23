from __future__ import annotations

import json
from pathlib import Path

from bili_md.client import get_mixin_key, normalize_error_status, sign_wbi
from bili_md.cli import build_parser
from bili_md.danmaku_pb import decode_dm_seg_mobile_reply
from bili_md.extractors import normalize_reply, normalize_subtitle_rows
from bili_md.pipeline import ParseOptions
from bili_md.report import build_report
from bili_md.utils import extract_bvid, write_json, write_jsonl, write_srt


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


def test_default_comment_depth_is_conservative() -> None:
    options = ParseOptions(url_or_bvid="BV1QEVY6jEYv")
    assert options.comment_pages == 1
    assert options.reply_pages == 1

    args = build_parser().parse_args(["parse", "BV1QEVY6jEYv"])
    assert args.comment_pages == 1
    assert args.reply_pages == 1


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
