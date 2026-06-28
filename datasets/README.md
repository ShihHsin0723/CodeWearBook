# Dataset plugins

Add ordinary CSV or Parquet datasets with a YAML manifest. A manifest defines
source files, database tables, column mappings, retrieval metadata, knowledge
base rules, and optional cross-dataset mappings.

Directory layout:

- `datasets/examples/`: reusable manifest examples.
- `datasets/manifests/`: editable source manifests.
- `datasets/registered/`: installed runtime copies; do not edit directly.
- `scripts/datasets/`: generation and registration commands.

Use [`tabular_dataset.example.yaml`](examples/tabular_dataset.example.yaml) for
a compact template or
[`tabular_dataset.annotated.example.yaml`](examples/tabular_dataset.annotated.example.yaml)
for a field-by-field explanation.

## 1. Generate a starter manifest

The built-in tabular loader supports local CSV, compressed CSV, Parquet, and
authorized `s3://` objects. S3 sources must be exact object URIs; folder prefixes
and wildcards are not expanded.

```bash
python scripts/datasets/inspect_source.py \
  --dataset MyStudy \
  --entity-key participant_id \
  --entity-source "Participant ID" \
  --source sleep=s3://my-bucket/my-study/sleep.csv \
  --source activity=s3://my-bucket/my-study/activity.parquet \
  --output datasets/manifests/my_study.yaml
```

Arguments:

- `--dataset`: stable display name for the dataset.
- `--entity-key`: normalized database column used to join the dataset's tables.
- `--entity-source`: exact, case-sensitive source header containing that key.
  Omit it only when the normalized source header already equals `--entity-key`.
- `--source TABLE_SUFFIX=URI`: one raw table and its source object; repeat for
  additional tables.
- `--output`: source manifest to create.

Inspection and ingestion are deterministic and do not import the LLM client.
The generated manifest contains structural metadata only; sampled source values
are discarded and are not written to retrieval documents or prompts.

## 2. Review and complete the manifest

The generated YAML is a starter. Before registration:

1. Verify identifier, date, datetime, boolean, numeric, and encoded-string
   types. Keep participant identifiers as text to preserve leading zeros.
2. Mark essential identifiers and dates with `required: true`.
3. Confirm the authoritative date, row granularity, units, missing-value
   meaning, and deduplication rules.
4. Keep `infer_unlisted_columns: false` when every accepted source field should
   be reviewed explicitly.
5. Complete `sql_guideline`, `schema_metadata`, `kb_documents`, and any
   `harmonized_views`.

Do not rerun `inspect_source.py` after manual edits unless you intend to
regenerate and overwrite the source manifest.

## 3. Add schema metadata

Schema metadata helps retrieval find and explain raw tables and columns.

```yaml
schema_metadata:
  my_study_sleep:
    description: Daily sleep summaries.
    keywords: [sleep, sleep duration, HRV]
    columns:
      rmssd:
        description: Nightly heart-rate variability using RMSSD.
        keywords: [recovery, heart rate variability]
        aliases: [HRV, RMSSD]
        unit: ms
        null_meaning: No valid nightly HRV measurement was available.
```

Use descriptions, keywords, aliases, units, and null meanings—not participant
values or source-row examples. This metadata is included in LLM schema context.

## 4. Add knowledge-base documents

KB documents describe semantics that column metadata cannot express, such as
row granularity, scoring, classification, frequency, and query rules.

```yaml
kb_documents:
  - id: my_study_sleep_structure
    table: my_study_sleep
    type: table_info
    include_in_cross_dataset: true
    content: One row represents one participant sleep summary.

  - id: my_study_sleep_scoring
    table: my_study_sleep
    type: scoring_rule
    include_in_cross_dataset: true
    refers_to: [my_study_sleep_structure]
    content: Sleep score uses a 0-100 scale; higher values are better.
```

Every `id` must be globally unique. `refers_to` links related documents and must
reference IDs in the same manifest. A cross-enabled document may refer only to
other cross-enabled documents. Do not include participant values or examples.

## 5. Add cross-dataset mappings when needed

`harmonized_views` contains dataset-specific `SELECT` statements appended to
shared `wearable_*` views. Each mapping must:

- be one `SELECT` without a semicolon or SQL comments;
- read from a raw table owned by the dataset's `table_prefix`;
- return every canonical column with the exact required alias;
- perform dataset-specific date selection, deduplication, and unit conversion;
- document material comparability rules in the KB.

Namespace participant keys in shared views, for example:

```sql
'MyStudy:' || participant_id AS participant_key
```

Supported views are `wearable_sleep_daily`, `wearable_activity_daily`,
`wearable_readiness_daily`, `wearable_bodyport_daily`,
`wearable_ebt_sessions`, and `wearable_ema_daily`. See the annotated template
for complete output-column lists and an example mapping.

## 6. Validate, register, and load

Validate the completed source manifest without installing it:

```bash
python scripts/datasets/register_dataset.py \
  --check-only datasets/manifests/my_study.yaml
```

Register it:

```bash
python scripts/datasets/register_dataset.py datasets/manifests/my_study.yaml
```

Registration validates the manifest and copies it into `datasets/registered/`,
where the querying system discovers it. Registration does not access source
data or create database tables.

Create and load only this dataset's raw tables first:

```bash
python scripts/db/init_shared_db.py --dataset-scope my-study
```

After standalone loading succeeds, load participating datasets and rebuild the
shared harmonized views when cross-dataset querying is required:

```bash
python scripts/db/init_shared_db.py --dataset-scope cross-dataset
```

Initialization recreates tables for the selected scope, so verify row counts,
participant counts, dates, units, null rates, and duplicate handling afterward.
Verification should also cover every declared harmonized view, standalone table
selection, cross-dataset joins, and curated prompts for dataset-specific edges.

## 7. Update a registered dataset

Edit the source manifest outside `datasets/registered/`, then validate and
replace the installed copy:

```bash
python scripts/datasets/register_dataset.py \
  --replace datasets/manifests/my_study.yaml
```

Restart a running query application so it rediscovers the manifest. Additional
database work depends on what changed:

- Schema metadata, KB documents, aliases, keywords, or `sql_guideline`: restart
  the query application; do not reinitialize the database.
- Harmonized mappings: run cross-dataset initialization to rebuild views.
- Tables, sources, columns, types, required fields, entity key, or loader code:
  reinitialize the dataset scope; then run cross-dataset initialization if its
  harmonized mappings participate in shared views.

## Custom loader path

Use a Python loader when the source cannot be represented as one CSV/Parquet
object per table—for example, partitioned S3 prefixes or custom transformations.
Start with [`dataset.example.yaml`](examples/dataset.example.yaml) and
[`example_dataset_adapter.py`](example_dataset_adapter.py). Configure:

```yaml
loader:
  type: python
  create_tables: package.module.create_tables
  load_data: package.module.load_data
```

The same metadata, KB, harmonization, validation, registration, and update rules
apply to custom loaders.

## Runtime and safety model

At startup, the registry discovers `datasets/registered/*.yaml` and combines
those plugins with BUMP and SinC. The query system uses each manifest to select
the dataset scope, filter schema and KB retrieval, apply join guidance, load raw
tables, and append mappings to shared views.

Queries are read-only and scope-checked. A standalone scope may reference only
raw tables with its registered prefix; cross-dataset scope may reference only
`wearable_*` views. Mutating or multiple statements, internal SQLite tables,
and implicit comma joins are rejected.

Schema descriptions, keywords, SQL guidance, and KB text may be sent to the
configured LLM. Never put participant identifiers, row values, or source-row
examples in those fields.
