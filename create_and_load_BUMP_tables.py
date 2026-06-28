from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Float, Boolean, DateTime, Text, JSON, Date
import pandas as pd
import numpy as np
import boto3
import gzip
import io
import re

metadata = MetaData()

s3 = boto3.client("s3")
bucket_name = "4youandme-bump"


def read_csv_from_s3(bucket, key) -> pd.DataFrame:
    """Read a CSV or CSV.GZ file directly from S3 into a DataFrame."""
    resp = s3.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()

    if key.endswith(".gz"):
        # Handle gzip-compressed CSV
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            return pd.read_csv(gz, dtype={'answer_text': str}, low_memory=False)
    else:
        # Plain CSV
        return pd.read_csv(io.BytesIO(body))


TABLE_SCHEMAS = {
    "bump_compliance": [
        Column("id", Integer),  # some ids are "-1"
        Column("user_id", Integer),
        Column("task_type", String),
        Column("stream", String),
        Column("event_date", DateTime),
        Column("data_present", Boolean),
        Column("record_id", String)
    ],
    "bump_quick_activities": [
        Column("user_id", Integer),
        Column("task_type", String),
        Column("stream", String),
        Column("event_date", DateTime),
        Column("id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("position", Integer, nullable=True),  # a lot are missing
        Column("label", String, nullable=True),  # a lot are missing
        Column("schedulable_id", Integer),
        Column("record_id", String)
    ],
    "bump_surveys": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("survey_id", Integer),
        # baseline survey, daily survey, engagement survey, etc
        Column("title", String),
        Column("event_date", DateTime),
        Column("from", DateTime),
        Column("to", DateTime),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("question_id", Integer),
        Column("question_text", Text),   # multiline text (quotes/newlines)
        Column("answer_text", String),
        Column("record_id", String),
    ],
    "bump_video_diary": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("schedulable_type", String),
        Column("title", String),
        Column("from", DateTime),
        Column("to", DateTime),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("event_date", DateTime),
        Column("result", JSON, nullable=True),  # some results are missing
        Column("record_id", String)
    ],
    "bump_bodyport": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("creation_date", DateTime),
        Column("event_date", Date),  # no time
        Column("backend_sway_area_mm2", Float),
        Column("bmi_kg_m2", Float),
        Column("device_id", String),
        Column("end_code", Integer),
        Column("heart_rate", Integer),
        Column("impedance_mag_1_ohms", Float),
        Column("impedance_phase_1_degs", Float),
        Column("impedance_ratio", Float),
        Column("muuid", String),
        Column("patient_id", String),
        Column("peripheral_fluid", Float),
        Column("run_time_sec", Float),
        Column("sway_velocity_mm_sec", Float),
        Column("total_body_water_percent", Float),
        Column("uuid", String),
        Column("weight_kg", Float),
        Column("record_id", String),
    ],
    "bump_ebt": [
        Column("user_id", Integer),  # CSV shows 32.0 etc.; cast to int on load
        Column("participant_id", String),
        Column("subject_id", String),
        Column("visit_id", String),
        Column("general_start_time", DateTime),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("task", String),  # EBT
        Column("EBTRTMDT", Float, nullable=True),
        Column("EBTRTMNT", Float, nullable=True),
        Column("EBTRTSDT", Float, nullable=True),
        Column("EBTRTMDH", Float, nullable=True),
        Column("EBTRTMNH", Float, nullable=True),
        Column("EBTRTSDH", Float, nullable=True),
        Column("EBTRTMDS", Float, nullable=True),
        Column("EBTRTMNS", Float, nullable=True),
        Column("EBTRTSDS", Float, nullable=True),
        Column("EBTRCH", Integer, nullable=True),
        Column("EBTRCS", Integer, nullable=True),
        Column("EBTBP", Float, nullable=True),
        # A lot of missings for these columns
        Column("EBTARMDT", Float, nullable=True),
        Column("EBTARMNT", Float, nullable=True),
        Column("EBTARSDT", Float, nullable=True),
        Column("EBTARMDH", Float, nullable=True),
        Column("EBTARMNH", Float, nullable=True),
        Column("EBTARSDH", Float, nullable=True),
        Column("EBTARMDS", Float, nullable=True),
        Column("EBTARMNS", Float, nullable=True),
        Column("EBTARSDS", Float, nullable=True),
        Column("EBTARCH", Integer, nullable=True),
        Column("EBTARCS", Integer, nullable=True),
        Column("EBTABP", Float, nullable=True),
        Column("record_id", String),
    ],
    "bump_nbx": [
        # CSV shows 28.0, 39.0 → cast to int on load
        Column("user_id", Integer),
        Column("participant_id", String),  # camcog-... IDs
        Column("subject_id", String),
        Column("visit_id", String),
        Column("general_start_time", DateTime),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("task", String),  # NBX
        Column("NBXPCFA", Float),
        Column("NBXPCH", Float),       # percent hits
        Column("NBXSCORE", Float),     # overall score
        Column("NBXHITS", Integer),
        Column("NBXFALRM", Integer),
        Column("NBXMISS", Integer),
        Column("NBXCRJCT", Integer),
        Column("NBXAPRME", Float),
        Column("NBXDPRME", Float),
        Column("record_id", String),
    ],
    "bump_pvt": [
        Column("user_id", Integer),  # CSV shows 31.0 etc.; cast to int on load
        Column("participant_id", String(100)),
        Column("subject_id", String(50)),
        Column("visit_id", String(50)),
        Column("general_start_time", DateTime),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("task", String(10)),  # PVT
        Column("PVTTH", Integer),     # total hits/trials (appears integer)
        Column("PVTTFP", Integer),    # false positives
        Column("PVTTTO", Integer),    # timeouts
        Column("PVTMRT", Float),      # mean reaction time
        Column("PVTSV", Float),       # (std or sum of variability)
        Column("PVTCV", Float),       # coefficient of variation
        Column("PVTSLF", Float),      # lapses (float in data)
        Column("PVTMPL", Float),      # mean psych lapse or similar
        Column("PVTSTH", Float),      # second-half metrics (often blank)
        Column("PVTSTFP", Float),
        Column("PVTSTTO", Float),
        Column("PVTSMRT", Float),
        Column("PVTSSV", Float),
        Column("PVTSCV", Float),
        Column("PVTSMPL", Float),
        Column("record_id", String(100)),
    ],
    "bump_oura_activity": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),  # activity
        Column("event_date", Date),
        Column("average_met", Float),
        Column("cal_active", Integer),
        Column("cal_total", Integer),
        Column("class_5min", Text, nullable=True),
        Column("daily_movement", Integer),
        Column("day_end", DateTime),
        Column("day_start", DateTime),
        Column("high", Integer),
        Column("inactive", Integer),
        Column("inactivity_alerts", Integer),
        Column("low", Integer),
        Column("medium", Integer),
        # Per-minute MET series (array of floats) — store as JSON
        Column("met_1min", JSON, nullable=True),
        Column("met_min_high", Integer),
        Column("met_min_inactive", Integer),
        Column("met_min_low", Integer),
        Column("met_min_medium", Integer),
        Column("non_wear", Integer),
        Column("rest", Integer),
        Column("rest_mode_state", Integer),
        # Score + components (several are often missing → nullable)
        Column("score", Integer, nullable=True),
        Column("score_meet_daily_targets", Integer, nullable=True),
        Column("score_move_every_hour", Integer, nullable=True),
        Column("score_recovery_time", Integer, nullable=True),
        Column("score_stay_active", Integer, nullable=True),
        Column("score_training_frequency", Integer, nullable=True),
        Column("score_training_volume", Integer, nullable=True),
        Column("steps", Integer),
        # Goals / targets
        Column("target_calories", Integer),
        Column("target_km", Float),
        Column("target_miles", Float),
        Column("timezone", Integer),  # Timezone offset in minutes
        # Distance remaining to target (derived by source)
        Column("to_target_km", Float),
        Column("to_target_miles", Float),
        Column("total", Integer),  # source total (e.g., total active mins)
        Column("record_id", String),
    ],
    "bump_oura_readiness": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),  # readiness
        Column("event_date", DateTime),
        Column("period_id", Integer),
        Column("rest_mode_state", Integer, nullable=True),
        Column("score", Integer),
        Column("score_activity_balance", Integer),
        Column("score_hrv_balance", Integer, nullable=True),
        Column("score_previous_day", Integer),
        Column("score_previous_night", Integer),
        Column("score_recovery_index", Integer),
        Column("score_resting_hr", Integer),
        Column("score_sleep_balance", Integer),
        Column("score_temperature", Integer),
        Column("record_id", String),
    ],
    "bump_oura_sleep": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),  # sleep
        Column("event_date", DateTime),
        Column("awake", Integer),
        Column("bedtime_end", DateTime),
        Column("bedtime_end_delta", Integer),
        Column("bedtime_start", DateTime),
        Column("bedtime_start_delta", Integer),
        Column("breath_average", Float),
        Column("deep", Integer),
        Column("duration", Integer),
        Column("efficiency", Integer),
        Column("hr_5min", JSON),
        Column("hr_average", Float),
        Column("hr_lowest", Integer),
        Column("hypnogram_5min", String),  # compact string like "441111..."
        Column("is_longest", Boolean),
        Column("light", Integer),
        Column("midpoint_at_delta", Integer),
        Column("midpoint_time", Integer),
        Column("onset_latency", Integer),
        Column("period_id", Integer),
        Column("rem", Integer),
        Column("restless", Integer),
        Column("rmssd", Integer),
        Column("rmssd_5min", JSON),
        Column("score", Integer),
        Column("score_alignment", Integer),
        Column("score_deep", Integer),
        Column("score_disturbances", Integer),
        Column("score_efficiency", Integer),
        Column("score_latency", Integer),
        Column("score_rem", Integer),
        Column("score_total", Integer),
        Column("temperature_delta", Float),
        Column("temperature_deviation", Float),
        Column("temperature_trend_deviation", Float),
        Column("timezone", Integer),
        Column("total", Integer),
        Column("record_id", String),
    ],
    "bump_garmin_activities": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),  # activities
        Column("event_date", DateTime),
        Column("active_kilocalories", Integer),
        Column("activity_description", String, nullable=True),
        Column("activity_id", String, nullable=True),
        Column("activity_name", String, nullable=True),
        Column("activity_type", String, nullable=True),
        Column("average_bike_cadence_in_rounds_per_minute", Float, nullable=True),
        Column("average_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("average_pace_in_minutes_per_kilometer", Float, nullable=True),
        Column("average_run_cadence_in_steps_per_minute", Float, nullable=True),
        Column("average_speed_in_meters_per_second", Float, nullable=True),
        Column("average_swim_cadence_in_strokes_per_minute",
               Float, nullable=True),
        Column("device_name", String, nullable=True),
        Column("distance_in_meters", Float),
        Column("duration_in_seconds", Integer),
        Column("manual", Boolean, nullable=True),
        Column("max_bike_cadence_in_rounds_per_minute", Float, nullable=True),
        Column("max_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("max_pace_in_minutes_per_kilometer", Float, nullable=True),
        Column("max_run_cadence_in_steps_per_minute", Float, nullable=True),
        Column("max_speed_in_meters_per_second", Float, nullable=True),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("starting_latitude_in_degree", Float),
        Column("starting_longitude_in_degree", Float),
        Column("steps", Integer),
        Column("summary_id", String, nullable=True),
        Column("total_elevation_gain_in_meters", Float),
        Column("total_elevation_loss_in_meters", Float),
        Column("record_id", String),
    ],
    "bump_garmin_activity_details": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("event_date", DateTime),
        Column("activity_id", String),
        # often present but allow nulls when empty strings show up
        Column("laps", JSON, nullable=True),
        Column("summary_id", String),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("activity_type", String),
        Column("duration_in_seconds", Integer),
        # lots of missings across cadence/HR/speed/pace calories/distance & “max_*”
        Column("average_bike_cadence_in_rounds_per_minute", Float, nullable=True),
        Column("average_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("average_run_cadence_in_steps_per_minute", Float, nullable=True),
        Column("average_speed_in_meters_per_second", Float, nullable=True),
        Column("average_swim_cadence_in_strokes_per_minute",
               Float, nullable=True),
        Column("average_pace_in_minutes_per_kilometer", Float, nullable=True),
        Column("active_kilocalories", Integer, nullable=True),
        Column("device_name", String, nullable=True),
        Column("distance_in_meters", Float, nullable=True),
        Column("max_bike_cadence_in_rounds_per_minute", Float, nullable=True),
        Column("max_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("max_pace_in_minutes_per_kilometer", Float, nullable=True),
        Column("max_run_cadence_in_steps_per_minute", Float, nullable=True),
        Column("max_speed_in_meters_per_second", Float, nullable=True),
        Column("number_of_active_lengths", Integer, nullable=True),
        Column("starting_latitude_in_degree", Float, nullable=True),
        Column("starting_longitude_in_degree", Float, nullable=True),
        Column("steps", Integer, nullable=True),
        Column("total_elevation_gain_in_meters", Float, nullable=True),
        Column("total_elevation_loss_in_meters", Float, nullable=True),
        Column("is_parent", Boolean, nullable=True),
        Column("parent_summary_id", String, nullable=True),
        Column("manual", Boolean, nullable=True),
        Column("record_id", String),
    ],
    "bump_garmin_user_metrics": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("event_date", DateTime),
        Column("enhanced", String, nullable=True),
        Column("fitness_age", Integer),
        Column("summary_id", String),
        Column("vo2_max", Float),
        Column("record_id", String),
    ],
    "bump_garmin_pulse_ox": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("event_date", DateTime),
        Column("duration_in_seconds", Integer),
        Column("on_demand", Boolean),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("summary_id", String),
        Column("time_offset_spo2_values", JSON),  # dict: {seconds: SpO2}
        Column("record_id", String),
    ],
    "bump_garmin_dailies": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),                      # e.g., 'dailies'
        Column("event_date", Date),
        Column("active_kilocalories", Integer, nullable=True),
        Column("active_time_in_seconds", Integer, nullable=True),
        Column("activity_stress_duration_in_seconds", Integer, nullable=True),
        Column("stress_duration_in_seconds", Integer, nullable=True),
        Column("activity_type", String, nullable=True),   # e.g., 'WALKING'
        Column("average_heart_rate_in_beats_per_minute", Integer, nullable=True),
        # -2 means missing → coerce to NULL on read
        Column("average_stress_level", Integer, nullable=True),
        Column("bmr_kilocalories", Integer, nullable=True),
        Column("consumed_calories", Integer, nullable=True),
        Column("distance_in_meters", Float, nullable=True),
        Column("duration_in_seconds", Integer, nullable=True),
        Column("floors_climbed", Integer, nullable=True),
        Column("floors_climbed_goal", Integer, nullable=True),
        Column("high_stress_duration_in_seconds", Integer, nullable=True),
        Column("intensity_duration_goal_in_seconds", Integer, nullable=True),
        Column("low_stress_duration_in_seconds", Integer, nullable=True),
        Column("medium_stress_duration_in_seconds", Integer, nullable=True),
        Column("rest_stress_duration_in_seconds", Integer, nullable=True),
        Column("max_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("max_stress_level", Integer, nullable=True),
        Column("min_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("resting_heart_rate_in_beats_per_minute", Integer, nullable=True),
        Column("start_time_in_seconds", Integer, nullable=True),
        Column("start_time_offset_in_seconds", Integer, nullable=True),
        Column("steps", Integer, nullable=True),
        Column("steps_goal", Integer, nullable=True),
        # e.g., 'balanced_awake', 'very_stressful_awake', 'unknown'
        Column("stress_qualifier", String, nullable=True),
        Column("summary_id", String, nullable=True),
        # JSON object: { "second_offset": HR }
        Column("time_offset_heart_rate_samples", JSON, nullable=True),
        Column("vigorous_intensity_duration_in_seconds", Integer, nullable=True),
        Column("record_id", String),
    ],
    "bump_garmin_respiration": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),             # e.g., 'allDayRespiration'
        Column("event_date", Date),
        Column("duration_in_seconds", Integer),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("summary_id", String),
        # maps epoch offset (sec) → breathing rate (float)
        Column("time_offset_epoch_to_breaths", JSON, nullable=True),
        Column("record_id", String),
    ],
    "bump_garmin_epochs": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),         # e.g., 'epochs'
        Column("event_date", Date),
        Column("active_kilocalories", Integer),
        Column("active_time_in_seconds", Integer),
        Column("activity_type", String),     # e.g., 'SEDENTARY'
        Column("distance_in_meters", Float),
        Column("duration_in_seconds", Integer),
        Column("intensity", String),         # stored as string
        Column("max_motion_intensity", Float),
        Column("mean_motion_intensity", Float),
        Column("met", Float),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("steps", Integer),
        Column("summary_id", String),
        Column("record_id", String),
    ],
    "bump_garmin_stress_details": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("event_date", Date),
        Column("duration_in_seconds", Integer),
        Column("start_time_in_seconds", Integer),
        Column("start_time_offset_in_seconds", Integer),
        Column("summary_id", String),
        Column("time_offset_body_battery_values", JSON, nullable=True),
        Column("time_offset_stress_level_values", JSON),
        Column("record_id", String)
    ],
    "bump_garmin_sleep": [
        Column("id", Integer),
        Column("user_id", Integer),
        Column("identity_id", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("retrieved_at", DateTime),
        Column("subsource", String),
        Column("event_date", Date),
        Column("awake_duration_in_seconds", Integer),
        Column("deep_sleep_duration_in_seconds", Integer),
        Column("duration_in_seconds", Integer),
        Column("light_sleep_duration_in_seconds", Integer),
        Column("rem_sleep_in_seconds", Integer),
        Column("sleep_levels_map", JSON),  # nested arrays of stage intervals
        Column("start_time_in_seconds", Integer),  # epoch seconds
        # timezone offset seconds
        Column("start_time_offset_in_seconds", Integer),
        Column("summary_id", String),
        # { offset_seconds: breaths_per_minute }
        Column("time_offset_sleep_respiration", JSON),
        Column("time_offset_sleep_spo2", JSON),  # { offset_seconds: spo2_pct }
        Column("unmeasurable_sleep_in_seconds", Integer),
        Column("validation", String),
        Column("record_id", String)
    ],
    "bump_ace": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("ace_1", Boolean),
        Column("ace_2", Boolean),
        Column("ace_3", Boolean),
        Column("ace_4", Boolean),
        Column("ace_5", Boolean),
        Column("ace_6", Boolean),
        Column("ace_7", Boolean),
        Column("ace_8", Boolean),
        Column("ace_9", Boolean),
        Column("ace_10", Boolean),
        Column("ace_complete", Integer),
    ],
    "bump_adverse_event_report": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("ae_report", Boolean),
        Column("ae_describe", Text, nullable=True),  # many missings
        Column("ae_notes", Text, nullable=True),     # many missing
        Column("adverse_event_report_complete", Integer),
    ],
    "bump_birthing_data": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("birth_number", Integer),  # Number of baby(ies) delivered
        Column("birth_date", Date),
        Column("birth_scheduled", Integer),  # 1, 2, or 3
        Column("birth_scheduled_unsure", Text, nullable=True),
        Column("birth_gestage", String, nullable=True),
        Column("gest_weeks", Integer, nullable=True),  # additional
        Column("gest_days", Integer, nullable=True),  # additional
        Column("gest_total_days", Integer, nullable=True),  # additional
        Column("gest_clean_text", String, nullable=True),  # additional
        # {1-Cesarean section; 2- Vaginal} or null
        Column("birth_mode", Integer, nullable=True),
        # Were you put in the hospital for induction of labor (e.g., doctor induced labor before body did naturally) - Yes/No
        Column("induction_yn", Boolean, nullable=True),
        # If yes to 4a: What was the reason for your hospital induced labor?
        Column("induction_reason", Text, nullable=True),
        Column("vacuumforceps", Boolean, nullable=True),
        Column("vaginalaftercesarean", Boolean, nullable=True),
        Column("birth_c_type", Integer, nullable=True),
        Column("birth_incision", Integer, nullable=True),
        Column("birth_indication", Text, nullable=True),
        Column("birth_apgar1", String, nullable=True),
        Column("birth_apgar2", String, nullable=True),
        Column("birth_abnormal1", Boolean, nullable=True),
        Column("birth_describe1", Text, nullable=True),
        Column("conditionsknown", Boolean, nullable=True),
        Column("birth_abnormal2", Boolean, nullable=True),
        Column("birth_describe2", Text, nullable=True),
        Column("conditionsknown_2", Boolean, nullable=True),
        Column("birth_maternal___1", Boolean, nullable=True),
        Column("birth_maternal___2", Boolean, nullable=True),
        Column("birth_maternal___4", Boolean, nullable=True),
        Column("hypertension", Boolean, nullable=True),
        Column("hellpsyndrome", Boolean, nullable=True),
        Column("birth_urine", Boolean, nullable=True),
        Column("postparhem", Boolean, nullable=True),
        Column("hem_meds", Boolean, nullable=True),
        Column("birth_matmorbid", Boolean, nullable=True),
        Column("birth_describe", Text, nullable=True),
        Column("birth_stay", Integer, nullable=True),
        Column("birth_stay_baby", Boolean, nullable=True),
        Column("birth_stay_baby_yes", Integer, nullable=True),
        Column("birth_feeding", Integer, nullable=True),
        Column("birth_matleave", Boolean, nullable=True),
        Column("birth_mat_leave_length", Integer, nullable=True),
        Column("birthing_data_cohort_2_only_complete", Integer)
    ],
    "bump_cssrs": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("cssrs_baseline", Integer, nullable=True),  # 0 or 1
        # Suicidal Ideation (SI) 1–5 (a/b)
        Column("si_1a", Integer, nullable=True),
        Column("si_1b", Integer, nullable=True),
        Column("si_2a", Integer, nullable=True),
        Column("si_2b", Integer, nullable=True),
        Column("si_3a", Integer, nullable=True),
        Column("si_3b", Integer, nullable=True),
        Column("si_4a", Integer, nullable=True),
        Column("si_4b", Integer, nullable=True),
        Column("si_5a", Integer, nullable=True),
        Column("si_5b", Integer, nullable=True),
        # Intensity Ideation 6–10 (a/b)
        Column("cssrs_6a", Integer, nullable=True),
        Column("cssrs_6b", Integer, nullable=True),
        Column("cssrs_7a", Integer, nullable=True),
        Column("cssrs_7b", Integer, nullable=True),
        Column("cssrs_8a", Integer, nullable=True),
        Column("cssrs_8b", Integer, nullable=True),
        Column("cssrs_9a", Integer, nullable=True),
        Column("cssrs_9b", Integer, nullable=True),
        Column("cssrs_10a", Integer, nullable=True),
        Column("cssrs_10b", Integer, nullable=True),
        # Suicidal Behavior (SB) block 1
        Column("suicide_1a", Integer, nullable=True),
        Column("suicide_1b", Integer, nullable=True),
        Column("sb_1c", Integer, nullable=True),
        Column("sb_1d", Integer, nullable=True),
        Column("suicide_1e", Integer, nullable=True),
        Column("suicide_1f", Integer, nullable=True),
        # SB block 2
        Column("suicide_2a", Integer, nullable=True),
        Column("suicide_2b", Integer, nullable=True),
        Column("sb_2c", Integer, nullable=True),
        Column("sb_2d", Integer, nullable=True),
        # SB block 3
        Column("suicide_3a", Integer, nullable=True),
        Column("suicide_3b", Integer, nullable=True),
        Column("sb_3c", Integer, nullable=True),
        # can contain free text like "a few, unsure"
        Column("sb_3d", Text, nullable=True),
        # SB block 4 + summary
        Column("suicide_4a", Integer, nullable=True),
        Column("suicide_4b", Integer, nullable=True),
        # can contain free text like "unable to quantify"
        Column("sb_5", Text, nullable=True),
        # Attempt Aborted/Interrupted (AA) items
        Column("aa_1", Integer, nullable=True),
        Column("aa_2a", Text, nullable=True),
        Column("aa_2b", Integer, nullable=True),
        Column("aa_3a", Text, nullable=True),
        Column("aa_3b", Integer, nullable=True),
        Column("aa_4a", Text, nullable=True),
        Column("aa_4b", Integer, nullable=True),
        Column("aa_5a", Integer, nullable=True),
        Column("aa_5b", Integer, nullable=True),
        Column("aa_5c", Integer, nullable=True),
        Column("cssrs_complete", Integer)
    ],
    "bump_phq9": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("phq9_1", Integer, nullable=True),
        Column("phq9_2", Integer, nullable=True),
        Column("phq9_3", Integer, nullable=True),
        Column("phq9_4", Integer, nullable=True),
        Column("phq9_5", Integer, nullable=True),
        Column("phq9_6", Integer, nullable=True),
        Column("phq9_7", Integer, nullable=True),
        Column("phq9_8", Integer, nullable=True),
        Column("phq9_9", Integer, nullable=True),
        Column("phq9_10", Integer, nullable=True),
        Column("phq9_complete", Integer)
    ],
    "bump_gad7": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("gad_1", Integer, nullable=True),
        Column("gad_2", Integer, nullable=True),
        Column("gad_3", Integer, nullable=True),
        Column("gad_4", Integer, nullable=True),
        Column("gad_5", Integer, nullable=True),
        Column("gad_6", Integer, nullable=True),
        Column("gad_7", Integer, nullable=True),
        Column("gad_8", Integer, nullable=True),
        Column("generalized_anxiety_disorder_scale_gad7_complete",
               Integer, nullable=True),
        Column("gad7_complete", Integer, nullable=True)
    ],
    "bump_epds": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("epds_1co2", Integer, nullable=True),
        Column("epds_2co2", Integer, nullable=True),
        Column("epds_3co2", Integer, nullable=True),
        Column("epds_4co2", Integer, nullable=True),
        Column("epds_5co2", Integer, nullable=True),
        Column("epds_6co2", Integer, nullable=True),
        Column("epds_7co2", Integer, nullable=True),
        Column("epds_8co2", Integer, nullable=True),
        Column("epds_9co2", Integer, nullable=True),
        Column("epds_10co2", Integer, nullable=True),
        Column("epds_cohort_2_only_2_complete", Integer, nullable=True),
        Column("epds_complete", Integer, nullable=True),
    ],
    "bump_pitting_edema": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("edema_yes", Integer, nullable=True),
        Column("edema_where___1", Integer, nullable=True),
        Column("edema_where___2", Integer, nullable=True),
        Column("edema_where___3", Integer, nullable=True),
        Column("edema_other", String, nullable=True),
        Column("pitting_edema", Integer, nullable=True),
        Column("pitting_edema_cohort_2_only_complete", Integer, nullable=True),
        Column("pitting_edema_complete", Integer, nullable=True),
    ],
    "bump_perinatal_ptsd": [
        Column("user_id", Integer),
        Column("record_id", String),
        Column("redcap_event_name", String),
        Column("date", Date),
        Column("ptsd1", Integer, nullable=True),
        Column("ptsd2", Integer, nullable=True),
        Column("ptsd3", Integer, nullable=True),
        Column("ptsd4", Integer, nullable=True),
        Column("ptsd5", Integer, nullable=True),
        Column("ptsd6", Integer, nullable=True),
        Column("ptsd7", Integer, nullable=True),
        Column("ptsd8", Integer, nullable=True),
        Column("ptsd9", Integer, nullable=True),
        Column("ptsd10", Integer, nullable=True),
        Column("ptsd11", Integer, nullable=True),
        Column("ptsd12", Integer, nullable=True),
        Column("ptsd13", Integer, nullable=True),
        Column("ptsd14", Integer, nullable=True),
        Column("perinatal_ptsd_complete", Integer, nullable=True),
    ]
}

