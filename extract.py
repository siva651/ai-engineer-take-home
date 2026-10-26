"""Extract structured lab results from a laboratory report using Gemini.

Usage:
    python extract.py reports/report_01.pdf
    python extract.py reports/report_01.pdf --output manual_outputs/report_01.json

The JSON result is printed to stdout. Each CLI run appends non-sensitive run
metadata to run_logs/extraction_runs.jsonl. PDF bytes are sent directly to
Gemini; Poppler or another PDF renderer is not required.
"""
from __future__ import annotations

import base64
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from env_loader import load_env

load_env()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Low thinking is appropriate for faithful extraction and reduces avoidable latency.
# Set GEMINI_THINKING_LEVEL=medium or high to override when explicitly needed.
THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "low").strip().lower()
MAX_INLINE_PDF_BYTES = 50_000_000  # Gemini inline PDF limit is documented as 50 MB
MAX_INLINE_IMAGE_BYTES = 70 * 1024 * 1024
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
MAX_TRANSIENT_RETRIES = 3
LAST_RETRY_COUNT = 0
RUN_LOG_PATH = Path(__file__).resolve().parent / "run_logs" / "extraction_runs.jsonl"

PROMPT = """You extract laboratory test results from medical lab reports.

Return JSON only, in this exact structure:
{"tests": [{"test_name": "...", "value": "...", "unit": null, "reference_range": null}]}

Rules:
- Read every page and extract every reported test-result row, in the order printed.
- Use the test name as printed. Do not replace it with a preferred or "standard" synonym.
- Copy the result faithfully. Preserve decimals, commas, signs, comparison symbols such as < or >, and text results such as Positive, Negative, Clear, or Dark Yellow. Do not force values to be numeric.
- Copy the unit into the unit field only. Never substitute a different unit that seems more familiar. Copy only the interval or threshold into reference_range; do not merge a unit into the reference range.
- When a row has a clearly labelled/shared unit column, apply that unit to the corresponding rows. If a range is printed with a unit suffix in the Ref. Range cell, split it when clear: "0 - 5 /hpf" means unit "/hpf" and reference_range "0 - 5"; "< 5.0 mg/L" means unit "mg/L" and reference_range "< 5.0". If the split is uncertain, do not guess.
- If a Result cell contains both a value and a unit, split them faithfully: "58.9 ng/mL" means value "58.9" and unit "ng/mL". Keep the printed unit unchanged.
- Copy only reference ranges printed in the report. Do not infer or substitute a standard range. Use null if a value, unit, or reference range is absent or cannot be read reliably.
- Do not add status classifications, diagnoses, explanations, or tests not present in the report.
- Include one entry per result row. Do not merge or omit rows.
"""

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
LAST_USAGE: dict[str, Any] = {}


def load_media(path: Path) -> tuple[str, str]:
    """Read a PDF or image and return its MIME type and base64-encoded bytes."""
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise RuntimeError(f"Input path is not a file: {path}")

    suffix = path.suffix.lower()
    media_type = MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise RuntimeError(f"Unsupported file type: {suffix or '(none)'}. Use PDF, JPG, JPEG, or PNG.")

    try:
        size = path.stat().st_size
    except OSError as error:
        raise RuntimeError(f"Cannot access input file: {error}") from None

    if size == 0:
        raise RuntimeError("Input file is empty.")
    if suffix == ".pdf" and size > MAX_INLINE_PDF_BYTES:
        raise RuntimeError("PDF exceeds Gemini's 50 MB inline-input limit. Use a smaller PDF or a supported file-upload workflow.")
    if suffix != ".pdf" and size > MAX_INLINE_IMAGE_BYTES:
        raise RuntimeError("Image exceeds the 70 MiB safe inline-input limit (base64 adds request overhead). Use a smaller image.")

    try:
        data = path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"Cannot read input file: {error}") from None

    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise RuntimeError("The file has a .pdf extension but does not have a valid PDF signature.")
    return media_type, base64.standard_b64encode(data).decode("ascii")


def _error_detail(error: urllib.error.HTTPError) -> str:
    """Extract a short API error message without exposing request secrets."""
    try:
        body = error.read(4096).decode("utf-8", errors="replace")
        parsed = json.loads(body)
        message = parsed.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return " ".join(message.split())[:400]
    except (OSError, ValueError, AttributeError):
        pass
    return "check API access, model name, request format, and quota"


