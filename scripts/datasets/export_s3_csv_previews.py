#!/usr/bin/env python3
"""Export the first rows of every CSV beneath an S3 prefix."""

import argparse
from pathlib import Path, PurePosixPath

import boto3
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a small local CSV preview for every CSV under an S3 prefix."
    )
    parser.add_argument("--bucket", default="4youandme-my-experiences")
    parser.add_argument("--prefix", default="final_wave_5_12_25/")
    parser.add_argument("--output", type=Path, default=Path("final_wave_5_12_25_previews"))
    parser.add_argument("--rows", type=int, default=5)
    return parser.parse_args()


def output_path(output_root: Path, prefix: str, key: str) -> Path:
    relative = PurePosixPath(key).relative_to(PurePosixPath(prefix.rstrip("/")))
    if relative.name.lower().endswith(".csv.gz"):
        relative = relative.with_name(relative.name[:-3])
    return output_root.joinpath(*relative.parts)


def main() -> None:
    args = parse_args()
    if args.rows < 1:
        raise SystemExit("--rows must be at least 1")

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    exported = 0
    failed = 0

    for page in paginator.paginate(Bucket=args.bucket, Prefix=args.prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.lower().endswith((".csv", ".csv.gz")):
                continue

            destination = output_path(args.output, args.prefix, key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            compression = "gzip" if key.lower().endswith(".gz") else None

            try:
                response = s3.get_object(Bucket=args.bucket, Key=key)
                with response["Body"] as body:
                    preview = pd.read_csv(body, nrows=args.rows, compression=compression)
                preview.to_csv(destination, index=False)
                exported += 1
                print(f"Exported s3://{args.bucket}/{key} -> {destination}")
            except Exception as error:
                failed += 1
                print(f"FAILED s3://{args.bucket}/{key}: {error}")

    print(f"Done: {exported} exported, {failed} failed, output={args.output}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
