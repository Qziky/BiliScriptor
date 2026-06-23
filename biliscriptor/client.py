from __future__ import annotations

import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    @property
    def cookie_names(self) -> list[str]:
        return sorted({cookie.name for cookie in self.cookie_jar})

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_at = time.monotonic()

    def _retry_delay(self, attempt: int) -> None:
        time.sleep(min(2.0, 0.5 * (2 ** attempt)))

    @staticmethod
    def _should_retry_error(error: BiliApiError) -> bool:
        if error.status in {"network_failed", "rate_limited"}:
            return True
        return isinstance(error.code, int) and 500 <= error.code <= 599

    def _request_once(self, url: str, *, referer: str | None = None) -> bytes:
        self._throttle()
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
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
            raise BiliApiError(normalize_error_status(code, exc.code), code, message, url) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BiliApiError("network_failed", None, str(exc), url) from exc

    def _request(self, url: str, *, referer: str | None = None) -> bytes:
        for attempt in range(self.max_retries + 1):
            try:
                return self._request_once(url, referer=referer)
            except BiliApiError as exc:
                if attempt >= self.max_retries or not self._should_retry_error(exc):
                    raise
                self._retry_delay(attempt)
        raise RuntimeError("unreachable request retry state")

    def get_bytes(self, url: str, *, params: dict[str, Any] | None = None, referer: str | None = None) -> bytes:
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
        payload = _decode_json_payload(raw, url)
        code = payload.get("code")
        if accept_api_code or code is None or code == 0:
            return payload
        raise BiliApiError(normalize_error_status(code), code, _message_from_payload(payload), url)

    def get_wbi_keys(self, *, refresh: bool = False) -> tuple[str, str]:
        if self._wbi_keys and not refresh:
            return self._wbi_keys
        payload = self.get_json("https://api.bilibili.com/x/web-interface/nav", accept_api_code=True)
        wbi_img = ((payload.get("data") or {}).get("wbi_img") or {})
        img_url = wbi_img.get("img_url") or ""
        sub_url = wbi_img.get("sub_url") or ""
        img_key = Path(urllib.parse.urlparse(img_url).path).stem
        sub_key = Path(urllib.parse.urlparse(sub_url).path).stem
        if not img_key or not sub_key:
            raise BiliApiError("wbi_key_failed", payload.get("code"), "Failed to obtain WBI keys.", "nav")
        self._wbi_keys = (img_key, sub_key)
        return self._wbi_keys

    def get_wbi_json(self, url: str, params: dict[str, Any], *, referer: str | None = None) -> dict[str, Any]:
        for refresh in (False, True):
            img_key, sub_key = self.get_wbi_keys(refresh=refresh)
            signed = sign_wbi(params, img_key, sub_key)
            payload = self.get_json(url, params=signed, referer=referer)
            data = payload.get("data")
            if not (isinstance(data, dict) and "v_voucher" in data):
                return payload
        raise BiliApiError("risk_control", "v_voucher", "WBI signing or risk control failed.", url)
