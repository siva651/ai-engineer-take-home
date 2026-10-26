# Plan — mera.health extractor improvement

1. Read the brief, schema, baseline extractor, reports, and gold labels before changing behavior.
2. Establish a reproducible row-level evaluation against the all labelled reports. Normalize only harmless formatting differences; report field-level errors as well as exact-row scores, and flag questionable gold labels rather than silently correcting them.
3. Diagnose the main failures visible in the starter code and sample outputs: test names being rewritten away from the report labels, non-numeric values being discarded by cleaning, reference ranges being invented, and PDF handling that only processes page one.
4. Make a small set of targeted changes to the prompt and the existing functions. Keep `python extract.py <file>` as the entry point and preserve schema-valid JSON output.
5. Run local tests and the evaluation where API access is available. Report measured API latency/cost separately from the preliminary Gemini-chat prompt experiment; do not claim the chat experiment is an API benchmark.
6. Document known limits, a failed or incomplete experiment, and concise recommendations on RAG, fine-tuning, and multi-step agents.

Expected result: a focused prototype, not a new extraction architecture. The final changes will be retained as separate git commits and compared against the original starter code.
