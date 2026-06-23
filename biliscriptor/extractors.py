from __future__ import annotations

import logging
import math
import time
from typing import Any

from .client import BiliClient
from .danmaku_pb import decode_dm_seg_mobile_reply
from .logging_config import log_event, sanitize_url
from .utils import normalize_resource_url, safe_name, utc_now_iso


VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYER_V2_API = "https://api.bilibili.com/x/player/wbi/v2"
PLAYURL_API = "https://api.bilibili.com/x/player/wbi/playurl"
DM_SEG_API = "https://api.bilibili.com/x/v2/dm/web/seg.so"
REPLY_API = "https://api.bilibili.com/x/v2/reply"
REPLY_REPLIES_API = "https://api.bilibili.com/x/v2/reply/reply"
COMMENT_PAGE_SAFETY_CAP = 1000
REPLY_PAGE_SAFETY_CAP = 500


def fetch_video_info(client: BiliClient, bvid: str) -> dict[str, Any]:
    started = time.perf_counter()
    log_event("extractors.video_info_start", "Fetching video metadata.", level=logging.DEBUG, bvid=bvid)
    payload = client.get_json(VIEW_API, params={"bvid": bvid})
    data = payload["data"]
    pages = list(data.get("pages") or [])
    log_event(
        "extractors.video_info_success",
        "Video metadata fetched.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=data.get("aid"),
        page_count=len(pages),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return data


def fetch_player_v2(client: BiliClient, *, bvid: str, aid: int, cid: int) -> dict[str, Any]:
    started = time.perf_counter()
    log_event(
        "extractors.player_start",
        "Fetching player data.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        cid=cid,
    )
    payload = client.get_wbi_json(PLAYER_V2_API, {"bvid": bvid, "aid": aid, "cid": cid})
    data = payload.get("data") or {}
    log_event(
        "extractors.player_success",
        "Player data fetched.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        cid=cid,
        subtitle_count=len(get_subtitle_items(data)),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return data


def fetch_streams(client: BiliClient, *, bvid: str, cid: int, qn: int = 80) -> dict[str, Any]:
    started = time.perf_counter()
    log_event(
        "extractors.streams_start",
        "Fetching stream candidates.",
        level=logging.DEBUG,
        bvid=bvid,
        cid=cid,
        qn=qn,
    )
    payload = client.get_wbi_json(
        PLAYURL_API,
        {
            "bvid": bvid,
            "cid": cid,
            "qn": qn,
            "fnver": 0,
            "fnval": 4048,
            "fourk": 1,
        },
    )
    data = payload.get("data") or {}
    summary = summarize_streams(data)
    log_event(
        "extractors.streams_success",
        "Stream candidates fetched.",
        level=logging.DEBUG,
        bvid=bvid,
        cid=cid,
        qn=qn,
        dash_video_count=summary["dash_video_count"],
        dash_audio_count=summary["dash_audio_count"],
        durl_count=summary["durl_count"],
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return {"raw": data, "summary": summary}


def summarize_streams(data: dict[str, Any]) -> dict[str, Any]:
    dash = data.get("dash") or {}
    videos = [
        {
            "id": item.get("id"),
            "codecid": item.get("codecid"),
            "bandwidth": item.get("bandwidth"),
            "width": item.get("width"),
            "height": item.get("height"),
            "frame_rate": item.get("frameRate"),
            "has_base_url": bool(item.get("baseUrl") or item.get("base_url")),
            "backup_url_count": len(item.get("backupUrl") or item.get("backup_url") or []),
        }
        for item in dash.get("video") or []
    ]
    audios = [
        {
            "id": item.get("id"),
            "bandwidth": item.get("bandwidth"),
            "has_base_url": bool(item.get("baseUrl") or item.get("base_url")),
            "backup_url_count": len(item.get("backupUrl") or item.get("backup_url") or []),
        }
        for item in dash.get("audio") or []
    ]
    durl = data.get("durl") or []
    return {
        "format": data.get("format"),
        "quality": data.get("quality"),
        "accept_quality": data.get("accept_quality") or [],
        "accept_description": data.get("accept_description") or [],
        "dash_video_count": len(videos),
        "dash_audio_count": len(audios),
        "durl_count": len(durl),
        "videos": videos,
        "audios": audios,
    }


def get_subtitle_items(player_data: dict[str, Any]) -> list[dict[str, Any]]:
    subtitle = player_data.get("subtitle") or {}
    items = subtitle.get("subtitles")
    if items is None:
        items = subtitle.get("list")
    return list(items or [])


def subtitle_source(item: dict[str, Any]) -> str:
    ai_type = item.get("ai_type")
    ai_status = item.get("ai_status")
    if ai_type not in (None, 0) or ai_status not in (None, 0):
        return "ai"
    if item.get("type") == 1:
        return "ai"
    return "official"


def normalize_subtitle_rows(
    body: list[dict[str, Any]],
    *,
    item: dict[str, Any],
    bvid: str,
    aid: int,
    page_index: int,
    cid: int,
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    source = subtitle_source(item)
    language = item.get("lan") or ""
    fetched_at = utc_now_iso()
    rows: list[dict[str, Any]] = []
    for row in body:
        start = float(row.get("from") or 0)
        end = float(row.get("to") or start)
        rows.append(
            {
                "bvid": bvid,
                "aid": aid,
                "page_index": page_index,
                "cid": cid,
                "source": source,
                "source_api": "subtitle_url",
                "fetched_at": fetched_at,
                "start_ms": round(start * 1000),
                "end_ms": round(end * 1000),
                "status": "ok",
                "error_code": None,
                "error_message": None,
                "language": language,
                "language_doc": item.get("lan_doc"),
                "confidence": "high" if source == "official" else "medium",
                "text": str(row.get("content") or "").replace("\r\n", "\n").strip(),
            }
        )
    log_event(
        "extractors.subtitle_normalized",
        "Subtitle rows normalized.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        page_index=page_index,
        cid=cid,
        language=language,
        source=source,
        input_count=len(body),
        event_count=len(rows),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return rows


def fetch_subtitle(
    client: BiliClient,
    *,
    subtitle_url: str,
    bvid: str,
) -> dict[str, Any]:
    url = normalize_resource_url(subtitle_url)
    started = time.perf_counter()
    log_event(
        "extractors.subtitle_start",
        "Fetching subtitle payload.",
        level=logging.DEBUG,
        bvid=bvid,
        subtitle_url=sanitize_url(url),
    )
    payload = client.get_json(url, referer=f"https://www.bilibili.com/video/{bvid}/", accept_api_code=True)
    body = payload.get("body") or []
    log_event(
        "extractors.subtitle_success",
        "Subtitle payload fetched.",
        level=logging.DEBUG,
        bvid=bvid,
        subtitle_url=sanitize_url(url),
        event_count=len(body) if isinstance(body, list) else None,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return payload


def subtitle_stem(page_index: int, item_index: int, item: dict[str, Any]) -> str:
    lang = safe_name(item.get("lan") or item.get("lan_doc") or f"item_{item_index}")
    return f"page_{page_index:03}_{item_index}_{lang}"


def fetch_danmaku(
    client: BiliClient,
    *,
    bvid: str,
    aid: int,
    cid: int,
    page_index: int,
    duration: int,
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    segment_count = max(1, math.ceil(max(duration, 1) / 360))
    rows: list[dict[str, Any]] = []
    fetched_at = utc_now_iso()
    log_event(
        "extractors.danmaku_start",
        "Fetching danmaku segments.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        cid=cid,
        page_index=page_index,
        duration=duration,
        segment_count=segment_count,
    )
    for segment_index in range(1, segment_count + 1):
        segment_started = time.perf_counter()
        raw = client.get_bytes(
            DM_SEG_API,
            params={"type": 1, "oid": cid, "pid": aid, "segment_index": segment_index},
            referer=f"https://www.bilibili.com/video/{bvid}/",
        )
        elems = decode_dm_seg_mobile_reply(raw)
        log_event(
            "extractors.danmaku_segment_success",
            "Danmaku segment decoded.",
            level=logging.DEBUG,
            bvid=bvid,
            aid=aid,
            cid=cid,
            page_index=page_index,
            segment_index=segment_index,
            raw_bytes=len(raw),
            event_count=len(elems),
            elapsed_ms=round((time.perf_counter() - segment_started) * 1000),
        )
        for elem in elems:
            progress = int(elem.get("progress") or 0)
            rows.append(
                {
                    "bvid": bvid,
                    "aid": aid,
                    "page_index": page_index,
                    "cid": cid,
                    "source": "danmaku",
                    "source_api": DM_SEG_API,
                    "fetched_at": fetched_at,
                    "start_ms": progress,
                    "end_ms": progress,
                    "status": "ok",
                    "error_code": None,
                    "error_message": None,
                    "segment_index": segment_index,
                    "dmid": elem.get("id_str") or elem.get("id"),
                    "user_hash": elem.get("user_hash"),
                    "mode": elem.get("mode"),
                    "fontsize": elem.get("fontsize"),
                    "color": elem.get("color"),
                    "ctime": elem.get("ctime"),
                    "pool": elem.get("pool"),
                    "text": elem.get("content") or "",
                }
            )
    rows.sort(key=lambda item: (item.get("start_ms") or 0, str(item.get("dmid") or "")))
    log_event(
        "extractors.danmaku_success",
        "Danmaku rows normalized.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        cid=cid,
        page_index=page_index,
        segment_count=segment_count,
        event_count=len(rows),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return rows


def normalize_reply(
    reply: dict[str, Any],
    *,
    bvid: str,
    aid: int,
    page_index: int | None,
    cid: int | None,
    fetched_at: str,
    source_api: str,
    is_hot: bool = False,
) -> dict[str, Any]:
    member = reply.get("member") or {}
    content = reply.get("content") or {}
    return {
        "bvid": bvid,
        "aid": aid,
        "page_index": page_index,
        "cid": cid,
        "source": "comment_hot" if is_hot else "comment",
        "source_api": source_api,
        "fetched_at": fetched_at,
        "start_ms": None,
        "end_ms": None,
        "status": "ok",
        "error_code": None,
        "error_message": None,
        "rpid": reply.get("rpid"),
        "root": reply.get("root"),
        "parent": reply.get("parent"),
        "dialog": reply.get("dialog"),
        "mid": member.get("mid"),
        "uname": member.get("uname"),
        "message": content.get("message") or "",
        "like": reply.get("like"),
        "ctime": reply.get("ctime"),
        "floor": reply.get("floor"),
        "reply_count": reply.get("rcount"),
        "state": reply.get("state"),
    }


def fetch_comments(
    client: BiliClient,
    *,
    bvid: str,
    aid: int,
    comment_pages: int,
    reply_pages: int,
    all_comments: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    fetched_at = utc_now_iso()
    flat: list[dict[str, Any]] = []
    tree: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    hot_seen: set[int] = set()
    pn = 1
    log_event(
        "extractors.comments_start",
        "Fetching comments.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        comment_pages=comment_pages,
        reply_pages=reply_pages,
        all_comments=all_comments,
    )
    while True:
        if not all_comments and pn > comment_pages:
            log_event(
                "extractors.comments_page_limit",
                "Comment page limit reached.",
                level=logging.DEBUG,
                bvid=bvid,
                aid=aid,
                next_page=pn,
                comment_pages=comment_pages,
            )
            break
        if pn > COMMENT_PAGE_SAFETY_CAP:
            truncations.append(
                {
                    "cap_kind": "comments",
                    "cap_pages": COMMENT_PAGE_SAFETY_CAP,
                    "next_page": pn,
                }
            )
            log_event(
                "extractors.comments_safety_cap",
                "Comment safety cap reached.",
                level=logging.WARNING,
                bvid=bvid,
                aid=aid,
                cap_pages=COMMENT_PAGE_SAFETY_CAP,
                next_page=pn,
            )
            break
        page_started = time.perf_counter()
        payload = client.get_json(
            REPLY_API,
            params={"oid": aid, "type": 1, "pn": pn, "ps": 20, "sort": 2},
            accept_api_code=False,
        )
        data = payload.get("data") or {}
        if pn == 1:
            for hot in data.get("hots") or []:
                rpid = hot.get("rpid")
                if rpid in hot_seen:
                    continue
                hot_seen.add(rpid)
                flat.append(
                    normalize_reply(
                        hot,
                        bvid=bvid,
                        aid=aid,
                        page_index=None,
                        cid=None,
                        fetched_at=fetched_at,
                        source_api=REPLY_API,
                        is_hot=True,
                    )
                )
        replies = list(data.get("replies") or [])
        log_event(
            "extractors.comments_page_success",
            "Comment page fetched.",
            level=logging.DEBUG,
            bvid=bvid,
            aid=aid,
            page=pn,
            reply_count=len(replies),
            hot_count=len(data.get("hots") or []) if pn == 1 else 0,
            elapsed_ms=round((time.perf_counter() - page_started) * 1000),
        )
        if not replies:
            break
        for reply in replies:
            root_row = normalize_reply(
                reply,
                bvid=bvid,
                aid=aid,
                page_index=None,
                cid=None,
                fetched_at=fetched_at,
                source_api=REPLY_API,
            )
            flat.append(root_row)
            children_result = _fetch_reply_children_result(
                client,
                bvid=bvid,
                aid=aid,
                root_rpid=int(reply.get("rpid") or 0),
                fetched_at=fetched_at,
                reply_pages=reply_pages,
                all_comments=all_comments,
            )
            children = children_result["replies"]
            flat.extend(children)
            truncations.extend(children_result["truncations"])
            tree.append({"comment": root_row, "replies": children})
        page = data.get("page") or {}
        count = int(page.get("count") or 0)
        size = int(page.get("size") or 20)
        if count and pn >= math.ceil(count / max(size, 1)):
            break
        pn += 1
    log_event(
        "extractors.comments_success",
        "Comments normalized.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        comment_count=len(flat),
        tree_count=len(tree),
        truncation_count=len(truncations),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return {"comments": flat, "tree": tree, "truncations": truncations}


def fetch_reply_children(
    client: BiliClient,
    *,
    bvid: str,
    aid: int,
    root_rpid: int,
    fetched_at: str,
    reply_pages: int,
    all_comments: bool,
) -> list[dict[str, Any]]:
    return _fetch_reply_children_result(
        client,
        bvid=bvid,
        aid=aid,
        root_rpid=root_rpid,
        fetched_at=fetched_at,
        reply_pages=reply_pages,
        all_comments=all_comments,
    )["replies"]


def _fetch_reply_children_result(
    client: BiliClient,
    *,
    bvid: str,
    aid: int,
    root_rpid: int,
    fetched_at: str,
    reply_pages: int,
    all_comments: bool,
) -> dict[str, list[dict[str, Any]]]:
    if not root_rpid:
        log_event(
            "extractors.comment_children_skip",
            "Skipping child comments because root rpid is empty.",
            level=logging.DEBUG,
            bvid=bvid,
            aid=aid,
        )
        return {"replies": [], "truncations": []}
    children: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    pn = 1
    log_event(
        "extractors.comment_children_start",
        "Fetching child comments.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        root_rpid=root_rpid,
        reply_pages=reply_pages,
        all_comments=all_comments,
    )
    while True:
        if not all_comments and pn > reply_pages:
            log_event(
                "extractors.comment_children_page_limit",
                "Child comment page limit reached.",
                level=logging.DEBUG,
                bvid=bvid,
                aid=aid,
                root_rpid=root_rpid,
                next_page=pn,
                reply_pages=reply_pages,
            )
            break
        if pn > REPLY_PAGE_SAFETY_CAP:
            truncations.append(
                {
                    "cap_kind": "replies",
                    "cap_pages": REPLY_PAGE_SAFETY_CAP,
                    "next_page": pn,
                    "root_rpid": root_rpid,
                }
            )
            log_event(
                "extractors.comment_children_safety_cap",
                "Child comment safety cap reached.",
                level=logging.WARNING,
                bvid=bvid,
                aid=aid,
                root_rpid=root_rpid,
                cap_pages=REPLY_PAGE_SAFETY_CAP,
                next_page=pn,
            )
            break
        page_started = time.perf_counter()
        payload = client.get_json(
            REPLY_REPLIES_API,
            params={"oid": aid, "type": 1, "root": root_rpid, "pn": pn, "ps": 20},
            accept_api_code=False,
        )
        data = payload.get("data") or {}
        replies = list(data.get("replies") or [])
        log_event(
            "extractors.comment_children_page_success",
            "Child comment page fetched.",
            level=logging.DEBUG,
            bvid=bvid,
            aid=aid,
            root_rpid=root_rpid,
            page=pn,
            reply_count=len(replies),
            elapsed_ms=round((time.perf_counter() - page_started) * 1000),
        )
        if not replies:
            break
        for reply in replies:
            children.append(
                normalize_reply(
                    reply,
                    bvid=bvid,
                    aid=aid,
                    page_index=None,
                    cid=None,
                    fetched_at=fetched_at,
                    source_api=REPLY_REPLIES_API,
                )
            )
        page = data.get("page") or {}
        count = int(page.get("count") or 0)
        size = int(page.get("size") or 20)
        if count and pn >= math.ceil(count / max(size, 1)):
            break
        pn += 1
    log_event(
        "extractors.comment_children_success",
        "Child comments normalized.",
        level=logging.DEBUG,
        bvid=bvid,
        aid=aid,
        root_rpid=root_rpid,
        reply_count=len(children),
        truncation_count=len(truncations),
    )
    return {"replies": children, "truncations": truncations}
