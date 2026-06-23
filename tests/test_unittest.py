from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import test_core


class CoreTests(unittest.TestCase):
    def test_extract_bvid_from_url(self) -> None:
        test_core.test_extract_bvid_from_url()

    def test_wbi_signing_shape(self) -> None:
        test_core.test_wbi_signing_shape()

    def test_normalize_error_status(self) -> None:
        test_core.test_normalize_error_status()

    def test_default_comment_depth_is_conservative(self) -> None:
        test_core.test_default_comment_depth_is_conservative()

    def test_subtitle_rows_and_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_subtitle_rows_and_srt(Path(tmp))

    def test_decode_dm_seg_mobile_reply(self) -> None:
        test_core.test_decode_dm_seg_mobile_reply()

    def test_normalize_reply(self) -> None:
        test_core.test_normalize_reply()

    def test_build_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_build_report(Path(tmp))

    def test_build_report_empty_and_failed_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_build_report_empty_and_failed_sections(Path(tmp))


if __name__ == "__main__":
    unittest.main()
