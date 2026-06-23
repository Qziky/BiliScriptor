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

    def test_runtime_artifact_defaults_use_runtime_directory(self) -> None:
        test_core.test_runtime_artifact_defaults_use_runtime_directory()

    def test_subtitles_command_exists(self) -> None:
        test_core.test_subtitles_command_exists()

    def test_cli_rejects_invalid_numeric_options(self) -> None:
        test_core.test_cli_rejects_invalid_numeric_options()

    def test_cli_accepts_zero_rate_limit_and_positive_pages(self) -> None:
        test_core.test_cli_accepts_zero_rate_limit_and_positive_pages()

    def test_client_parses_http_json_error_body(self) -> None:
        test_core.test_client_parses_http_json_error_body()

    def test_client_wraps_invalid_json_response(self) -> None:
        test_core.test_client_wraps_invalid_json_response()

    def test_client_retries_rate_limited_http_error(self) -> None:
        test_core.test_client_retries_rate_limited_http_error()

    def test_client_retries_http_5xx_even_with_zero_api_code(self) -> None:
        test_core.test_client_retries_http_5xx_even_with_zero_api_code()

    def test_get_wbi_json_refreshes_keys_on_voucher(self) -> None:
        test_core.test_get_wbi_json_refreshes_keys_on_voucher()

    def test_subtitle_rows_and_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_subtitle_rows_and_srt(Path(tmp))

    def test_decode_dm_seg_mobile_reply(self) -> None:
        test_core.test_decode_dm_seg_mobile_reply()

    def test_normalize_reply(self) -> None:
        test_core.test_normalize_reply()

    def test_fetch_comments_records_comment_safety_cap(self) -> None:
        test_core.test_fetch_comments_records_comment_safety_cap()

    def test_fetch_comments_records_reply_safety_cap(self) -> None:
        test_core.test_fetch_comments_records_reply_safety_cap()

    def test_pipeline_comment_truncation_manifest_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_pipeline_comment_truncation_manifest_fields(Path(tmp))

    def test_build_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_build_report(Path(tmp))

    def test_build_report_empty_and_failed_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_build_report_empty_and_failed_sections(Path(tmp))

    def test_build_report_snapshot_structure_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_core.test_build_report_snapshot_structure_and_privacy(Path(tmp))


if __name__ == "__main__":
    unittest.main()
