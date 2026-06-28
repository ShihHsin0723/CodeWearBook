import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session
from langchain.schema import Document


def _with_prefix(prefix, mapping):
    return {f"{prefix}{key}": value for key, value in mapping.items()}


def generate_schema_description(engine):
    """
    Generate a textual description of the current database schema.

    SQLite uses PRAGMA

    whereas

    PostgreSQL, MSQL use
    """
    if engine.name == 'sqlite':
        # return _generate_schema_via_pragma(engine)
        return _generate_schema_docs_with_keywords(engine)
    else:
        return _generate_schema_via_information_schema(engine)


def _generate_schema_docs_with_keywords(engine):
    """
    Generates one Document per table in the schema with formatted schema text and keywords in metadata.
    Returns a list of Document objects.
    """
    bump_description_map = {
        "compliance": "Task completion and data-presence tracking for app-based activities. Records whether data was present for a given participant, task type, stream, and event date.",
        "quick_activities": "App-based ecological momentary assessments using Self-Assessment Manikin scales. Captures daily mood, energy, stress, and cognition responses, typically as stream/type plus selected rating.",
        "surveys": "Long-form BUMP app survey responses across many survey instruments. Stores one row per question/answer for baseline, daily, PROMIS, COVID, symptom, demographics, medical-history, and pregnancy-related surveys.",
        "video_diary": "Video diary task records from the BUMP app. Tracks scheduled diary entries with titles, time windows, event dates, and a JSON result payload when present.",
        "bodyport": "BUMP Bodyport cardiac scale measurements collected during study participation. Includes weight, BMI, heart rate, fluid-related impedance markers, balance/sway, and related physiologic metrics.",
        "ebt": "BUMP Emotion Bias Task results from the CamCog cognition kit. Captures reaction-time, response-count, and bias metrics for recognizing happy versus sad stimuli in a social-cognition task.",
        "nbx": "BUMP N-Back task results from the CamCog cognition kit. Measures working memory and attention using hits, misses, false alarms, and sensitivity/performance scores.",
        "pvt": "BUMP Psychomotor Vigilance Task results from the CamCog cognition kit. Stores vigilance and reaction-time metrics such as hits, false positives, timeouts, and latency statistics.",
        "oura_activity": "BUMP Oura daily activity summaries. Includes activity score and related contributors such as movement, target attainment, training frequency/volume, and recovery-time signals.",
        "oura_readiness": "BUMP Oura readiness/recovery summaries. Captures readiness score and contributors tied to sleep, activity balance, heart rate, HRV, recovery, and temperature.",
        "oura_sleep": "BUMP Oura sleep summaries. Stores sleep score and related contributors such as total sleep, REM, deep sleep, latency, disturbances, alignment, and physiology.",
        "garmin_activities": "BUMP Garmin recorded activity sessions. Stores workout-level summaries such as activity type/name, duration, distance, steps, calories, heart rate, speed, cadence, and elevation.",
        "garmin_activity_details": "BUMP detailed Garmin activity records tied to individual activity sessions. Extends activity summaries with lap JSON and more granular per-session metrics, including parent/child activity relationships.",
        "garmin_user_metrics": "BUMP Garmin user-level fitness metrics. Holds participant fitness indicators such as VO2 max and fitness age by event date.",
        "garmin_pulse_ox": "BUMP Garmin pulse oximetry summaries. Contains SpO2 measurements over time, including minute-offset oxygen saturation values and whether the reading was on-demand.",
        "garmin_dailies": "BUMP Garmin daily summary metrics. Includes derived daily physiology and activity fields such as average stress level, intensity-duration totals, and stress-duration breakdowns.",
        "garmin_respiration": "BUMP Garmin respiration summaries. Stores breaths-per-minute values over time as offset-based respiration mappings within a monitoring window.",
        "garmin_epochs": "BUMP Garmin 15-minute activity epochs. Stores short-interval summaries of active time, calories, MET values, and qualitative intensity.",
        "garmin_stress_details": "BUMP continuous Garmin stress/body battery time series. Stores time-offset stress scores and body battery values across the day.",
        "garmin_sleep": "BUMP Garmin nightly sleep summaries. Includes sleep-stage maps, unmeasurable sleep time, and time-aligned respiration and SpO2 values during sleep.",
        "ace": "BUMP Adverse Childhood Events questionnaire responses. Each ACE column records a yes/no exposure to one of the 10 standard childhood adversity items.",
        "adverse_event_report": "BUMP free-text adverse event reporting. Contains participant descriptions of adverse events reported since the last study contact, whether study-related or not.",
        "birthing_data": "BUMP postnatal delivery and birth outcome data. Includes gestational age, delivery mode, maternal complications, newborn indicators, hospital stay, and feeding details.",
        "cssrs": "BUMP Columbia-Suicide Severity Rating Scale responses. Captures suicidal ideation, ideation intensity, attempts, interrupted/aborted attempts, and related self-injury history.",
        "phq9": "BUMP PHQ-9 questionnaire responses. Stores item-level depression symptom data used to compute depression severity and screen for depressive syndromes.",
        "gad7": "BUMP GAD-7 questionnaire responses. Contains item-level anxiety symptom data used to score and classify generalized anxiety severity.",
        "epds": "BUMP Edinburgh Perinatal/Postnatal Depression Scale responses. Stores 10-item depression screening data used to assess depressive symptoms during pregnancy or postpartum.",
        "pitting_edema": "BUMP clinical pitting-edema assessment data. Records whether edema is present, where it occurs, and the severity of indentation-based swelling.",
        "perinatal_ptsd": "BUMP perinatal PTSD questionnaire responses. Contains one-time postpartum survey data measuring childbirth-related post-traumatic stress symptoms across 14 items.",
    }

    sinc_description_map = {
        "user_site_mapping": "SinC participant-to-site mapping metadata.",
        "ema": "SinC ecological momentary assessment records collected through the app.",
        "reaction_time": "SinC reaction-time task measurements collected from device motion and timing signals during a reaction task.",
        "surveys": "SinC app survey responses stored in long format, with one row per user, survey title, question, answer, and assessment date. Covers repeated and one-time instruments such as Demographics, Family Medical History, IBD Daily, Nightly Stress, IBD Control, Emotional Support, Fatigue, Global Ten, Healthcare Utilization, Life Events, Manitoba IBD Index, Pain Interference, Perceived Stress Scale, Personality Inventory, Sleep Related Impairment, Flu/COVID, and Adverse Childhood Events.",
        "trail_making": "SinC trail-making task results including timestamps, ordering, and errors during the cognitive assessment.",
        "video_diaries_metadata": "SinC video diary metadata records. Tracks uploaded diary files, timing, duration, and storage metadata.",
        "bodyport": "SinC Bodyport measurements including weight, BMI, HR, HRV, fluid, and balance-related signals.",
        "oura_activity": "SinC Oura daily activity summaries including steps, activity score, MET-derived movement, and related contributors.",
        "oura_readiness": "SinC Oura readiness summaries including readiness score and recovery-related contributors.",
        "oura_sleep": "SinC Oura sleep summaries including sleep score, duration, RMSSD, heart rate, and sleep-stage-related fields.",
        "camcog_ebt": "SinC CamCog Emotion Bias Task results for cognitive assessment visits.",
        "camcog_not_run": "SinC CamCog task records for visits where a task was not run.",
        "ibdoc": "SinC IBDoc fecal calprotectin measurements and classifications.",
        "pooply_events": "SinC Pooply stool event records including stool characteristics and symptoms.",
        "pooply_no_stool_days": "SinC Pooply records for days when no stool event was reported.",
        "chart_review_medical_history": "SinC chart review medical history responses abstracted from records.",
        "chart_review_medications": "SinC chart review medication responses abstracted from records.",
        "chart_review_vitalslabs": "SinC chart review vitals and lab responses abstracted from records.",
        "check_in_survey": "SinC check-in survey responses collected during study visits.",
        "extraintestinal_manifestations": "SinC extraintestinal manifestation survey responses describing IBD-related diagnoses reported by participants.",
        "medications_monthly": "SinC monthly medication survey responses.",
        "phq_8": "SinC PHQ-8 questionnaire responses used to assess depression symptoms.",
        "study_visit_1_crohns_specifics": "SinC study visit 1 Crohn's disease specific survey responses.",
        "study_visit_1_medical_history": "SinC study visit 1 medical history survey responses.",
        "study_visit_1_surgical_history": "SinC study visit 1 surgical history survey responses.",
    }

    description_map = {}
    description_map.update(_with_prefix("bump_", bump_description_map))
    description_map.update(_with_prefix("sinc_", sinc_description_map))
    description_map.update({
        "wearable_sleep_daily": "Cross-dataset harmonized sleep view across registered datasets. Normalizes daily sleep score, duration, heart rate, RMSSD, and efficiency.",
        "wearable_activity_daily": "Cross-dataset harmonized activity view across registered datasets. Normalizes daily activity score, steps, calories, and average MET.",
        "wearable_readiness_daily": "Cross-dataset harmonized readiness view across registered datasets. Normalizes daily readiness score and core recovery contributors.",
        "wearable_bodyport_daily": "Cross-dataset harmonized Bodyport view across registered datasets. Normalizes daily weight, BMI, heart rate, peripheral fluid, and sway metrics.",
        "wearable_ebt_sessions": "Cross-dataset harmonized Emotional Bias Task view across registered datasets. Normalizes session-level bias, reaction time, and response counts.",
        "wearable_ema_daily": "Cross-dataset harmonized EMA view across registered datasets. Normalizes daily mood, energy, stress, and cognition item scores.",
    })

    bump_keyword_map = {
        "bodyport": ["bodyport", "cardiac scale", "heart rate", "hrv", "impedance", "fluid", "sway", "balance", "weight", "bmi", "underweight", "obesity", "overwieght"],
        "oura_activity": ["oura", "activity", "steps", "calories", "movement", "activity score", "Metabolic Equivalents", "MET"],
        "oura_readiness": ["oura", "readiness", "recovery", "hrv", "temperature", "resting heart rate", "rhr", "sleep balance", "readiness score"],
        "oura_sleep": ["oura", "sleep", "bedtime", "sleep score", "hrv", "heart rate", "respiration", "hypnogram", "REM"],
        "ebt": ["emotion bias", "ebt", "social cognition", "happy", "sad", "camcog"],
        "nbx": ["n-back", "nbx", "2-back", "working memory", "attention", "camcog"],
        "pvt": ["psychomotor vigilance", "pvt", "reaction time", "vigilance", "camcog"],
        "quick_activities": ["app activities", "quick activities", "ema", "ecological momentary assessment", "sam", "self-assessment manikin", "mood", "energy", "stress", "cognition"],
        "ace": ["ace", "childhood", "adverse", "adverse events", "family", "parents", "trauma", "early life"],
        "adverse_event_report": ["adverse", "adverese events", "report"],
        "birthing_data": ["birth", "birthing", "trimester", "pml date", "postnatal", "gestational", "gestational age", "delivery", "induced labor", "induction", "vaginal", "vacuum", "forceps", "cesarean", "uterine", "incision", "APGAR", "baby", "pre-eclampsia", "preeclampsia", "eclampsia", "toxemia", "hypertension", "HELLP", "postpartum", "hemorrhage", "morbidity", "maternal", "hospital stay", "feeding", "maternity"],
        "cssrs": ["cssrs", "columbia-suicide severity rating scale", "c-ssrs", "suicide", "kill", "dead", "injured", "suicidal"],
        "phq9": ["phq9", "depression", "depressed", "feeling down", "Major depressive disorder", "mdd"],
        "gad7": ["gad7", "anxiety", "generalized anxiety disorder", "nervous", "anxious", "worrying", "restless", "irritable"],
        "epds": ["epds", "edinburgh postnatal depression scale", "edinburgh perinatal depression scale", "pregnancy mental health", "postpartum mental health", "perinatal depression screening", "maternal mental health", "mental health during pregnancy"],
        "pitting_edema": ["pitting edema", "edema", "pitting", "indentation", "peripheral fluid", "tissue swelling", "extremity", "fluid retention"],
        "perinatal_ptsd": ["perinatal ptsd", "ptsd", "trauma", "childbirth trauma", "birth-related trauma", "maternal ptsd", "post-traumatic stress disorder"],
        "surveys": ["survey", "surveys", "self-reported", "reported", "survey response", "demographics", "social determinants of health", "sdh", "social", "pregnancy-unique quantification of emesis & nausea", "puqe",
                    "age", "date of birth", "dob", "gender", "sex", "ethnicity", "race", "languages", "education", "smoking", "health insurance",
                    "physical activities", "household", "live with", "lose housing", "work situation", "work hours", "resources", "lack of transportation", "visiting friends", "talking to friends",
                    "nausea", "emesis", "vomit", "throw up", "retching", "dry heaves", "stomach",
                    "fas", "fatigue assessment scale", "fatigue score", "exhuasted", "tired", "concentrate",
                    "healthcare", "utilization", "healthcare ultilization", "admission", "emergency", "emergency room", "emergency department", "hospital", "facility", "care",
                    "covid", "19", "severity", "symptoms", "positive", "exposure",
                    "patient-reported outcomes measurement information system", "promis", "emotional", "support", "confide", "listen", "appreciated", "talk to", "bad day",
                    "pain", "interference", "physical pain", "interfere",
                    "sleep related impairment", "sri", "sleepy", "poor sleep", "alert", "trouble staying awake", "tired", "sleep",
                    "global 10", "overall health", "social health", "physical health", "mental health", "global physical health", "global mental health",
                    "tipi", "ten item personality inventory", "10 item", "big five", "personality", "trait", "dimensions", "extraversion", "agreeableness", "conscientiousness", "emotional stability", "openness to experiences",
                    "daily symptoms", "severe symptom", "complication", "stress", "stress measure", "feelings of stress", "in control", "abdominal cramps", "anxiety", "body aches", "swollen", "constipation", "engorged breasts", "sore nipples", "hemorrhoids", "night sweats", "sore perineum", "vaginal bleeding", "vaginal dryness",
                    "medical history", "biological family history", "diabetes", "hypertension", "heart disease", "autoimmune disorder", "kidney disease", "neurologic disorders", "mood disorder", "postpartum depression", "anxiety disorder", "sleep disorder", "psychotic spectrum disorder", "eating disorder", "hepatitis", "liver disease", "thyroid dysfunction", "pulmonary", "allergies", "cancer", "blood clotting disorder", "antiretroviral treatment", "obesity"],
        "garmin_dailies": ["dailies", "steps", "calories", "stress", "average stress", "heart rate", "intensity"],
        "garmin_epochs": ["epochs", "steps", "calories", "motion intensity", "met", "metabolic equivalent of task"],
        "garmin_pulse_ox": ["spo2", "oxygen saturation", "pulse ox", "blood oxygen"],
        "garmin_respiration": ["respiration", "breathing rate", "breaths per minute", "rr"],
        "garmin_sleep": ["sleep", "deep sleep", "rem", "light sleep", "awake"],
        "garmin_stress_details": ["stress", "body battery", "wellbeing"],
    }

    sinc_keyword_map = {
        "user_site_mapping": ["site", "study site", "participant site"],
        "ema": ["ema", "ecological momentary assessment", "mood", "energy", "stress", "cognition"],
        "reaction_time": ["reaction time", "motor", "timing", "device motion"],
        "surveys": [
            "survey", "surveys", "questionnaire", "questionnaires", "survey response", "self-reported", "reported",
            "abdominal", "abdominal pain", "pain", "stool", "soft stools", "bowel symptoms", "pro2", "check-in", "check in",
            "crohn", "ibd", "ibd daily", "ibd control", "disease control",
            "family medical history", "medical history", "ace", "adverse childhood events", "childhood", "childhood trauma",
            "demographics", "extended demographics", "demographic profile", "social determinants", "housing", "transportation", "employment", "education", "income", "insurance", "smoking",
            "fatigue", "fss",
            "flu", "flue", "covid", "flu vaccine",
            "gad", "gad7", "gad-7", "generalized anxiety disorder", "worry", "anxious",
            "global ten", "global 10", "promis global 10", "quality of life", "mental health", "physical health", "social health",
            "healthcare utilization", "healthcare utlization", "hospitalization", "hospital admission", "emergency room", "emergency department",
            "emotional support", "social support", "confide", "appreciated",
            "pain interference", "household chores",
            "perceived stress", "stress scale", "nightly stress", "stress",
            "sleep related impairment", "poor sleep", "sleep quality", "sleepy",
            "life events", "life event", "major life event",
            "manitoba ibd index",
            "personality inventory", "tipi", "big five",
            "engagement", "study experience", "video diary", "cognition tasks", "app feature",
            "pilot", "onboarding", "device sync", "study participation",
        ],
        "trail_making": ["trail making", "cognition", "errors", "task"],
        "video_diaries_metadata": ["video diary", "video", "upload", "duration", "filename"],
        "bodyport": ["bodyport", "bmi", "weight", "heart rate", "hrv", "fluid", "balance"],
        "oura_activity": ["oura", "activity", "steps", "movement", "activity score", "met"],
        "oura_readiness": ["oura", "readiness", "recovery", "hrv", "temperature", "readiness score"],
        "oura_sleep": ["oura", "sleep", "rmssd", "sleep score", "duration", "heart rate"],
        "camcog_ebt": ["camcog", "emotion bias", "ebt", "cognition"],
        "camcog_not_run": ["camcog", "not run", "task not run"],
        "ibdoc": ["ibdoc", "fecal", "calprotectin"],
        "pooply_events": ["pooply", "stool", "bowel", "pain", "urgency", "mucus", "blood"],
        "pooply_no_stool_days": ["pooply", "no stool", "bowel"],
        "chart_review_medical_history": ["chart review", "medical history"],
        "chart_review_medications": ["chart review", "medications"],
        "chart_review_vitalslabs": ["chart review", "vitals", "labs"],
        "check_in_survey": ["check-in", "check in survey"],
        "extraintestinal_manifestations": ["eims", "extraintestinal", "manifestations", "pyoderma", "gangrenosum", "erythema", "nodosum", "metastatic", "crohn", "uveitis", "iritis", "episcleritis", "scleritis", "primary", "sclerosing", "cholangitis", "arthritis", "ankylosing", "spondylitis", "sacroiliitis", "ibd", "mouth ulcers", "venous thrombosis", "arterial thrombosis", "kidney stones", "amyloidosis"],
        "medications_monthly": ["medications", "monthly medications"],
        "phq_8": ["phq8", "phq-8", "depression"],
        "study_visit_1_crohns_specifics": ["crohn", "study visit 1"],
        "study_visit_1_medical_history": ["medical history", "study visit 1"],
        "study_visit_1_surgical_history": ["surgical history", "study visit 1"],
    }

    keyword_map = {}
    keyword_map.update(_with_prefix("bump_", bump_keyword_map))
    keyword_map.update(_with_prefix("sinc_", sinc_keyword_map))
    keyword_map.update({
        "wearable_sleep_daily": ["cross-dataset", "cross dataset", "harmonized", "wearable", "oura", "sleep", "sleep score", "rmssd", "heart rate", "efficiency"],
        "wearable_activity_daily": ["cross-dataset", "cross dataset", "harmonized", "wearable", "oura", "activity", "activity score", "steps", "calories", "met"],
        "wearable_readiness_daily": ["cross-dataset", "cross dataset", "harmonized", "wearable", "oura", "readiness", "recovery", "readiness score", "hrv", "resting heart rate"],
        "wearable_bodyport_daily": ["cross-dataset", "cross dataset", "harmonized", "wearable", "bodyport", "weight", "bmi", "heart rate", "peripheral fluid", "sway", "balance"],
        "wearable_ebt_sessions": ["cross-dataset", "cross dataset", "harmonized", "wearable", "ebt", "emotion bias", "camcog", "happy", "sad", "reaction time"],
        "wearable_ema_daily": ["cross-dataset", "cross dataset", "harmonized", "wearable", "ema", "quick activities", "mood", "energy", "stress", "cognition", "brain capability"],
    })

    # Manifest metadata overlays defaults and adds new datasets without edits to
    # this module. Physical columns still come from SQLite introspection below.
    from dataset_registry import get_schema_metadata
    manifest_schema_metadata = get_schema_metadata()
    for table_name, metadata in manifest_schema_metadata.items():
        if metadata.get("description"):
            description_map[table_name] = str(metadata["description"])
        if metadata.get("keywords"):
            keyword_map[table_name] = list(metadata["keywords"])

    docs = []

    try:
        with Session(engine) as session:
            tables_stmt = text(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view');")
            tables_result = session.execute(tables_stmt)

            for table_name, object_type in tables_result.fetchall():
                schema_text = f"Table: {table_name}\n"
                schema_text += f"Object Type: {object_type}\n"
                description = description_map.get(
                    table_name,
                    f"Contains data related to {table_name.replace('_', ' ')}."
                )
                schema_text += f"Description: {description}\n"
                schema_text += "Columns:\n"

                table_metadata = manifest_schema_metadata.get(table_name, {})
                column_metadata = table_metadata.get("columns", {})
                cols_stmt = text(f"PRAGMA table_info({table_name})")
                cols_result = session.execute(cols_stmt)

                for col in cols_result.fetchall():
                    col_id, col_name, col_type, notnull, default_val, pk = col
                    schema_text += f" - {col_name} ({col_type})"
                    if pk:
                        schema_text += " [PRIMARY KEY]"
                    if notnull:
                        schema_text += " [NOT NULL]"
                    if default_val is not None:
                        schema_text += f" [DEFAULT: {default_val}]"
                    column_info = column_metadata.get(col_name, {})
                    if column_info.get("description"):
                        schema_text += f" — {column_info['description']}"
                    if column_info.get("unit"):
                        schema_text += f" [UNIT: {column_info['unit']}]"
                    if column_info.get("aliases"):
                        schema_text += f" [ALIASES: {', '.join(column_info['aliases'])}]"
                    if column_info.get("null_meaning"):
                        schema_text += f" [NULL: {column_info['null_meaning']}]"
                    schema_text += "\n"

                keywords = list(keyword_map.get(table_name, []))
                for column_name, column_info in column_metadata.items():
                    keywords.extend(column_info.get("keywords", []))
                    keywords.extend(column_info.get("aliases", []))

                doc = Document(
                    page_content=schema_text,
                    metadata={
                        "table": table_name,
                        "type": "schema",
                        "keywords": keywords,
                        "column_metadata": column_metadata,
                        "object_type": object_type,
                    }
                )
                docs.append(doc)

        return docs

    except Exception as e:
        print(f"Error generating schema docs: {e}")
        return []


def _generate_schema_via_pragma(engine):
    try:
        output = ""
        with Session(engine) as session:
            tables_stmt = text(
                "SELECT name FROM sqlite_master WHERE type='table';")
            tables_result = session.execute(tables_stmt)

            for (table_name,) in tables_result.fetchall():
                output += f"\n\nTable: {table_name}\n"

                # Get column details for each table
                cols_stmt = text(f"PRAGMA table_info({table_name})")
                cols_result = session.execute(cols_stmt)

                for col in cols_result.fetchall():
                    output += f" - Column: {col}"
        return output

    except Exception as e:
        return f"Error generating schema description: {e}"


def _generate_schema_via_information_schema(engine):
    try:
        with engine.connect() as connection:
            # Query to get tables and columns from the database
            query = """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position;
            """
            schema_df = pd.read_sql(query, connection)

        # Build the schema description
        schema_description = "Tables:\n"
        for table, group in schema_df.groupby("table_name"):
            schema_description += f"{table} ("
            schema_description += ", ".join(
                f"{row['column_name']} ({row['data_type']})" for _, row in group.iterrows())
            schema_description += ")\n"

        return schema_description.strip()
    except Exception as e:
        return f"Error generating schema description: {e}"