# app_activities
APP_ACTIVITY_DIR = "bump/app_activities"
COMPLIANCE_GZ = f"{APP_ACTIVITY_DIR}/compliance.csv.gz"
QUICK_ACTIVITIES_GZ = f"{APP_ACTIVITY_DIR}/quick_activities.csv.gz"
SURVERYS_GZ = f"{APP_ACTIVITY_DIR}/surveys.csv.gz"
VIDEO_DIARY_GZ = f"{APP_ACTIVITY_DIR}/video_diary.csv.gz"
# gait_task.csv.gz and walk_test.csv.gz are not loaded

# bodyport
BODYPORT_DIR = "bump/bodyport"
BODYPORT_GZ = f"{BODYPORT_DIR}/bodyport.csv.gz"

# camcog
CAMCOG_DIR = "bump/camcog"
EBT_GZ = f"{CAMCOG_DIR}/EBT.csv.gz"
NBX_GZ = f"{CAMCOG_DIR}/NBX.csv.gz"
PVT_GZ = f"{CAMCOG_DIR}/PVT.csv.gz"

# oura
OURA_DIR = "bump/oura"
OURA_ACTIVITY_GZ = f"{OURA_DIR}/oura_activity.csv.gz"
OURA_READINESS_GZ = f"{OURA_DIR}/oura_readiness.csv.gz"
OURA_SLEEP_GZ = f"{OURA_DIR}/oura_sleep.csv.gz"

