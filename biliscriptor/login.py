from __future__ import annotations

import http.cookiejar
import logging
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import qr_login
from .logging_config import log_event, log_exception, sanitize_url


def run_login(
    *,
    cookie_file: Path,
    qr_file: Path,
    no_open: bool = False,
    generate_only: bool = False,
    poll_interval: float = 2.0,
) -> int:
    cookie_file = cookie_file.resolve()
    qr_file = qr_file.resolve()
    log_event(
        "login.start",
        "Login flow started.",
        level=logging.INFO,
        cookie_file=str(cookie_file),
        qr_file=str(qr_file),
        no_open=no_open,
        generate_only=generate_only,
        poll_interval=poll_interval,
    )
    cookie_jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    try:
        generated, _ = qr_login.api_get(opener, qr_login.LOGIN_GENERATE_URL)
    except Exception as exc:  # noqa: BLE001
        log_exception("login.qr_request_failed", exc, "Failed to request login QR.", url=qr_login.LOGIN_GENERATE_URL)
        print(f"Failed to request login QR: {exc}", file=sys.stderr)
        return 1

    if generated.get("code") != 0:
        log_event(
            "login.qr_api_failed",
            "Login QR API returned failure.",
            level=logging.ERROR,
            api_code=generated.get("code"),
            message=generated.get("message"),
        )
        print(
            f"Login QR API failed: code={generated.get('code')} message={generated.get('message')}",
            file=sys.stderr,
        )
        return 1

    data = generated.get("data") or {}
    login_url = data.get("url")
    qrcode_key = data.get("qrcode_key")
    if not login_url or not qrcode_key:
        log_event(
            "login.qr_response_invalid",
            "Login QR API returned no url/qrcode_key.",
            level=logging.ERROR,
            response_keys=sorted(generated.keys()),
            data_keys=sorted(data.keys()),
        )
        print("Login QR API returned no url/qrcode_key.", file=sys.stderr)
        return 1

    qr_file.parent.mkdir(parents=True, exist_ok=True)
    qr_file.write_text(qr_login.make_qr_svg(login_url), encoding="utf-8")
    log_event(
        "login.qr_saved",
        "Login QR SVG saved.",
        level=logging.INFO,
        qr_file=str(qr_file),
        login_url=sanitize_url(login_url),
        qrcode_key=qrcode_key,
        bytes=qr_file.stat().st_size,
    )
    print(f"QR code saved to: {qr_file}")
    print("Open it and scan with the Bilibili mobile app.")
    if not no_open:
        webbrowser.open(qr_file.as_uri())
        log_event("login.qr_opened", "Login QR SVG opened in browser.", level=logging.INFO, qr_file=str(qr_file))
    if generate_only:
        log_event("login.generate_only_end", "Login flow ended after QR generation.", level=logging.INFO)
        print("Generated only. Run without --generate-only to keep polling and save cookies after confirmation.")
        return 0

    poll_url = qr_login.LOGIN_POLL_URL + "?" + urllib.parse.urlencode({"qrcode_key": qrcode_key})
    log_event("login.poll_start", "Login polling started.", level=logging.INFO, poll_url=sanitize_url(poll_url))
    while True:
        time.sleep(max(poll_interval, 1.0))
        try:
            polled, _ = qr_login.api_get(opener, poll_url)
        except Exception as exc:  # noqa: BLE001
            log_exception(
                "login.poll_failed",
                exc,
                "Login polling request failed and will retry.",
                level=logging.WARNING,
                poll_url=sanitize_url(poll_url),
            )
            print(f"Polling failed, will retry: {exc}")
            continue

        poll_data = polled.get("data") or {}
        code = poll_data.get("code")
        message = poll_data.get("message") or polled.get("message") or ""
        log_event(
            "login.poll_status",
            "Login poll status received.",
            level=logging.INFO,
            code=code,
            message=message,
            data_keys=sorted(poll_data.keys()),
        )
        if code == 0:
            qr_login.save_cookie_jar(cookie_jar, cookie_file)
            names = sorted({cookie.name for cookie in cookie_jar})
            log_event(
                "login.success",
                "Login succeeded and cookies were saved.",
                level=logging.INFO,
                cookie_file=str(cookie_file),
                cookie_names=names,
            )
            print("Login succeeded.")
            print(f"Cookies saved to: {cookie_file}")
            print(f"Saved cookie names: {', '.join(names)}")
            return 0
        if code == 86101:
            print("Waiting for scan...")
        elif code == 86090:
            print("Scanned. Please confirm login on your phone...")
        elif code == 86038:
            log_event("login.qr_expired", "Login QR expired.", level=logging.WARNING, code=code, message=message)
            print("QR code expired. Run this command again.", file=sys.stderr)
            return 1
        else:
            log_event("login.poll_unexpected", "Unexpected login poll status.", level=logging.WARNING, code=code, message=message)
            print(f"Unexpected login status: code={code} message={message}")
