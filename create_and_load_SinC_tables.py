from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Float, Boolean, DateTime, Text, JSON, Date
import pandas as pd
import boto3
import gzip
import io
import re

metadata = MetaData()

s3 = boto3.client("s3")
bucket_name = "4youandme-sinc"

def read_csv_from_s3(bucket, key) -> pd.DataFrame:
    """Read a CSV or CSV.GZ file directly from S3 into a DataFrame."""
    resp = s3.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read()

    if key.endswith(".gz"):
        # Handle gzip-compressed CSV
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            return pd.read_csv(gz)
    else:
        # Plain CSV
        return pd.read_csv(io.BytesIO(body))


TABLE_SCHEMAS = {
    "sinc_user_site_mapping": [
        Column("user_id", String, primary_key=True),
        Column("site", String)
    ],
    "sinc_oura_activity": [
        Column("user_id", String, primary_key=True),
        Column("average_met", Float),
        Column("cal_active", Float),
        Column("cal_total", Float),
        Column("class_5min", String),
        Column("daily_movement", Float),
        Column("day_end", Date),
        Column("day_start", Date),
        Column("high", Float),
        Column("inactive", Float),
        Column("inactivity_alerts", Float),
        Column("low", Float),
        Column("medium", Float),
        Column("met_1min", JSON),
        Column("met_min_high", Float),
        Column("met_min_inactive", Float),
        Column("met_min_low", Float),
        Column("met_min_medium", Float),
        Column("non_wear", Float),
        Column("rest", Float),
        Column("score", Float),
        Column("score_meet_daily_targets", Float),
        Column("score_move_every_hour", Float),
        Column("score_recovery_time", Float),
        Column("score_stay_active", Float),
        Column("score_training_frequency", Float),
        Column("score_training_volume", Float),
        Column("steps", Float),
        Column("summary_date", Date),
        Column("total", Float),
        Column("rest_mode_state", Float),
        Column("event_date", Date),
    ],
    "sinc_oura_readiness": [
        Column("user_id", String, primary_key=True),
        Column("period_id", Float),
        Column("score", Float),
        Column("score_activity_balance", Float),
        Column("score_previous_day", Float),
        Column("score_previous_night", Float),
        Column("score_recovery_index", Float),
        Column("score_resting_hr", Float),
        Column("score_sleep_balance", Float),
        Column("score_temperature", Float),
        Column("summary_date", Date),
        Column("score_hrv_balance", Float),
        Column("rest_mode_state", Float),
        Column("event_date", Date),
    ],
    "sinc_oura_sleep": [
        Column("user_id", String, primary_key=True),
        Column("awake", Float),
        Column("bedtime_end", DateTime),
        Column("bedtime_end_delta", Float),
        Column("bedtime_start", DateTime),
        Column("bedtime_start_delta", Float),
        Column("breath_average", Float),
        Column("deep", Float),
        Column("duration", Float),
        Column("efficiency", Float),
        Column("hr_5min", JSON),
        Column("hr_average", Float),
        Column("hr_lowest", Float),
        Column("hypnogram_5min", String),
        Column("is_longest", Float),
        Column("light", Float),
        Column("midpoint_at_delta", Float),
        Column("midpoint_time", Float),
        Column("onset_latency", Float),
        Column("period_id", Float),
        Column("rem", Float),
        Column("restless", Float),
        Column("rmssd", Float),
        Column("rmssd_5min", JSON),
        Column("score", Float),
        Column("score_alignment", Float),
        Column("score_deep", Float),
        Column("score_disturbances", Float),
        Column("score_efficiency", Float),
        Column("score_latency", Float),
        Column("score_rem", Float),
        Column("score_total", Float),
        Column("summary_date", Date),
        Column("temperature_delta", Float),
        Column("temperature_deviation", Float),
        Column("temperature_trend_deviation", Float, nullable=True),
        Column("total", Float),
        Column("timestamp", DateTime, nullable=True),
        Column("type", String, nullable=True),
        Column("average_breath_variation", Float, nullable=True),
        Column("got_up_count", Float, nullable=True),
        Column("wake_up_count", Float, nullable=True),
        Column("lowest_heart_rate_time_offset", Float, nullable=True),
        Column("event_date", Date),
    ],
    "sinc_ema": [
        Column("id", String, primary_key=True),
        Column("user_id", String),
        Column("datatype", String),
        Column("datasource", String),
        Column("gathered_at", DateTime),
        Column("value", String, nullable=True),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("date", Date),
        Column("event_date", Date),
    ],
    "sinc_reaction_time": [
        Column("user_id", String, primary_key=True),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("attempt_timestamp", Float),
        Column("timestamp", Float),
        Column("attitude_y", Float),
        Column("attitude_w", Float),
        Column("attitude_z", Float),
        Column("attitude_x", Float),
        Column("rotation_rate_x", Float),
        Column("rotation_rate_y", Float),
        Column("rotation_rate_z", Float),
        Column("user_acceleration_x", Float),
        Column("user_acceleration_y", Float),
        Column("user_acceleration_z", Float),
        Column("gravity_x", Float),
        Column("gravity_y", Float),
        Column("gravity_z", Float),
        Column("magnetic_field_y", Float),
        Column("magnetic_field_z", Float),
        Column("magnetic_field_x", Float),
        Column("magnetic_field_accuracy", Integer),
        Column("key", String),
        Column("event_date", Date),
    ],
    "sinc_surveys": [
        Column("user_id", String, primary_key=True),
        Column("title", String),
        Column("name", String),
        Column("delivery", String),
        Column("first_study_day", Integer),
        Column("question", Text),
        Column("question_type", String),
        Column("answer", String),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("event_date", Date),
    ],
    "sinc_trail_making": [
        Column("user_id", String, primary_key=True),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("number_of_errors", Integer),
        Column("index", Integer),
        Column("incorrect", Boolean),
        Column("timestamp", Float),
        Column("key", String),
        Column("event_date", Date),
    ],
    "sinc_video_diaries_metadata": [
        Column("id", String, primary_key=True),
        Column("user_id", String),
        Column("filename", String, nullable=True),
        Column("bucket", String, nullable=True),
        Column("region", String, nullable=True),
        Column("started_at", DateTime),
        Column("completed_at", DateTime),
        Column("size", Integer),
        Column("created_at", DateTime),
        Column("updated_at", DateTime),
        Column("duration", Float),
        Column("event_date", Date),
    ],
    "sinc_bodyport": [
        Column("user_id", String, primary_key=True),
        Column("age", Integer, nullable=True),
        Column("balance_score", Float, nullable=True),
        Column("battery_state", String, nullable=True),
        Column("bcg_amplitude", Float, nullable=True),
        Column("creation_date", DateTime),
        Column("device_id", String, nullable=True),
        Column("duration", Float, nullable=True),
        Column("end_code", String, nullable=True),
        Column("height", Float, nullable=True),
        Column("hr", Float, nullable=True),
        Column("hrv", Float, nullable=True),
        Column("humidity", Float, nullable=True),
        Column("impedance1_mag", Float, nullable=True),
        Column("impedance1_phase", Float, nullable=True),
        Column("internal_user_id", String, nullable=True),
        Column("lvet", Float, nullable=True),
        Column("measurement_id", String, nullable=True),
        Column("pat", Float, nullable=True),
        Column("path_length", Float, nullable=True),
        Column("peak_count", Integer, nullable=True),
        Column("pep", Float, nullable=True),
        Column("pfc", Float, nullable=True),
        Column("ptt", Float, nullable=True),
        Column("quality_index", Float, nullable=True),
        Column("sampling_frequency", Float, nullable=True),
        Column("str", Float, nullable=True),
        Column("sway_area", Float, nullable=True),
        Column("temperature", Float, nullable=True),
        Column("weight", Float, nullable=True),
        Column("date", DateTime),
        Column("file_timestamp", DateTime),
        Column("key", String),
        Column("impedance_high_freq_mag", Float, nullable=True),
        Column("impedance_high_freq_phase", Float, nullable=True),
        Column("backend_sway_area_mm2", Float, nullable=True),
        Column("bmi_kg_m2", Float, nullable=True),
        Column("heart_rate", Float, nullable=True),
        Column("impedance_mag_1_ohms", Float, nullable=True),
        Column("impedance_phase_1_degs", Float, nullable=True),
        Column("impedance_ratio", Float, nullable=True),
        Column("muuid", String, nullable=True),
        Column("peripheral_fluid", Float, nullable=True),
        Column("run_time_sec", Float, nullable=True),
        Column("sway_velocity_mm_sec", Float, nullable=True),
        Column("total_body_water_percent", Float, nullable=True),
        Column("bodyport_id", String, nullable=True),
        Column("weight_kg", Float, nullable=True),
        Column("patient_id", String, nullable=True),
        Column("event_date", Date),
    ],
    "sinc_camcog_ebt": [
        Column("user_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("visit_id", String, nullable=False),
        Column("general_start_time", DateTime),
        Column("start_time", DateTime),
        Column("end_time", DateTime),
        Column("task", String, nullable=False),
        Column("EBTRTMDT", Float, nullable=True),
        Column("EBTRTMNT", Float, nullable=True),
        Column("EBTRTSDT", Float, nullable=True),
        Column("EBTRTMDH", Float, nullable=True),
        Column("EBTRTMNH", Float, nullable=True),
        Column("EBTRTSDH", Float, nullable=True),
        Column("EBTRTMDS", Float, nullable=True),
        Column("EBTRTMNS", Float, nullable=True),
        Column("EBTRTSDS", Float, nullable=True),
        Column("EBTRCH", Float, nullable=True),
        Column("EBTRCS", Float, nullable=True),
        Column("EBTBP", Float, nullable=True),
        Column("event_date", Date),
    ], 
    "sinc_camcog_not_run": [
        Column("user_id", String, primary_key=True),
        Column("subject_id", String, nullable=False),
        Column("visit_id", String, nullable=False),
        Column("general_start_time", DateTime),
        Column("task", String, nullable=False),
        Column("event_date", Date),
    ],
    "sinc_ibdoc": [
        Column("user_id", String, primary_key=True),
        Column("date", Date, nullable=False),
        Column("test_name", String, nullable=False),
        Column("batch", String, nullable=True),
        Column("patient_name", String, nullable=True),
        Column("patient_initials", String, nullable=True),
        Column("patient_id", String, nullable=False),
        Column("patient_dob", DateTime, nullable=True),
        Column("result_value", Float, nullable=True),  # Keeping as String since it includes units (e.g., "742.0µg/g")
        Column("result_classification", String, nullable=False)
    ],
    "sinc_pooply_events": [
        Column("user_id", String, primary_key=True),
        Column("pooply_id", Integer, nullable=False),
        Column("time", DateTime, nullable=False),
        Column("blood", Boolean, nullable=False),
        Column("fat", Boolean, nullable=False),
        Column("mucus", Boolean, nullable=False),
        Column("flatulence", Boolean, nullable=False),
        Column("shape", Float, nullable=False),
        Column("color", Float, nullable=False),
        Column("size", Float, nullable=False),
        Column("float", Float, nullable=False),
        Column("pain", Float, nullable=False),
        Column("urgency", Float, nullable=False),
        Column("key", String, nullable=False),
        Column("event_date", Date, nullable=False)
    ],
    "sinc_pooply_no_stool_days": [
        Column("user_id", String, primary_key=True),
        Column("pooply_id", Integer, nullable=False),
        Column("date", DateTime, nullable=False),
        Column("key", String, nullable=False),
        Column("event_date", Date, nullable=False)
    ],
    "sinc_chart_review_medical_history": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("study_visit_date", DateTime),
        Column("question", Text),
        Column("answer", Text)
    ],
    "sinc_chart_review_medications": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("question", Text),
        Column("answer", String)
    ],
    "sinc_chart_review_vitalslabs": [
        Column("user_id", String, primary_key=True),
        Column("date", DateTime),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("date_of_study_visit", Date),
        Column("question", Text),
        Column("answer", String)
    ],
    "sinc_check_in_survey": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("check_in_date", Date),
        Column("question", Text),
        Column("answer", Text)
    ],
    "sinc_extraintestinal_manifestations": [
        Column("user_id", String),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("date_of_visit", Date),
        Column("question", Text),
        Column("answer", Text)
    ],
    "sinc_medications_monthly": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("question", Text),
        Column("answer", Text)
    ],
    "sinc_phq_8": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("study_visit_date", Date),
        Column("question", Text),
        Column("answer", Text)
    ],
    "sinc_study_visit_1_crohns_specifics": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("date_of_study_visit", Date),
        Column("question", Text, primary_key=True),
        Column("answer", String)
    ],
    "sinc_study_visit_1_medical_history": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("site", String),
        Column("unnamed_10", String, nullable=True),  # Placeholder for unidentified column
        Column("study_personnels_initials", String),
        Column("study_visit_date", Date),
        Column("question", Text, primary_key=True),
        Column("answer", String)
    ],
    "sinc_study_visit_1_surgical_history": [
        Column("user_id", String, primary_key=True),
        Column("date", Date),
        Column("respondent_id", String),
        Column("collector_id", String),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("study_personnels_initials", String),
        Column("date_of_visit", Date),
        Column("question", Text, primary_key=True),
        Column("answer", Integer)
    ]
}

