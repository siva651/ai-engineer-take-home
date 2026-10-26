"""Run the selected extractor on the eight labelled reports and score its output.

Examples:
  python evaluate.py --compare
  python evaluate.py --extractor extract.py --output-dir eval_runs/final

Set GEMINI_API_KEY before running. Compare mode evaluates both versions (16 API requests total).
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from env_loader import load_env

load_env()
from typing import Any

FIELDS = ("test_name", "value", "unit", "reference_range")
# Standard paid-tier rates in USD per 1M tokens checked against Google's official
# Gemini API pricing page on 2026-10-26. Unknown models deliberately receive no
# estimate rather than a misleading one. Re-check this table before future runs.
PRICE_RATES_USD_PER_M_TOKENS = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),  # scheduled to increase 2027-01-01
}


def canonical(value: Any, field: str) -> str:
    if value is None:
        return "<null>"
    s = unicodedata.normalize("NFKC", str(value)).replace("µ", "u").replace("μ", "u").strip().casefold()
    s = re.sub(r"\s+", " ", s)
    if field == "value":
        compact = s.replace(",", "").replace(" ", "")
        try:
            from decimal import Decimal, InvalidOperation
            if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)", compact):
                return "number:" + str(Decimal(compact).normalize())
        except (InvalidOperation, ValueError):
            pass
        s = re.sub(r"\s*([<>≤≥])\s*", r"\1", s)
    elif field == "unit":
        s = re.sub(r"\s+", "", s)
    elif field == "reference_range":
        s = re.sub(r"\s*([<>≤≥=])\s*", r"\1", s)
        s = re.sub(r"\s*(-|–|—)\s*", "-", s)
    return s


def row_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(canonical(row.get(field), field) for field in FIELDS)


def load_module(path: Path):
    path = path.resolve()
    spec = importlib.util.spec_from_file_location("selected_extractor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import extractor at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def score_report(predicted: list[dict], gold: list[dict]) -> dict[str, Any]:
    pred_rows = collections.Counter(row_key(row) for row in predicted)
    gold_rows = collections.Counter(row_key(row) for row in gold)
    true_positive = sum((pred_rows & gold_rows).values())
    precision = true_positive / len(predicted) if predicted else (1.0 if not gold else 0.0)
    recall = true_positive / len(gold) if gold else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    aligned = min(len(predicted), len(gold))
    field_correct = {field: 0 for field in FIELDS}
    for p_row, g_row in zip(predicted, gold):
        for field in FIELDS:
            field_correct[field] += int(canonical(p_row.get(field), field) == canonical(g_row.get(field), field))
    aligned_field_accuracy = {
        field: field_correct[field] / aligned if aligned else 0.0 for field in FIELDS
    }
    exact_sequence = len(predicted) == len(gold) and all(
        row_key(p_row) == row_key(g_row) for p_row, g_row in zip(predicted, gold)
    )
    return {
        "predicted_rows": len(predicted), "gold_rows": len(gold),
        "exact_row_matches": true_positive,
        "row_precision": precision, "row_recall": recall, "row_f1": f1,
        "field_accuracy_by_position": aligned_field_accuracy,
        "exact_sequence_match": exact_sequence,
    }


def token_usage(module) -> dict[str, Any]:
    usage = getattr(module, "LAST_USAGE", {}) or {}
    input_tokens = usage.get("promptTokenCount")
    output_tokens = (usage.get("candidatesTokenCount", 0) or 0) + (usage.get("thoughtsTokenCount", 0) or 0)
    model = getattr(module, "MODEL", None)
    rates = PRICE_RATES_USD_PER_M_TOKENS.get(model)
    estimate = None
    if rates and isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
        input_rate, output_rate = rates
        estimate = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    if not usage:
        note = "Token usage unavailable from this extractor run."
    elif not rates:
        note = f"No configured pricing rate for model {model!r}; cost estimate omitted."
    else:
        note = "Estimated at Google's standard paid-tier rates checked 2026-10-26; free-tier billing may be zero, PDF input billing may depend on modality details, and prices may change."
    return {
        "input_tokens": input_tokens,
        "output_tokens_including_thinking_if_reported": output_tokens if usage else None,
        "paid_tier_cost_estimate_usd": estimate,
        "pricing_model": model,
        "pricing_basis": "standard paid tier, USD per 1M tokens",
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score lab-report extraction and compare baseline with improved results.")
    parser.add_argument("--compare", action="store_true", help="Run baseline and improved extractor with one command")
    parser.add_argument("--output-root", type=Path, default=Path("eval_runs"), help="Parent folder used by --compare")
    parser.add_argument("--extractor", type=Path, default=Path("extract.py"), help="Extractor script containing extract(path)")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--gold-dir", type=Path, default=Path("gold"))
    parser.add_argument("--output-dir", type=Path, default=Path("eval_runs/current"))
    parser.add_argument("--score-only", action="store_true", help="Score JSON outputs already present in --output-dir; do not call the API")
    args = parser.parse_args()

    if args.compare:
        jobs = (
            (Path("baseline/extract.py"), args.output_root / "baseline"),
            (Path("extract.py"), args.output_root / "final"),
        )
        print("Running baseline and improved extractor. This makes one API request per labelled report for each version.")
        for extractor_path, output_path in jobs:
            command = [
                sys.executable, str(Path(__file__).resolve()),
                "--extractor", str(extractor_path),
                "--reports-dir", str(args.reports_dir),
                "--gold-dir", str(args.gold_dir),
                "--output-dir", str(output_path),
            ]
            completed = subprocess.run(command, check=False)
            if completed.returncode != 0:
                return completed.returncode
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    module = load_module(args.extractor)
    report_metrics: dict[str, Any] = {}
    total_latency = 0.0
    total_cost = 0.0
    known_cost_count = 0

    for gold_path in sorted(args.gold_dir.glob("report_*.json")):
        stem = gold_path.stem
        candidates = sorted(args.reports_dir.glob(stem + ".*"))
        candidates = [p for p in candidates if p.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
        if not candidates:
            print(f"ERROR: no source report found for {gold_path.name}", file=sys.stderr)
            return 2
        report_path = candidates[0]
        output_path = args.output_dir / f"{stem}.json"
        latency = None
        usage = {}
        if args.score_only:
            if not output_path.exists():
                print(f"ERROR: missing prediction {output_path}", file=sys.stderr)
                return 2
            predicted_doc = json.loads(output_path.read_text(encoding="utf-8"))
        else:
            start = time.perf_counter()
            try:
                predicted_doc = module.extract(report_path)
            except Exception as error:
                print(f"ERROR extracting {report_path.name}: {error}", file=sys.stderr)
                return 1
            latency = time.perf_counter() - start
            total_latency += latency
            usage = token_usage(module)
            if isinstance(usage.get("paid_tier_cost_estimate_usd"), (int, float)):
                total_cost += usage["paid_tier_cost_estimate_usd"]
                known_cost_count += 1
            output_path.write_text(json.dumps(predicted_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        predicted = predicted_doc.get("tests", []) if isinstance(predicted_doc, dict) else []
        gold_doc = json.loads(gold_path.read_text(encoding="utf-8"))
        gold_rows = gold_doc.get("tests", [])
        metrics = score_report(predicted, gold_rows)
        metrics["latency_seconds"] = latency
        metrics["usage_and_cost"] = usage
        report_metrics[stem] = metrics
        print(
            f"{stem}: rows {metrics['exact_row_matches']}/{metrics['gold_rows']} | "
            f"P={metrics['row_precision']:.3f} R={metrics['row_recall']:.3f} F1={metrics['row_f1']:.3f}"
            + (f" | latency={latency:.2f}s" if latency is not None else "")
        )

    tp = sum(m["exact_row_matches"] for m in report_metrics.values())
    pred_count = sum(m["predicted_rows"] for m in report_metrics.values())
    gold_count = sum(m["gold_rows"] for m in report_metrics.values())
    precision = tp / pred_count if pred_count else 0.0
    recall = tp / gold_count if gold_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    result = {
        "extractor": str(args.extractor),
        "model": getattr(module, "MODEL", None),
        "score_definition": "A row is correct only when test_name, value, unit and reference_range all match after harmless whitespace/numeric formatting normalization. Test-name synonyms are not merged.",
        "reports_scored": len(report_metrics),
        "predicted_rows": pred_count,
        "gold_rows": gold_count,
        "exact_row_matches": tp,
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": f1,
        "mean_latency_seconds": total_latency / len(report_metrics) if report_metrics and not args.score_only else None,
        "total_latency_seconds": total_latency if not args.score_only else None,
        "total_paid_tier_cost_estimate_usd": total_cost if known_cost_count else None,
        "cost_estimates_available_for_reports": known_cost_count,
        "per_report": report_metrics,
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"TOTAL: exact rows {tp}/{gold_count}; P={precision:.3f} R={recall:.3f} F1={f1:.3f}")
    if not args.score_only:
        print(f"Mean API latency: {result['mean_latency_seconds']:.2f}s")
        if known_cost_count:
            print(f"Estimated paid-tier API cost: ${total_cost:.6f} across {known_cost_count} report(s)")
    print(f"Saved predictions and metrics to: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
