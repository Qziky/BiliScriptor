from __future__ import annotations

import hashlib
import http.cookiejar
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logging_config import log_event, log_exception, new_request_id, sanitize_mapping_keys, sanitize_url


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

ERROR_STATUS = {
    -101: "not_logged_in",
    -111: "csrf_failed",
    -352: "risk_control",
    -400: "bad_request",
    -403: "permission_denied",
    -404: "not_found",
    -412: "risk_control",
    -503: "rate_limited",
    -688: "geo_limited",
    -689: "geo_limited",
    -799: "rate_limited",
    62002: "video_invisible",
}


@dataclass
class BiliApiError(RuntimeError):
    status: str
    code: int | str | None
    message: str
    url: str

    def __str__(self) -> str:
        return f"{self.status}: code={self.code} message={self.message} url={self.url}"


def _decode_json_payload(raw: bytes, url: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BiliApiError("invalid_json", None, str(exc), url) from exc
    if not isinstance(payload, dict):
        raise BiliApiError("invalid_json", None, "API response was not a JSON object.", url)
    return payload


def _message_from_payload(payload: dict[str, Any], fallback: str = "") -> str:
    message = payload.get("message") or payload.get("msg") or fallback
    return str(message)


def normalize_error_status(code: int | str | None, http_status: int | None = None) -> str:
    if isinstance(code, int) and code in ERROR_STATUS:
        return ERROR_STATUS[code]
    if http_status == 412:
        return "risk_control"
    if http_status == 429:
        return "rate_limited"
    if http_status == 403:
        return "permission_denied"
    if http_status == 404:
        return "not_found"
    if isinstance(code, int) and code != 0:
        return "api_failed"
    return "request_failed"


def get_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, str]:
    signed = {key: str(value) for key, value in params.items() if value is not None}
    signed["wts"] = str(round(time.time()))
    signed = dict(sorted(signed.items()))
    signed = {key: "".join(char for char in value if char not in "!'()*") for key, value in signed.items()}
    query = urllib.parse.urlencode(signed, quote_via=urllib.parse.quote)
    signed["w_rid"] = hashlib.md5((query + get_mixin_key(img_key, sub_key)).encode("utf-8")).hexdigest()
    return signed