# metadata
METADATA_DIR = "metadata"
USER_SITE_MAPPING_GZ = f"{METADATA_DIR}/user_site_mapping.csv.gz"

# app_activities
APP_ACTIVITY_DIR = "app_activities"
EMA_GZ = f"{APP_ACTIVITY_DIR}/ema.csv.gz"
REACTION_TIME_GZ = f"{APP_ACTIVITY_DIR}/reaction_time.csv.gz"
SURVERYS_GZ = f"{APP_ACTIVITY_DIR}/surveys.csv.gz"
TRAIL_MAKING_GZ = f"{APP_ACTIVITY_DIR}/trail_making.csv.gz"
VIDEO_DIARIES_METADATA_GZ = f"{APP_ACTIVITY_DIR}/video_diaries_metadata.csv.gz"

# oura
OURA_DIR = "oura"
OURA_ACTIVITY_GZ = f"{OURA_DIR}/oura_activity.csv.gz"
OURA_READINESS_GZ = f"{OURA_DIR}/oura_readiness.csv.gz"
OURA_SLEEP_GZ = f"{OURA_DIR}/oura_sleep.csv.gz"

# bodyport
BODYPORT_DIR = "bodyport"
BODYPORT_GZ = f"{BODYPORT_DIR}/bodyport.csv.gz"

