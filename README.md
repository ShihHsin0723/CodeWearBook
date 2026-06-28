# SinC Data Querying System

Natural-language-to-SQL research system for the BUMP and SinC datasets, with a
restricted cross-dataset mode over harmonized wearable views.

This public repository contains code, docs, tests, and synthetic/example dataset configuration only. Real study data, clinical notes, generated evaluation outputs, credentials, databases, and private dataset manifests are intentionally excluded.

## Prerequisites

- Python 3.8
- Authorized BUMP/SinC source data
- AWS credentials that can read the `llm/api_keys` secret in `us-west-1`, or a
  project-approved replacement for `get_llm_api_keys.py`
- Access to the LLM and embedding backends selected in `config.py`

Primary BUMP/SinC source datasets and generated databases are intentionally not
committed. Place authorized source files in the paths documented in
[README_DETAILS.md](README_DETAILS.md). The clinical-note experiment contains a
derived sensitive dataframe and requires separate release approval.

## Setup

```bash
git clone <repository-url>
cd SinC-Data-Querying-System
make setup
```

Review `config.py` before running. In particular, confirm `LLM_SOURCE`,
`EMBEDDING_SOURCE`, model names, and model paths.

## Build the database

Place the authorized `.csv.gz` inputs in their expected data directories, then
build the shared SQLite database:

```bash
make init-db
```

This loads BUMP and SinC raw tables and creates the harmonized cross-dataset
views. The generated `wearable_shared.sqlite` file is ignored by Git.

## Add a dataset

New datasets are installed through validated YAML manifests instead of edits to
the query-system core. Copy `datasets/examples/dataset.example.yaml`, implement the two
loader entrypoints, and declare the dataset's table metadata, retrieval
keywords, KB documents, and supported harmonized-view SELECT mappings. Then run:

```bash
.venv/bin/python scripts/datasets/register_dataset.py path/to/dataset.yaml
make init-db
```

See `datasets/README.md` and `scripts/datasets/README.md` for the setup workflow,
command reference, manifest contract, normalization responsibilities, and
verification checklist.

For CSV or Parquet datasets, generate the table and type configuration without
writing a Python adapter:

```bash
.venv/bin/python scripts/datasets/inspect_source.py \
  --dataset MyStudy \
  --entity-key participant_id \
  --entity-source user_id \
  --source sleep=data/my_study/sleep.csv \
  --output datasets/manifests/my_study.yaml
```

Inspection and loading are local and deterministic; source row values are not
written into retrieval metadata or sent to an LLM.

## Run

With the virtual environment activated, start the interactive system with:

```bash
python query_system.py
```

This prompts for the dataset scope and loads its authorized source files into
an in-memory database. To use the shared database and select a scope directly:

```bash
.venv/bin/python query_system.py --shared-db --dataset-scope bump
.venv/bin/python query_system.py --shared-db --dataset-scope sinc
.venv/bin/python query_system.py --shared-db --dataset-scope cross-dataset
```

Omit `--dataset-scope` to choose interactively. Omit `--shared-db` to load the
selected scope into an in-memory database from the authorized source files.

Equivalent Makefile shortcuts are:

```bash
make run-bump
make run-sinc
make run-cross
```

Each command starts the interactive query system using the shared database.
The cross-dataset scope only permits harmonized `wearable_*` views.

## Evaluate

```bash
# Small BUMP smoke evaluation
make eval-smoke
```

This invokes the configured LLM and may incur provider cost.

Full evaluation workflows, prompt sets, and output interpretation are described
in [docs/EVALUATION_DETAILS.md](docs/EVALUATION_DETAILS.md).

## Documentation

- [README_DETAILS.md](README_DETAILS.md): project structure, data layout,
  architecture, configuration, and complete workflows
- [docs/SYSTEM_DETAILS.md](docs/SYSTEM_DETAILS.md): query pipeline internals
- [docs/EVALUATION_DETAILS.md](docs/EVALUATION_DETAILS.md): benchmark methodology
- [clinical_notes_analysis/README.md](clinical_notes_analysis/README.md):
  adverse-event note-label experiment