# garmin
GARMIN_DIR = "bump/garmin"
GARMIN_ACTIVITIES_GZ = f"{GARMIN_DIR}/garmin_activities.csv.gz"
GARMIN_ACTIVITIES_DETAILS_GZ = f"{GARMIN_DIR}/garmin_activity_details.csv.gz"
GARMIN_PULSE_OX_GZ = f"{GARMIN_DIR}/garmin_pulse_ox.csv.gz"
GARMIN_USER_METRICS_GZ = f"{GARMIN_DIR}/garmin_user_metrics.csv.gz"
GARMIN_DAILIES_GZ = f"{GARMIN_DIR}/garmin_dailies.csv.gz"
GARMIN_EPOCHS_GZ = f"{GARMIN_DIR}/garmin_epochs.csv.gz"
GARMIN_RESPIRATION_GZ = f"{GARMIN_DIR}/garmin_respiration.csv.gz"
GARMIN_STRESS_DETAILS_GZ = f"{GARMIN_DIR}/garmin_stress_details.csv.gz"
GARMIN_SLEEP_GZ = f"{GARMIN_DIR}/garmin_sleep.csv.gz"

# redcap
REDCAP_DIR = "bump/redcap"
ACE_GZ = f"{REDCAP_DIR}/ace.csv.gz"
ADVERSE_EVENT_REPORT_GZ = f"{REDCAP_DIR}/adverse_event_report.csv.gz"
BIRTHING_DATA_GZ = f"{REDCAP_DIR}/birthing_data.csv.gz"
CSSRS_GZ = f"{REDCAP_DIR}/cssrs.csv.gz"
PHQ9_GZ = f"{REDCAP_DIR}/phq9.csv.gz"
GAD7_GZ = f"{REDCAP_DIR}/gad7.csv.gz"
EPDS_GZ = f"{REDCAP_DIR}/epds.csv.gz"
PITTING_EDEMA_GZ = f"{REDCAP_DIR}/pitting_edema.csv.gz"
PERINATAL_PTSD_GZ = f"{REDCAP_DIR}/perinatal_ptsd.csv.gz"

