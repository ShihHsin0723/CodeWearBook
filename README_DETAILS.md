# Project details

## Purpose and supported scopes

The system translates natural-language research questions into SQL, executes
the SQL against SQLite, and returns a dataframe plus a short explanation.

- `bump`: raw tables prefixed with `bump_`.
- `sinc`: raw tables prefixed with `sinc_`.
- `cross-dataset`: harmonized `wearable_*` views only. It supports cohort-level
  comparison and does not link participant identities across studies.

## Repository map

```text
config.py                         Runtime model and database configuration
query_system.py        Main interactive query pipeline
dataset_registry.py               Dataset-scope and loader registry
create_and_load_*_tables.py       Raw table definitions and CSV loaders
harmonized_views.py               Cross-dataset wearable views
generate_schema.py                Schema-document generation
knowledge_base_context.py         Domain and SQL guidance
kb_registry.py                    Scoped knowledge-base construction
embedding.py                      Embedding and FAISS retrieval
llm.py                            LLM provider interface
prompts/                          Versioned benchmark prompts
prompt_simulation/                Prompt generation grouped by dataset scope
scripts/db/                       Database initialization
scripts/eval/                     Evaluation runners and scoring utilities
clinical_notes_analysis/          Adverse-event note-label experiment
docs/                             System and evaluation details
```

Prompt schema snapshots are grouped under `prompt_simulation/bump/`,
`prompt_simulation/sinc/`, and `prompt_simulation/cross_dataset/`. Prompt sets
are grouped in `prompts/curated/`, `prompts/simulated/`, and `prompts/prev/`.
The shared `prompt_simulation/simulate_prompts.py`
generator handles BUMP and SinC; the cross-dataset generator lives with its
harmonized-view schema.

## Environment and credentials

The pinned environment is in the root `requirements.txt` and targets Python
3.8. Create it with `make setup` or manually:

```bash
python3.8 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`get_llm_api_keys.py` currently reads AWS Secrets Manager secret `llm/api_keys`
from `us-west-1`. The expected keys are `anthropic_api_key`, `openai_api_key`,
`gemini_api_key`, and `together_api_key`. Never store these values in the
repository. `config.py` selects the active LLM, embedding backend, model names,
local model paths, and SQLite path.

## Required source-data layout

Source data is not distributed by this repository. Run all commands from the
repository root because loader paths are root-relative.

```text
bump/
├── app_activities/*.csv.gz
├── bodyport/*.csv.gz
├── camcog/*.csv.gz
├── garmin/*.csv.gz
├── oura/*.csv.gz
└── redcap/*.csv.gz

metadata/user_site_mapping.csv.gz
app_activities/*.csv.gz
bodyport/*.csv.gz
camcog/*.csv.gz
ibdoc/*.csv.gz
oura/*.csv.gz
pooply/*.csv.gz
survey_monkey/*.csv.gz
```

The top-level directories after `bump/` are SinC inputs. Exact filenames are
declared in `create_and_load_BUMP_tables.py` and
`create_and_load_SinC_tables.py`.

## Database workflows

Build or update the shared database:

```bash
make init-db
# Equivalent:
.venv/bin/python scripts/db/init_shared_db.py --dataset-scope cross-dataset
```

Scope-specific initialization is also supported:

```bash
.venv/bin/python scripts/db/init_shared_db.py --dataset-scope bump
.venv/bin/python scripts/db/init_shared_db.py --dataset-scope sinc
```

Run against the shared database:

```bash
.venv/bin/python query_system.py --shared-db --dataset-scope bump
.venv/bin/python query_system.py --shared-db --dataset-scope sinc
.venv/bin/python query_system.py --shared-db --dataset-scope cross-dataset
```

Omit `--shared-db` to create and load an in-memory database for the selected
scope. This requires the raw source files on every run.

## Query pipeline

1. Normalize the requested scope and user question.
2. Resolve ambiguous table intent.
3. Retrieve relevant schema documents and scoped domain guidance.
4. Construct the SQL-generation prompt with scope-specific constraints.
5. Generate candidate SQL and select a majority-voted candidate.
6. Validate scope restrictions and execute the SQL.
7. Return a dataframe, explanation, and optional refinement loop.

For implementation-level detail, see [docs/SYSTEM_DETAILS.md](docs/SYSTEM_DETAILS.md).

## Harmonized cross-dataset layer

Cross-dataset queries operate on six views:

- `wearable_sleep_daily`
- `wearable_activity_daily`
- `wearable_readiness_daily`
- `wearable_bodyport_daily`
- `wearable_ebt_sessions`
- `wearable_ema_daily`

Each view exposes a common dataset identifier, participant key, event date, and
measure-specific columns. SQL referencing raw `bump_*` or `sinc_*` tables is
rejected in cross-dataset scope.

## Evaluation workflows

The repository contains curated, simulated, cross-dataset, ablation, and
clinical-note evaluations. Typical commands are:

```bash
# BUMP simulated benchmark
.venv/bin/python scripts/eval/test_simulated_prompts.py \
  --dataset-scope bump --shared-db

# SinC benchmark
.venv/bin/python scripts/eval/test_sinc_prompts.py --shared-db

# Cross-dataset benchmark
.venv/bin/python scripts/eval/test_cross_dataset_prompts.py --shared-db

# Score existing simulated results without an LLM call
.venv/bin/python scripts/eval/test_simulated_prompts.py \
  --dataset-scope bump --shared-db --score-existing
```

Evaluation commands can incur model-provider cost and produce generated result
files. Use explicit output paths for exploratory work. See
[docs/EVALUATION_DETAILS.md](docs/EVALUATION_DETAILS.md) for scoring semantics and all
prompt collections.

## Clinical-note experiment

`clinical_notes_analysis/` is a focused adverse-event label-extraction package.
It includes an uploaded source dataframe, prompts, reviewed gold labels, and
preserved outputs. Its raw note text and record
identifiers require separate data-governance approval before public release.

## Generated-file policy

The root `.gitignore` excludes raw research inputs, SQLite databases, prompt
ground-truth exports, runtime logs, Python/model caches, generated evaluation
outputs, and visualizations. Only deliberately reviewed code, prompts,
documentation, and approved aggregate evidence should be committed.
