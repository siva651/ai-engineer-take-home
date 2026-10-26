# Results and evaluation audit

## 1. Live API benchmark supplied by the project owner

The project owner ran both extractors on the same eight labelled reports with `gemini-3.5-flash-lite`, low thinking, and the same API environment. The uploaded outputs contain eight predictions per run, the token usage reported for each successful response, and the metrics printed by `evaluate.py`. I independently re-counted exact rows from the saved predictions and supplied gold JSON; the counts and aggregate precision/recall/F1 exactly reproduce the uploaded metrics.

| Metric | Baseline | Improved extractor | Change |
|---|---:|---:|---:|
| Reports scored | 8/8 | 8/8 | — |
| Gold rows | 138 | 138 | — |
| Predicted rows | 114 | 138 | +24 rows |
| Strict exact rows | 66/138 | 137/138 | +71 matches |
| Micro precision | 57.89% | 99.28% | +41.39 percentage points |
| Micro recall | 47.83% | 99.28% | +51.45 percentage points |
| Micro F1 | 52.38% | 99.28% | +46.90 percentage points |
| Total API latency | 28.21 s | 26.81 s | -1.41 s |
| Mean API latency | 3.527 s | 3.351 s | -4.98% |
| Input tokens reported | 7,268 | 9,164 | +1,896 |
| Output tokens reported, incl. thoughts if reported | 6,200 | 5,549 | -651 |
| Estimated standard paid-tier cost, 8 reports | $0.0176804 | $0.0166217 | -5.99% |
| Estimated cost per report | $0.0022101 | $0.0020777 | -$0.0001323 |

**Interpretation:** the final extractor matches 137 of 138 supplied gold rows, versus 66 of 138 for the baseline. Mean latency was about 5.0% lower in this run. The token-based paid-tier cost estimate was about 6.0% lower, even though the final extractor used more input tokens; it returned fewer output tokens. With only eight reports, treat these as results on this small benchmark, not a guarantee of generalization or production performance.

## 2. Report-level results

| Report | Gold rows | Baseline predicted / exact | Improved predicted / exact | Baseline latency | Improved latency |
|---|---:|---:|---:|---:|---:|
| `report_01` | 14 | 14 / 13 | 14 / 13 | 2.57 s | 3.02 s |
| `report_02` | 16 | 16 / 10 | 16 / 16 | 3.67 s | 2.90 s |
| `report_03` | 8 | 8 / 0 | 8 / 8 | 2.84 s | 2.04 s |
| `report_05` | 14 | 14 / 13 | 14 / 14 | 3.65 s | 3.53 s |
| `report_06` | 12 | 12 / 2 | 12 / 12 | 3.24 s | 3.47 s |
| `report_08` | 6 | 6 / 0 | 6 / 6 | 2.65 s | 2.65 s |
| `report_09` | 14 | 14 / 9 | 14 / 14 | 3.72 s | 3.48 s |
| `report_11` | 54 | 30 / 19 | 54 / 54 | 5.87 s | 5.73 s |

The report-level token counts and prediction JSON files are preserved under `evaluation_results/baseline/` and `evaluation_results/final/`. Field accuracy by position is also recorded by the evaluator, but it is a secondary diagnostic: when result rows are missing or ordered differently, positional comparison can misstate field fidelity. Strict multiset row matches are the primary score here.

## 3. Cost methodology and limits

The original uploaded `metrics.json` files had `null` cost fields because the evaluator did not have a price configured for `gemini-3.5-flash-lite`. Cost amounts above were recalculated after the run from the input/output token counts saved in those files using Google's published **Standard paid-tier** prices checked on **October 26, 2026**: input $0.30 / 1 million tokens and output $2.50 / 1 million tokens. Output pricing includes thinking tokens. The updated `evaluate.py` now has the corresponding model-rate entry, so future runs populate cost estimates automatically.

Formula for each report: `(input_tokens × 0.30 + output_tokens × 2.50) / 1,000,000`. These are estimated comparison figures, not invoice data. Google lists free-tier input/output as free where the model is eligible; the user's actual charge may therefore be $0. Additionally, current Gemini 3 document billing distinguishes PDF/document modality tokens and does not charge native text extracted from PDFs; this benchmark archive only retained aggregate `promptTokenCount`, not modality-level billable-token details. Treat the computed values as an aggregate-token estimate, not exact billing. See [official pricing](https://ai.google.dev/gemini-api/docs/pricing) and [document processing details](https://ai.google.dev/gemini-api/docs/document-processing). Re-check current prices before using the evaluator later; the Gemini 3.8 Flash rate in the script is scheduled to increase on January 1, 2027.

## 4. Gold-label mismatch

The only unmatched row in the improved output is `report_01` Platelet Count:

- Source PDF and improved prediction: `340000 /cumm`, reference range `150000 - 410000`.
- Supplied `gold/report_01.json`: `304000 /cumm`, same range.

The source document visibly prints `340000`; the provided gold value appears wrong. The benchmark must retain the employer's gold JSON unchanged. The official/raw-gold score is therefore **137/138 (99.28%)**. For a separate sensitivity check only, if that single verified gold value were adjudicated to `340000`, the improved output would score **138/138** on this dataset. Do not replace the official score with the sensitivity result or edit the supplied gold file.

