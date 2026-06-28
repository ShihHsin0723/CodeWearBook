# Clinical Notes Analysis

This experiment evaluates whether the BUMP query scope can derive eight
adverse-event labels from clinical-note text.

## Contents

- `data/source/`: uploaded, filtered clinical-note dataframe.
- `data/prompts/`: eight natural-language label-extraction prompts.
- `data/gold/`: reviewed labels and the manual-correction audit log.
- `scripts/`: gold-data construction, model execution, and evaluation.
- `results/generated_dfs/`: outputs from the preserved single run.
- `results/adverse_event_column_results.jsonl`: generated SQL and previews.
- `results/adverse_event_column_accuracy.json`: metrics against gold labels.
- `results/repeated_runs/`: independent runs and aggregate summaries.

## Run new repetitions

Run three model repetitions without overwriting preserved evidence:

```bash
.venv/bin/python clinical_notes_analysis/scripts/run_repeated_adverse_event_column_tests.py \
  --repeats 3 \
  --runs-dir clinical_notes_analysis/results/local_runs

.venv/bin/python clinical_notes_analysis/scripts/summarize_repeated_adverse_event_column_tests.py \
  --runs-dir clinical_notes_analysis/results/local_runs
```
