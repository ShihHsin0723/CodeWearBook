# Dataset onboarding commands

These scripts manage editable manifests in `datasets/manifests/`. Installed
runtime copies belong in `datasets/registered/` and should not be edited by
hand.

## Generate

Create a starter manifest from CSV or Parquet sources:

```bash
python scripts/datasets/inspect_source.py \
  --dataset MyStudy \
  --entity-key participant_id \
  --entity-source "Participant ID" \
  --source sleep=s3://my-bucket/my-study/sleep.csv \
  --output datasets/manifests/my_study.yaml
```

## Validate

```bash
python scripts/datasets/register_dataset.py \
  --check-only datasets/manifests/my_study.yaml
```

## Register or update

```bash
# First installation
python scripts/datasets/register_dataset.py \
  datasets/manifests/my_study.yaml

# Replace an installed manifest after edits
python scripts/datasets/register_dataset.py \
  --replace datasets/manifests/my_study.yaml
```

See `datasets/README.md` for the complete workflow, metadata and KB examples,
harmonized mappings, database initialization, and update rules.

## Optional S3 previews

Export a small local preview of CSV objects for manual schema review:

```bash
python scripts/datasets/export_s3_csv_previews.py \
  --bucket my-bucket \
  --prefix my-study/ \
  --output my-study-previews \
  --rows 5
```

Preview output may contain source values. Keep its output directory ignored and
do not add preview files to Git.
