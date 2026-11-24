# Evaluation cases

This directory ships **synthetic routing benchmark templates** so the evaluation
pipeline in `caprouter.eval` can be exercised without publishing personal data:

- `routing_cases.json` — development template cases.
- `heldout_cases.json` — held-out template cases.

The `expect_any` ids are demo placeholders (`skill:demo-*`). Replace them with ids
from your own registry, or replace the files with your own cases, before
measuring routing quality. Do not read the numbers produced by these templates as
a quality claim about any real skill set.

## Private evaluation data is not published

The real Phase 2 evaluation (baseline model transcripts, blind samples, judge
inputs, `results.csv`, `summary.json`, decision notes) is kept in a private
archive. Those files contain full session transcripts and machine-specific
details, so they are intentionally absent from this repository. Published
numbers are summarized in the project documentation; reruns are new data and
should carry their own date.

## Rules

- Do not hand-edit the template cases to make a run pass; add a case only when it
  reflects a real routing requirement.
- Do not publish raw transcripts that contain machine paths, session ids or
  credentials. Strip or keep them private.
- A translated copy of a transcript is a new document, not evidence.