# camcog
CAMCOG_DIR = "camcog"
CAMCOG_EBT_GZ = f"{CAMCOG_DIR}/camcog_ebt.csv.gz"
CAMCOG_NOT_RUN_GZ = f"{CAMCOG_DIR}/camcog_not_run.csv.gz"

# ibdoc
IBDOC_DIR = "ibdoc"
IBDOC_GZ = f"{IBDOC_DIR}/ibdoc.csv.gz"

# pooply
POOPLY_DIR = "pooply"
POOPLY_EVENTS_GZ = f"{POOPLY_DIR}/pooply_events.csv.gz"
POOPLY_NO_STOOL_DAYS_GZ = f"{POOPLY_DIR}/pooply_no_stool_days.csv.gz"

# survey_monkey
SURVERY_MONKEY = "survey_monkey"
CHART_REVIEW_MEDICAL_HISTORY_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/chart_review_medical_history_possible_answers.csv.gz"
CHART_REVIEW_MEDICAL_HISTORY_GZ = f"{SURVERY_MONKEY}/chart_review_medical_history.csv.gz"
CHART_REVIEW_MEDICATIONS_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/chart_review_medications_possible_answers.csv.gz"
CHART_REVIEW_MEDICATIONS_GZ = f"{SURVERY_MONKEY}/chart_review_medications.csv.gz"
CHART_REVIEW_VITALSLABS_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/chart_review_vitalslabs_possible_answers.csv.gz"
CHART_REVIEW_VITALSLABS_GZ = f"{SURVERY_MONKEY}/chart_review_vitalslabs.csv.gz"
CHECK_IN_SURVERY_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/check_in_survey_possible_answers.csv.gz"
CHECK_IN_SURVERY_GZ = f"{SURVERY_MONKEY}/check_in_survey.csv.gz"
EXTRAINTESTINAL_MANIFESTATIONS_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/extraintestinal_manifestations_possible_answers.csv.gz"
EXTRAINTESTINAL_MANIFESTATIONS_GZ = f"{SURVERY_MONKEY}/extraintestinal_manifestations.csv.gz"
MEDICATIONS_MONTHLY_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/medications_monthly_possible_answers.csv.gz"
MEDICATIONS_MONTHLY_GZ = f"{SURVERY_MONKEY}/medications_monthly.csv.gz"
PHQ_8_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/phq_8_possible_answers.csv.gz"
PHQ_8_GZ = f"{SURVERY_MONKEY}/phq_8.csv.gz"
STUDY_VISIT_1_CROHNS_SPECIFICS_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/study_visit_1_crohns_specifics_possible_answers.csv.gz"
STUDY_VISIT_1_CROHNS_SPECIFICS_GZ = f"{SURVERY_MONKEY}/study_visit_1_crohns_specifics.csv.gz"
STUDY_VISIT_1_MEDICAL_HISTORY_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/study_visit_1_medical_history_possible_answers.csv.gz"
STUDY_VISIT_1_MEDICAL_HISTORY_GZ = f"{SURVERY_MONKEY}/study_visit_1_medical_history.csv.gz"
STUDY_VISIT_1_SURGICAL_HISTORY_POSSIBLE_ANSWERS_GZ = f"{SURVERY_MONKEY}/study_visit_1_surgical_history_possible_answers.csv.gz"
STUDY_VISIT_1_SURGICAL_HISTORY_GZ = f"{SURVERY_MONKEY}/study_visit_1_surgical_history.csv.gz"


