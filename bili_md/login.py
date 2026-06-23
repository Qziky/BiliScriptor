from __future__ import annotations

import http.cookiejar
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


def run_login(
    *,
    cookie_file: Path,
    qr_file: Path,
    no_open: bool = False,
    generate_only: bool = False,
    poll_interval: float = 2.0,
) -> int:
    try:
        import login_bilibili_qr as legacy
    except ImportError:
        print("login_bilibili_qr.py was not found; cannot run QR login.", file=sys.stderr)
        return 1

    cookie_file = cookie_file.resolve()
    qr_file = qr_file.resolve()
    cookie_jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    try:
        generated, _ = legacy.api_get(opener, legacy.LOGIN_GENERATE_URL)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to request login QR: {exc}", file=sys.stderr)
        return 1

    if generated.get("code") != 0:
        print(
            f"Login QR API failed: code={generated.get('code')} message={generated.get('message')}",
            file=sys.stderr,
        )
        return 1

    data = generated.get("data") or {}
    login_url = data.get("url")
    qrcode_key = data.get("qrcode_key")
    if not login_url or not qrcode_key:
        print("Login QR API returned no url/qrcode_key.", file=sys.stderr)
        return 1

    qr_file.write_text(legacy.make_qr_svg(login_url), encoding="utf-8")
    print(f"QR code saved to: {qr_file}")
    print("Open it and scan with the Bilibili mobile app.")
    if not no_open:
        webbrowser.open(qr_file.as_uri())
    if generate_only:
        print("Generated only. Run without --generate-only to keep polling and save cookies after confirmation.")
        return 0

    poll_url = legacy.LOGIN_POLL_URL + "?" + urllib.parse.urlencode({"qrcode_key": qrcode_key})
    while True:
        time.sleep(max(poll_interval, 1.0))
        try:
            polled, _ = legacy.api_get(opener, poll_url)
        except Exception as exc:  # noqa: BLE001
            print(f"Polling failed, will retry: {exc}")
            continue

        poll_data = polled.get("data") or {}
        code = poll_data.get("code")
        message = poll_data.get("message") or polled.get("message") or ""
        if code == 0:
            legacy.save_cookie_jar(cookie_jar, cookie_file)
            names = sorted({cookie.name for cookie in cookie_jar})
            print("Login succeeded.")
            print(f"Cookies saved to: {cookie_file}")
            print(f"Saved cookie names: {', '.join(names)}")
            return 0
        if code == 86101:
            print("Waiting for scan...")
        elif code == 86090:
            print("Scanned. Please confirm login on your phone...")
        elif code == 86038:
            print("QR code expired. Run this command again.", file=sys.stderr)
            return 1
        else:
            print(f"Unexpected login status: code={code} message={message}")