def _retry_delay(error: urllib.error.HTTPError, retry_index: int) -> float:
    """Use Retry-After when supplied; otherwise wait 1, 2, then 4 seconds."""
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after:
        try:
            return min(30.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return float(2 ** retry_index)


def call_model(media_type: str, data: str) -> str:
    """Send one document/image to Gemini, retrying transient server/rate errors."""
    global LAST_USAGE, LAST_RETRY_COUNT
    LAST_USAGE = {}
    LAST_RETRY_COUNT = 0
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to .env; no API call was made.")

    if THINKING_LEVEL not in {"low", "medium", "high"}:
        raise RuntimeError("GEMINI_THINKING_LEVEL must be low, medium, or high.")

    body = {
        "contents": [{"role": "user", "parts": [
            {"text": PROMPT},
            {"inlineData": {"mimeType": media_type, "data": data}},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 16000,
            "thinkingConfig": {"thinkingLevel": THINKING_LEVEL},
        },
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as error:
            if error.code in RETRYABLE_HTTP_CODES and attempt < MAX_TRANSIENT_RETRIES:
                LAST_RETRY_COUNT += 1
                time.sleep(_retry_delay(error, attempt))
                continue
            detail = _error_detail(error)
            tried = f" after {attempt + 1} attempts" if attempt else ""
            raise RuntimeError(f"Gemini HTTP {error.code}{tried}: {detail}") from None
        except urllib.error.URLError as error:
            reason = str(getattr(error, "reason", "network error"))
            raise RuntimeError(f"Gemini connection failed ({reason}). Check network/TLS access and retry.") from None
        except TimeoutError:
            raise RuntimeError("Gemini request timed out after 180 seconds. Retry the report.") from None
    else:  # Defensive; loop always exits by break or raise.
        raise RuntimeError("Gemini request failed after automatic retries.")

    LAST_USAGE = result.get("usageMetadata", {}) or {}
    candidates = result.get("candidates", [])
    if not candidates:
        block_reason = result.get("promptFeedback", {}).get("blockReason")
        suffix = f" (prompt blocked: {block_reason})" if block_reason else ""
        raise RuntimeError(f"Gemini returned no candidate{suffix}.")
    candidate = candidates[0]
    text = "".join(
        part["text"] for part in candidate.get("content", {}).get("parts", [])
        if "text" in part and not part.get("thought")
    )
    if not text.strip():
        raise RuntimeError("Gemini returned an empty text response.")
    return text


def parse(text: str) -> list[dict[str, Any]]:
    """Find a JSON object containing a tests array; reject malformed structures."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "tests" in parsed:
            tests = parsed["tests"]
            if not isinstance(tests, list):
                raise ValueError('Gemini output has a "tests" field that is not a JSON array.')
            if any(not isinstance(item, dict) for item in tests):
                raise ValueError('Gemini output contains a test row that is not a JSON object.')
            return tests
    raise ValueError('Gemini response did not contain a valid JSON object with a "tests" array.')


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (dict, list)):
        raise ValueError("A unit or reference_range field had an unexpected JSON type.")
    text = str(value).strip()
    return text or None


def clean(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize fields to the four required schema fields without losing text."""
    out = []
    for index, test in enumerate(tests, start=1):
        name = test.get("test_name")
        if not isinstance(name, (str, int, float)) or isinstance(name, bool):
            raise ValueError(f"Test row {index} is missing a usable test_name.")
        name = str(name).strip()
        if not name:
            raise ValueError(f"Test row {index} has an empty test_name.")

        value = test.get("value")
        if isinstance(value, str):
            value = value.strip() or None
        elif isinstance(value, bool):
            value = str(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Test row {index} contains a non-finite numeric value.")
        elif value is not None and not isinstance(value, (int, float)):
            raise ValueError(f"Test row {index} has an invalid value type.")

        unit = _optional_text(test.get("unit"))
        reference_range = _optional_text(test.get("reference_range"))
        out.append({
            "test_name": name,
            "value": value,
            "unit": unit,
            "reference_range": reference_range,
        })
    return out


def extract(path: Path) -> dict[str, Any]:
    media_type, data = load_media(Path(path))
    return {"tests": clean(parse(call_model(media_type, data)))}


def append_run_log(
    *, path: Path, status: str, duration_seconds: float, error: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Append run metadata only; never log the API key, document bytes, or result values."""
    try:
        input_size = path.stat().st_size if path.is_file() else None
    except OSError:
        input_size = None
    usage = LAST_USAGE or {}
    entry: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        # Keep the fixture names useful while redacting arbitrary/potentially identifying filenames.
        "input_file": path.name if path.stem.lower().startswith("report_") and all(c.isalnum() or c in "_-" for c in path.stem) else "[redacted]",
        "input_size_bytes": input_size,
        "input_type": path.suffix.lower().lstrip("."),
        "model": MODEL,
        "status": status,
        "duration_seconds": round(max(duration_seconds, 0.0), 4),
        "retry_count": LAST_RETRY_COUNT,
        "input_tokens": usage.get("promptTokenCount"),
        "output_tokens": (
            (usage.get("candidatesTokenCount", 0) or 0) + (usage.get("thoughtsTokenCount", 0) or 0)
            if usage else None
        ),
    }
    if result is not None:
        entry["extracted_row_count"] = len(result.get("tests", []))
    if error:
        entry["error"] = " ".join(error.split())[:500]
    try:
        RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as log_error:
        print(f"Warning: could not write run metadata log: {log_error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Extract laboratory results from one PDF or image.")
    parser.add_argument("file", type=Path, help="Input PDF, JPG, JPEG, or PNG")
    parser.add_argument("--output", type=Path, help="Also save JSON to this UTF-8 file")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        result = extract(args.file)
        output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
        append_run_log(path=args.file, status="success", duration_seconds=time.perf_counter() - started, result=result)
        print(f"Run metadata logged to: {RUN_LOG_PATH}", file=sys.stderr)
        if args.output:
            print(f"JSON output saved to: {args.output}", file=sys.stderr)
        return 0
    except (RuntimeError, ValueError, OSError) as error:
        message = str(error)
        append_run_log(path=args.file, status="error", duration_seconds=time.perf_counter() - started, error=message)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