# Data table and file path mappings for SinC dataset
DATA_FILES = {
    "sinc_user_site_mapping": USER_SITE_MAPPING_GZ,

    "sinc_ema": EMA_GZ,
    "sinc_reaction_time": REACTION_TIME_GZ,
    "sinc_surveys": SURVERYS_GZ,
    "sinc_trail_making": TRAIL_MAKING_GZ,
    "sinc_video_diaries_metadata": VIDEO_DIARIES_METADATA_GZ,
    
    "sinc_oura_activity": OURA_ACTIVITY_GZ,
    "sinc_oura_readiness": OURA_READINESS_GZ,
    "sinc_oura_sleep": OURA_SLEEP_GZ,
    
    "sinc_bodyport": BODYPORT_GZ,
    
    "sinc_camcog_ebt": CAMCOG_EBT_GZ,
    "sinc_camcog_not_run": CAMCOG_NOT_RUN_GZ,
    
    "sinc_ibdoc": IBDOC_GZ,
    
    "sinc_pooply_events":POOPLY_EVENTS_GZ,
    "sinc_pooply_no_stool_days":POOPLY_NO_STOOL_DAYS_GZ,
    
    # "chart_review_medical_history_possible_answers": CHART_REVIEW_MEDICAL_HISTORY_POSSIBLE_ANSWERS_GZ,
    "sinc_chart_review_medical_history": CHART_REVIEW_MEDICAL_HISTORY_GZ,
    # "chart_review_medications_possible_answers": CHART_REVIEW_MEDICATIONS_POSSIBLE_ANSWERS_GZ,
    "sinc_chart_review_medications": CHART_REVIEW_MEDICATIONS_GZ,
    # "chart_review_vitalslabs_possible_answers":CHART_REVIEW_VITALSLABS_POSSIBLE_ANSWERS_GZ,
    "sinc_chart_review_vitalslabs":CHART_REVIEW_VITALSLABS_GZ,
    # "check_in_survey_possible_answers": CHECK_IN_SURVERY_POSSIBLE_ANSWERS_GZ,
    "sinc_check_in_survey": CHECK_IN_SURVERY_GZ,
    # "extraintestinal_manifestations_possible_answers": EXTRAINTESTINAL_MANIFESTATIONS_POSSIBLE_ANSWERS_GZ,
    "sinc_extraintestinal_manifestations": EXTRAINTESTINAL_MANIFESTATIONS_GZ,
    # "medications_monthly_possible_answers": MEDICATIONS_MONTHLY_POSSIBLE_ANSWERS_GZ,
    "sinc_medications_monthly": MEDICATIONS_MONTHLY_GZ, 
    # "phq_8_possible_answers": PHQ_8_POSSIBLE_ANSWERS_GZ,
    "sinc_phq_8": PHQ_8_GZ,
    # "study_visit_1_crohns_specifics_possible_answers": STUDY_VISIT_1_CROHNS_SPECIFICS_POSSIBLE_ANSWERS_GZ,
    "sinc_study_visit_1_crohns_specifics": STUDY_VISIT_1_CROHNS_SPECIFICS_GZ, 
    # "study_visit_1_medical_history_possible_answers": STUDY_VISIT_1_MEDICAL_HISTORY_POSSIBLE_ANSWERS_GZ,
    "sinc_study_visit_1_medical_history": STUDY_VISIT_1_MEDICAL_HISTORY_GZ,
    # "study_visit_1_surgical_history_possible_answers": STUDY_VISIT_1_SURGICAL_HISTORY_POSSIBLE_ANSWERS_GZ,
    "sinc_study_visit_1_surgical_history": STUDY_VISIT_1_SURGICAL_HISTORY_GZ
}

