#!/usr/bin/env python3
"""Run one prompt case across the main BUMP ablation variants."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

os.environ.setdefault("MPLCONFIGDIR", str(Path("/private/tmp/matplotlib-cache")))
os.environ.setdefault("XDG_CACHE_HOME", str(Path("/private/tmp/fontconfig-cache")))

import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


EVAL_SCRIPT = PROJECT_ROOT / "scripts/eval/test_prompts_with_ambiguity_replay.py"
DEFAULT_PROMPTS = PROJECT_ROOT / "prompts/curated/bump_prompts.jsonl"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scripts/eval/results/ablation/bump"
DEFAULT_CHOICES = SCRIPT_DIR / "bump_ambiguity_choices.json"
DEFAULT_VIS_DIR = DEFAULT_OUTPUT_DIR / "query_visualizations"

ABLATIONS = [
    ("full_system", "Full system", "full_system"),
    ("no_kb", "No KB", "no_kb"),
    ("no_schema_keywords", "No schema keywords", "no_all_schema_keywords"),
    ("no_ambiguity_detection", "No ambiguity detection", "no_ambiguity_resolver"),
]

METRICS = [
    ("DF exact match", "dataframe_match"),
    ("DF score", "df_match_score"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a single BUMP prompt case through full_system, no_kb, "
            "no_schema_keywords, and no_ambiguity_detection."
        )
    )
    parser.add_argument("case", help="Case index or comma/range expression accepted by --cases, e.g. 29 or 29,37.")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tag", default="case_ablation", help="Filename tag for generated result files.")
    parser.add_argument("--ambiguity-choices", type=Path, default=DEFAULT_CHOICES)
    parser.add_argument("--dataset-scope", default="bump")
    parser.add_argument(
        "--ablations",
        default="full_system,no_kb,no_schema_keywords,no_ambiguity_detection",
        help=(
            "Comma-separated ablation labels to run. Available labels: "
            "full_system,no_kb,no_schema_keywords,no_ambiguity_detection."
        ),
    )
    shared_db_group = parser.add_mutually_exclusive_group()
    shared_db_group.add_argument("--shared-db", dest="shared_db", action="store_true")
    shared_db_group.add_argument("--no-shared-db", dest="shared_db", action="store_false")
    parser.set_defaults(shared_db=True)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--manual-ambiguity",
        action="store_true",
        help="Let you choose ambiguity options interactively for variants that use ambiguity detection.",
    )
    parser.add_argument(
        "--record-ambiguity-choices",
        action="store_true",
        help="Record ambiguity choices made during this run.",
    )
    parser.add_argument(
        "--force-ambiguity-prompt",
        action="store_true",
        help="Force ambiguity prompts instead of accepting authoritative table matches.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    parser.add_argument(
        "--query-visualizations",
        dest="query_visualizations",
        action="store_true",
        default=True,
        help="Generate app-style visualization PNGs from each ablation's generated SQL.",
    )
    parser.add_argument(
        "--no-query-visualizations",
        dest="query_visualizations",
        action="store_false",
        help="Skip app-style query-result visualization generation.",
    )
    parser.add_argument(
        "--visualization-dir",
        type=Path,
        default=DEFAULT_VIS_DIR,
        help="Directory for per-case/per-ablation query visualization PNGs.",
    )
    parser.add_argument(
        "--metric-plots",
        action="store_true",
        help="Also write the old per-case ablation metric bar plot.",
    )
    return parser.parse_args()


def selected_ablations(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    requested = [item.strip() for item in args.ablations.split(",") if item.strip()]
    by_label = {label: (label, display_label, ablation) for label, display_label, ablation in ABLATIONS}
    unknown = [label for label in requested if label not in by_label]
    if unknown:
        raise ValueError(
            "Unknown ablation label(s): "
            + ", ".join(unknown)
            + ". Available labels: "
            + ", ".join(by_label)
        )
    return [by_label[label] for label in requested]


def case_filename_fragment(case_expr: str) -> str:
    return (
        case_expr.strip()
        .replace(",", "_")
        .replace("-", "_to_")
        .replace(" ", "")
    )


def result_path(args: argparse.Namespace, label: str) -> Path:
    case_fragment = case_filename_fragment(args.case)
    return args.output_dir / f"{args.tag}_{case_fragment}_{label}.jsonl"


def build_command(args: argparse.Namespace, label: str, ablation: str) -> list[str]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = result_path(args, label)

    command = [
        sys.executable,
        str(EVAL_SCRIPT),
        "--dataset-scope",
        args.dataset_scope,
        "--prompts",
        str(args.prompts),
        "--output",
        str(output),
        "--ablation",
        ablation,
        "--cases",
        args.case,
        "--rerun-tested",
        "--ambiguity-choices",
        str(args.ambiguity_choices),
    ]
    if args.shared_db:
        command.append("--shared-db")
    if args.verbose:
        command.append("--verbose")
    if args.manual_ambiguity and ablation != "no_ambiguity_resolver":
        command.append("--manual-ambiguity")
    if args.record_ambiguity_choices and ablation != "no_ambiguity_resolver":
        command.append("--record-ambiguity-choices")
    if args.force_ambiguity_prompt and ablation != "no_ambiguity_resolver":
        command.append("--force-ambiguity-prompt")
    return command


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def combine_results(args: argparse.Namespace) -> dict[str, dict[str, dict]]:
    combined: dict[str, dict[str, dict]] = {}
    for label, display_label, _ablation in selected_ablations(args):
        for row in load_jsonl(result_path(args, label)):
            case_index = str(row.get("index"))
            combined.setdefault(case_index, {})[display_label] = row
    return combined


def write_combined_json(args: argparse.Namespace, combined: dict[str, dict[str, dict]]) -> Path:
    output = args.output_dir / f"{args.tag}_{case_filename_fragment(args.case)}_combined_review.json"
    output.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


def visualizations_by_case(rows: list[dict]) -> dict[str, dict[str, dict]]:
    by_case: dict[str, dict[str, dict]] = {}
    for row in rows:
        case_index = str(row.get("case_index"))
        ablation = str(row.get("ablation"))
        by_case.setdefault(case_index, {})[ablation] = row
    return by_case


def write_per_case_json(
    args: argparse.Namespace,
    combined: dict[str, dict[str, dict]],
    query_visualizations: list[dict],
) -> list[Path]:
    visualization_lookup = visualizations_by_case(query_visualizations)
    outputs = []
    for case_index in sorted(combined, key=lambda value: int(value) if value.isdigit() else value):
        output = args.output_dir / f"{args.tag}_case_{case_index}_all_ablations.json"
        ablations = {}
        for _label, display_label, _ablation in selected_ablations(args):
            row = combined[case_index].get(display_label)
            if not row:
                continue
            ablations[display_label] = {
                "result": row,
                "visualization": visualization_lookup.get(case_index, {}).get(
                    display_label,
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "status": "not_generated",
                    },
                ),
            }
        output.write_text(
            json.dumps(
                {
                    "case_index": case_index,
                    "question": next(
                        (
                            row.get("question")
                            for row in combined[case_index].values()
                            if row.get("question")
                        ),
                        None,
                    ),
                    "ablations": ablations,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        outputs.append(output)
    return outputs


def write_metrics_csv(args: argparse.Namespace, combined: dict[str, dict[str, dict]]) -> Path:
    output = args.output_dir / f"{args.tag}_{case_filename_fragment(args.case)}_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "case_index",
                "ablation",
                "dataframe_match",
                "df_match_score",
                "sql_match",
                "raw_sql_match",
                "error",
                "generated_rows",
                "expected_rows",
            ],
        )
        writer.writeheader()
        for case_index in sorted(combined, key=lambda value: int(value) if value.isdigit() else value):
            for _label, display_label, _ablation in selected_ablations(args):
                row = combined[case_index].get(display_label)
                if not row:
                    continue
                generated_preview = row.get("generated_df_preview") or {}
                expected_preview = row.get("expected_df_preview") or {}
                writer.writerow(
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "dataframe_match": row.get("dataframe_match"),
                        "df_match_score": row.get("df_match_score"),
                        "sql_match": row.get("sql_match"),
                        "raw_sql_match": row.get("raw_sql_match"),
                        "error": row.get("error"),
                        "generated_rows": generated_preview.get("row_count"),
                        "expected_rows": expected_preview.get("row_count"),
                    }
                )
    return output


def plot_case(args: argparse.Namespace, case_index: str, case_results: dict[str, dict]) -> Path:
    output = args.output_dir / (
        f"{args.tag}_{case_filename_fragment(args.case)}_case_{case_index}_ablation_plot.png"
    )

    metric_labels = [label for label, _key in METRICS]
    x_positions = list(range(len(metric_labels)))
    bar_width = 0.16
    rows = [
        (display_label, case_results.get(display_label) or {})
        for _label, display_label, _ablation in selected_ablations(args)
    ]
    center = (len(rows) - 1) / 2
    colors = ["#4C78A8", "#F28E2B", "#59A14F", "#B07AA1"]

    fig, ax = plt.subplots(figsize=(10, 5.8))
    for idx, ((display_label, row), color) in enumerate(zip(rows, colors)):
        values = []
        for _metric_label, metric_key in METRICS:
            raw_value = row.get(metric_key, 0)
            if isinstance(raw_value, bool):
                raw_value = 1.0 if raw_value else 0.0
            values.append(float(raw_value or 0.0))
        offset = (idx - center) * bar_width
        bars = ax.bar(
            [x + offset for x in x_positions],
            values,
            width=bar_width,
            label=display_label,
            color=color,
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(value + 0.025, 1.05),
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

    question = next((row.get("question") for row in case_results.values() if row.get("question")), "")
    title = f"Case {case_index} Ablation Results"
    if question:
        title = f"{title}\n{question[:95]}{'...' if len(question) > 95 else ''}"
    ax.set_title(title, fontsize=12, pad=14)
    ax.set_ylabel("Score / rate")
    ax.set_ylim(0, 1.12)
    ax.set_xticks(x_positions)
    ax.set_xticklabels(metric_labels)
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.35)
    ax.legend(ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.15), frameon=False)
    fig.tight_layout()
    fig.savefig(output, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return output


def write_visualizations(args: argparse.Namespace, combined: dict[str, dict[str, dict]]) -> list[Path]:
    return [
        plot_case(args, case_index, combined[case_index])
        for case_index in sorted(combined, key=lambda value: int(value) if value.isdigit() else value)
    ]


def snapshot_pngs(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {item.resolve() for item in path.glob("*.png")}


def newest_created_png(path: Path, before: set[Path]) -> Optional[Path]:
    if not path.exists():
        return None
    created = [item for item in path.glob("*.png") if item.resolve() not in before]
    if not created:
        return None
    return max(created, key=lambda item: item.stat().st_mtime)


def generate_query_visualizations(args: argparse.Namespace, combined: dict[str, dict[str, dict]]) -> list[dict]:
    import pandas as pd

    import query_system as uqs
    from visualization import auto_visualize_data

    args.visualization_dir.mkdir(parents=True, exist_ok=True)
    generated: list[dict] = []
    engine = uqs.create_shared_db_engine() if args.shared_db else uqs.create_in_memory_db_engine(args.dataset_scope)
    app_visualization_dir = PROJECT_ROOT / "visualizations"
    app_visualization_dir.mkdir(parents=True, exist_ok=True)

    for case_index in sorted(combined, key=lambda value: int(value) if value.isdigit() else value):
        for label, display_label, _ablation in selected_ablations(args):
            row = combined[case_index].get(display_label)
            if not row:
                generated.append(
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "status": "skipped",
                        "reason": "missing result row",
                    }
                )
                continue
            sql = row.get("generated_sql")
            if row.get("error") or not sql:
                generated.append(
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "status": "skipped",
                        "reason": row.get("error") or "missing generated SQL",
                    }
                )
                continue

            output = args.visualization_dir / f"{args.tag}_case_{case_index}_{label}_query_visualization.png"
            try:
                dataframe = pd.read_sql(sql, engine)
                if dataframe.empty:
                    generated.append(
                        {
                            "case_index": case_index,
                            "ablation": display_label,
                            "status": "skipped",
                            "reason": "generated SQL returned an empty dataframe",
                        }
                    )
                    continue

                before = snapshot_pngs(app_visualization_dir)
                auto_visualize_data(row.get("question", ""), sql, dataframe)
                created = newest_created_png(app_visualization_dir, before)
                if created is None:
                    generated.append(
                        {
                            "case_index": case_index,
                            "ablation": display_label,
                            "status": "skipped",
                            "reason": "visualization function did not create a PNG",
                        }
                    )
                    continue
                shutil.move(str(created), output)
                generated.append(
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "status": "created",
                        "path": str(output),
                        "rows": int(len(dataframe)),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                generated.append(
                    {
                        "case_index": case_index,
                        "ablation": display_label,
                        "status": "failed",
                        "reason": str(exc),
                    }
                )
    return generated


def write_query_visualization_manifest(args: argparse.Namespace, rows: list[dict]) -> Path:
    output = args.visualization_dir / f"{args.tag}_{case_filename_fragment(args.case)}_query_visualizations_manifest.json"
    output.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


def main() -> int:
    args = parse_args()

    if not EVAL_SCRIPT.exists():
        raise FileNotFoundError(f"Eval script not found: {EVAL_SCRIPT}")
    if not args.prompts.exists():
        raise FileNotFoundError(f"Prompt file not found: {args.prompts}")

    for label, display_label, ablation in selected_ablations(args):
        command = build_command(args, label, ablation)
        printable = " ".join(command)
        print("\n" + "=" * 100)
        print(f"Running {display_label}:")
        print(printable)
        if args.dry_run:
            continue
        completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if completed.returncode != 0:
            print(f"\n{display_label} failed with exit code {completed.returncode}", file=sys.stderr)
            return completed.returncode

    if not args.dry_run:
        combined = combine_results(args)
        metric_plots = write_visualizations(args, combined) if args.metric_plots else []
        query_visualizations = []
        if args.query_visualizations:
            query_visualizations = generate_query_visualizations(args, combined)
        per_case_json = write_per_case_json(args, combined, query_visualizations)
        print("\nWrote per-case all-ablation review JSON file(s):")
        for output in per_case_json:
            print(output)
        if metric_plots:
            print("Wrote metric plot(s):")
            for plot in metric_plots:
                print(plot)
        if query_visualizations:
            print("Query visualization result(s) embedded in the per-case JSON file(s):")
            for item in query_visualizations:
                if item.get("status") == "created":
                    print(item["path"])
                else:
                    print(
                        f"case {item.get('case_index')} / {item.get('ablation')}: "
                        f"{item.get('status')} ({item.get('reason')})"
                    )

    print("\nCompleted ablation suite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
