# BUMP retrieval sensitivity experiment

This experiment evaluates the current system on all 53 curated BUMP researcher
questions. It is a new robustness study, not a reproduction of the original
manuscript's historical model outputs.

## Design

The baseline is chunk size 400 characters, overlap 50 characters, schema retention
8 chunks, and KB retention 5 unique parent documents. Change one setting at a
time: chunk size 200/800; overlap 0/100; schema retention 4/12; KB retention 3/8.
Hold candidate searches at 32 schema chunks and 40 KB chunks, and retain existing
keyword ranking, final schema-selection rules, guidelines, and reference expansion.
The constructor default k=12 is not an active sweep variable.

An offline preparation checks chunk contents for both indexes. Equivalent
configurations are documented and skipped. With the current corpus, overlap 100
is identical to overlap 50; eight configurations remain. The reduced, user-selected plan uses one repetition per configuration, giving
424 question evaluations. A complete baseline is run first; subsequent
configurations are ordered randomly within repetitions using seed 42.

The ambiguity resolver is run for every question. The same unattended
clarification policy selects option 1 when clarification is requested. Replaying
a fixed selected-table set would bypass the ambiguity resolver and mask much of
the schema-retention effect. These are end-to-end results under an automatic
clarification policy, not a replication of individual human clarification choices.
No table choices are inferred from reference SQL.

The existing CharacterTextSplitter uses blank-line boundaries. Size and overlap
are targets, not guaranteed exact lengths. KB matches are restored to full
parent documents and deduplicated before table filtering and retention. Linked
references are added afterward with no fixed document-count cap. Do not replace
the splitter for this experiment.

## Run

From the project root:

```bash
venv/bin/python scripts/eval/run_retrieval_sensitivity.py --prepare-only
venv/bin/python scripts/eval/run_retrieval_sensitivity.py
venv/bin/python scripts/eval/run_retrieval_sensitivity.py --summarize-only
```

Use a different `--output` directory for a changed experiment. The runner saves
prompt/code hashes, database metadata, chunk signatures, exact settings, model
ID, and the plan in `manifest.json`. The model is resolved once and pinned across
all configurations; a resumed run uses that same ID. Results resume after the last
saved question, including query failures; provider failures stop the run rather
than becoming accuracy outcomes. Reference SQL is checked before model calls.
Index construction is outside query latency. Embedding usage/cost is not included
in LLM token/cost totals.

Generated JSONL, logs, manifest, summary JSON, and REPORT.md live under
`scripts/eval/results/sensitivity/bump_retrieval_20261007/` and are Git-ignored.
Logs and dataframe previews can contain study data; publish reviewed aggregate
results only.

## Scoring and reporting

Use the existing curated scorer: canonical SQL equality scores 1; otherwise
align columns and compare row multisets to obtain dataframe similarity in [0,1].
Keep all query failures in the denominator with score zero. Report mean similarity
and exact-match rate, failure count/rate,
mean latency, and mean provider-reported input tokens. With one repetition,
run-to-run model variability is not assessed and run standard deviations are unavailable. The paired bootstrap
resamples question-level differences after averaging each question across
repetitions (10,000 resamples, seed 42). These intervals are exploratory,
conditional on saved outputs, and unadjusted for multiple comparisons.

For the reviewer response, describe these as implementation defaults if that is
how the original settings were chosen; do not claim they were tuned or optimal.
Report actual measured changes only after all repetitions complete. Correct S5's
retrieval definitions against the code version used for the reported experiment.
