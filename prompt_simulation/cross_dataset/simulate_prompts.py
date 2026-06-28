import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset_registry import SCOPE_CROSS


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODE = "w"
DEFAULT_LIMIT_N = 20


VIEW_METADATA: Dict[str, Dict[str, object]] = {
    "wearable_sleep_daily": {
        "description": (
            "Daily harmonized Oura sleep metrics for BUMP and SinC. "
            "Use this view for sleep score, duration, HR, HRV, and efficiency comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "sleep_score",
            "total_sleep_duration",
            "hr_average",
            "hr_lowest",
            "rmssd",
            "sleep_efficiency",
        ],
    },
    "wearable_activity_daily": {
        "description": (
            "Daily harmonized Oura activity metrics for BUMP and SinC. "
            "Use this view for steps, calories, MET, and activity score comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "activity_score",
            "steps",
            "active_calories",
            "total_calories",
            "average_met",
        ],
    },
    "wearable_readiness_daily": {
        "description": (
            "Daily harmonized Oura readiness metrics for BUMP and SinC. "
            "Use this view for recovery and readiness comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "readiness_score",
            "score_hrv_balance",
            "score_recovery_index",
            "score_resting_hr",
        ],
    },
    "wearable_bodyport_daily": {
        "description": (
            "Daily harmonized Bodyport metrics for BUMP and SinC. "
            "Use this view for BMI, weight, heart rate, balance, and peripheral fluid comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "weight_kg",
            "bmi_kg_m2",
            "heart_rate",
            "peripheral_fluid",
            "sway_velocity_mm_sec",
            "backend_sway_area_mm2",
        ],
    },
    "wearable_ebt_sessions": {
        "description": (
            "Harmonized emotion bias task sessions for BUMP and SinC. "
            "Use this view for reaction time and response count comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "ebt_bias_point",
            "median_rt_total",
            "mean_rt_total",
            "happy_response_count",
            "sad_response_count",
        ],
    },
    "wearable_ema_daily": {
        "description": (
            "Harmonized EMA-style daily responses for BUMP and SinC. "
            "Use this view for mood, energy, stress, and brain capability comparisons."
        ),
        "columns": [
            "dataset",
            "participant_key",
            "raw_participant_id",
            "event_date",
            "source_table",
            "measure",
            "item_score",
            "normalized_score",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate schema metadata and synthetic cross-dataset text-to-SQL "
            "prompts for the harmonized wearable_* views."
        )
    )
    parser.add_argument(
        "--schema-out",
        type=Path,
        default=SCRIPT_DIR / "schema_index.jsonl",
        help="Path to write cross-dataset schema metadata JSONL.",
    )
    parser.add_argument(
        "--test-out",
        type=Path,
        default=PROJECT_ROOT / "prompts" / "simulated" / "cross_dataset_prompts.jsonl",
        help="Path to write generated cross-dataset prompt JSONL.",
    )
    parser.add_argument(
        "--mode",
        choices=["w", "a"],
        default=DEFAULT_MODE,
        help="File write mode for the output JSONL files.",
    )
    parser.add_argument(
        "--limit-n",
        type=int,
        default=DEFAULT_LIMIT_N,
        help="Default LIMIT value used in top-k comparison questions.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        help="Optional subset of wearable_* views to include in the schema index.",
    )
    return parser.parse_args()


def select_views(requested: Optional[Iterable[str]]) -> List[str]:
    if not requested:
        return list(VIEW_METADATA.keys())

    requested_set = {item.strip() for item in requested}
    unknown = sorted(requested_set - set(VIEW_METADATA))
    if unknown:
        raise ValueError(
            "Unknown cross-dataset views requested: " + ", ".join(unknown)
        )
    return [view for view in VIEW_METADATA if view in requested_set]


def add_prompt(
    rows: List[Dict[str, object]],
    question: str,
    sql: str,
    tags: List[str],
    tables: List[str],
) -> None:
    rows.append(
        {
            "id": f"cross_dataset::q_{len(rows):03d}",
            "dataset": SCOPE_CROSS,
            "tables": tables,
            "question": question,
            "sql": sql,
            "tags": tags,
        }
    )


def build_prompt_rows(limit_n: int, selected_views: List[str]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    selected_set = set(selected_views)

    def can_use(tables: List[str]) -> bool:
        return all(table in selected_set for table in tables)

    def maybe_add(question: str, sql: str, tags: List[str], tables: List[str]) -> None:
        if can_use(tables):
            add_prompt(rows, question, sql, tags, tables)

    maybe_add(
        "How many distinct participants are represented in each dataset across harmonized sleep records?",
        (
            "SELECT dataset, COUNT(DISTINCT participant_key) AS participant_count "
            "FROM wearable_sleep_daily "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["count", "distinct", "group_by", "dataset_compare"],
        ["wearable_sleep_daily"],
    )
    maybe_add(
        "Compare average sleep score between BUMP and SinC.",
        (
            "SELECT dataset, AVG(sleep_score) AS avg_sleep_score "
            "FROM wearable_sleep_daily "
            "WHERE sleep_score IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_sleep_daily"],
    )
    maybe_add(
        "Which dataset has the higher average total sleep duration?",
        (
            "SELECT dataset, AVG(total_sleep_duration) AS avg_total_sleep_duration "
            "FROM wearable_sleep_daily "
            "WHERE total_sleep_duration IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY avg_total_sleep_duration DESC "
            f"LIMIT {limit_n};"
        ),
        ["avg", "group_by", "order_by", "limit", "dataset_compare"],
        ["wearable_sleep_daily"],
    )
    maybe_add(
        "For each dataset and calendar month, return year_month and average sleep efficiency, ordered by year_month then dataset.",
        (
            "SELECT dataset, SUBSTR(event_date, 1, 7) AS year_month, "
            "AVG(sleep_efficiency) AS avg_sleep_efficiency "
            "FROM wearable_sleep_daily "
            "WHERE sleep_efficiency IS NOT NULL "
            "GROUP BY dataset, year_month "
            "ORDER BY year_month, dataset;"
        ),
        ["avg", "group_by", "time_series", "dataset_compare"],
        ["wearable_sleep_daily"],
    )
    maybe_add(
        "Compare average daily steps between BUMP and SinC.",
        (
            "SELECT dataset, AVG(steps) AS avg_steps "
            "FROM wearable_activity_daily "
            "WHERE steps IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_activity_daily"],
    )
    maybe_add(
        f"What are the top {limit_n} participant-days with the highest activity score across both datasets?",
        (
            "SELECT dataset, participant_key, event_date, activity_score "
            "FROM wearable_activity_daily "
            "WHERE activity_score IS NOT NULL "
            "ORDER BY activity_score DESC "
            f"LIMIT {limit_n};"
        ),
        ["order_by", "limit", "top_k"],
        ["wearable_activity_daily"],
    )
    maybe_add(
        "Compare average readiness score between BUMP and SinC.",
        (
            "SELECT dataset, AVG(readiness_score) AS avg_readiness_score "
            "FROM wearable_readiness_daily "
            "WHERE readiness_score IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_readiness_daily"],
    )
    maybe_add(
        "Compare average BMI between BUMP and SinC.",
        (
            "SELECT dataset, AVG(bmi_kg_m2) AS avg_bmi "
            "FROM wearable_bodyport_daily "
            "WHERE bmi_kg_m2 IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_bodyport_daily"],
    )
    maybe_add(
        "For each dataset and BMI category, return the count of participant-days, where BMI category is one of Underweight, Normal weight, Overweight, or Obesity.",
        (
            "SELECT dataset, "
            "CASE "
            "WHEN bmi_kg_m2 < 18.5 THEN 'Underweight' "
            "WHEN bmi_kg_m2 < 25.0 THEN 'Normal weight' "
            "WHEN bmi_kg_m2 < 30.0 THEN 'Overweight' "
            "ELSE 'Obesity' "
            "END AS bmi_category, "
            "COUNT(*) AS row_count "
            "FROM wearable_bodyport_daily "
            "WHERE bmi_kg_m2 IS NOT NULL "
            "GROUP BY dataset, bmi_category "
            "ORDER BY dataset, bmi_category;"
        ),
        ["case_when", "group_by", "dataset_compare"],
        ["wearable_bodyport_daily"],
    )
    maybe_add(
        "Compare average emotion bias point between BUMP and SinC.",
        (
            "SELECT dataset, AVG(ebt_bias_point) AS avg_ebt_bias_point "
            "FROM wearable_ebt_sessions "
            "WHERE ebt_bias_point IS NOT NULL "
            "GROUP BY dataset "
            "ORDER BY dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_ebt_sessions"],
    )
    maybe_add(
        "For each dataset and calendar month, return year_month and average mean reaction time from EBT sessions, ordered by year_month then dataset.",
        (
            "SELECT dataset, SUBSTR(event_date, 1, 7) AS year_month, "
            "AVG(mean_rt_total) AS avg_mean_rt_total "
            "FROM wearable_ebt_sessions "
            "WHERE mean_rt_total IS NOT NULL "
            "GROUP BY dataset, year_month "
            "ORDER BY year_month, dataset;"
        ),
        ["avg", "group_by", "time_series", "dataset_compare"],
        ["wearable_ebt_sessions"],
    )
    maybe_add(
        "Compare average normalized EMA score by measure and dataset.",
        (
            "SELECT dataset, measure, AVG(normalized_score) AS avg_normalized_score "
            "FROM wearable_ema_daily "
            "WHERE normalized_score IS NOT NULL "
            "GROUP BY dataset, measure "
            "ORDER BY measure, dataset;"
        ),
        ["avg", "group_by", "dataset_compare"],
        ["wearable_ema_daily"],
    )
    maybe_add(
        "For each EMA measure, return the average normalized score in BUMP, the average normalized score in SinC, and the absolute difference between them, then return the single measure with the largest absolute difference.",
        (
            "WITH per_dataset AS ( "
            "SELECT dataset, measure, AVG(normalized_score) AS avg_normalized_score "
            "FROM wearable_ema_daily "
            "WHERE normalized_score IS NOT NULL "
            "GROUP BY dataset, measure "
            ") "
            "SELECT measure, "
            "MAX(CASE WHEN dataset = 'BUMP' THEN avg_normalized_score END) AS bump_avg, "
            "MAX(CASE WHEN dataset = 'SinC' THEN avg_normalized_score END) AS sinc_avg, "
            "ABS( "
            "MAX(CASE WHEN dataset = 'BUMP' THEN avg_normalized_score END) - "
            "MAX(CASE WHEN dataset = 'SinC' THEN avg_normalized_score END) "
            ") AS avg_score_gap "
            "FROM per_dataset "
            "GROUP BY measure "
            "ORDER BY avg_score_gap DESC "
            "LIMIT 1;"
        ),
        ["cte", "avg", "case_when", "group_by", "dataset_compare"],
        ["wearable_ema_daily"],
    )
    maybe_add(
        "For each dataset, using same-day matched sleep and readiness records, return the average readiness score on days where sleep score is below 70.",
        (
            "SELECT s.dataset, AVG(r.readiness_score) AS avg_readiness_on_low_sleep_days "
            "FROM wearable_sleep_daily s "
            "JOIN wearable_readiness_daily r "
            "ON s.dataset = r.dataset "
            "AND s.participant_key = r.participant_key "
            "AND s.event_date = r.event_date "
            "WHERE s.sleep_score < 70 "
            "AND r.readiness_score IS NOT NULL "
            "GROUP BY s.dataset "
            "ORDER BY s.dataset;"
        ),
        ["join", "avg", "group_by", "dataset_compare"],
        ["wearable_sleep_daily", "wearable_readiness_daily"],
    )
    maybe_add(
        "For each dataset, using same-day matched sleep and readiness records, return the average sleep score, average readiness score, and number of paired days.",
        (
            "SELECT s.dataset, "
            "AVG(s.sleep_score) AS avg_sleep_score, "
            "AVG(r.readiness_score) AS avg_readiness_score, "
            "COUNT(*) AS paired_days "
            "FROM wearable_sleep_daily s "
            "JOIN wearable_readiness_daily r "
            "ON s.dataset = r.dataset "
            "AND s.participant_key = r.participant_key "
            "AND s.event_date = r.event_date "
            "WHERE s.sleep_score IS NOT NULL "
            "AND r.readiness_score IS NOT NULL "
            "GROUP BY s.dataset "
            "ORDER BY s.dataset;"
        ),
        ["join", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_sleep_daily", "wearable_readiness_daily"],
    )
    maybe_add(
        "For each dataset, using same-day matched activity and sleep records, return average steps, average sleep score, and the number of paired days.",
        (
            "SELECT a.dataset, AVG(a.steps) AS avg_steps, AVG(s.sleep_score) AS avg_sleep_score, COUNT(*) AS paired_days "
            "FROM wearable_activity_daily a "
            "JOIN wearable_sleep_daily s "
            "ON a.dataset = s.dataset "
            "AND a.participant_key = s.participant_key "
            "AND a.event_date = s.event_date "
            "WHERE a.steps IS NOT NULL "
            "AND s.sleep_score IS NOT NULL "
            "GROUP BY a.dataset "
            "ORDER BY a.dataset;"
        ),
        ["join", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_activity_daily", "wearable_sleep_daily"],
    )
    maybe_add(
        "For each dataset, using same-day matched Bodyport and sleep records, return average BMI, average total sleep duration, and the number of paired days.",
        (
            "SELECT b.dataset, AVG(b.bmi_kg_m2) AS avg_bmi, AVG(s.total_sleep_duration) AS avg_total_sleep_duration, COUNT(*) AS paired_days "
            "FROM wearable_bodyport_daily b "
            "JOIN wearable_sleep_daily s "
            "ON b.dataset = s.dataset "
            "AND b.participant_key = s.participant_key "
            "AND b.event_date = s.event_date "
            "WHERE b.bmi_kg_m2 IS NOT NULL "
            "AND s.total_sleep_duration IS NOT NULL "
            "GROUP BY b.dataset "
            "ORDER BY b.dataset;"
        ),
        ["join", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_bodyport_daily", "wearable_sleep_daily"],
    )
    maybe_add(
        "For each dataset, on days with at least 10000 steps and same-day matched EMA records, return the average normalized mood score and the number of paired days.",
        (
            "SELECT e.dataset, AVG(e.normalized_score) AS avg_mood_score, COUNT(*) AS paired_days "
            "FROM wearable_ema_daily e "
            "JOIN wearable_activity_daily a "
            "ON e.dataset = a.dataset "
            "AND e.participant_key = a.participant_key "
            "AND e.event_date = a.event_date "
            "WHERE e.measure = 'mood' "
            "AND e.normalized_score IS NOT NULL "
            "AND a.steps >= 10000 "
            "GROUP BY e.dataset "
            "ORDER BY e.dataset;"
        ),
        ["join", "where", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_ema_daily", "wearable_activity_daily"],
    )
    maybe_add(
        "For each dataset, on days where same-day matched sleep efficiency is below 80, return the average normalized stress score and the number of paired days.",
        (
            "SELECT e.dataset, AVG(e.normalized_score) AS avg_stress_score, COUNT(*) AS paired_days "
            "FROM wearable_ema_daily e "
            "JOIN wearable_sleep_daily s "
            "ON e.dataset = s.dataset "
            "AND e.participant_key = s.participant_key "
            "AND e.event_date = s.event_date "
            "WHERE e.measure = 'stress' "
            "AND e.normalized_score IS NOT NULL "
            "AND s.sleep_efficiency < 80 "
            "GROUP BY e.dataset "
            "ORDER BY e.dataset;"
        ),
        ["join", "where", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_ema_daily", "wearable_sleep_daily"],
    )
    maybe_add(
        "For each dataset, using same-day matched readiness and Bodyport records where both resting HR and Bodyport heart rate are non-null, return average resting HR, average Bodyport heart rate, and the number of paired days.",
        (
            "SELECT r.dataset, AVG(r.score_resting_hr) AS avg_resting_hr, AVG(b.heart_rate) AS avg_bodyport_hr, COUNT(*) AS paired_days "
            "FROM wearable_readiness_daily r "
            "JOIN wearable_bodyport_daily b "
            "ON r.dataset = b.dataset "
            "AND r.participant_key = b.participant_key "
            "AND r.event_date = b.event_date "
            "WHERE r.score_resting_hr IS NOT NULL "
            "AND b.heart_rate IS NOT NULL "
            "GROUP BY r.dataset "
            "ORDER BY r.dataset;"
        ),
        ["join", "avg", "count", "group_by", "dataset_compare"],
        ["wearable_readiness_daily", "wearable_bodyport_daily"],
    )
    maybe_add(
        "Return each dataset and its count of participant-days where both same-day readiness score and sleep score are below 70, ordered from highest to lowest count.",
        (
            "SELECT s.dataset, COUNT(*) AS low_sleep_low_readiness_days "
            "FROM wearable_sleep_daily s "
            "JOIN wearable_readiness_daily r "
            "ON s.dataset = r.dataset "
            "AND s.participant_key = r.participant_key "
            "AND s.event_date = r.event_date "
            "WHERE s.sleep_score < 70 "
            "AND r.readiness_score < 70 "
            "GROUP BY s.dataset "
            "ORDER BY low_sleep_low_readiness_days DESC;"
        ),
        ["join", "where", "count", "group_by", "dataset_compare"],
        ["wearable_sleep_daily", "wearable_readiness_daily"],
    )

    return rows


def write_schema_rows(path: Path, mode: str, selected_views: List[str]) -> None:
    with path.open(mode, encoding="utf-8") as f:
        for view_name in selected_views:
            metadata = VIEW_METADATA[view_name]
            f.write(
                json.dumps(
                    {
                        "dataset": SCOPE_CROSS,
                        "sql_table": view_name,
                        "columns": metadata["columns"],
                        "description": metadata["description"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def write_prompt_rows(path: Path, mode: str, rows: List[Dict[str, object]]) -> None:
    with path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    selected_views = select_views(args.include)
    prompt_rows = build_prompt_rows(args.limit_n, selected_views)

    args.schema_out.parent.mkdir(parents=True, exist_ok=True)
    args.test_out.parent.mkdir(parents=True, exist_ok=True)

    write_schema_rows(args.schema_out, args.mode, selected_views)
    write_prompt_rows(args.test_out, args.mode, prompt_rows)

    print(f"Wrote {len(selected_views)} cross-dataset schema rows to {args.schema_out}")
    print(f"Wrote {len(prompt_rows)} cross-dataset prompts to {args.test_out}")


if __name__ == "__main__":
    main()