## 5. What the comparison says about the failure modes

- **Textual labels and numeric coercion:** The original prompt insists on standard medical names and numeric values, while the cleaner coerces values. The large failures on `report_03` and `report_08` (zero strict rows despite multiple correctly positioned values/units) are consistent with name/value representation changes breaking exact rows. The improved prompt preserves source labels and text values.
- **Unit/reference-range separation:** The baseline scored 2/12 exact rows on `report_06`, while the improved extractor scored 12/12. The revised instructions explicitly separate the source's unit from its reference range rather than merging them.
- **All-page coverage:** The baseline returned 30 of 54 rows on the two-page `report_11`; the improved extractor returned all 54 and matched every labelled row. This is the largest observable coverage gain.
- **Missing or invented ranges:** The original prompt tells the model to fill absent ranges with standard values. The revised prompt limits extraction to what is printed and returns `null` where absent or unreadable. The preliminary manual chat experiment's reference-range accuracy decreased slightly, from 95.2% (80/84) to 94.0% (79/84), so that earlier experiment did not improve every field even though the final API run's exact-row count improved sharply.

## 6. Benchmark comparability limitation

The baseline retains the original extraction prompt, numeric coercion, and status field. To keep the complete project standard-library-only, its PDF adapter sends the PDF directly and tells Gemini to inspect only page one; it does not locally split or rasterize page one. The model technically receives the full PDF, unlike the original Poppler-rendered single-page image path. The final extractor also receives the PDF but is instructed to read every page. This input-modality difference is a documented limitation: the measured outcome compares the full baseline behavior against the revised extractor and all-page instructions, not a strictly identical byte-for-byte modality ablation.

Both runs used the same model `gemini-3.5-flash-lite` and low thinking setting in the supplied metrics. The 8-report result is small and should not be generalized beyond the supplied reports without a held-out test set.

## 7. Transient API errors and retries

The uploaded manual run log records two `HTTP 503` high-demand failures on `gemini-3.8-flash`, followed by a successful `gemini-3.5-flash-lite` extraction of `report_01` (14 output rows, 939 input tokens, 570 output tokens). The pasted terminal transcript also shows a transient HTTP 503 before success. The current source now retries HTTP 429, 500, 502, 503, and 504 responses up to three times with exponential backoff (respecting numeric `Retry-After` when provided); it does not automatically retry a 180-second timeout. The 33 local unit tests include retry simulation, but these tests do not replace live service verification.

The uploaded run log does not contain the baseline/final benchmark calls themselves; those request token counts and latencies are retained in each `metrics.json`. It should be read as a partial manual-run log, not a complete API audit trail.

## 8. Four reports without gold labels

The assignment has no gold JSON for `report_04.pdf`, `report_07.pdf`, `report_10.jpg`, and `report_12.jpg`, so no accuracy score is valid for them. The candidate review points remain qualitative only:

- `report_04.pdf`: inspect completeness of the digital table rows.
- `report_07.pdf`: preserve uncertainty for the obscured result and do not turn handwritten annotations into measured results.
- `report_10.jpg`: check the photographed table for glare/cropping and unit/range separation.
- `report_12.jpg`: this is a prescription for investigations, not a result report; do not invent measured values.

No final accuracy claims are made for these four unlabelled reports in this benchmark.

## 9. The unsuccessful experiment

In the preliminary manual prompt-chat experiment, the revised prompt raised exact rows from 23/84 (27.4%) to 75/84 (89.3%), but reference-range exactness decreased from 80/84 (95.2%) to 79/84 (94.0%). This is the concrete negative result: the first prompt revision did not improve every field, and `report_06` showed why the unit/range boundary remained fragile at that stage. The later final API benchmark performs better, but this earlier negative result remains part of the development evidence rather than being discarded.

## 10. Design questions

**Would RAG help?** Not as the first fix. The measured failure types are transcription fidelity, page coverage, and field separation. Retrieval of standard ranges could actively cause hallucination. A small alias map might be considered later for downstream search, but should not replace source labels in this extraction schema.

**Would fine-tuning help?** Not yet. Eight labelled reports are too few to establish generalization and one gold label is demonstrably wrong. First test the focused prompt and validation changes on genuinely unseen reports; fine-tuning would require more adjudicated data and ongoing maintenance.

**Would a multi-step agent help?** Not yet. A single multimodal request with explicit source-fidelity instructions is simpler and easy to measure. An additional verification request should be considered only if unseen-report errors show sufficient expected quality gains to justify extra latency and cost.

## 11. Tests and process evidence

After adding transient-HTTP retries, **33 local unit tests passed** in the editing environment (Python 3.13.5). They cover direct PDF byte pass-through, the two-page report, mock REST request construction, missing API key behavior, parsing/cleaning, evaluator metrics, run-log privacy, and retry behavior for both baseline and final extractors. The successful API benchmark itself was run by the project owner on the Windows machine; its saved predictions and metrics are preserved in `evaluation_results/baseline/` and `evaluation_results/final/`.

The source archives reviewed did not include Git metadata, so the requirement that `PLAN.md` was committed before implementation cannot be verified. Preserve the real repository and history if available; do not fabricate commits. No agent session transcripts were included in the submitted archives.