# Data table and file path mappings for BUMP dataset
DATA_FILES = {
    "bump_compliance": COMPLIANCE_GZ,
    "bump_quick_activities": QUICK_ACTIVITIES_GZ,
    "bump_surveys": SURVERYS_GZ,
    "bump_video_diary": VIDEO_DIARY_GZ,
    "bump_bodyport": BODYPORT_GZ,
    "bump_ebt": EBT_GZ,
    "bump_nbx": NBX_GZ,
    "bump_pvt": PVT_GZ,
    "bump_oura_activity": OURA_ACTIVITY_GZ,
    "bump_oura_readiness": OURA_READINESS_GZ,
    "bump_oura_sleep": OURA_SLEEP_GZ,
    "bump_garmin_activities": GARMIN_ACTIVITIES_GZ,
    "bump_garmin_activity_details": GARMIN_ACTIVITIES_DETAILS_GZ,
    "bump_garmin_pulse_ox": GARMIN_PULSE_OX_GZ,
    "bump_garmin_user_metrics": GARMIN_USER_METRICS_GZ,
    "bump_garmin_dailies": GARMIN_DAILIES_GZ,
    "bump_garmin_respiration": GARMIN_RESPIRATION_GZ,
    "bump_garmin_epochs": GARMIN_EPOCHS_GZ,
    "bump_garmin_stress_details": GARMIN_STRESS_DETAILS_GZ,
    "bump_garmin_sleep": GARMIN_SLEEP_GZ,
    "bump_ace": ACE_GZ,
    "bump_adverse_event_report": ADVERSE_EVENT_REPORT_GZ,
    "bump_birthing_data": BIRTHING_DATA_GZ,
    "bump_cssrs": CSSRS_GZ,
    "bump_phq9": PHQ9_GZ,
    "bump_gad7": GAD7_GZ,
    "bump_epds": EPDS_GZ,
    "bump_pitting_edema": PITTING_EDEMA_GZ,
    "bump_perinatal_ptsd": PERINATAL_PTSD_GZ
}


