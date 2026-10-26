# mera.health AI Engineer take-home

## What changed

I kept the supplied command, `python extract.py <file>`, and made focused changes to extraction instructions and output handling. The improved prompt preserves printed test names and text values, separates units from reference ranges, uses only ranges printed in the report, and asks for results from every page.

## Before and after

Both live API runs used `gemini-3.5-flash-lite` with low thinking and the same eight labelled reports (138 gold rows).

| Metric | Baseline | Improved |
|---|---:|---:|
| Exact rows | 66/138 | 137/138 |
| Precision / recall / F1 | 57.9% / 47.8% / 52.4% | 99.3% / 99.3% / 99.3% |
| Mean latency | 3.53 s | 3.35 s |
| Estimated cost for 8 reports | $0.01768 | $0.01662 |

Cost is an estimate from reported aggregate token counts and standard paid-tier rates checked on 26 October 2026, not an invoice. Actual charges can differ; free-tier use may cost $0. The saved predictions and report-level metrics are in `evaluation_results/`.

## Run on Windows (PowerShell)

Requires Python 3.10+ and a Gemini API key. No `pip install`, virtual environment, or Poppler is needed. First unzip the project and open PowerShell in the `mera-health-minimal` folder (the folder containing `extract.py`).

### 1. Set your API key (one time)

Run these commands in PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

In Notepad, replace `replace_with_your_key` with your Gemini API key on the `GEMINI_API_KEY=` line, save, and close the file. Keep `.env` private; never submit it or your API key.

### 2. Extract a report

Run:

```powershell
python extract.py reports/report_01.pdf --output manual_outputs/report_01.json
```

The extracted JSON is printed in the terminal and saved to `manual_outputs/report_01.json`. Replace the input path with another PDF or supported JPG/PNG image to process a different report.

### Optional: rerun the baseline-versus-improved benchmark

```powershell
python evaluate.py --compare
```

This makes about 16 API requests (eight reports per version, excluding retries) and writes fresh predictions to `eval_runs/baseline/` and `eval_runs/final/`. The included results can be reviewed without spending API quota again.

### Optional: run local code checks

```powershell
python -m unittest discover -s tests -v
```

These checks use Python's built-in test runner and do not call Gemini.

## Main failure categories

- **Names and textual values:** baseline scored 0/8 strict rows on `report_03` and 0/6 on `report_08`. Rewriting printed labels or forcing values to numbers can break exact matches; the improved prompt preserves source text.
- **Unit/range confusion:** baseline scored 2/12 on `report_06`; the improved extractor scored 12/12 after unit and reference-range instructions were clarified.
- **Incomplete page coverage:** baseline returned 30/54 rows from two-page `report_11`; the improved version returned and matched all 54.

The only final strict-row mismatch is likely a supplied gold-label error: `report_01.pdf` prints Platelet Count `340000`, while its gold file says `304000`. I left the employer's gold label unchanged, so the official score remains 137/138. `report_04`, `report_07`, `report_10`, and `report_12` have no gold labels; no accuracy score is claimed for them.

## Earlier prompt experiment and decisions

The separate manual chat experiment in `evaluation_results/manual_prompt_experiment/` improved exact rows from 23/84 (27.4%) to 75/84 (89.3%), but reference-range accuracy slipped from 80/84 (95.2%) to 79/84 (94.0%). Its exports duplicate `report_09` and omit `report_11`, so these are supplementary results, not the live API benchmark.

**RAG:** not the first fix; retrieved standard ranges could introduce values absent from the report. **Fine-tuning:** eight labelled reports are too few, and one label appears incorrect. **Multi-step agent:** not yet; one request is simpler to measure, cheaper, and easier to debug.

**Decision log:** I prioritized source fidelity over normalizing names or inventing ranges, kept the gold files unchanged, and checked the platelet discrepancy against the source PDF. The baseline comparison is not a perfect input-controlled experiment: the original starter used Poppler to render page one, while this no-install adapter sends PDF bytes and tells the model to inspect page one only. `PLAN.md` and `PROMPTS.md` record the plan and actual prompt text. The supplied ZIP does not contain Git metadata or agent transcripts; preserve genuine history/transcripts if you have them, and do not fabricate either.