class BiliClient:
    def __init__(
        self,
        cookie_file: Path | None = None,
        rate_limit: float = 1.0,
        timeout: int = 30,
        max_retries: int = 2,
    ) -> None:
        self.cookie_file = cookie_file
        self.rate_limit = max(rate_limit, 0.0)
        self.timeout = timeout
        self.max_retries = max(max_retries, 0)
        self._last_request_at = 0.0
        self._wbi_keys: tuple[str, str] | None = None
        self.cookie_jar = http.cookiejar.MozillaCookieJar()
        if cookie_file and cookie_file.exists():
            self.cookie_jar.load(str(cookie_file), ignore_discard=True, ignore_expires=True)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        log_event(
            "client.created",
            "BiliClient created.",
            level=logging.DEBUG,
            cookie_file=str(cookie_file) if cookie_file else None,
            cookie_names=self.cookie_names,
            rate_limit=self.rate_limit,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    @property
    def cookie_names(self) -> list[str]:
        return sorted({cookie.name for cookie in self.cookie_jar})

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit:
            wait_seconds = self.rate_limit - elapsed
            log_event(
                "client.throttle_wait",
                "Waiting for request rate limit.",
                level=logging.DEBUG,
                wait_ms=round(wait_seconds * 1000),
                rate_limit=self.rate_limit,
            )
            time.sleep(wait_seconds)
        self._last_request_at = time.monotonic()

    def _retry_delay(self, attempt: int) -> None:
        delay = min(2.0, 0.5 * (2 ** attempt))
        log_event(
            "client.retry_wait",
            "Waiting before retry.",
            level=logging.DEBUG,
            attempt=attempt + 1,
            delay_ms=round(delay * 1000),
        )
        time.sleep(delay)

    @staticmethod
    def _should_retry_error(error: BiliApiError) -> bool:
        if error.status in {"network_failed", "rate_limited"}:
            return True
        return isinstance(error.code, int) and 500 <= error.code <= 599

    def _request_once(self, url: str, *, referer: str | None = None, request_id: str, attempt: int) -> bytes:
        self._throttle()
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        parsed = urllib.parse.urlsplit(url)
        query_keys = sorted(urllib.parse.parse_qs(parsed.query, keep_blank_values=True))
        started = time.perf_counter()
        log_event(
            "client.request_start",
            "HTTP request started.",
            level=logging.DEBUG,
            request_id=request_id,
            attempt=attempt + 1,
            url=sanitize_url(url),
            endpoint=urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
            query_keys=query_keys,
            referer=referer,
            timeout=self.timeout,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                log_event(
                    "client.request_success",
                    "HTTP request succeeded.",
                    level=logging.DEBUG,
                    request_id=request_id,
                    attempt=attempt + 1,
                    url=sanitize_url(url),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    response_bytes=len(raw),
                )
                return raw
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            finally:
                exc.close()
            code: int | str | None = exc.code
            message = str(exc.reason)
            if raw:
                try:
                    payload = _decode_json_payload(raw, url)
                    code = payload.get("code", exc.code) or exc.code
                    message = _message_from_payload(payload, message)
                except BiliApiError:
                    pass
            error = BiliApiError(normalize_error_status(code, exc.code), code, message, url)
            log_exception(
                "client.request_http_error",
                error,
                "HTTP request returned an error response.",
                level=logging.WARNING,
                request_id=request_id,
                attempt=attempt + 1,
                url=sanitize_url(url),
                http_status=exc.code,
                api_code=code,
                status=error.status,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                response_bytes=len(raw or b""),
            )
            raise error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            error = BiliApiError("network_failed", None, str(exc), url)
            log_exception(
                "client.request_network_error",
                error,
                "HTTP request failed before receiving a response.",
                level=logging.WARNING,
                request_id=request_id,
                attempt=attempt + 1,
                url=sanitize_url(url),
                status=error.status,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
            raise error from exc

    def _request(self, url: str, *, referer: str | None = None) -> bytes:
        request_id = new_request_id()
        for attempt in range(self.max_retries + 1):
            try:
                return self._request_once(url, referer=referer, request_id=request_id, attempt=attempt)
            except BiliApiError as exc:
                if attempt >= self.max_retries or not self._should_retry_error(exc):
                    log_event(
                        "client.request_give_up",
                        "HTTP request will not be retried.",
                        level=logging.DEBUG,
                        request_id=request_id,
                        attempt=attempt + 1,
                        status=exc.status,
                        code=exc.code,
                        url=sanitize_url(url),
                    )
                    raise
                log_event(
                    "client.request_retry",
                    "HTTP request scheduled for retry.",
                    level=logging.INFO,
                    request_id=request_id,
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    status=exc.status,
                    code=exc.code,
                    url=sanitize_url(url),
                )
                self._retry_delay(attempt)
        raise RuntimeError("unreachable request retry state")

    def get_bytes(self, url: str, *, params: dict[str, Any] | None = None, referer: str | None = None) -> bytes:
        log_event(
            "client.get_bytes",
            "Preparing byte request.",
            level=logging.DEBUG,
            url=sanitize_url(url),
            param_keys=sanitize_mapping_keys(params),
            referer=referer,
        )
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return self._request(url, referer=referer)

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        accept_api_code: bool = False,
    ) -> dict[str, Any]:
        raw = self.get_bytes(url, params=params, referer=referer)
        try:
            payload = _decode_json_payload(raw, url)
        except BiliApiError as exc:
            log_exception(
                "client.json_decode_failed",
                exc,
                "Failed to decode JSON response.",
                url=sanitize_url(url),
                response_bytes=len(raw),
            )
            raise
        code = payload.get("code")
        if accept_api_code or code is None or code == 0:
            log_event(
                "client.get_json_success",
                "JSON API response accepted.",
                level=logging.DEBUG,
                url=sanitize_url(url),
                param_keys=sanitize_mapping_keys(params),
                api_code=code,
                accept_api_code=accept_api_code,
                response_keys=sanitize_mapping_keys(payload),
            )
            return payload
        error = BiliApiError(normalize_error_status(code), code, _message_from_payload(payload), url)
        log_exception(
            "client.api_error",
            error,
            "JSON API response reported failure.",
            level=logging.WARNING,
            url=sanitize_url(url),
            param_keys=sanitize_mapping_keys(params),
            api_code=code,
            status=error.status,
        )
        raise error

    def get_wbi_keys(self, *, refresh: bool = False) -> tuple[str, str]:
        if self._wbi_keys and not refresh:
            log_event("client.wbi_keys_cache_hit", "Using cached WBI keys.", level=logging.DEBUG)
            return self._wbi_keys
        log_event("client.wbi_keys_fetch", "Fetching WBI keys.", level=logging.DEBUG, refresh=refresh)
        payload = self.get_json("https://api.bilibili.com/x/web-interface/nav", accept_api_code=True)
        wbi_img = ((payload.get("data") or {}).get("wbi_img") or {})
        img_url = wbi_img.get("img_url") or ""
        sub_url = wbi_img.get("sub_url") or ""
        img_key = Path(urllib.parse.urlparse(img_url).path).stem
        sub_key = Path(urllib.parse.urlparse(sub_url).path).stem
        if not img_key or not sub_key:
            error = BiliApiError("wbi_key_failed", payload.get("code"), "Failed to obtain WBI keys.", "nav")
            log_exception("client.wbi_keys_failed", error, "Failed to obtain WBI keys.")
            raise error
        self._wbi_keys = (img_key, sub_key)
        log_event("client.wbi_keys_success", "WBI keys fetched.", level=logging.DEBUG, refresh=refresh, key_count=2)
        return self._wbi_keys

    def get_wbi_json(self, url: str, params: dict[str, Any], *, referer: str | None = None) -> dict[str, Any]:
        for refresh in (False, True):
            log_event(
                "client.wbi_request_sign",
                "Preparing WBI signed request.",
                level=logging.DEBUG,
                url=sanitize_url(url),
                param_keys=sanitize_mapping_keys(params),
                refresh=refresh,
            )
            img_key, sub_key = self.get_wbi_keys(refresh=refresh)
            signed = sign_wbi(params, img_key, sub_key)
            payload = self.get_json(url, params=signed, referer=referer)
            data = payload.get("data")
            if not (isinstance(data, dict) and "v_voucher" in data):
                return payload
            log_event(
                "client.wbi_voucher",
                "WBI response contained v_voucher; refreshing keys.",
                level=logging.WARNING,
                url=sanitize_url(url),
                refresh=refresh,
            )
        raise BiliApiError("risk_control", "v_voucher", "WBI signing or risk control failed.", url)
