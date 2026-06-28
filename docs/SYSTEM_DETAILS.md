# System details

The system converts natural-language research questions into read-only SQLite
queries. It retrieves relevant schema and knowledge-base context, generates and
validates SQL, executes it, and presents the resulting dataframe with a short
explanation.

## Runtime architecture

```text
Editable manifest                 Registered manifest
datasets/manifests/*.yaml  ───▶  datasets/registered/*.yaml
                                      │
                                      ▼
                              Dataset plugin registry
                               │                  │
                               ▼                  ▼
                       Raw table loading    Schema/KB context
                               │                  │
                               └───────┬──────────┘
                                       ▼
                              Natural language query
                                       │
                         retrieve → generate → validate
                                       │
                                       ▼
                                SQLite execution
```

Important modules:

- `dataset_plugins.py`: manifest parsing, validation, and plugin objects.
- `dataset_registry.py`: built-in and registered datasets, scope resolution,
  table creation, loading, and cross-dataset orchestration.
- `generic_dataset_loader.py`: manifest-driven CSV/Parquet schema inference,
  conversion, table creation, and loading.
- `harmonized_views.py`: canonical cross-dataset `wearable_*` views.
- `generate_schema.py`: physical SQLite introspection plus manifest metadata.
- `kb_registry.py`: built-in and manifest-defined KB documents.
- `embedding.py`: schema/KB vector indexes and semantic retrieval.
- `sql_scope_validation.py`: read-only and dataset-scope SQL enforcement.
- `query_system.py`: interactive retrieval, SQL generation, execution,
  explanation, refinement, visualization, and export workflow.

## Dataset registry and scopes

Two datasets are built in:

- `BUMP`: raw `bump_*` tables; participant joins normally use `record_id`.
- `SinC`: raw `sinc_*` tables; participant joins normally use `user_id`.

Additional datasets are discovered dynamically from
`datasets/registered/*.yaml`. The current registered plugin is:

- `MyExperiences` (`myexperiences` alias): raw `myexperiences_*` tables;
  participant joins use `user_id`.

The available scopes are therefore dynamic. Current scopes are BUMP, SinC,
MyExperiences, and Cross-dataset. A registered plugin contributes its name,
aliases, raw-table prefix, entity key, loader, metadata, KB documents, SQL
guidance, and harmonized mappings.

Cross-dataset scope exposes only canonical `wearable_*` views and uses
`participant_key` for participant-level joins. Participant keys are namespaced,
for example `MyExperiences:<user_id>`, so identifiers from different datasets
cannot collide.

## Manifest lifecycle

Dataset source manifests are authored in `datasets/manifests/`. Reusable
templates live in `datasets/examples/`. The workflow is:

1. Generate or write an editable manifest.
2. Review types, identifiers, dates, units, row granularity, and semantics.
3. Add schema metadata, KB documents, and optional harmonized mappings.
4. Validate and register the manifest.
5. Initialize the required database scope.

Registration validates the manifest and copies it into
`datasets/registered/`. It does not access source data, create tables, or load
rows. Runtime discovery reads the registered copy.

For tabular plugins, the generic loader supports local or authorized S3 CSV,
compressed CSV, and Parquet sources. S3 CSV samples and load batches are
streamed rather than buffering the complete object. CSV insertion defaults to
50,000-row chunks; Parquet uses `pyarrow`.

See `datasets/README.md` for the complete authoring and update workflow and
`scripts/datasets/README.md` for the command reference.

## Current MyExperiences integration

The MyExperiences manifest defines six S3-backed raw tables:

- `myexperiences_sleep`
- `myexperiences_activity`
- `myexperiences_readiness`
- `myexperiences_heartrate`
- `myexperiences_spo2`
- `myexperiences_workout`

Its schema metadata describes the legacy and newer Oura fields, units,
cross-version date conventions, encoded series, and missing-value meaning. Its
KB documents cover row granularity, cadence, scoring, units, and interpretation.

It contributes mappings to:

