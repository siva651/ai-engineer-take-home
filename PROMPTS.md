# Actual prompts and why they differ

The exact prompt strings used by the submitted baseline adapter and improved extractor are copied below from `baseline/extract.py` and `extract.py`. Those Python constants remain the runtime source of truth; update this document if either prompt changes.

## Baseline benchmark prompt (`baseline/extract.py`)

This retains the starter's instructions to standardize names, force numeric values, invent missing reference ranges, and add a status label. The adapter adds an explicit page-one-only instruction to reflect the original starter's behavior of rendering only the first PDF page.

```text
You are an expert at reading medical lab reports.

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
```

**Comparability caveat:** the employer's original `extract.py` rendered page one to a PNG with Poppler. To avoid a system-level dependency, this adapter sends the PDF bytes directly and asks the model to read page one only. The model receives a different input representation, so treat the baseline comparison as useful but not perfectly input-controlled. The adapted baseline prompt is not claimed to be byte-for-byte identical to the original starter's prompt: the final page-one bullet is an explicit behavioral substitute for the original rasterization behavior.

## Improved prompt (`extract.py`)

```text
You extract laboratory test results from medical lab reports.

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
```

**Why the changes:**

- Preserve source test names rather than rewrite them to standard synonyms.
- Preserve string values, signs, decimals and comparison operators rather than requiring numeric conversion.
- Copy only printed reference ranges; use `null` rather than inventing clinical ranges.
- Keep units separate from reference ranges, while splitting a unit suffix only when clear.
- Extract every result row on every page and do not add statuses, diagnoses or non-source tests.
- Require JSON in the supplied four-field schema so malformed or extra clinical output is not accepted as a successful extraction.

## Separate manual prompt experiment

`evaluation_results/manual_prompt_experiment/` contains the saved exports and score summary from an earlier manual chat-based prompt comparison. This is supplementary evidence, not the official benchmark: the exports duplicate `report_09.jpg` and omit `report_11.pdf`. The official benchmark is the live API baseline/final run in `evaluation_results/baseline/` and `evaluation_results/final/`.
