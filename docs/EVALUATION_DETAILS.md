# Evaluation details

Evaluation outputs are generated locally under `scripts/eval/results/` and are
excluded from Git. Versioned prompts and evaluation code remain in the
repository so reviewers can rerun experiments with authorized data and models.

## Latency, token usage, and cost

Evaluation runs use the single LLM selected in `config.py` (currently Claude
Opus). The newest available Opus is selected once per process. To pin a model
for a reproducible run, set `ANTHROPIC_MODEL=claude-opus-5-5` before invoking
the evaluation script. Use a new `--output` path for each measured run so older
results are not mixed with new measurements. The exact model returned by the
provider is saved in each case's `llm_calls` list.

Each case records `query_elapsed_seconds` (end-to-end query processing),
`llm_elapsed_seconds` (sum of LLM call times), provider-reported input and
output tokens, API attempts, and errors. Failed calls have unknown token usage
unless the provider returned counts. Startup health checks and retrieval-index
construction occur before the case timer; they are not included in case latency
or cost. Embedding API usage is not reported by this instrumentation.

The efficiency summary reports success/error counts, accuracy where scored,
mean, median, and 95th-percentile latency, total and mean tokens per measured
prompt, calls, and estimated LLM cost. The
cost calculator uses `scripts/eval/model_prices.json`, a dated USD-per-million-
token price table for Opus 5.5. Add a documented rate if a newer Opus is used.
An unpriced or unmetered call makes the total cost `null`, rather than silently
undercounting it. Run the summary tool directly for existing measured JSONL:

```bash
venv/bin/python scripts/eval/summarize_efficiency.py \
  scripts/eval/results/simulated/bump_results.jsonl
```

For a Results section, report the exact model ID, accuracy, efficiency, and
number of repetitions. Do not merge historical rows lacking measured usage into
the new aggregates.

## Prompt sets

- `prompts/curated/bump_prompts.jsonl`: 53 curated BUMP prompts.
- `prompts/curated/sinc_prompts.jsonl`: 40 curated SinC prompts.
- `prompts/simulated/bump_prompts.jsonl`: 925 simulated BUMP prompts.
- `prompts/simulated/sinc_prompts.jsonl`: 582 simulated SinC prompts.
- `prompts/simulated/cross_dataset_prompts.jsonl`: 21 harmonized-view prompts.
- `prompts/prev/`: previous BUMP and SinC prompt sets retained for comparison.

## Simulated benchmarks

Run BUMP or SinC prompts with:

```bash
.venv/bin/python scripts/eval/test_simulated_prompts.py \
  --dataset-scope bump --shared-db

.venv/bin/python scripts/eval/test_simulated_prompts.py \
  --dataset-scope sinc --shared-db
```

Use `--max-cases`, `--start-index`, `--verbose`, or an explicit `--output` as
needed. `--score-existing` recalculates scores from saved SQL without calling
the query system.

Cross-dataset prompts use their dedicated runner:

```bash
.venv/bin/python scripts/eval/test_cross_dataset_prompts.py --shared-db
```

Default per-case outputs are JSONL files under
`scripts/eval/results/simulated/`; matching `.summary.json` files contain
aggregate accuracy and an `efficiency` section with mean latency and mean
tokens per prompt.

## Curated prompts

Interactive logging:

```bash
.venv/bin/python scripts/eval/interactive_bump_prompt_logger.py --shared-db
.venv/bin/python scripts/eval/interactive_sinc_prompt_logger.py --shared-db
```

Only questions exactly matching a curated prompt are recorded. Generated SQL,
errors, dataframe paths, and small previews are written locally.

The curated SinC set also has an unattended runner:

```bash
.venv/bin/python scripts/eval/test_sinc_prompts.py --shared-db
```

Summaries are produced by `summarize_bump_prompt_results.py` and
`summarize_sinc_prompt_results.py`. Older researcher, survey, and SinC result
folders are retained locally under `scripts/eval/results/archive/`.

## Ablation evaluation

The reusable runner is `scripts/eval/test_prompts_with_ambiguity_replay.py`.
Dataset-specific case-suite wrappers and their current ambiguity choices are:

- `scripts/eval/bump_ablation/`
- `scripts/eval/sinc_ablation/`

Generated outputs are stored under:

- `scripts/eval/results/ablation/bump/`
- `scripts/eval/results/ablation/sinc/`

The retained local result variants use stable names such as `bump_full_system`,
`bump_no_kb`, `sinc_full_system`, and `sinc_no_majority_voting`. Each JSONL has
a matching `.summary.json` file.

## SQL matching

Simulated benchmarks canonicalize SQL by normalizing case, whitespace, quoted
identifiers, aliases, selected columns, grouping columns, and top-level `AND`
terms. `ORDER BY` and `LIMIT` do not affect the simulated SQL match.

Curated summaries use the more complete canonicalizer in
`scripts/eval/prompt_eval_utils.py`, which additionally normalizes CTE names and
CTE output aliases.

## Dataframe matching

Simulated benchmarks normalize columns and scalar values, sort columns and
rows, preserve duplicates, and compare the resulting dataframes.

Curated summaries calculate a score from `0.0` to `1.0`:

1. align columns using 60% name similarity and 40% content similarity;
2. calculate column coverage;
3. compare aligned row multisets;
4. multiply column coverage by row overlap.

If canonical SQL matches, the dataframe score is treated as `1.0`.

Summary files report tested/untested prompts, errors, SQL matches, exact
dataframe matches, average dataframe score, and per-case details.

## Uncertainty estimates from saved results

Run `python3 scripts/eval/summarize_uncertainty.py` to calculate primary BUMP
and SinC uncertainty estimates without API calls. The script resamples saved
per-question similarity scores with replacement 10,000 times within each
dataset (seed 42). It reports a 95% percentile bootstrap interval for the mean
and a 95% Wilson interval for the exact-match rate. All scored questions,
including failures, are retained; missing scores and duplicate questions raise
an error instead of being silently excluded. Use repeated `--input PATH`
arguments for other saved JSONL evaluations.

Results and source-file SHA-256 hashes are saved to
`scripts/eval/results/uncertainty/primary_uncertainty.json` by default.
These intervals assume questions are independent resampling units and are
conditional on the saved model outputs. Curated questions can share patterns;
the intervals do not assess repeated-run model variability, external dataset
generalization, or independent validation.

For paired ablation comparisons, run
`python3 scripts/eval/summarize_ablation_uncertainty.py`. Each ablation is
matched to the full system by exact question text; identical question sets and
reference SQL are required. The script bootstraps the per-question differences
(full system minus ablation) 10,000 times with seed 42, preserving pairing.
It reports differences and 95% percentile intervals for mean similarity and
exact-match rate in `scripts/eval/results/uncertainty/ablation_uncertainty.json`.
Positive differences favor the full system. These are exploratory intervals
without adjustment for multiple comparisons, conditional on the saved outputs.