- `wearable_sleep_daily`: chooses the longest sleep period per participant/day.
- `wearable_activity_daily`: coalesces legacy and newer calorie/MET fields.
- `wearable_readiness_daily`: maps overall readiness and recovery contributors.

Heart-rate, SpO2, and workout data remain dataset-specific raw tables because
there are no corresponding canonical views. Manifest mappings become active in
SQLite after cross-dataset views are rebuilt.

## Database initialization

The shared database is `wearable_shared.sqlite`, configured through
`DB_CONNECTION_STRING` in `config.py`.

Standalone initialization:

```bash
python scripts/db/init_shared_db.py --dataset-scope myexperiences
```

This resolves the registered plugin, recreates its raw tables, streams the
configured sources, applies manifest conversions and required-field checks, and
inserts the rows. Other dataset tables are left in the shared SQLite file.

Cross-dataset initialization:

```bash
python scripts/db/init_shared_db.py --dataset-scope cross-dataset
```

This recreates and reloads every plugin with
`include_in_cross_dataset: true`, then drops and rebuilds the canonical views.
It is more expensive than standalone initialization because it reloads all
participating datasets.

The query application can instead create an in-memory database. Without
`--shared-db`, the selected scope is created and loaded again for that process.
For large datasets, the preloaded shared database is the practical option.

## Harmonized views

The canonical views are:

- `wearable_sleep_daily`
- `wearable_activity_daily`
- `wearable_readiness_daily`
- `wearable_bodyport_daily`
- `wearable_ebt_sessions`
- `wearable_ema_daily`

`harmonized_views.py` begins with BUMP and SinC branches. During cross-dataset
initialization, each participating plugin's declared `SELECT` is appended with
`UNION ALL`. Registration requires each mapping to return every canonical
column with its exact alias and to read from a raw table owned by that plugin.

Dataset-specific date selection, deduplication, score semantics, and unit
conversion belong in the mapping. Material comparability limitations belong in
cross-enabled KB documents.

## Query startup

Run against the shared database and select a scope explicitly:

```bash
python query_system.py --shared-db --dataset-scope myexperiences
python query_system.py --shared-db --dataset-scope cross-dataset
```

If `--dataset-scope` is omitted, the application presents all registered scopes
plus Cross-dataset. If `--shared-db` is omitted, it creates and loads an
in-memory database for the selected scope.

At startup, the system:

1. verifies an available LLM provider or loads the configured local model;
2. loads the configured embedding model;
3. generates schema documents from the active SQLite database;
4. overlays manifest table and column metadata;
5. filters schema and KB documents to the active scope;
6. builds separate FAISS indexes for schema and KB retrieval.

## Schema and knowledge retrieval

Physical table and column definitions always come from SQLite introspection.
Manifest `schema_metadata` adds descriptions, keywords, aliases, units, and
null meanings. Column keywords and aliases participate in table retrieval.

KB context combines:

- global rules;
- built-in BUMP or SinC documents where applicable;
- the active plugin's manifest documents;
- cross-enabled plugin documents in Cross-dataset scope;
- the active scope's `sql_guideline`.

`refers_to` links are validated at registration. Retrieval expands links
forward and backward so a scoring rule can pull in its instrument definition
and an instrument definition can pull in linked scoring or classification
rules. KB IDs are unique across built-in and registered content.

## Natural-language query pipeline

`process_natural_query(...)` performs the following stages:

1. Normalize the question and attach the active dataset scope.
2. Retrieve schema candidates through keyword and semantic matching.
3. Ask the LLM to resolve the request to one or more authoritative tables.
4. Accept high-confidence table selection or ask the user to disambiguate.
5. Retrieve table-linked KB rules and expand `refers_to` relationships.
6. Add global and scope-specific SQL guidance.
7. Build a compact prompt containing the selected schema, KB context, join
   rules, scope-specific examples, and the question.
8. Generate SQL and retry once when generation, validation, or execution fails.
9. Validate SQL against read-only and dataset-scope restrictions.
10. Execute the SQL and return a dataframe, generated SQL, and explanation.
11. Offer refinement, visualization, dataframe debug mode, or local export.

