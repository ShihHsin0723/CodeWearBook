# Evaluation details

Evaluation outputs are generated locally under `scripts/eval/results/` and are
excluded from Git. Versioned prompts and evaluation code remain in the
repository so reviewers can rerun experiments with authorized data and models.

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
aggregate metrics.

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