def create_tables(engine):
    """Creates all tables in the database dynamically from TABLE_SCHEMAS."""
    for table_name, columns in TABLE_SCHEMAS.items():
        Table(table_name, metadata, *columns)
    metadata.create_all(engine)
    print("All tables created successfully.")


def load_data_to_db_from_s3(engine, table_name, s3_key, bucket = bucket_name):
    try:
        df = read_csv_from_s3(bucket, s3_key)

        if table_name == "sinc_ibdoc" and "result_value" in df.columns:
            df["result_value"] = df["result_value"].apply(clean_result_value)

        df.to_sql(table_name, con=engine, if_exists="replace", index=False)
        print(f"Loaded '{table_name}' from s3://{bucket}/{s3_key}")
    except Exception as e:
        print(f"Error loading '{table_name}' from s3://{bucket}/{s3_key}: {e}")


def load_all_data_from_s3(engine, bucket = bucket_name):
    final_msg = "Done loading the data."
    print(f'\n\nLoading all of the data from S3, this might take a few minutes...\n\n'
          f'...play your favourite tune and wait until you see the final line "{final_msg}"\n\n')

    for table_name, s3_key in DATA_FILES.items():
        load_data_to_db_from_s3(engine, table_name, s3_key, bucket)

    print(f'\n\n{final_msg} Thanks for your patience!')


def clean_result_value(value):
    """
    Converts result_value strings like '<30.0µg/g' to 29.9 (as float),
    and strips units like 'µg/g'. Returns None if parsing fails.
    """
    if pd.isnull(value):
        return None

    match = re.search(r"([<]?)\s*([0-9.]+)", value)
    if not match:
        return None

    operator, number = match.groups()
    number = float(number)

    # Subtract 0.1 if the original string had a "<"
    return number - 0.1 if operator == '<' else number