The system prints schema and KB retrieval statistics for each question. Empty
dataframes are valid executions and remain available to the refinement loop.

Questions containing missingness terms use a separate prompt path. The user
chooses between observed NULL/NA counts and expected-cadence gaps. Structured
frequency-catalog KB documents can answer cadence availability questions
without SQL generation.

## SQL generation and voting

`MAJORITY_VOTE_RUNS` controls how many SQL candidates are generated. The current
value is `1`, so the single successful candidate is used. Higher values enable
canonicalization, execution-aware comparison, and majority selection among
successful candidates or matching errors.

`MAX_REPROMPT_RETRY` is currently `1`. Failed generation, scope validation, or
execution can therefore trigger one corrected SQL attempt before returning the
error to the interactive refinement loop.

The explanation prompt receives schema context, KB context, the natural-language
question, and generated SQL. It does not receive executed dataframe rows.

## SQL safety and scope isolation

All executed SQL must begin with `SELECT` or `WITH` and contain exactly one
statement. The validator rejects:

- mutating or administrative keywords such as `INSERT`, `UPDATE`, `DELETE`,
  `DROP`, `CREATE`, `ALTER`, `ATTACH`, `PRAGMA`, and `VACUUM`;
- implicit comma joins;
- internal or out-of-scope table prefixes;
- cross-dataset queries that do not reference a `wearable_*` view.

CTEs are supported, but their underlying physical tables remain scope-checked.
`json_each` and `json_tree` are allowed table-valued functions.

Scope rules are:

- BUMP: only `bump_*` raw tables.
- SinC: only `sinc_*` raw tables.
- Registered standalone plugin: only its declared raw-table prefix.
- Cross-dataset: only `wearable_*` views.

## LLM and embedding configuration

`config.py` currently selects Anthropic for SQL/explanation generation and
Gemini for embeddings. API fallback order is Anthropic, OpenAI, Gemini, then
Together. `llm.py` performs a small health request, recognizes quota/rate-limit
errors, and moves to the next configured provider. API calls retry transient
connection and read-timeout failures.

Provider model names, local model paths, output/cache directories, and the
shared SQLite path are configured in `config.py`. API keys are loaded from the
AWS Secrets Manager secret `llm/api_keys` in `us-west-1`; credentials must not
be stored in source files.

Embeddings use a LangChain-compatible wrapper for Gemini or OpenAI, or a local
SentenceTransformer when `EMBEDDING_SOURCE` is `LOCAL`. Schema and KB chunks are
stored in separate in-memory FAISS indexes for each interactive session.

## Privacy boundary

Source rows are used locally for parsing, validation, database insertion, and
SQL execution. Manifest generation writes only structural metadata. Schema
descriptions, keywords, aliases, SQL guidance, KB content, the user's question,
and generated SQL may be sent to configured model providers.

Do not place participant identifiers, row values, source-row examples, secrets,
or protected health information in schema metadata, KB documents, SQL guidance,
or prompts. Query results remain local unless a separate workflow explicitly
transmits them.

## Updating the system

After editing an editable manifest:

```bash
python scripts/datasets/register_dataset.py \
  --replace datasets/manifests/my_study.yaml
```

- Metadata, KB, aliases, keywords, or SQL-guidance changes require an
  application restart, not database reinitialization.
- Harmonized mapping changes require rebuilding cross-dataset views.
- Source, table, column, type, required-field, entity-key, or loader changes
  require reinitializing the affected standalone scope and then Cross-dataset
  when shared mappings are used.

## Verification

Relevant automated coverage lives in:

- `tests/test_dataset_plugins.py`
- `tests/test_dataset_integration.py`
- `tests/test_generic_dataset_loader.py`
- `tests/test_sql_scope_validation.py`

Dataset onboarding should also verify raw row counts, participant counts, date
ranges, units, null rates, duplicate rules, every declared harmonized view,
standalone table selection, cross-dataset joins, and curated prompts for
dataset-specific edge cases.