def create_tables(engine):
    """Creates all tables in the database dynamically from TABLE_SCHEMAS."""
    for table_name, columns in TABLE_SCHEMAS.items():
        Table(table_name, metadata, *columns)
    metadata.create_all(engine)
    print("All tables created successfully.")


def load_data_to_db_from_s3(engine, table_name, s3_key, bucket=bucket_name):
    try:
        df = read_csv_from_s3(bucket, s3_key)

        if table_name == "bump_birthing_data":
            df = clean_birth_gestage(df)

        df.to_sql(table_name, con=engine, if_exists="replace", index=False)
        print(f"Loaded '{table_name}' from s3://{bucket}/{s3_key}")
    except Exception as e:
        print(f"Error loading '{table_name}' from s3://{bucket}/{s3_key}: {e}")


def load_all_data_from_s3(engine, bucket=bucket_name):
    final_msg = "Done loading the data."
    print(f'\n\nLoading all of the data from S3, this might take a few minutes...\n\n'
          f'...play your favourite tune and wait until you see the final line "{final_msg}"\n\n')

    for table_name, s3_key in DATA_FILES.items():
        load_data_to_db_from_s3(engine, table_name, s3_key, bucket)

    print(f'\n\n{final_msg} Thanks for your patience!')

# Helper Functions


