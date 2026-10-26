"""Instrumented baseline extractor, using only Python's standard library.

The original prompt, numeric coercion and status field are retained. Since the
original first-page-only raster path required Poppler, this no-dependency
benchmark adapter submits the PDF directly and explicitly instructs Gemini to
use only page one. That input adaptation is a documented baseline limitation.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
import time
from pathlib import Path
from typing import Any

from env_loader import load_env

load_env()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Match the final extractor so benchmark request settings differ only where intended.
THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "low").strip().lower()
LAST_USAGE: dict[str, Any] = {}  # Instrumentation for evaluator only.
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
MAX_TRANSIENT_RETRIES = 3
LAST_RETRY_COUNT = 0
PROMPT = """You are an expert at reading medical lab reports.

Look at this lab report and extract every test result from it.

Return JSON in exactly this format:
{"tests": [{"test_name": "...", "value": 0.0, "unit": "...", "reference_range": "...", "status": "normal"}]}

Rules:
- Use the standard medical name for each test.
- value must be a number.
- If a reference range is missing from the report, fill in the standard reference range for that test.
- status is "normal", "high" or "low", based on the value and the reference range.
- Be thorough. Do not miss any tests.
- For a multi-page PDF, inspect only page 1 and ignore all later pages.
"""
MEDIA_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def load_image(path: Path) -> tuple[str, str]:
    """Load the first page of a PDF or a whole supported image as base64."""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Input file does not exist or is not a file: {path}")
    suffix = path.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise RuntimeError(f"Unsupported file type: {suffix or '(none)'}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError("Input file is empty.")
    if suffix == ".pdf":
        if len(raw) > 50 * 1024 * 1024:
            raise RuntimeError("PDF exceeds Gemini's 50 MiB inline-input limit.")
        if not raw.startswith(b"%PDF-"):
            raise RuntimeError("The file has a .pdf extension but does not have a valid PDF signature.")
    return MEDIA_TYPES[suffix], base64.standard_b64encode(raw).decode("ascii")


def call_model(media_type: str, data: str) -> str:
    global LAST_USAGE, LAST_RETRY_COUNT
    LAST_USAGE = {}
    LAST_RETRY_COUNT = 0
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY in .env; no API call made.")
    if THINKING_LEVEL not in {"low", "medium", "high"}:
        raise RuntimeError("GEMINI_THINKING_LEVEL must be low, medium, or high.")
    body = {"contents": [{"role": "user", "parts": [
        {"text": PROMPT}, {"inlineData": {"mimeType": media_type, "data": data}},
    ]}], "generationConfig": {
        "temperature": 0, "maxOutputTokens": 16000,
        "thinkingConfig": {"thinkingLevel": THINKING_LEVEL},
    }}
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
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(30.0, max(0.0, float(retry_after))) if retry_after else float(2 ** attempt)
                except ValueError:
                    delay = float(2 ** attempt)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Gemini HTTP {error.code}; check API access, model and quota (attempts: {attempt + 1}).") from None
        except urllib.error.URLError:
            raise RuntimeError("Gemini connection failed; check network/TLS access and retry.") from None
        except TimeoutError:
            raise RuntimeError("Gemini request timed out after 180 seconds. Retry the report.") from None
    else:
        raise RuntimeError("Gemini request failed after automatic retries.")

    LAST_USAGE = result.get("usageMetadata", {}) or {}
    candidates = result.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidate.")
    return "".join(part["text"] for part in candidates[0].get("content", {}).get("parts", [])
                   if "text" in part and not part.get("thought"))


def parse(text: str) -> list[dict]:
    import re
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        parsed = json.loads(match.group(0))
        tests = parsed["tests"]
        if not isinstance(tests, list):
            return []
        return tests
    except (AttributeError, KeyError, TypeError, ValueError):
        return []


def to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean(tests: list[dict]) -> list[dict]:
    out = []
    for test in tests:
        out.append({
            "test_name": str(test.get("test_name", "")).strip(),
            "value": to_number(test.get("value")),
            "unit": test.get("unit") or None,
            "reference_range": test.get("reference_range") or None,
            "status": test.get("status"),
        })
    return out


def extract(path: Path) -> dict:
    media_type, data = load_image(path)
    return {"tests": clean(parse(call_model(media_type, data)))}
