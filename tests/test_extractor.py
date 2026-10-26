import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import extract
import baseline.extract as baseline_extract


ROOT = Path(__file__).resolve().parents[1]


class PromptAndCleaningTests(unittest.TestCase):
    def test_parse_json_with_fence(self):
        raw = '```json\n{"tests": [{"test_name": "Colour", "value": "Dark Yellow", "unit": null, "reference_range": "Pale Yellow"}]}\n```'
        self.assertEqual(extract.parse(raw)[0]["value"], "Dark Yellow")

    def test_parse_skips_non_json_prefix(self):
        raw = 'Result follows:\n{"tests": [{"test_name": "CRP", "value": "<0.5", "unit": "mg/L", "reference_range": "<5.0"}]}\nDone.'
        self.assertEqual(extract.parse(raw)[0]["value"], "<0.5")

    def test_parse_rejects_missing_tests_array(self):
        with self.assertRaisesRegex(ValueError, '"tests" array'):
            extract.parse("not JSON")

    def test_parse_rejects_invalid_row_type(self):
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            extract.parse('{"tests": ["not a row"]}')

    def test_parse_accepts_valid_empty_result(self):
        self.assertEqual(extract.parse('{"tests": []}'), [])

    def test_clean_preserves_text_and_comparison_values(self):
        result = extract.clean([
            {"test_name": " Colour ", "value": " Dark Yellow ", "unit": " ", "reference_range": " Pale Yellow "},
            {"test_name": "CRP", "value": "<0.5", "unit": "mg/L", "reference_range": "<5.0"},
            {"test_name": "FBS", "value": "88", "unit": "mg/dL", "reference_range": "70-100"},
        ])
        self.assertEqual(result[0], {"test_name": "Colour", "value": "Dark Yellow", "unit": None, "reference_range": "Pale Yellow"})
        self.assertEqual(result[1]["value"], "<0.5")
        self.assertEqual(result[2]["value"], "88")
        self.assertTrue(all(set(row) == {"test_name", "value", "unit", "reference_range"} for row in result))

    def test_clean_preserves_numeric_values_if_model_returns_numbers(self):
        result = extract.clean([{"test_name": "FBS", "value": 88.0, "unit": "mg/dL", "reference_range": None}])
        self.assertEqual(result[0]["value"], 88.0)
        self.assertIsNone(result[0]["reference_range"])

    def test_clean_rejects_non_finite_number(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            extract.clean([{"test_name": "FBS", "value": float("nan"), "unit": None, "reference_range": None}])

    def test_clean_rejects_missing_test_name(self):
        with self.assertRaisesRegex(ValueError, "missing a usable test_name"):
            extract.clean([{"value": "13.5", "unit": "g/dL", "reference_range": None}])

    def test_output_has_only_required_fields_and_is_json_serializable(self):
        result = {"tests": extract.clean([{"test_name": "Hb", "value": "13.5", "unit": "g/dL", "reference_range": "13-17", "status": "normal"}])}
        self.assertEqual(set(result), {"tests"})
        self.assertEqual(set(result["tests"][0]), {"test_name", "value", "unit", "reference_range"})
        json.dumps(result, allow_nan=False)


class DirectFileInputTests(unittest.TestCase):
    def test_pdf_is_submitted_as_original_pdf_bytes_without_renderer(self):
        report = ROOT / "reports" / "report_11.pdf"
        media_type, encoded = extract.load_media(report)
        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(base64.b64decode(encoded), report.read_bytes())
        self.assertTrue(base64.b64decode(encoded).startswith(b"%PDF-"))

    def test_pdf_is_multi_page_and_still_submitted_as_one_document(self):
        import re
        report = ROOT / "reports" / "report_11.pdf"
        raw = report.read_bytes()
        # These supplied PDFs have uncompressed page dictionaries. Match /Page
        # as a whole word so the /Pages tree node is not counted as a page.
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", raw)), 2)
        media_type, encoded = extract.load_media(report)
        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(base64.b64decode(encoded), raw)

    def test_baseline_adapter_needs_no_pdf_library_and_requests_page_one_only(self):
        report = ROOT / "reports" / "report_11.pdf"
        raw = report.read_bytes()
        media_type, encoded = baseline_extract.load_image(report)
        self.assertEqual(media_type, "application/pdf")
        self.assertEqual(base64.b64decode(encoded), raw)
        self.assertIn("inspect only page 1", baseline_extract.PROMPT)

    def test_image_loads_as_single_media_item(self):
        report = ROOT / "reports" / "report_08.jpg"
        media_type, encoded = extract.load_media(report)
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(base64.b64decode(encoded), report.read_bytes())

    def test_missing_file_fails_clearly(self):
        with self.assertRaisesRegex(RuntimeError, "does not exist"):
            extract.load_media(ROOT / "reports" / "not_a_real_report.pdf")

    def test_unsupported_type_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.docx"
            path.write_bytes(b"content")
            with self.assertRaisesRegex(RuntimeError, "Unsupported file type"):
                extract.load_media(path)

    def test_empty_file_fails_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.pdf"
            path.write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "empty"):
                extract.load_media(path)

    def test_oversized_pdf_rejected_before_reading_all_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.pdf"
            with path.open("wb") as stream:
                stream.truncate(extract.MAX_INLINE_PDF_BYTES + 1)
            with self.assertRaisesRegex(RuntimeError, "50 MB"):
                extract.load_media(path)

    def test_generate_content_request_uses_application_pdf_and_records_usage(self):
        fake_response = {
            "candidates": [{"content": {"parts": [{"text": '{"tests": []}'}]}}],
            "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 7},
        }
        report_bytes = (ROOT / "reports" / "report_01.pdf").read_bytes()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with mock.patch.object(extract, "THINKING_LEVEL", "low"):
                with mock.patch("extract.urllib.request.urlopen") as mocked_urlopen:
                    mocked_urlopen.return_value.__enter__.return_value = __import__("io").BytesIO(json.dumps(fake_response).encode())
                    result_text = extract.call_model("application/pdf", base64.b64encode(report_bytes).decode())
                    request = mocked_urlopen.call_args.args[0]
                    body = json.loads(request.data.decode())
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low")
        self.assertEqual(body["contents"][0]["parts"][1]["inlineData"]["mimeType"], "application/pdf")
        self.assertEqual(base64.b64decode(body["contents"][0]["parts"][1]["inlineData"]["data"]), report_bytes)
        self.assertIn('"tests"', result_text)
        self.assertEqual(extract.LAST_USAGE["promptTokenCount"], 123)


    def test_baseline_request_uses_same_low_thinking_level(self):
        fake_response = {"candidates": [{"content": {"parts": [{"text": '{"tests": []}'}]}}]}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with mock.patch.object(baseline_extract, "THINKING_LEVEL", "low"):
                with mock.patch("baseline.extract.urllib.request.urlopen") as mocked_urlopen:
                    mocked_urlopen.return_value.__enter__.return_value = __import__("io").BytesIO(json.dumps(fake_response).encode())
                    baseline_extract.call_model("image/jpeg", base64.b64encode(b"mock-image").decode())
                    request = mocked_urlopen.call_args.args[0]
                    body = json.loads(request.data.decode())
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low")

    def test_transient_503_is_retried_and_success_is_returned(self):
        import io
        error_body = io.BytesIO(b'{"error":{"message":"temporary high demand"}}')
        transient = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "https://example.invalid", 503, "Service Unavailable", {"Content-Type": "application/json"}, error_body
        )
        success = io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": '{"tests": []}'}]}}], "usageMetadata": {"promptTokenCount": 5}}).encode())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with mock.patch("extract.time.sleep") as sleep_mock:
                with mock.patch("extract.urllib.request.urlopen", side_effect=[transient, success]) as urlopen_mock:
                    text = extract.call_model("application/pdf", base64.b64encode(b"%PDF-test").decode())
        self.assertIn('"tests"', text)
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(sleep_mock.call_args.args[0], 1.0)
        self.assertEqual(extract.LAST_RETRY_COUNT, 1)

    def test_baseline_also_retries_transient_503(self):
        import io
        error_body = io.BytesIO(b'{"error":{"message":"temporary high demand"}}')
        transient = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "https://example.invalid", 503, "Service Unavailable", {"Content-Type": "application/json"}, error_body
        )
        success = io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": '{"tests": []}'}]}}]}).encode())
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-not-real"}):
            with mock.patch("baseline.extract.time.sleep"):
                with mock.patch("baseline.extract.urllib.request.urlopen", side_effect=[transient, success]) as urlopen_mock:
                    baseline_extract.call_model("image/jpeg", base64.b64encode(b"mock-image").decode())
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(baseline_extract.LAST_RETRY_COUNT, 1)

    def test_missing_api_key_fails_before_network_request(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("extract.urllib.request.urlopen") as mocked_urlopen:
                with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is missing"):
                    extract.call_model("application/pdf", "YWJj")
                mocked_urlopen.assert_not_called()


class RunLogTests(unittest.TestCase):
    def test_cli_saves_utf8_output_and_logs_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            input_path = directory / "report_01.pdf"
            input_path.write_bytes(b"%PDF-test")
            output_path = directory / "out.json"
            log_path = directory / "run.jsonl"
            result = {"tests": [{"test_name": "Hb", "value": "13.5", "unit": "g/dL", "reference_range": "13-17"}]}
            with mock.patch.object(extract, "extract", return_value=result):
                with mock.patch.object(extract, "RUN_LOG_PATH", log_path):
                    with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                        code = extract.main([str(input_path), "--output", str(output_path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), result)
            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["extracted_row_count"], 1)

    def test_run_log_contains_metadata_not_result_values_or_key(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "report_01.pdf"
            input_path.write_bytes(b"%PDF-test")
            log_path = Path(directory) / "run.jsonl"
            result = {"tests": [{"test_name": "Private Test Name", "value": "987.65", "unit": "mg/dL", "reference_range": None}]}
            with mock.patch.object(extract, "RUN_LOG_PATH", log_path):
                extract.append_run_log(path=input_path, status="success", duration_seconds=1.23, result=result)
            logged = log_path.read_text(encoding="utf-8")
            record = json.loads(logged)
            self.assertEqual(record["extracted_row_count"], 1)
            self.assertNotIn("Private Test Name", logged)
            self.assertNotIn("987.65", logged)
            self.assertNotIn("test-key-not-real", logged)


if __name__ == "__main__":
    unittest.main()