def clean_birth_gestage(df):
    """
    Clean and standardize the 'birth_gestage' column into numeric weeks and days.
    Handles multiple messy formats like:
    - '40 weeks, 4 days'
    - '40w3d'
    - '38, 5'
    - '38 weeks'
    - '39 weeks, 1 day'
    - '>38 weeks (38 weeks, 0 days at time of last check-in but exact delivery date unknown)'
    - '39 Weeks, 3 Days'

    Output columns:
      - 'gest_weeks' (int)
      - 'gest_days' (int)
      - 'gest_total_days' (int)
      - 'gest_clean_text' (standardized string, e.g. '38w4d')
    """

    def parse_gestage(text):
        if pd.isna(text):
            return np.nan, np.nan

        s = str(text).lower().strip()

        # Prefer the value inside parentheses if exists
        if '(' in s and ')' in s:
            inner = re.search(r'\(([^)]*)\)', s)
            if inner:
                s = inner.group(1)

        # Remove leading '>' or other symbols
        s = re.sub(r'[^\dwd, ]', '', s)

        # Match patterns like "38w3d" or "38 weeks, 4 days" or "38,5"
        weeks = None
        days = 0

        # Common patterns
        patterns = [
            # 38w3d or 38 weeks, 3 days
            r'(?P<w>\d+)\s*w(?:eeks?)?[, ]*\s*(?P<d>\d+)\s*d',
            r'(?P<w>\d+)[^\d]+(?P<d>\d+)',                       # 38,5
            r'(?P<w>\d+)\s*w(?:eeks?)?',                         # 38 weeks
            # just a number
            r'(?P<w>\d+)'
        ]

        for pat in patterns:
            m = re.search(pat, s)
            if m:
                weeks = int(m.group('w'))
                if 'd' in m.groupdict() and m.group('d'):
                    days = int(m.group('d'))
                break

        # Fallback if nothing matched
        if weeks is None:
            return np.nan, np.nan

        # Ensure valid range
        if days >= 7:
            weeks += days // 7
            days = days % 7

        return weeks, days

    # Apply parsing
    parsed = df["birth_gestage"].apply(lambda x: parse_gestage(x))
    df["gest_weeks"] = parsed.apply(lambda x: x[0])
    df["gest_days"] = parsed.apply(lambda x: x[1])
    df["gest_total_days"] = df["gest_weeks"] * 7 + df["gest_days"]
    df["gest_clean_text"] = df.apply(
        lambda r: f"{int(r.gest_weeks)}w{int(r.gest_days)}d" if pd.notna(r.gest_weeks) else np.nan, axis=1
    )

    return df
