from sqlalchemy import text


VIEW_DEFINITIONS = {
    "wearable_sleep_daily": """
        CREATE VIEW wearable_sleep_daily AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'bump_oura_sleep' AS source_table,
            CAST(score AS REAL) AS sleep_score,
            CAST(duration AS REAL) AS total_sleep_duration,
            CAST(hr_average AS REAL) AS hr_average,
            CAST(hr_lowest AS REAL) AS hr_lowest,
            CAST(rmssd AS REAL) AS rmssd,
            CAST(efficiency AS REAL) AS sleep_efficiency
        FROM bump_oura_sleep

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_oura_sleep' AS source_table,
            CAST(score AS REAL) AS sleep_score,
            CAST(duration AS REAL) AS total_sleep_duration,
            CAST(hr_average AS REAL) AS hr_average,
            CAST(hr_lowest AS REAL) AS hr_lowest,
            CAST(rmssd AS REAL) AS rmssd,
            CAST(efficiency AS REAL) AS sleep_efficiency
        FROM sinc_oura_sleep;
    """,
    "wearable_activity_daily": """
        CREATE VIEW wearable_activity_daily AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'bump_oura_activity' AS source_table,
            CAST(score AS REAL) AS activity_score,
            CAST(steps AS REAL) AS steps,
            CAST(cal_active AS REAL) AS active_calories,
            CAST(cal_total AS REAL) AS total_calories,
            CAST(average_met AS REAL) AS average_met
        FROM bump_oura_activity

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_oura_activity' AS source_table,
            CAST(score AS REAL) AS activity_score,
            CAST(steps AS REAL) AS steps,
            CAST(cal_active AS REAL) AS active_calories,
            CAST(cal_total AS REAL) AS total_calories,
            CAST(average_met AS REAL) AS average_met
        FROM sinc_oura_activity;
    """,
    "wearable_readiness_daily": """
        CREATE VIEW wearable_readiness_daily AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'bump_oura_readiness' AS source_table,
            CAST(score AS REAL) AS readiness_score,
            CAST(score_hrv_balance AS REAL) AS score_hrv_balance,
            CAST(score_recovery_index AS REAL) AS score_recovery_index,
            CAST(score_resting_hr AS REAL) AS score_resting_hr
        FROM bump_oura_readiness

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_oura_readiness' AS source_table,
            CAST(score AS REAL) AS readiness_score,
            CAST(score_hrv_balance AS REAL) AS score_hrv_balance,
            CAST(score_recovery_index AS REAL) AS score_recovery_index,
            CAST(score_resting_hr AS REAL) AS score_resting_hr
        FROM sinc_oura_readiness;
    """,
    "wearable_bodyport_daily": """
        CREATE VIEW wearable_bodyport_daily AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'bump_bodyport' AS source_table,
            CAST(weight_kg AS REAL) AS weight_kg,
            CAST(bmi_kg_m2 AS REAL) AS bmi_kg_m2,
            CAST(heart_rate AS REAL) AS heart_rate,
            CAST(peripheral_fluid AS REAL) AS peripheral_fluid,
            CAST(sway_velocity_mm_sec AS REAL) AS sway_velocity_mm_sec,
            CAST(backend_sway_area_mm2 AS REAL) AS backend_sway_area_mm2
        FROM bump_bodyport

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_bodyport' AS source_table,
            CAST(COALESCE(weight_kg, weight) AS REAL) AS weight_kg,
            CAST(bmi_kg_m2 AS REAL) AS bmi_kg_m2,
            CAST(COALESCE(heart_rate, hr) AS REAL) AS heart_rate,
            CAST(COALESCE(peripheral_fluid, pfc) AS REAL) AS peripheral_fluid,
            CAST(sway_velocity_mm_sec AS REAL) AS sway_velocity_mm_sec,
            CAST(backend_sway_area_mm2 AS REAL) AS backend_sway_area_mm2
        FROM sinc_bodyport;
    """,
    "wearable_ebt_sessions": """
        CREATE VIEW wearable_ebt_sessions AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(general_start_time) AS event_date,
            'bump_ebt' AS source_table,
            CAST(EBTBP AS REAL) AS ebt_bias_point,
            CAST(EBTRTMDT AS REAL) AS median_rt_total,
            CAST(EBTRTMNT AS REAL) AS mean_rt_total,
            CAST(EBTRCH AS REAL) AS happy_response_count,
            CAST(EBTRCS AS REAL) AS sad_response_count
        FROM bump_ebt

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_camcog_ebt' AS source_table,
            CAST(EBTBP AS REAL) AS ebt_bias_point,
            CAST(EBTRTMDT AS REAL) AS median_rt_total,
            CAST(EBTRTMNT AS REAL) AS mean_rt_total,
            CAST(EBTRCH AS REAL) AS happy_response_count,
            CAST(EBTRCS AS REAL) AS sad_response_count
        FROM sinc_camcog_ebt;
    """,
    "wearable_ema_daily": """
        CREATE VIEW wearable_ema_daily AS
        SELECT
            'BUMP' AS dataset,
            'BUMP:' || record_id AS participant_key,
            CAST(record_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'bump_quick_activities' AS source_table,
            CASE
                WHEN LOWER(stream) LIKE '%mood%' THEN 'mood'
                WHEN LOWER(stream) LIKE '%energy%' THEN 'energy'
                WHEN LOWER(stream) LIKE '%stress%' THEN 'stress'
                WHEN LOWER(stream) LIKE '%cognition%' THEN 'brain_capability'
                ELSE LOWER(stream)
            END AS measure,
            CASE LOWER(label)
                WHEN 'very negative' THEN 1.0
                WHEN 'negative' THEN 2.0
                WHEN 'very low energy' THEN 1.0
                WHEN 'low energy' THEN 2.0
                WHEN 'very calm' THEN 1.0
                WHEN 'calm' THEN 2.0
                WHEN 'some simple tasks' THEN 1.0
                WHEN 'all simple tasks' THEN 2.0
                WHEN 'neutral' THEN 3.0
                WHEN 'positive' THEN 4.0
                WHEN 'very positive' THEN 5.0
                WHEN 'energetic' THEN 4.0
                WHEN 'very energetic' THEN 5.0
                WHEN 'stressed' THEN 4.0
                WHEN 'very stressed' THEN 5.0
                WHEN 'moderate tasks' THEN 3.0
                WHEN 'some complex tasks' THEN 4.0
                WHEN 'all complex tasks' THEN 5.0
                WHEN 'anxious' THEN 4.0
                WHEN 'very anxious' THEN 5.0
                WHEN 'no answer' THEN NULL
                ELSE NULL
            END AS item_score,
            CASE LOWER(label)
                WHEN 'very negative' THEN 0.0
                WHEN 'negative' THEN 0.25
                WHEN 'very low energy' THEN 0.0
                WHEN 'low energy' THEN 0.25
                WHEN 'very calm' THEN 0.0
                WHEN 'calm' THEN 0.25
                WHEN 'some simple tasks' THEN 0.0
                WHEN 'all simple tasks' THEN 0.25
                WHEN 'neutral' THEN 0.5
                WHEN 'positive' THEN 0.75
                WHEN 'very positive' THEN 1.0
                WHEN 'energetic' THEN 0.75
                WHEN 'very energetic' THEN 1.0
                WHEN 'stressed' THEN 0.75
                WHEN 'very stressed' THEN 1.0
                WHEN 'moderate tasks' THEN 0.5
                WHEN 'some complex tasks' THEN 0.75
                WHEN 'all complex tasks' THEN 1.0
                WHEN 'anxious' THEN 0.75
                WHEN 'very anxious' THEN 1.0
                WHEN 'no answer' THEN NULL
                ELSE NULL
            END AS normalized_score
        FROM bump_quick_activities

        UNION ALL

        SELECT
            'SinC' AS dataset,
            'SinC:' || user_id AS participant_key,
            CAST(user_id AS TEXT) AS raw_participant_id,
            DATE(event_date) AS event_date,
            'sinc_ema' AS source_table,
            CASE
                WHEN datatype = 'brain_capability' THEN 'brain_capability'
                ELSE LOWER(datatype)
            END AS measure,
            CAST(value AS REAL) AS item_score,
            CASE
                WHEN CAST(value AS REAL) BETWEEN 1 AND 5 THEN (CAST(value AS REAL) - 1) / 4.0
                ELSE NULL
            END AS normalized_score
        FROM sinc_ema;
    """,
}


def _extend_view_sql(create_sql, view_name, dataset_plugins):
    """Append manifest-provided SELECT branches to a built-in view definition."""
    sql = create_sql.strip().rstrip(";")
    for plugin in dataset_plugins or ():
        select_sql = plugin.harmonized_views.get(view_name)
        if select_sql:
            sql += f"\n\nUNION ALL\n\n{select_sql.strip().rstrip(';')}"
    return sql + ";"


def create_harmonized_views(engine, dataset_plugins=()) -> None:
    """Create canonical wearable views across all registered dataset mappings."""
    with engine.begin() as connection:
        for view_name, create_sql in VIEW_DEFINITIONS.items():
            connection.execute(text(f"DROP VIEW IF EXISTS {view_name}"))
            connection.execute(text(_extend_view_sql(create_sql, view_name, dataset_plugins)))
