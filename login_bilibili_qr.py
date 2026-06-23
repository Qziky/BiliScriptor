# -*- coding: utf-8 -*-
"""
Scan a Bilibili login QR code and save cookies locally.

Usage:
    python login_bilibili_qr.py
    python login_bilibili_qr.py --cookie-file bilibili_cookies.txt

The script writes a Netscape-format cookie file that can be reused by
fetch_bilibili_subtitles.py. It intentionally does not print sensitive
cookie values.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


LOGIN_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
LOGIN_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
DEFAULT_COOKIE_FILE = "bilibili_cookies.txt"
DEFAULT_QR_FILE = "bilibili_login_qr.svg"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


class BitBuffer:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def append(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def to_bytes(self) -> list[int]:
        out: list[int] = []
        for i in range(0, len(self.bits), 8):
            byte = 0
            for bit in self.bits[i : i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return out


QR_L_TABLE = {
    # version: (data_codewords, ec_codewords_per_block, [(block_count, data_len)])
    1: (19, 7, [(1, 19)]),
    2: (34, 10, [(1, 34)]),
    3: (55, 15, [(1, 55)]),
    4: (80, 20, [(1, 80)]),
    5: (108, 26, [(1, 108)]),
    6: (136, 18, [(2, 68)]),
    7: (156, 20, [(2, 78)]),
    8: (194, 24, [(2, 97)]),
    9: (232, 30, [(2, 116)]),
    10: (274, 18, [(2, 68), (2, 69)]),
}

QR_BYTE_CAPACITY_L = {
    1: 17,
    2: 32,
    3: 53,
    4: 78,
    5: 106,
    6: 134,
    7: 154,
    8: 192,
    9: 230,
    10: 271,
}

ALIGNMENT_POSITIONS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}


def gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = gf_tables()


def gf_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return GF_EXP[GF_LOG[x] + GF_LOG[y]]


def rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= gf_mul(coef, GF_EXP[i])
        poly = nxt
    return poly


def rs_remainder(data: list[int], degree: int) -> list[int]:
    gen = rs_generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(degree):
            rem[i] ^= gf_mul(gen[i + 1], factor)
    return rem


def choose_qr_version(payload: bytes) -> int:
    for version, capacity in QR_BYTE_CAPACITY_L.items():
        if len(payload) <= capacity:
            return version
    raise ValueError("Login URL is too long for the built-in QR encoder.")


def make_data_codewords(payload: bytes, version: int) -> list[int]:
    data_len, _, _ = QR_L_TABLE[version]
    capacity_bits = data_len * 8
    buf = BitBuffer()
    buf.append(0b0100, 4)  # byte mode
    buf.append(len(payload), 8 if version <= 9 else 16)
    for b in payload:
        buf.append(b, 8)
    buf.append(0, min(4, capacity_bits - len(buf.bits)))
    while len(buf.bits) % 8:
        buf.append(0, 1)
    data = buf.to_bytes()
    pad = [0xEC, 0x11]
    i = 0
    while len(data) < data_len:
        data.append(pad[i % 2])
        i += 1
    return data


def interleave_with_ec(data: list[int], version: int) -> list[int]:
    _, ec_len, groups = QR_L_TABLE[version]
    blocks: list[list[int]] = []
    pos = 0
    for count, block_len in groups:
        for _ in range(count):
            block = data[pos : pos + block_len]
            pos += block_len
            blocks.append(block)

    ec_blocks = [rs_remainder(block, ec_len) for block in blocks]
    out: list[int] = []
    for i in range(max(len(block) for block in blocks)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_len):
        for block in ec_blocks:
            out.append(block[i])
    return out


class QRMatrix:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.reserved = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool, reserved: bool = True) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            self.modules[y][x] = dark
            if reserved:
                self.reserved[y][x] = True

    def add_finder(self, x: int, y: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx, yy = x + dx, y + dy
                if not (0 <= xx < self.size and 0 <= yy < self.size):
                    continue
                dark = (
                    0 <= dx <= 6
                    and 0 <= dy <= 6
                    and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                )
                self.set(xx, yy, dark)

    def add_alignment(self, cx: int, cy: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                dark = max(abs(dx), abs(dy)) != 1
                self.set(cx + dx, cy + dy, dark)

    def add_function_patterns(self) -> None:
        self.add_finder(0, 0)
        self.add_finder(self.size - 7, 0)
        self.add_finder(0, self.size - 7)

        for i in range(8, self.size - 8):
            dark = i % 2 == 0
            self.set(i, 6, dark)
            self.set(6, i, dark)

        pos = ALIGNMENT_POSITIONS[self.version]
        for cy in pos:
            for cx in pos:
                overlaps_top_left = cx == 6 and cy == 6
                overlaps_top_right = cx == self.size - 7 and cy == 6
                overlaps_bottom_left = cx == 6 and cy == self.size - 7
                if overlaps_top_left or overlaps_top_right or overlaps_bottom_left:
                    continue
                self.add_alignment(cx, cy)

        self.set(8, self.size - 8, True)
        for i in range(9):
            if i != 6:
                self.reserved[8][i] = True
                self.reserved[i][8] = True
        for i in range(8):
            self.reserved[self.size - 1 - i][8] = True
            self.reserved[8][self.size - 1 - i] = True

        if self.version >= 7:
            self.add_version_info()

    def add_version_info(self) -> None:
        rem = self.version
        for _ in range(12):
            high_bit = (rem >> 11) & 1
            rem <<= 1
            if high_bit:
                rem ^= 0x1F25
        bits = (self.version << 12) | rem
        for i in range(18):
            dark = ((bits >> i) & 1) == 1
            x = self.size - 11 + (i % 3)
            y = i // 3
            self.set(x, y, dark)
            self.set(y, x, dark)

    def add_data(self, codewords: list[int], mask: int = 0) -> None:
        bits: list[int] = []
        for byte in codewords:
            for i in range(7, -1, -1):
                bits.append((byte >> i) & 1)
        bit_index = 0
        upward = True
        x = self.size - 1
        while x > 0:
            if x == 6:
                x -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for y in rows:
                for dx in (0, 1):
                    xx = x - dx
                    if self.reserved[y][xx]:
                        continue
                    dark = bit_index < len(bits) and bits[bit_index] == 1
                    bit_index += 1
                    if self.mask_bit(mask, xx, y):
                        dark = not dark
                    self.modules[y][xx] = dark
            upward = not upward
            x -= 2

    @staticmethod
    def mask_bit(mask: int, x: int, y: int) -> bool:
        if mask == 0:
            return (x + y) % 2 == 0
        raise ValueError("Only QR mask 0 is implemented.")

    def add_format_info(self, mask: int = 0) -> None:
        # Error correction level L uses format bits 01.
        data = (0b01 << 3) | mask
        rem = data
        for _ in range(10):
            high_bit = (rem >> 9) & 1
            rem <<= 1
            if high_bit:
                rem ^= 0x537
        bits = ((data << 10) | rem) ^ 0x5412

        coords1 = [
            (8, 0),
            (8, 1),
            (8, 2),
            (8, 3),
            (8, 4),
            (8, 5),
            (8, 7),
            (8, 8),
            (7, 8),
            (5, 8),
            (4, 8),
            (3, 8),
            (2, 8),
            (1, 8),
            (0, 8),
        ]
        coords2 = (
            [(self.size - 1 - i, 8) for i in range(8)]
            + [(8, self.size - 7 + i) for i in range(7)]
        )
        for i, (x, y) in enumerate(coords1):
            self.set(x, y, ((bits >> i) & 1) == 1)
        for i, (x, y) in enumerate(coords2):
            self.set(x, y, ((bits >> i) & 1) == 1)

    def to_svg(self, border: int = 4, scale: int = 8) -> str:
        total = (self.size + border * 2) * scale
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
            f'viewBox="0 0 {self.size + border * 2} {self.size + border * 2}">',
            '<rect width="100%" height="100%" fill="#fff"/>',
            '<path fill="#000" d="',
        ]
        rects = []
        for y, row in enumerate(self.modules):
            for x, dark in enumerate(row):
                if dark:
                    rects.append(f"M{x + border},{y + border}h1v1h-1z")
        parts.append(" ".join(rects))
        parts.append('"/>')
        parts.append("</svg>")
        return "\n".join(parts)


def make_qr_svg(text: str) -> str:
    payload = text.encode("utf-8")
    version = choose_qr_version(payload)
    data = make_data_codewords(payload, version)
    codewords = interleave_with_ec(data, version)
    qr = QRMatrix(version)
    qr.add_function_patterns()
    qr.add_data(codewords, mask=0)
    qr.add_format_info(mask=0)
    return qr.to_svg()


def api_get(opener: urllib.request.OpenerDirector, url: str) -> tuple[dict, urllib.response.addinfourl]:
    request = urllib.request.Request(url, headers=HEADERS)
    with opener.open(request, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
        return payload, resp


def save_cookie_jar(cookie_jar: http.cookiejar.MozillaCookieJar, cookie_file: Path) -> None:
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_jar.save(str(cookie_file), ignore_discard=True, ignore_expires=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili QR login helper.")
    parser.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE, help="Cookie output path.")
    parser.add_argument("--qr-file", default=DEFAULT_QR_FILE, help="QR SVG output path.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the QR SVG automatically.")
    parser.add_argument("--generate-only", action="store_true", help="Generate a fresh QR SVG and exit without polling.")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between login polls.")
    args = parser.parse_args()

    cookie_file = Path(args.cookie_file).resolve()
    qr_file = Path(args.qr_file).resolve()
    cookie_jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    try:
        generated, _ = api_get(opener, LOGIN_GENERATE_URL)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Failed to request login QR: {exc}", file=sys.stderr)
        return 1

    if generated.get("code") != 0:
        print(f"Login QR API failed: code={generated.get('code')} message={generated.get('message')}", file=sys.stderr)
        return 1

    data = generated.get("data") or {}
    login_url = data.get("url")
    qrcode_key = data.get("qrcode_key")
    if not login_url or not qrcode_key:
        print("Login QR API returned no url/qrcode_key.", file=sys.stderr)
        return 1

    qr_file.write_text(make_qr_svg(login_url), encoding="utf-8")
    print(f"QR code saved to: {qr_file}")
    print("Open it and scan with the Bilibili mobile app.")
    if not args.no_open:
        webbrowser.open(qr_file.as_uri())
    if args.generate_only:
        print("Generated only. Run without --generate-only to keep polling and save cookies after confirmation.")
        return 0

    poll_url = LOGIN_POLL_URL + "?" + urllib.parse.urlencode({"qrcode_key": qrcode_key})
    while True:
        time.sleep(max(args.poll_interval, 1.0))
        try:
            polled, _ = api_get(opener, poll_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Polling failed, will retry: {exc}")
            continue

        poll_data = polled.get("data") or {}
        code = poll_data.get("code")
        message = poll_data.get("message") or polled.get("message") or ""
        if code == 0:
            save_cookie_jar(cookie_jar, cookie_file)
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
            print("QR code expired. Run this script again.", file=sys.stderr)
            return 1
        else:
            print(f"Unexpected login status: code={code} message={message}")


if __name__ == "__main__":
    raise SystemExit(main())
