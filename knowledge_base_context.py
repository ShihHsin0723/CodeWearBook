sql_guideline = """
  Join and column usage:
  - When joining tables with overlapping column names (e.g., record_id, event_date), always qualify columns with table aliases in all clauses.
  - Use the identifier column that matches the active dataset and schema. Do not assume the same participant key across datasets.

  User selection:
  - If a placeholder user is referenced, choose the identifier column that exists in the active table/schema.
  - If a valid participant identifier is provided, use it directly.

  Surveys table rules:
  - If a survey is identified, MUST filter by its title.
    - Prefer exact match: title = '<full survey name>' (e.g. title = 'Healthcare Utilization survey')
    - Use LIKE only if title may vary.
  - Do NOT rely on question_text alone when title is known.
  - Match survey questions using question_text LIKE with distinctive substrings.
  - When multiple survey items are required, explicitly enumerate each item using separate LIKE conditions.
  - When pivoting responses into columns, use:
    MAX(CASE WHEN question_text LIKE ... THEN answer_text END)
  - If the question asks for self-reported values, return raw answer_text (do NOT compute unless explicitly asked).
  - If the question asks for a score, apply scoring rules from the knowledge base.

  Unit of analysis:
  - Determine output level before writing SQL:
    - participant-level → one row per participant identifier from the active dataset
    - event-level → one row per participant identifier and event date
  - Do not treat repeated rows for the same participant identifier as different participants.

  NULL handling:
  - Use NULL (not 0) for missing values unless 0 is valid.
  - Exclude NULLs from aggregates unless explicitly required.
  - Exclude NULL values from grouping keys unless the question explicitly asks to include missing or unknown values.
  - For grouped summaries, add `WHERE <grouping_column> IS NOT NULL` for the variable being grouped.
  - Also exclude NULL measured values used in aggregates unless the question asks to measure missingness.

  Date handling:
  - Use DATE(...) when comparing dates if time is irrelevant.
  - Ensure date filters match the time window in the question.
"""

bump_sql_guideline = """
  BUMP-specific entity and join rules:
  - For BUMP participant-level joins, use `record_id` unless the schema explicitly requires otherwise.
  - If a placeholder user is referenced and no participant identifier is provided (e.g. "user X", "participant XXX"), use:
    - record_id = (SELECT record_id FROM <table> LIMIT 1)
    - MUST include `record_id` in the SELECT output so the returned result identifies which placeholder participant was used.
  - For BUMP unit of analysis:
    - participant-level → one row per record_id
    - event-level → one row per record_id, event_date
  - Do not treat repeated rows for the same record_id as different participants.
"""

sinc_sql_guideline = """
  SinC-specific entity and join rules:
  - For SinC participant-level joins, use `user_id` unless the schema explicitly requires otherwise.
  - If a placeholder user is referenced and no participant identifier is provided (e.g. "user X", "patient X", "participant XXX"), use:
    - user_id = (SELECT user_id FROM <table> LIMIT 1)
    - MUST include `user_id` in the SELECT output so the returned result identifies which placeholder participant was used.
  - For SinC unit of analysis:
    - participant-level → one row per user_id
    - event-level → one row per user_id, event_date
  - Do not treat repeated rows for the same user_id as different participants.
"""

cross_dataset_sql_guideline = """
  Cross-dataset entity and join rules:
  - Use the harmonized views `wearable_sleep_daily`, `wearable_activity_daily`, `wearable_readiness_daily`, `wearable_bodyport_daily`, `wearable_ebt_sessions`, and `wearable_ema_daily`.
  - Do not directly join raw `bump_*` participant rows to raw `sinc_*` participant rows.
  - In cross-dataset mode, compare cohorts by `dataset`.
  - Use `participant_key` only within the harmonized views.
"""

bmi_calculation = """
  To retrieve or reference Body Mass Index (BMI), use the value in the `bmi_kg_m2` column from the dataset-qualified bodyport table in schema (for example `bump_bodyport` or `sinc_bodyport`). Do not recalculate it manually from height and weight.
"""

bmi_classification = """
  To classify Body Mass Index (BMI), use the value from the `bmi_kg_m2` column in the dataset-qualified bodyport table and apply the following standard ranges:
  - BMI < 18.5 = Underweight
  - 18.5 <= BMI < 25.0 = Normal weight
  - 25.0 <= BMI < 30.0 = Overweight
  - BMI >= 30.0 = Obesity
"""

###################################################### BUMP Data Knowledge Base ######################################################

pregnancy_time_calculations_info = """
  Pregnancy time calculation rules for pregnancy-related queries:
  - If a patient has multiple birth dates, use the earliest non-null birth_date: `MIN(DATE(birth_date))`.

  Definitions:
  - Last Menstrual Period (LMP, start of pregnancy):
    DATE(birth_date, '-' || gest_total_days || ' days')

  - Estimated Due Date (EDD / Naegele’s rule):
    DATE(DATE(birth_date, '-' || gest_total_days || ' days'), '+280 days')

  Trimester boundaries:
  - Trimester 1 (T1): from LMP through 13 weeks
    - start: DATE(birth_date, '-' || gest_total_days || ' days')
    - end: DATE(birth_date, '-' || (gest_total_days - 13 * 7) || ' days')

  - Trimester 2 (T2): from 14 weeks through 27 weeks
    - start: DATE(birth_date, '-' || (gest_total_days - 13 * 7 - 1) || ' days')
    - end: DATE(birth_date, '-' || (gest_total_days - 27 * 7) || ' days')

  - Trimester 3 (T3): from 28 weeks through delivery
    - start: DATE(birth_date, '-' || (gest_total_days - 27 * 7 - 1) || ' days')
    - end: birth_date

  Usage rules:
  - Use these formulas only for pregnancy-relative time windows.
  - If either `birth_date` or `gest_total_days` is NULL, pregnancy-relative calculations cannot be computed.
"""

# Bodyport
bodyport_info = """
  The Bodyport cardiac scale measures multiple biomarkers including weight, peripheral fluid levels, HR, HRV, cardiac time intervals, and ejection forces.
  Frequency: daily.
  Some columns in the `bump_bodyport` table are the following:
    - heart_rate (Average Heart Rate): Calculated from the IPG derivative signal.
    - impedance_mag_1_ohms (Body impedance magnitude (64khz)): Well-correlated to body composition and fluid status. May be used to determine total body water content.
    - impedance_phase_1_degs (Body impedance phase (64khz)): May be used alongside the impedance magnitude to discern between intracellular and extracellular water.
    - peripheral_fluid (Peripheral fluid content (PFC)): A unitless index representing fluid content in the lower body. pcf = 1000 / impedance magnitude.
    - backend_sway_area_mm2 (Body sway area): The area that encloses the data points of the center of pressure. (mm^2)
    - sway_velocity_mm_sec (Balance score (sway velocity)): A unitless index (0-10) representing postural sway.
"""

# Oura Activity
oura_activity_info = """
  Activity score provides an estimate how well recent physical activity has matched ring user's needs. It is calculated as a weighted average of activity score contributors that represent one aspect of suitability of the activity each.
  Frequency: daily.
  Each score contributor can range from 1 to 100, or 0 if not available.
  In the `bump_oura_activity` table, the score contributors to the score column are:
    - score_stay_active (weight: 0.15): how well the user avoided inactivity over the past 24 hours: it scores 100 if inactive time is under 5 hours and above 95 if under 7 hours.
    - score_move_every_hour (weight: 0.10): how well the user avoided long (≥60 min) inactive periods in the past 24 hours, excluding sleep. It scores 100 if none occur and above 95 if only one occurs.
    - score_meet_daily_targets (weight: 0.25): how often the user has reached their daily activity target during seven last days (100 = six or seven times, 95 = five times).
    - score_training_frequency (weight: 0.10): how regularly the user exercised in the past seven days. It scores 100 for over 100 minutes of medium or high intensity activity on at least four days, and 95 for at least three.
    - score_training_volume (weight: 0.15): how much exercise the user performed in the past seven days. It scores 100 when weekly MET minutes exceed 2000 and 95 when over 750, with recent days weighted more heavily.
    - core_recovery_time (weight: 0.25): whether the user has had sufficient recovery time in the past seven days. It scores 100 with at least two recovery days and no more than two days since the last one, and 95 with at least one recovery day and no more than three days since the last. A recovery day is defined by low activity levels (≤100 MET min high intensity, ≤200 MET min medium intensity, age- and gender-adjusted).
"""

# Oura Sleep
oura_sleep_info = """
  Sleep score represents overall sleep quality during the sleep period. It is calculated as a weighted average of sleep score contributors that represent one aspect of sleep quality each.
  Frequency: nightly.
  Each score contributor can range from 1 to 100, or 0 if not available.
  In the `bump_oura_sleep` table, the score contributors to the score column are:
    - score_total (weight: 0.35): total sleep time's contribution for sleep quality. The value depends on age of the user - the younger, the more sleep is needed for good score.
    - score_rem (weight: 0.10): REM sleep time's contribution for sleep quality. The value depends on age of the user - the younger, the more sleep is needed for good score.
    - score_deep (weight: 0.10): deep sleep time's contribution for sleep quality. The value depends on age of the user - the younger, the more sleep is needed for good score.
    - score_efficiency (weight: 0.10): sleep efficiency's contribution for sleep quality. The higher efficiency, the higher score.
    - score_latency (weight: 0.10): sleep onset latency's contribution for sleep quality. Optimal latency is around 15 minutes; longer durations may indicate difficulty falling asleep, while very short ones may suggest sleep debt.
    - score_disturbances (weight: 0.15): sleep disturbances' contribution for sleep quality. It combines wake-up count, got-up count, and restless sleep—each reducing the score as their values increase. Each metric has a weight of 0.05.
    - score_alignment (weight: 0.10): circadian alignment's contribution for sleep score. A sleep midpoint between 12 PM and 3 AM yields the highest score, with greater deviation lowering it.
"""

# Oura Readiness
oura_readiness_info = """
  Readiness score reflects how prepared your body is for the day. Scores above 85% indicate good recovery, while scores below 70% suggest that key factors—such as body temperature or prior sleep—are outside normal or recommended ranges.
  Frequency: daily.
  Each score contributor can range from 1 to 100, or 0 if not available.
  In the `bump_oura_readiness` table, the score contributors to the score column are:
    - score_previous_night: how the individual’s sleep over the past 24 hours compares to their baseline.
    - score_sleep_balance: compares recent sleep quality to long-term average.
    - score_previous_day: compares the previous day's activity level and sedentary time to the long-term average and recommended activity levels.
    - score_activity_balance: compares recent physical load to the long-term average.
    - score_resting_hr: compares the lowest heart rate during sleep from the previous night with the long-term average.
    - score_recovery_index: the amount of time the individual slept after their heart rate stabilized (remaining within 3 BPM of the lowest heart rate) during sleep.
    - score_temperature: compares body temperature from the previous night to the long-term, average body temperature.
"""

# Camcog - EBT
camcog_ebt_info = """
  The EBT task is a social cognition task identifying emotional biases in populations.
  Frequency: task-based.
  Data columns in the `bump_ebt` table:
  - EBTRTMDT (EBT Median Reaction Time Total)
  - EBTRTMNT (EBT Mean Reaction Time Total)
  - EBTRTSDT (EBT Standard Deviation Reaction Time Total)
  - EBTRTMDH (EBT Median Reaction Time Happy)
  - EBTRTSDH (EBT Standard Deviation Reaction Time Happy)
  - EBTRTMDS (EBT Median Reaction Time Sad)
  - EBTRTMNS (EBT Mean Reaction Time Sad)
  - EBTRTSDS (EBT Standard Deviation Reaction Time Sad)
  - EBTRCH (EBT Response Count Happy)
  - EBTRCS (EBT Response Count Sad)
  - EBTBP (EBT Bias Point - number of assessed trials selected as 'Happy'/Number of all assessed trials * 15)
"""

# Camcog - NBX
camcog_nbx_info = """
  The Cognition Kit N-Back assesses attention and working memory. Participants were presented with a series of symbols and asked to touch the screen if the symbol was the same as the symbol that was presented two trials previously.
  Frequency: task-based.
  Data columns in the `bump_nbx` table:
  - NBXSCORE (Performance score between 0-100 calculated from aggregating the correct hits and false alarm occurrences)
  - NBXPCH (Percentage Hits - correct detection of an n-back match)
  - NBXPCFA (Percentage False Alarms - incorrectly responding when no n-back match was present)
  - NBXHITS (Number of hits - the count of go trials (out of 7 total) where the participant made a timely and successful response to the stimulus)
  - NBXFALRM (Number of False Alarms - the count of stop trials (23 in total) where the participant failed to withhold a timely response to the stimulus)
  - NBXMISS (Number of Misses — the count of go trials where the participant failed to make a timely or successful response to the stimulus)
  - NBXCRJCT (Number of Correct Rejections — the count of stop trials where the participant successfully withheld their response to the stimulus)
  - NBXAPRME (Prime - a non-parametric measure of sensitivity for participant’s discrimination between go and stop trials)
  - NBXDPRME (D Prime - a parametric measure of sensitivity for participant’s discrimination between go and stop trials including adjustment for extreme values)
"""

# Camcog - PVT
camcog_pvt_info = """
  The Psychomotor Vigilance Task (PVT) assesses vigilance and reaction time. Participants were asked to monitor their smartphone screen and touch the screen as soon as the numbers appeared. The number in the box at the center of the screen increased each msec, until the participant touched the screen, providing immediate feedback on reaction time.
  Frequency: task-based.
  Data columns in the `bump_pvt` table:
  - PVTTH (PVT Total Hits)
  - PVTTFP (PVT Total False Positives)
  - PVTTTO (PVT Total Timeouts)
  - PVTMRT (PVT Reaction latency (mean))
  - PVTSV (PVT Reaction latency (variance))
  - PVTCV (PVT Reaction latency (Coefficient of Variation) - (sample standard deviation / mean) * 100)
  - PVTSLF (PVT Slope of Linear Fit)
  - PVTMPL (PVT Mean Press Length)
"""

# REDCap - Adverse Childhood Events (ACE)
ace_info = """
  ACE stands for Adverse Childhood Events.
  Frequency: once.
  Each column in the `bump_ace` table corresponds to a 'Yes' (1) or 'No' (0) response to one of the following 10 questions, all under the context: "While you were growing up, during your first 18 years of life ..."
  - ace_1: Did a parent or other adult in the household often … Swear at you, insult you, put you down, or humiliate you? or Act in a way that made you afraid that you might be physically hurt?
  - ace_2: Did a parent or other adult in the household often … Push, grab, slap, or throw something at you? or Ever hit you so hard that you had marks or were injured?
  - ace_3: Did an adult or person at least 5 years older than you ever… Touch or fondle you or have you touch their body in a sexual way? or Try to or actually have oral, anal, or vaginal sex with you?
  - ace_4: Did you often feel that... No one in your family loved you or thought you were importan or special? or Your family didn't look out for each other, feel close to each other, or support each other?
  - ace_5: Did you often feel that … You didn’t have enough to eat, had to wear dirty clothes, and had no one to protect you? or Your parents were too drunk or high to take care of you or take you to the doctor if you needed it?
  - ace_6: Were your parents ever separated or divorced?
  - ace_7: Was your mother or stepmother: Often pushed, grabbed, slapped, or had something thrown at her? or Sometimes or often kicked, bitten, hit with a fist, or hit with something hard? or Ever repeatedly hit over at least a few minutes or threatened with a gun or knife?
  - ace_8: Did you live with anyone who was a problem drinker or alcoholic or who used street drugs?
  - ace_9: Was a household member depressed or mentally ill or did a household member attempt suicide?
  - ace_10: Did a household member go to prison?
"""

# REDCap - Adverse Event Report
adverse_event_report_info = """
  Frequency: event-driven / as needed.
  In the `bump_adverse_event_report` table, the ae_describe column contains the answer to the question: Have you had any adverse events since we last spoke either relating or unrelated to study activities?

  For derived adverse event labels based on ae_describe:
  - Return "Yes" only when the participant experienced the event described by the target label.
  - Return "No" when the event is only mentioned as negative, ruled out, exposure-only, testing-only, prevention-only, or unrelated context.

  Covid:
  - Yes: participant tested positive for COVID, had COVID illness, was diagnosed with COVID, had COVID infection, currently has COVID, had a positive COVID-19/RAT/PCR/rapid antigen test, or the report states the participant had COVID-19.
  - Yes examples: "tested positive for COVID", "diagnosed with COVID", "covid infection", "currently has covid", "positive COVID-19 test", "test positive for covid".
  - No: negative COVID test, COVID exposure only, COVID vaccine/shot symptoms, COVID test performed without positive result, partner/family member COVID only, or anxiety/risk context mentioning COVID without participant illness.
  - No examples: "tested negative for covid", "covid exposure", "partner tested positive", "received covid vaccine", "covid test" with no positive result, "has not had covid symptoms".
  - SQL caution: do not use only `LIKE '%covid%'` or broad `LIKE '%had COVID%'`; include positive-test/infection/diagnosis phrases and exclude negative, exposure-only, vaccine/shot, and family/partner-only contexts.

  ER/ED visit:
  - Yes: participant went to ER, ED, emergency room, emergency department, urgent care, or emergency care.
  - Yes examples: "went to the ER", "went to ER", "went to ED", "visited ED", "went to the emergency room", "went to the emergency department", "Emergency Department", "urgent care".
  - No: avoid matching substrings inside ordinary words such as "reported", "redness", "underneath", "wearing", "resolved", "started", "discharged", "received", "experienced".
  - SQL caution: never use `LIKE '%ER%'` or `LIKE '%ED%'` by itself because it matches ordinary words. For SQLite, approximate word boundaries with patterns such as `LIKE '% ER %'`, `LIKE '% ER,%'`, `LIKE '% ER.%'`, `LIKE 'ER %'`, `LIKE '% ED %'`, `LIKE '% ED,%'`, `LIKE '% ED.%'`, plus explicit phrases `LIKE '%emergency room%'`, `LIKE '%emergency department%'`, and `LIKE '%urgent care%'`.

  OB triage or L&D (Labor and Delivery) triage:
  - Yes: participant went to OB triage, L&D, Labor and Delivery, Labor & Delivery, hospital L&D department, L&D department, or was sent to/visited/monitored in L&D for evaluation.
  - Yes examples: "went to OB triage", "went into OB triage", "sent to OB triage", "called OB triage", "went to L&D", "visited L&D", "sent to L&D", "hospital L&D", "L&D dpt", "Labor and Delivery triage", "Labor and Delivery", "Labor & Delivery".
  - No: ordinary OB visit, OB office/check-up, OB/GYN appointment, ultrasound/check-up, provider phone call, or labor/delivery admission only when the question is specifically about triage and the text does not indicate L&D/triage evaluation.
  - SQL caution: do not only match `L&D triage`; also match broader L&D visit phrases such as `%went to L&D%`, `%visited L&D%`, `%sent to L&D%`, `%hospital L&D%`, `%L&D dpt%`, `%Labor and Delivery%`, and `%Labor & Delivery%`.

  Vaccine/shot and symptoms:
  - Yes: vaccine/shot/immunization is the adverse event cause or participant had symptoms after vaccine/shot.
  - No: generic "symptoms" without vaccine/shot context.

  Skin irritation and hive:
  - Yes: rash, hives, itching, itchiness, contact dermatitis, red bumps, skin reaction, skin irritation, watch/ring/band irritation.
"""

# REDCap - Birthing Data
birthing_data_info = """
  Birthing data records patients' postnatal information.
  Frequency: once.
  Data columns in the `bump_birthing_data` table:
  - birth_date: Birth date of the baby, not the birth date of the patient.
  - birth_gestage: Gestational age at delivery (duration of pregnancy from the first day of the mothers last normal menstrual period).
  - gest_weeks: Completed gestational weeks at delivery.
  - gest_days: Additional days beyond full gestational weeks (0–6).
  - gest_total_days: Total gestational age in days (weeks × 7 + days).
  - birth_mode: Mode of delivery (1-Cesarean section or 2- Vaginal)
  - induction_yn: Was put in the hospital for induction of labor? (Yes = 1, No = 0)
  - vacuumforceps: If vaginal delivery, operative vaginal delivery using vacuum and/or forceps? (Yes = 1, No = 0)
  - vaginalaftercesarean: Vaginal birth after cesarean? (Yes = 1, No = 0)
  - birth_c_type: If cesarean delivery, type of cesarean section: {1-Primary elective; 2-Primary emergent; 3-Repeat elective; 4-Repeat emergent; 5-primary for failed induction; 6-repeat for failed VBAC; 7-primary for failure to pregress in labor (baby did not srop); 8-cervix did not fully dilate; 9-pushed for 2+ hours with no delivery}
  - birth_incision: If cesarean delivery, type of cesarean uterine incision: {1-classical; 2-low transverse; 3-low vertical; 4-other}
  - birth_indication: Primary indication for cesarean delivery.
  - birth_apgar1: APGAR score with scale 0-10 for baby 1 (1 minute, 5 minutes).
  - birth_abnormal1: Abnormal or genetic conditions for baby 1 (Yes = 1, No = 0)
  - birth_maternal___1: Pre-eclampsia? (Yes = 1, No = 0)
  - birth_maternal___2: Eclampsia? (Yes = 1, No = 0)
  - birth_maternal___4: Toxemia? (Yes = 1, No = 0)
  - birth_urine: High protein in the urine? (Yes = 1, No = 0)
  - birth_matmorbid: Any of the maternal morbidity worth noting? (Yes = 1, No = 0)
  - birth_describe: Description of any maternal morbidity.
  - birth_stay: The length of hospital stay after delivery.
  - birth_stay_baby_yes: If the baby had to stay in hospital for additional period of time, what was the length.
  - birth_feeding: How are you feeding the baby? {1-exclusively breast feeding; 2-breast feeding with supplementation; 3-exclusively supplementation}
  - birth_mat_leave_length: If on maternity leave, how many months?
"""

# REDCap - Columbia-Suicide Severity Rating Scale (C-SSRS)
# https://suicidepreventionlifeline.org/wp-content/uploads/2016/09/Suicide-Risk-Assessment-C-SSRS-Lifeline-Version-2014.pdf
cssrs_info = """
  The `bump_cssrs` table contains responses to the Columbia-Suicide Severity Rating Scale.
  Frequency: assessment-based.

  Suicidal Ideation (si): (Yes = 1, No = 2), where:
  - (a) indicates experiences within the past month,
  - (b) indicates experiences ever in the lifetime.
  1) Have you wished you were dead or wished you could go to sleep and not wake up ...
  2) Have you actually had any thoughts of killing yourself?
  If yes to 1) or 2):
  3) Have you been thinking about how you might do this?
  4) Have you had these thoughts and had some intention of acting on them?
  5) Have you started to work out or worked out the details of how to kill yourself? Do you intend to carry out this plan?

  Intensity of Ideation (cssrs)
  - (a) indicates experiences within the past month,
  - (b) indicates experiences ever in the lifetime.
  6) How many times have you had these thoughts?
  (1=Less than once a week, 2=once a week, 3= 2-5 times in a week, 4=daily or almost daily, 5=many times each day)
  7) When you have the thoughts how long do they last?
  (1=Fleeting - few seconds or minutes, 2=Less than 1 hour/some of the time, 3=1-4 hours/a lot of the time, 4=4-8 hours/most of day, 5=More than 8 hours/persistent or continuous)
  8) Could/can you stop thinking about killing yourself or wanting to die if you want to?
  (1=Easily able to control thoughts, 2=can control thoughts with little difficulty, 3=can control thoughts with some difficulty, 4= Can control thoughts with a lot of difficulty, 5= Unable to control thoughts, 0=Do not attempt to control thoughts)
  9) Are there things - anyone or anything (e.g., family, religion, pain of death) - that stopped you from wanting to die or acting on thoughts of committing suicide?
  (1= Deterrents definitely stopped you from attempting suicide, 2=Deterrents probably stopped you, 3=Uncertain that deterrents stopped you, 4= Deterrents most likely did not stop you, 5=Deterrents definitely did not stop you, 0=Does not apply)
  10) What sort of reasons did you have for thinking about wanting to die or killing yourself? Was it to end the pain or stop the way you were feeling (in other words you couldn’t go on living with this pain or how you were feeling) or was it to get attention, revenge or a reaction from others? Or both?
  (1=Completely to get attention, revenge or a reaction from others; 2=Mostly to get attention, revenge or a reaction from others living with the pain or how you were feeling; 3=Equally to get attention, revenge or a reaction from others; 4=Mostly to end or stop the pain (you couldn’t go on); 5=Completely to end or stop the pain (you couldn’t go on and to end/stop the pain living with the pain or how you were feeling); 0=Does not apply")

  Suicidal Behavior (suicide or sb)
  1) Have you made a suicide attempt?
    - a) ...in the past 1 month (Yes = 1, No = 2)
    - b) ...ever? (Yes = 1, No = 2)
    - c) Total # of attempts in the past 1 month
    - d) Total # of attempts in lifetime
    Has subject engaged in Non-Suicidal Self-Injurious Behavior?
    - e) ...in the past 1 month (Yes = 1, No = 2)
    - f) ...ever? (Yes = 1, No = 2)
  2) Has there been a time when you started to do something to end your life but someone or something stopped you before you actually did anything?
    - a) ...in the past 1 month? (Yes = 1, No = 2)
    - b) ...ever? (Yes = 1, No = 2)
    - c) Total # of interrupted in the past 1 month?
    - d) Total # of interrupted in lifetime
  3) Has there been a time when you started to do something to try to end your life but you stopped yourself before you actually did anything?
    - a) ...in the past 1 month? (Yes = 1, No = 2)
    - b) ...ever? (Yes = 1, No = 2)
    - c) Total # of aborted in the past 1 month
    - d) Total # of aborted in lifetime
  4) Have you taken any steps towards making a suicide attempt or preparing to kill yourself (such as collecting pills, getting a gun, giving valuables away or writing a suicide note)?
    - a) ...in the past 1 month?
    - b) ...ever?
  5) Suicidal behavior was present during the assessment period? (Yes = 1, No = 0)
"""

# REDCap - GAD7
gad7_info = """
  The GAD-7 (Generalized Anxiety Disorder-7) screens for anxiety severity using seven items.
  Frequency: assessment-based.
  Each item (gad_1 to gad_8) is rated from 0 to 3:
  - For gad_1 to gad_7: 0 = Not at all, 1 = Several days, 2 = More than half the days, 3 = Nearly every day.
  - For gad_8 (functional difficulty): 0 = Not difficult at all, 1 = Somewhat difficult, 2 = Very difficult, 3 = Extremely difficult
  The GAD-7 score is calculated by summing scores from gad_1 to gad_7. The questions are:
  - gad_1: Feeling nervous, anxious, or on edge
  - gad_2: Not being able to stop or control worrying
  - gad_3: Worrying too much about different things
  - gad_4: Trouble relaxing
  - gad_5: Being so restless that it's hard to sit still
  - gad_6: Becoming easily annoyed or irritable
  - gad_7: Feeling afraid as if something awful might happen
  - gad_8: If you checked off any problems, how difficult have these made it for you to do your work, take If you checked off any problems, how difficult have these made it for you to do your work, take care of things at home, or get along with other people?

  Classification of GAD-7 score:
    - 0-4: Minimal anxiety
    - 5-9: Mild anxiety
    - 10-14: Moderate anxiety
    - 15-21: Severe anxiety
"""

# REDCap - PHQ9
phq9_info = """
  The PHQ-9 (Patient Health Questionnaire-9) consists of 9 questions assessing depression symptoms.
  Frequency: assessment-based.
  Each item (phq9_1 to phq9_10) is rated from 0 to 3:
  - For phq9_1 to phq9_9: 0 = Not at all, 1 = Several days, 2 = More than half the days, 3 = Nearly every day
  - For phq9_10 (functional difficulty): 0 = Not difficult at all, 1 = Somewhat difficult, 2 = Very difficult, 3 = Extremely difficult
  The PHQ-9 score is calculated by summing scores from phq9_1 to phq9_9. The questions are:
  - phq9_1: Little interest or pleasure in doing things
  - phq9_2: Feeling down, depressed, or hopeless
  - phq9_3: Feeling tired or having little energy
  - phq9_4: Trouble falling or staying asleep, or sleeping too much
  - phq9_5: Poor appetite or overeating
  - phq9_6: Feeling bad about yourself or that you are a failure or have let yourself or your family down
  - phq9_7: Trouble concentrating on things, such as reading the newspaper or watching television
  - phq9_8: Moving or speaking so slowly that other people could have noticed or the opposite - being so fidgety or restless that you have been moving around a lot more than usual
  - phq9_9: Thoughts that you would be better off dead or of hurting yourself in some way
  - phq9_10: If you checked off any problems, how difficult have these problems made it for you to do your work, take care of things at home, or get along with other people?

  Classification of PHQ-9 score:
    - 0-4: Minimal depression
    - 5–9: Mild depression
    - 10–14: Moderate depression
    - 15–19: Moderately severe depression
    - 20-27: Severe depression

  Major depressive disorder (MDD) is suggested if:
    - Of the 9 items, 5 or more are rated as 2 or higher
    - Either phq9_1 or phq9_2 is checked as at least 2
  Other depressive syndrome is suggested if:
    - Of the 9 items, between 2 to 4 are rated as 2 or higher
    - Either phq9_1 or phq9_2 is checked as at least 2
"""

# REDCap - EPDS
epds_info = """
  Edinburgh Perinatal/Postnatal Depression Scale (EPDS) is a screening questionnaire (10 questions) used to identify people who may be experiencing depression during pregnancy or after giving birth.
  Frequency: assessment-based.
  Respondents are asked to reflect on how they have felt over the past 7 days.
  Each item (epds_1co2 to epds_10co2) is rated from 0 to 3.
  The EPDS total score is calculated by summing epds_1co2 through epds_10co2 (range: 0–30).
  The questions assess:
  - epds_1co2: I have been able to laugh and see the funny side of things
  - epds_2co2: I have looked forward with enjoyment to things
  - epds_3co2: I have blamed myself unnecessarily when things went wrong
  - epds_4co2: I have been anxious or worried for no good reason
  - epds_5co2: I have felt scared or panicky for no very good reason
  - epds_6co2: Things have been getting on top of me
  - epds_7co2: I have been so unhappy that I have had difficulty sleeping
  - epds_8co2: I have felt sad or miserable
  - epds_9co2: I have been so unhappy that I have been crying
  - epds_10co2: The thought of harming myself has occurred to me

  Interpretation of EPDS score:
    - 0–9: Low likelihood of depression
    - 10–12: Possible minor depression; further evaluation recommended
    - ≥13: Likely major depression; clinical follow-up recommended
  
  Safety note: Any positive response (score 3 or 2)to epds_10co2 (self-harm thoughts) should prompt immediate clinical review.
"""

# REDCap - Pitting Edema
pitting_edema_info = """
  Pitting edema is a clinical sign assessed by applying firm pressure to the skin and observing the depth of the indentation that remains after release.
  Frequency: assessment-based.

  - edema_yes: Presence of edema (0 = No, 1 = Yes)

  Location of edema:
  - edema_where___1: Dorsum of the foot
  - edema_where___2: Behind the medial malleolus
  - edema_where___3: Lower calf above the medial malleolus
  (0 = No, 1 = Yes)

  - pitting_edema: Severity score based on indentation depth
      0 = No clinical edema
      1 = ≤ 2 mm
      2 = 2–4 mm
      3 = 4–6 mm
      4 = 6–8 mm
"""

# REDCap - Perinatal PTSD
perinatal_ptsd_info = """
  The Perinatal PTSD questionnaire was a one-time survey administered during the postpartum study period, and included 14 questions to assess symptoms of post-traumatic stress disorder related to the birth experience.
  Frequency: once.

  Each item (ptsd1 to ptsd14) is scored on a 0–4 scale:
    0 = Not at all
    1 = Once or twice
    2 = Sometimes
    3 = Often, but less than 1 month
    4 = Often, for more than 1 month

  The questions are:
  - ptsd1: Bad dreams of giving birth or of the baby’s hospital stay
  - ptsd2: Upsetting memories of giving birth or of the baby’s hospital stay
  - ptsd3: Sudden feelings as though the baby’s birth was happening again
  - ptsd4: Avoiding thinking about childbirth or the baby’s hospital stay
  - ptsd5: Avoiding activities that might bring up feelings about childbirth or hospital stay
  - ptsd6: Inability to remember parts of the baby’s hospital stay
  - ptsd7: Loss of interest in usual activities (e.g., work or family)
  - ptsd8: Feeling alone or emotionally removed from others
  - ptsd9: Difficulty feeling tenderness or love toward others
  - ptsd10: Difficulty falling asleep or staying asleep
  - ptsd11: Increased irritability or anger toward others
  - ptsd12: Greater difficulty concentrating than before childbirth
  - ptsd13: Feeling more jumpy or easily startled
  - ptsd14: Feelings of excessive guilt related to childbirth
"""


# App Activities - Quick Activities
quick_activities_info = """
  The `bump_quick_activities` table stores EMA responses:
    - frequency: daily
    - `stream`: the quick activity type
    - `label`: the submitted score
    - `position`: the selected answer option

  Quick activities are Ecological Momentary Assessments (EMA) with Self-Assessment Manikin (SAM), where participants answered four questions by rating their mood, energy, cognition, and stress from 1 to 6 (1 = No Answer).
  Activities:
  - Mood: "Please assess your mood"
    Options: (1) No Answer, (2) Very Negative, (3) Negative, (4) Neutral, (5) Positive, (6) Very Positive
  - Energy: "Please assess your current energy level"
    Options: (1) No Answer, (2) Very Low Energy, (3) Low Energy, (4) Neutral, (5) Energetic, (6) Very Energetic
  - Stress: "What is your current stress level?"
    Options: (1) No Answer, (2) Very Calm, (3) Calm, (4) Neutral, (5) Stressed, (6) Very Stressed
  - Cognition: "What is your brain capable of performing right now?"
    Options: (1) No Answer, (2) Some Simple Tasks, (3) All Simple Tasks, (4) Moderate Tasks, (5) Some Complex Tasks, (6) All Complex Tasks
  Data columns in the `bump_quick_activities` table:
  - stream: Type of quick activity (Mood Quick Activity, Energy Quick Activity, Stress Quick Activity, or Cognition Quick Activity).
  - position: Numerical value (1–6) of the selected answer.
  - label: The answer option chosen for the quick activity.
"""

# App Activities - Surveys
surveys_basic_demographics_info = """
  The `bump_surveys` table stores survey responses:
    - title: Baseline survey
    - frequency: once
    - question_text: the exact survey question
    - answer_text: the submitted answer

  Basic/vital demographics survey contains questions about patients themselves:  
    1. Select your date of birth
    2. Select your gender
    3. Select your race (choose all that apply)
    4. Describe your ethnicity
    5. Are you of Hispanic or Latino origin?
    6. Language(s) fully fluent- choose all that apply
    7. Are you fluent in any other language(s)? If so, please list them below. If not, please skip this question.
    8. What is the highest level of education you have completed? 
    9. What is the total number of years of education you have completed?
    10. What is your smoking status?
    11. Do you have health insurance?
"""

surveys_extended_demographics_info = """
  The `bump_surveys` table stores survey responses:
    - title: Baseline survey
    - frequency: once
    - question_text: the exact survey question
    - answer_text: the submitted answer

  Extended demographics/Social Determinants of Health (SDH) survey asks about common social experiences that may affect patients' health:
    - SDH-Family & Home:
      1. How many people, including yourself, do you currently live with?
      2. Describe the individual(s) with whom you live (Check all that apply)
      3. What is your housing situation today?
      4. Are you worried about losing your housing?
    - SDH-Money & Resources
      1. What is your current work situation?
      2. Do you usually work 35 hours or more per week?
      3. In the past year, have you or any family members you live with been unable to get any of the following when it was really needed? (Check all that apply)
      4. Has lack of transportation kept you from medical appointments, meetings, work, or from getting things needed for daily living? (Check all that apply)
    - SDH-Social and Emotional Health
      1. How often do you see or talk to people that you care about and feel close to?
"""

# https://swsphn.com.au/wp-content/uploads/2022/06/PUQE-24-score.pdf
surveys_puqe_info = """
  The `bump_surveys` table stores survey responses:
    - title: PUQE
    - frequency: weekly
    - question_text: the exact survey question
    - answer_text: the submitted answer
  
  The Pregnancy-Unique Quantification of Emesis & Nausea (PUQE) score is used to determine the severity of mausea and vomiting in preganant women.

  If the patient answered "No" to: "During the past week, did you experience any nausea?", then assign PUQE score = 0.
  If the patient answered "Yes", calculate the score by summing scores across the following three items, each scored 1–5 and then summed (range 3–15):
    1. "On average in a day, for how long do you feel nauseated or sick to your stomach?"
      Maps score to answer_text: 1 = "Not at all", 2 = "1 hour or less", 3 = "2 to 3 hours", 4 = "4 to 6 hours", 5 = "6 or more hours"
    2. "On average in a day, how many times do you vomit or throw up?"
      Maps score to answer_text: 1 = "Not at all", 2 = "1-2 times", 3 = "3-4 times", 4 = "5-6 times", 5 = "7 or more times"
    3. "On average in a day, how many times have you had retching or dry heaves without bringing anything up?"
      Maps score to answer_text: 1 = "Not at all", 2 = "1-2 times", 3 = "3-4 times", 4 = "5-6 times", 5 = "7 or more times"

  Classification of PUQE total score:
    - 0-6: Mild
    - 7-12: Moderate
    - 13-15: Severe
"""

# https://www.med.upenn.edu/cbti/assets/user-content/documents/Fatigue%20Assessment%20Scale%20%28FAS%29.pdf
surveys_fas_info = """"
  The `bump_surveys` table stores survey responses:
    - title: Fatigue survey
    - frequency: weekly
    - question_text: the exact survey question
    - answer_text: the submitted answer
  
  The Fatigue Assessment survey evaluates the level of physical and mental exhaustion experienced during the past week.
  If the patient answered "No" to: "During the past week, did you experience any fatigue (feeling physically or mentally exhausted)?", then assign Fatigue score = 0.
  If the patient answered "Yes", compute the FAS score based on the following 10 items. Each question is rated on a 5-point scale:
    - 1 = "Never", 2 = "Sometimes", 3 = "Regularly", 4 = "Often", 5 = "Always"
  Survey items:
    1. I was bothered by fatigue
    2. I got tired very quickly
    3. I didn’t do much during the day
    4. I had enough energy for everyday life
    5. Physically, I felt exhausted
    6. I had problems to start things
    7. I had problems to think clearly
    8. I felt no desire to do anything
    9. Mentally, I felt exhausted
    10. When I was doing something, I could concentrate quite well

  Scoring:
    - For positively worded items (4 and 10), reverse the score:
        1 → 5, 2 → 4, 3 → 3, 4 → 2, 5 → 1
    - For all other items, keep scores as-is.
    - Total Fatigue score = sum of all 10 item scores (range 10–50).  

  Classification of Fatigue total score:
    - 0–21   → No fatigue (normal)
    - 22–34  → Moderate fatigue
    - 35–50  → Severe fatigue
"""

surveys_healthcare_utilization_info = """
  The `bump_surveys` table stores survey responses:
    - title: Healthcare Utilization survey
    - frequency: monthly
    - question_text: the exact survey question
    - answer_text: the submitted answer

  These questions ask about a patient's contact with the healthcare system for pregnancy:
    1. In the past month, have you visited an emergency room, hospital, or acute care facility? 
    2. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last month (pregnancy-related)?
    3. How many of these admissions were planned?
    4. How many of these admissions were unplanned?
    5. What is the total length of all, (all-cause) admissions and readmissions within the last month (in days)?
    6. How many times have you visited an emergency room or emergency department (irrespective of being admitted) within the last month?
"""

surveys_covid19_info = """
  The `bump_surveys` table stores survey responses:
    - title: COVID-19 Baseline survey
    - frequency: once
    - title: COVID Infection survey
    - frequency: monthly
    - question_text: the exact survey question
    - answer_text: the submitted answer
  
  The COVID-19 baseline survey asks about basic COVID-19 history:
    1. Have you had COVID-19?
    2. Was this confirmed by a positive test result from a viral COVID-19 test?
    3. What was the approximate date that you suspect you contracted the virus?
    4. What was the severity of your COVID-19 symptoms?

  The COVID-19 infection survey asks about COVID-19 history details:
    1. In the last 30 days, did you have a COVID-19-like illness with symptoms such as high fever, shortness of breath and cough? OR Did you have to get tested for COVID-19 due to a possible exposure event?
    2. If you took a COVID-19 viral test, Did you test positive?
"""

# https://heal.nih.gov/files/CDEs/2023-07/promis-emotional-support-4a-crf.pdf
surveys_promis_emotional_support_info = """
  The `bump_surveys` table stores survey responses:
    - title: Emotional Support survey
    - frequency: monthly
    - question_text: the exact survey question
    - answer_text: the submitted answer
  
  The PROMIS Emotional Support item bank assesses perceived feelings of being cared for and valued as a person; having confidant relationships.
  Each question is rated on a 5-point scale:
    - 1 = "Never", 2 = "Rarely", 3 = "Sometimes", 4 = "Usually", 5 = "Always"
  Calcualte the score by summing scores across the following four items:
    1. I have someone who will listen to me when I need to talk.
    2. I have someone to confide in or talk to about myself or my problems.
    3. I have someone who makes me feel appreciated.
    4. I have someone to talk with when I have a bad day.
"""

# https://heal.nih.gov/files/CDEs/2023-09/promis-pain-interference-6a-crf.pdf
surveys_promis_pain_interference_info = """
  The `bump_surveys` table stores survey responses:
    - title: Pain Interference survey
    - frequency: weekly
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The PROMIS Pain Interference survey measures how pain affects daily activities and emotions over the past 7 days.
  The survey begins with a screening question (Q0). If no pain is reported, the survey ends; otherwise, five items are asked.
  Response Options:
    - Q0: 1 = "No", 2 = "Yes"
    - Q1–Q4: 1 = "Not at all", 2 = "A little bit", 3 = "Somewhat", 4 = "Quite a bit", 5 = "Very much"
    - Q5: 1 = "Never", 2 = "Rarely", 3 = "Sometimes", 4 = "Usually", 5 = "Always"
  Questions:
    0. In the past 7 days, did you experience any physical pain?
       - If 1 ("No"), end survey; if 2 ("Yes"), continue.
    1. How much did pain interfere with your day to day activities?
    2. How much did pain interfere with work around the home?
    3. How much did pain interfere with your ability to participate in social activities?
    4. How much did pain interfere with your household chores?
    5. How often did pain make you feel discouraged?
  Scoring: if Q0 = "Yes", sum responses to Q1–Q5.  
  Higher scores indicate greater pain interference.
"""

# https://www.healthmeasures.net/images/PROMIS/manuals/PROMIS_Sleep-Related_Impairment_Scoring_Manual.pdf
surveys_promis_sleep_related_impairment_info = """
  The `bump_surveys` table stores survey responses:
    - title: Sleep Related Impairment survey
    - frequency: weekly
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The PROMIS Sleep Related Impairment (SRI) survey assesses the impact of poor sleep on daily functioning and mood over the past 7 days. 
  It contains 8 items rated on a 5-point scale: 
   - 1 = "Not at all", 2 = "A little bit", 3 = "Somewhat", 4 = "Quite a bit", 5 = "Very much"
  Questions:
    1. I was sleepy during the daytime.
    2. I had trouble staying awake during the day.
    3. I had a hard time getting things done because I was sleepy.
    4. I felt tired.
    5. I had problems during the day because of poor sleep.
    6. I had a hard time concentrating because of poor sleep.
    7. I felt irritable because of poor sleep.
    8. I felt alert when I woke up.
  Scoring: sum all item responses (reverse-score item 8).  
  Higher total scores indicate greater sleep-related impairment.
"""

# https://marcqi.org/dev/wp-content/uploads/2019/07/PROMIS-Global-Health.pdf
surveys_promis_global_10_info = """
  The `bump_surveys` table stores survey responses:
    - title: Global survey
    - frequency: monthly
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The PROMIS Global 10 survey provides a broad assessment of overall physical, mental, and social health. 
  It consists of 10 items covering general health, quality of life, social satisfaction, emotional well-being, and symptoms such as fatigue and pain.
  Response Options:
    - Q1–Q6: "Excellent", "Very good", "Good", "Fair", "Poor"
    - Q7: "Completely", "Mostly", "Moderately", "A little", "Not at all"
    - Q8: "Never", "Rarely", "Sometimes", "Often", "Always"
    - Q9: "None", "Mild", "Moderate", "Severe", "Very severe"
    - Q10: 0–10 scale, from "No pain" to "Worst pain imaginable"
  Questions:
    1. In general, would you say your health is:
    2. In general, would you say your quality of life is:
    3. In general, how would you rate your physical health?
    4. In general, how would you rate your mental health, including your mood and your ability to think?
    5. In general, how would you rate your satisfaction with your social activities and relationships?
    6. In general, please rate how well you carry out your usual social activities and roles.
    7. To what extent are you able to carry out your everyday physical activities such as walking, climbing stairs, carrying groceries, or moving a chair?
    In the past 7 days:
    8. How often have you been bothered by emotional problems such as feeling anxious, depressed or irritable?
    9. How would you rate your fatigue on average?
    10. How would you rate your pain on average?
  Scoring: 
  - Two summary scores can be derived: Global Physical Health (GPH) and Global Mental Health (GMH)
    Step 1: Recalculate Q10 (Pain)
      - “0: No pain” → +5  
      - “1–3” → +4  
      - “4–6” → +3  
      - “7–9” → +2  
      - “10: Worst pain imaginable” → +1  
      (Record this as Q10r)
    Step 2: Compute Global Physical Health Raw Score
      - GPH_raw = Q3 + Q6 + Q7 + Q10r
    Step 3: Compute Global Mental Health Raw Score
      - GMH_raw = Q2 + Q4 + Q5 + Q8
"""

surveys_tipi_info = """
  The `bump_surveys` table stores survey responses:
    - title: Personality survey
    - frequency: once
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The Ten-Item Personality Inventory (TIPI) assesses the Big Five personality traits. 
  Response Options:
    1 = "Disagree strongly", 2 = "Disagree moderately", 3 = "Disagree a little", 4 = "Neither agree nor disagree", 5 = "Agree a little", 6 = "Agree moderately", 7 = "Agree strongly"
  Questions:
    1. Extraverted, enthusiastic
    2. Critical, quarrelsome
    3. Dependable, self-disciplined
    4. Anxious, easily upset
    5. Open to new experiences, complex
    6. Reserved, quiet
    7. Sympathetic, warm
    8. Disorganized, careless
    9. Calm, emotionally stable
    10. Conventional, uncreative
  Scoring:
    - Each trait is computed as the mean of two items (with reverse-coded items as noted).
    Extraversion = (Q1 + (8 - Q6)) / 2  
    Agreeableness = ((8 - Q2) + Q7) / 2  
    Conscientiousness = (Q3 + (8 - Q8)) / 2  
    Emotional Stability = ((8 - Q4) + Q9) / 2  
    Openness to Experience = (Q5 + (8 - Q10)) / 2  
    Higher scores indicate stronger presence of that personality dimension.
"""

surveys_daily_symptom_info = """
  The `bump_surveys` table stores survey responses:
    - title: Daily survey
    - frequency: daily
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The Daily survey tracks changes or new symptoms experienced within the past day. It begins with a screening question; if no symptoms are reported, the survey ends. 
  If symptoms are present, participants rate their severity on a 7-point scale.
  Response Options:
    - Q1: "Yes" / "No"
    - Q2–Q8: 1 = "None", 7 = "Severe"
    - Q9: "Yes" / "No"
    - Q9a: Open text (if Q9 = "Yes")
  Questions:
    1. In the past day, have you noticed any symptoms? (e.g. nausea, fatigue, swelling, memory, mood, shortness of breath, the way you walk) 
       - If "No", end survey; if "Yes", continue.
    2. Nausea or any episodes of vomiting
    3. Feeling fatigued or easily tired
    4. Changes in your mood
    5. Shortness of breath
    6. Change in swollen hands and/or feet
    7. Changes in the way you walk
    8. Changes in the way you remember things or how your brain performs
    9. Are there any other symptoms not mentioned here that you have experienced in the past day?
    9a. If you like, please describe these other symptoms. 
  Scoring:
    - If Q1 = "Yes", calculate the mean or sum of Q2–Q8 (higher values = greater symptom severity)

  Questions:
    1. In the past day, have you noticed any symptoms? (e.g. fatigue, mood, anxiety, vaginal bleeding, constipation, abdominal cramps, trouble urinating) 
      - If "No", end survey; if "Yes", continue.
    2. Abdominal cramps
    3. Anxiety
    4. Body aches
    5. Constipation
    6. Engorged breasts or sore nipples
    7. Feeling fatigued or easily tired
    8. Hemorrhoids
    9. Night sweats
    10. Sore perineum
    11. Trouble urinating
    12. Vaginal bleeding
    13. Vaginal dryness
  Scoring:
    - If Q1 = "Yes", calculate the mean or sum of Q2–Q11 (higher values = greater symptom severity)
"""

surveys_daily_stress_measure_info = """
   The `bump_surveys` table stores survey responses:
    - title: Daily survey
    - frequency: daily
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The Daily Stress Measure survey tracks stress experienced within the past day. 
    1. At any time during today did you feel very stressed or extremely stressed?
    2. When did these feelings of stress occur? (Select all that apply)
    3. In total how long did these feelings of stress last?
    4. Right now do you feel in control?
"""

surveys_severe_symptom_complication_info = """
  The `bump_surveys` table stores survey responses:
    - title: Severe Symptom and Complication survey
    - frequency: biweekly
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The Severe Symptom and Complication Survey screens for acute or serious health symptoms experienced 
  within the past 2 weeks. It includes 7 yes/no questions assessing signs of infection, pain, and other complications.
  Response Options:
    - All items: "Yes" / "No"
  Questions:
    1. Have you had any cold or infection of any kind in the past 2 weeks?
    2. Have you had any fever, sore throat or cough in the past 2 weeks?
    3. Have you had burning or frequent urination in the past 2 weeks?
    4. Have you experienced severe headaches in the past 2 weeks?
    5. Have you experienced changes in vision in the past 2 weeks?
    6. Have you experienced changes in upper abdominal pain in the past 2 weeks?
    7. Have you experienced heavy bleeding in the past 2 weeks?
  Scoring:
    - Assign 1 point for each "Yes" response.
    - Total score = number of “Yes” responses (range: 0–7).
    - Higher scores indicate more severe or numerous complications.
"""

surveys_medical_history_info = """
  The `bump_surveys` table stores survey responses:
    - title: Baseline survey
    - frequency: once
    - question_text: the exact survey question
    - answer_text: the submitted answer

  The Medical History Survey collects information about an individual's personal and family medical background.
  Response Options:
    - Q1–Q19: "Yes" / "No" / "Not sure"
    - Q21–Q25: "Father" / "Mother" / "Sibling"
  Questions:
    Has a doctor said you have the following condition:
    1. Diabetes
    2. Hypertension
    3. Heart disease
    4. Autoimmune disorder
    5. Kidney disease
    6. Neurologic disorders
    7. Mood disorder
    8. Postpartum depression
    9. Anxiety disorder
    10. Sleep disorder
    11. Psychotic spectrum disorder
    12. Eating disorder
    13. Hepatitis/Liver disease
    14. Thyroid dysfunction
    15. Pulmonary disorder
    16. Allergies
      16a. What type of allergies?
    17. Cancer
      17a. What type of cancer?
    18. Blood clotting disorder
    19. Are you on antiretroviral treatment?
    Do you have a known biological family history of: 
    21. Obesity
    22. Cancer
    23. Hypertension
    24. Diabetes
    25. Blood clotting disorder
  Scoring:
    - Each "Yes" response for Q1–Q19 may be summed for a personal condition count.
    - Family history items (Q21–Q25) can be used to compute a hereditary risk indicator.
"""

# Garmin - Dailies
garmin_dailies_info = """
  The `bump_garmin_dailies` table includes select derived physiological and activity metrics from Garmin smartwatch daily summaries. 
  Frequency: daily. `event_date` is day-level, while `time_offset_heart_rate_samples` is a within-day series.

  - average_stress_level: Average stress level (1-100), or -1 / -2 if insufficient data (should be excluded). 
    Scores are categorized as: 1–25 = rest, 26–50 = low stress, 51–75 = medium stress, 76–100 = high stress.

  - moderate_intensity_duration_in_seconds: Cumulative duration of activities of moderate intensity, lasting at least 600 seconds at a time (MET value range 3-6).
  - vigorous_intensity_duration_in_seconds: Cumulative duration of activities of vigorous intensity, lasting at least 600 seconds at a time (MET value > 6).
  
  - stress_duration_in_seconds: Total seconds where stress level measurements were in the stressful range (26–100).  
  - rest_stress_duration_in_seconds: Total seconds where stress measurements were in the restful range (1–25).  
  - activity_stress_duration_in_seconds: Seconds where stress could not be measured due to physical activity. Any remaining unclassified time indicates device non-wear or insufficient data.
  
  - low_stress_duration_in_seconds: Portion of stress duration with low-level stress (26–50).  
  - medium_stress_duration_in_seconds: Portion of stress duration with medium-level stress (51–75).  
  - high_stress_duration_in_seconds: Portion of stress duration with high-level stress (76–100).
  
  - stress_qualifier: A qualitative label applied based on all stress measurements in this monitoring period. 
    Possible values: unknown, calm, balanced, stressful, very_stressful, calm_awake, balanced_awake, stressful_awake, very_stressful_awake. 
"""

# Garmin - Epochs
garmin_epochs_info = """
  The `bump_garmin_epochs` table contains 15-minute aggregated summaries of physical activity and motion intensity 
  from Garmin smartwatches.
  Frequency: every 15 minutes.
  - active_time_in_seconds: Portion of the monitoring period (in seconds) in which the device wearer was active 
    for this activity type. The sum of active times for all epochs of the same start time should equal the duration.
  - active_kilocalories: Calories burned through activity during the epoch, excluding those from basal metabolism (BMR).
  - met: Metabolic Equivalent of Task (MET) value for the active time of the epoch, representing relative exercise intensity.
  - intensity: Qualitative classification of the epoch’s activity level.
"""

# Garmin - Pulse Ox
garmin_pulse_ox_info = """
  The `bump_garmin_pulse_ox` table contains oxygen saturation (SpO2) summaries collected from Garmin smartwatches. 
  Frequency: daily summary windows; may also include on-demand readings. `duration_in_seconds` is row-level, while `time_offset_spo2_values` is the within-window series.
  - time_offset_spo2_values: A mapping of time offsets (in seconds from the start time) to SpO₂ measurements. 
    Each value represents the percentage of blood oxygen saturation for that minute.
    Example: {"300": 13.89, "420": 14.06, "540": 14.05, "600": 14.06, "660": 13.95}
  - on_demand: A Boolean to show whether this pulse ox summary represents an on-demand reading or an averaged acclimation reading.
"""

# Garmin - Respiration
garmin_respiration_info = """ 
  The `bump_garmin_respiration` table contains respiration rate (breaths per minute) summaries collected from Garmin smartwatches. 
  Frequency: short summary windows within a day. `event_date` is day-level, while `time_offset_epoch_to_breaths` is the within-window series and is often roughly minute-level in the schema examples.
  - time_offset_epoch_to_breaths: A mapping of time offsets (in seconds from start_time_in_seconds) to respiration rate values. 
    Each value represents the number of breaths per minute recorded at that offset.
    Example: {"300": 13.89, "420": 14.06, "540": 14.05, "600": 14.06, "660": 13.95}
"""

# Garmin - Sleep
garmin_sleep_info = """ 
  The `bump_garmin_sleep` table contains nightly sleep summaries from Garmin smartwatches.
  Frequency: nightly. Sleep-stage durations are nightly totals, while `time_offset_sleep_respiration` and `time_offset_sleep_spo2` are within-sleep series.
  - unmeasurable_sleep_in_seconds: Time in seconds that the sleep level of the user could not be measured. This may or may not correspond to off-wrist time.
  - sleep_levels_map: A map of sleep level time ranges, currently deep, light, and awake. Time ranges are represented as unix timestamps in seconds.
    Example: {"rem": [{"endTimeInSeconds": 1615096380, "startTimeInSeconds": 1615093800}, {"endTimeInSeconds": 1615114680, "startTimeInSeconds": 1615112280}], 
              "deep": [{"endTimeInSeconds": 1615091760, "startTimeInSeconds": 1615089360}], 
              "awake": [{"endTimeInSeconds": 1615116540, "startTimeInSeconds": 1615116480}], 
              "light": [{"endTimeInSeconds": 1615089360, "startTimeInSeconds": 1615086480}, {"endTimeInSeconds": 1615092000, "startTimeInSeconds": 1615091760}, {"endTimeInSeconds": 1615093800, "startTimeInSeconds": 1615093320}]}
  - time_offset_sleep_respiration: Mapping of time offsets (in seconds from sleep start) to respiration rate measurements (breaths per minute).
    Example: {"300": 13.89, "420": 14.06, "540": 14.05, "600": 14.06, "660": 13.95}
  - time_offset_sleep_spo2: Mapping of time offsets (in seconds from sleep start) to SpO₂ readings, available only if the user’s device is SpO2-enabled.
    Example: {"300": 13.89, "420": 14.06, "540": 14.05, "600": 14.06, "660": 13.95}
"""

# Garmin - Stress Details
garmin_stress_details_info = """ 
  The `bump_garmin_stress_details` table contains continuous stress and body battery readings collected from Garmin smartwatches. 
  Frequency: daily summary windows. `event_date` is day-level, while `time_offset_stress_level_values` and `time_offset_body_battery_values` are within-window series, often about every 3 minutes in the schema examples.
  - time_offset_stress_level_values: A mapping of time offsets (in seconds from start_time_in_seconds) to stress level scores (range: 1–100, -2 if not available).  
    Example: {"0": -2, "180": -2, "360": -2, "540": -2, "720": -2, "900": -2, "1080": -2}
  - time_offset_body_battery_values: A mapping of time offsets (in seconds from start_time_in_seconds) to Body Battery values.
    Example: {"0": -2, "180": -2, "360": -2, "540": -2, "720": -2, "900": -2, "1080": -2}
"""

###################################################### SinC Data Knowledge Base ######################################################

# App Activities - EMA
sinc_ema_info = """
  The `sinc_ema` table stores Self-Assessment Mannequin (SAM) ecological momentary assessment responses from the SinC study app.
  - Each row is one EMA item for a participant at a given `gathered_at` / `event_date`.
  - Use `datatype` to distinguish the prompt or measure collected (brain_capability, mood, energy, stress), and use `value` for the participant's response.
  - The four EMA prompts are:
    - Mood: 5 = Very Positive, 4 = Positive, 3 = Neutral, 2 = Negative, 1 = Very Negative
    - Energy: 5 = Very Energetic, 4 = Energetic, 3 = Neutral, 2 = Relaxed, 1 = Very Relaxed
    - Stress: 5 = Very Calm, 4 = Calm, 3 = Neutral, 2 = Stressed, 1 = Very Stressed
    - Brain capability: 5 = All Complex Tasks, 4 = Some Complex Tasks, 3 = Moderate Tasks, 2 = All Simple Tasks, 1 = Some Simple Tasks
  - Frequency: EMA is scheduled daily at a random time between 10 AM and 4 PM local time and expires after 2 hours.
  - A stress-triggered EMA add-on can also be sent after a very stressed response.
"""

# App Activities - Reaction Time
sinc_reaction_time_info = """
  The `sinc_reaction_time` table stores app-based reaction time task data.
  - Each row contains high-frequency device and motion telemetry captured during a reaction time task, including:
    - task start/end timestamps (`start_time`, `end_time`)
    - per-sample `timestamp` or `attempt_timestamp`
    - device orientation fields (`attitude_x`, `attitude_y`, `attitude_z`, `attitude_w`)
    - rotation, acceleration, gravity, and magnetic field measurements
    - `key`, which identifies the response or trial event
  - Frequency: Reaction time tasks were scheduled every other day.
  - The data dictionary notes that motion is recorded from the beginning of each attempt until threshold acceleration is reached, so the raw motion stream can support more precise reaction-time derivation than a simple threshold timestamp alone.
  - The task is tied to the EMA release time and expires after 2 hours, so analyses should usually aggregate by `user_id` and `event_date` or by task window.
"""

# App Activities - Surveys
sinc_surveys_info = """
  The `sinc_surveys` table stores app-delivered survey responses in long format.
  - Each row is one question-response pair with:
    - `title`: survey instrument name
    - `question`: prompt text
    - `answer`: participant response
    - `delivery`: delivery mode
    - `first_study_day`: study-day anchor for the survey instance
    - `created_at` / `event_date`: response timing
  - When the requested measures belong to a specific survey instrument, filter by its exact title whenever a matching title exists. Do not add a title filter when no applicable survey title can be identified; use question_text alone instead.
  - Based on the SinC app schedule, common survey cadences include:
    - daily: PRO-2 and nightly stress
    - weekly: PROMIS pain interference, PROMIS sleep disturbance, PROMIS sleep related impairment, fatigue (FSS), and PSS-4
    - biweekly: IBD-Control 8 and GAD-7
    - monthly: healthcare utilization, PROMIS emotional support 4a, PROMIS global 10, flu, and survey response inclination
    - every 90 days: Patient Manitoba Inflammatory Bowel Disease Index
  - Most app surveys are released at 6 PM local time and remain available until 9 PM the same day.
"""

sinc_surveys_adverse_childhood_events_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Adverse Childhood Events
    - frequency: once
    - first_study_day: 60
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Adverse Childhood Events survey captures early-life adversity exposures during the participant's first 18 years of life.
  Questions:
    1. While you were growing up, during your first 18 years of life: Did a household member go to prison?
    2. While you were growing up, during your first 18 years of life: Did a parent or other adult in the household often ... Push, grab, slap, or throw something at you? or Ever hit you so hard that you had marks or were injured?
    3. While you were growing up, during your first 18 years of life: Did a parent or other adult in the household often ... Swear at you, insult you, put you down, or humiliate you? or Act in a way that made you afraid that you might be physically hurt?
    4. While you were growing up, during your first 18 years of life: Did an adult or person at least 5 years older than you ever... Touch or fondle you or have you touch their body in a sexual way? or Try to or actually have oral, anal, or vaginal sex with you?
    5. While you were growing up, during your first 18 years of life: Did you live with anyone who was a problem drinker or alcoholic or who used street drugs?
    6. While you were growing up, during your first 18 years of life: Did you often feel that ... You didn't have enough to eat, had to wear dirty clothes, and had no one to protect you? or Your parents were too drunk or high to take care of you or take you to the doctor if you needed it?
    7. While you were growing up, during your first 18 years of life: Did you often feel that... No one in your family loved you or thought you were importan or special? or Your family didn't look out for each other, feel close to each other, or support each other?
    8. While you were growing up, during your first 18 years of life: Was a household member depressed or mentally ill or did a household member attempt suicide?
    9. While you were growing up, during your first 18 years of life: Was your mother or stepmother: Often pushed, grabbed, slapped, or had something thrown at her? or Sometimes or often kicked, bitten, hit with a fist, or hit with something hard? or Ever repeatedly hit over at least a few minutes or threatened with a gun or knife?
    10. While you were growing up, during your first 18 years of life: Were your parents ever separated or divorced?
"""

sinc_surveys_demographics_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Demographics
    - frequency: once
    - first_study_day: 2
    - question_type: date-input, numerical, pick-many, pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Demographics survey captures baseline participant demographics, education, language, insurance, smoking status, and household income.
  Questions:
    1. Are you fully fluent in any other languages?
    2. Are you of Hispanic or Latino origin?
    3. Do you have health insurance?
    4. Language(s) fully fluent - choose all that apply
    5. Number of people supported by this income, including you:
    6. Select your date of birth
    7. Select your ethnicity
    8. Select your gender
    9. Total years of education:
    10. What is the highest level of education you have completed?
    11. What is your smoking status?
    12. Which of the following best describes the total annual income of all members of your household?
"""

sinc_surveys_demographics_part_2_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Demographics Part 2
    - frequency: once
    - first_study_day: 2
    - question_type: numerical, pick-many, pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Demographics Part 2 survey captures baseline social determinants of health such as housing, work, transportation, household composition, and social connection. This aligns with the extended demographics / SDH survey.
  Questions:
    1. Are you worried about losing your housing?
    2. Describe the individual(s) with whom you live (Choose all that apply)
    3. Do you usually work 35 hours or more per week?
    4. Has lack of transportation kept you from medical appointments, meetings, work, or from getting things needed for daily living? Check all that apply
    5. How many family members, including yourself, do you currently live with?
    6. How often do you see or talk to people that you care about and feel close to? (For example: talking to friends on the phone, visiting friends or family, going to church or club meetings)
    7. In the past year, have you or any family members you live with been unable to get any of the following when it was really needed? Check all that apply.
    8. What is your current work situation?
    9. What is your housing situation today?
"""

sinc_surveys_emotional_support_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Emotional Support
    - frequency: monthly
    - first_study_day: 4
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Emotional Support survey is a PROMIS Emotional Support 4a instrument about perceived availability of supportive relationships.
  Questions:
    1. I have someone to confide in or talk to about myself or my problems.
    2. I have someone to talk with when I have a bad day.
    3. I have someone who makes me feel appreciated.
    4. I have someone who will listen to me when I need to talk.
"""

sinc_emotional_support_score_calculation = """
To calculate the Emotional Support score in SinC:
  - Use the `sinc_surveys` table with `title = 'Emotional Support'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Score the four items using:
    - `Never` = 1
    - `Rarely` = 2
    - `Sometimes` = 3
    - `Usually` = 4
    - `Always` = 5
  - Sum the four item scores for a total emotional support score.
  - Higher scores indicate greater perceived emotional support.
"""

sinc_surveys_family_medical_history_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Family Medical History
    - frequency: once
    - first_study_day: 3
    - question_type: numerical, pick-many, pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Family Medical History survey captures family history of inflammatory bowel disease and related ancestry information.
  Questions:
    1. Are any of your 1st degree family members (biological parents, full siblings, children) affected by...(select all that apply)
    2. Do you consider yourself to be of Jewish Ancestry?
    3. Has your biological father ever been diagnosed with: (select one)
    4. Has your biological mother ever been diagnosed with: (select one)
    5. Is your maternal grandfather Ashkenazi Jewish?
    6. Is your maternal grandfather Jewish?
    7. Is your maternal grandmother Ashkenazi Jewish?
    8. Is your maternal grandmother Jewish?
    9. Is your paternal grandfather Ashkenazi Jewish?
    10. Is your paternal grandfather Jewish?
    11. Is your paternal grandmother Ashkenazi Jewish?
    12. Is your paternal grandmother Jewish?
    13. Number of children with unknown diagnosis:
    14. Number of children with: Crohn's disease
    15. Number of children with: Inflammatory Bowel Disease affected (type unclear)
    16. Number of children with: Ulcerative Colitis/Ischemic Colitis
    17. Number of siblings with unknown diagnosis:
    18. Number of siblings with: Crohn's disease
    19. Number of siblings with: Inflammatory Bowel Disease affected (type unclear)
    20. Number of siblings with: Ulcerative Colitis/Ischemic Colitis
    21. Number of unaffected children
    22. Number of unaffected siblings
"""

sinc_surveys_fatigue_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Fatigue
    - frequency: weekly
    - first_study_day: 4
    - question_type: scale
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Fatigue survey is a weekly fatigue severity questionnaire assessing how fatigue affects function, responsibilities, motivation, and daily life over the past week. This corresponds to the FSS fatigue survey.
  Questions:
    1. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: I am easily fatigued
    2. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: Fatigue causes frequent problems for me
    3. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: Fatigue interferes with carrying out certain duties and responsibilities
    4. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: Fatigue interferes with my physical functioning
    5. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: Fatigue interferes with my work, family, or social life
    6. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: Fatigue is among my three most disabling symptoms
    7. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: My exercise brings on my fatigue
    8. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: My fatigue prevents sustained physical functioning
    9. On a scale of 1-7(1=disagree and 7=agree), during the past week I have found that: My motivation is lower when I am fatigued
"""

sinc_fatigue_score_calculation = """
To calculate the Fatigue score in SinC:
  - Use the `sinc_surveys` table with `title = 'Fatigue'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Extract the 1-7 response from each of the 9 fatigue items:
    - `1` = strongly disagree / lowest fatigue endorsement
    - `2` = disagree
    - `3` = slightly disagree
    - `4` = neutral
    - `5` = slightly agree
    - `6` = agree
    - `7` = strongly agree / highest fatigue endorsement
  - Sum the 9 weekly fatigue items for a total fatigue score.
  - Higher scores indicate more severe fatigue.
"""

sinc_fatigue_score_classification = """
To interpret the SinC Fatigue score:
  - The SinC weekly fatigue instrument corresponds to an FSS-style questionnaire.
  - Higher total scores indicate greater fatigue burden.
  - If a label is needed, report the total score and describe higher values as worse fatigue rather than assuming a stored category.
"""

sinc_surveys_flu_covid_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Flu/COVID
    - frequency: monthly
    - first_study_day: 7
    - question_type: pick-many, pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Flu/COVID survey captures recent flu-like illness, flu testing, vaccination, and illness-related care or symptoms.
  Questions:
    1. Did a medical professional confirm that you have had the flu in the last month? (e.g. via nasal swab or blood test)
    2. Did you get a flu vaccine this year? (either a shot or nasal spray)
    3. During this illness did you: (select all that apply)
    4. In the last 30 days, did you have a flu-like illness with high fever, muscle aches, and cough or sore throat?
    5. Why didn't you get a flu vaccine this year?
"""

sinc_surveys_gad7_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Generalized Anxiety Disorder
    - frequency: biweekly
    - first_study_day: 9
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Generalized Anxiety Disorder survey corresponds to the GAD-7 instrument with an additional difficulty item.
  Questions:
    1. If you checked off any problems, how difficult have these made it for you to do your work, take care of things at home, or get along with other people? (Select one)
    2. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Becoming easily annoyed or irritable
    3. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Being so restless that it's hard to sit still
    4. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Feeling afraid as if something awful might happen
    5. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Feeling nervous, anxious, or on edge
    6. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Not being able to stop or control worrying
    7. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Trouble relaxing
    8. Over the last 2 weeks, how often have you been bothered by the following problems? (Select one) Worrying too much about different things
"""

sinc_gad7_score_calculation = """
To calculate the GAD-7 anxiety score in SinC:
  - Use the `sinc_surveys` table with `title = 'Generalized Anxiety Disorder'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Score only the seven anxiety symptom items; exclude the functional difficulty item asking how difficult the problems made work, home care, or relationships.
  - Score symptom responses using:
    - `Not at all` = 0
    - `Several days` = 1
    - `More than half the days` = 2
    - `Nearly every day` = 3
  - Sum the seven symptom item scores for a total GAD-7 score.
  - Higher scores indicate greater anxiety symptom severity.
"""

sinc_surveys_global_ten_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Global Ten
    - frequency: monthly
    - first_study_day: 3
    - question_type: pick-one, scale
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Global Ten survey corresponds to PROMIS Global-10, covering overall physical, mental, and social health as well as fatigue and pain.
  Questions:
    1. In general would you say your health is:.
    2. In general, how would you rate your mental health, including your mood and your ability to think?
    3. In general, how would you rate your physical health?
    4. In general, how would you rate your satisfaction with your social activities and relationships?
    5. In general, please rate how well you carry out your usual social activities and roles. (This includes activities at home, at work and in your community, and responsibilities as a parent, child, spouse, employee, friend, etc.)
    6. In general, would you say your quality of life is:
    7. In the past 7 days: How often have you been bothered by emotional problems such as feeling anxious, depressed, or irritable?
    8. In the past 7 days: How would you rate your fatigue on average?
    9. In the past 7 days: How would you rate your pain on average ( 1 being no pain and 10 being worst pain imaginable)?
    10. To what extent are you able to carry out your everyday physical activities such as walking, climbing stairs, carrying groceries, or moving a chair?
"""

sinc_global_ten_score_calculation = """
To calculate PROMIS Global-10 style summary scores in SinC:
  - Use the `sinc_surveys` table with `title = 'Global Ten'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Derive the Global Physical Health (GPH) and Global Mental Health (GMH) raw scores.
  - Score general health, quality of life, physical health, mental health, social satisfaction, and social roles items using:
    - `Poor` = 1
    - `Fair` = 2
    - `Good` = 3
    - `Very good` = 4
    - `Excellent` = 5
  - Score the physical activities item using:
    - `Not at all` = 1
    - `A little` = 2
    - `Moderately` = 3
    - `Mostly` = 4
    - `Completely` = 5
  - Score the emotional problems item so fewer problems map to higher score:
    - `Always` = 1
    - `Often` = 2
    - `Sometimes` = 3
    - `Rarely` = 4
    - `Never` = 5
  - Score the fatigue item so lower fatigue maps to higher score:
    - `Very severe` = 1
    - `Severe` = 2
    - `Moderate` = 3
    - `Mild` = 4
    - `None` = 5
  - Recode the pain item so lower pain maps to higher score:
    - `0` = 5
    - `1-3` = 4
    - `4-6` = 3
    - `7-9` = 2
    - `10` = 1
  - Compute:
    - `GPH_raw = physical_health + social_roles + physical_activities + recoded_pain`
    - `GMH_raw = quality_of_life + mental_health + social_satisfaction + emotional_problems`
"""

sinc_surveys_healthcare_utilization_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Healthcare Utilization
    - frequency: biweekly
    - first_study_day: 17
    - question_type: numerical
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Healthcare Utilization survey captures recent IBD-related hospitalizations, planned and unplanned admissions, emergency visits, and total admission days over the last month.
  Questions:
    1. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last month (IBD-related) for a planned admission?
    2. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last month (IBD-related) for an unplanned admission?
    3. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last month (IBD-related)?
    4. How many times have you visited an emergency room or emergency department within the last month?
    5. What is the total length of all, (all-cause) admissions and readmission within the last month (in days)?
"""

sinc_surveys_healthcare_utilization_baseline_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Healthcare Utilization (One-time)
    - frequency: once
    - first_study_day: 3
    - question_type: numerical
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Healthcare Utilization (One-time) survey captures the same utilization concepts as the recurring survey but over the last 12 months at baseline.
  Questions:
    1. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last 12 months (IBD-related) for a planned admission?
    2. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last 12 months (IBD-related) for an unplanned admission?
    3. How many times have you been admitted (requiring at least an overnight stay) to a hospital/acute care facility in the last 12 months (IBD-related)?
    4. How many times have you visited an emergency room or emergency department within the last 12 months?
    5. What is the total length of all, (all-cause) admissions and readmission within the last 12 months (in days)?
"""

sinc_surveys_ibd_control_info = """
  The `sinc_surveys` table stores survey responses:
    - title: IBD Control
    - frequency: biweekly
    - first_study_day: 2
    - question_type: pick-one, range
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The IBD Control survey captures perceived disease control, treatment usefulness, symptom burden, fatigue, nocturnal symptoms, and change in bowel symptoms over the past 2 weeks.
  Questions:
    1. Do you believe that: Your IBD has been well controlled in the past two weeks?
    2. Do you believe that: Your current treatment is useful in controlling your IBD?
    3. How would you rate the OVERALL control of your IBD in the past two weeks?
    4. In the past 2 weeks, did you: Feel anxious or depressed because of your IBD?
    5. In the past 2 weeks, did you: Miss any planned activities because of IBD? (e.g. attending school/college, going to work or a social event)
    6. In the past 2 weeks, did you: Often feel lacking in energy (or fatigued)? (By 'often' we mean more than half of the time)
    7. In the past 2 weeks, did you: Suffer from significant pain or discomfort?
    8. In the past 2 weeks, did you: Think you needed a change to your treatment?
    9. In the past 2 weeks, did you: Wake up at night because of symptoms of IBD?
    10. Over the past 2 weeks, have your bowel symptoms been getting worse, getting better, or not changed?
"""

sinc_surveys_ibd_daily_info = """
  The `sinc_surveys` table stores survey responses:
    - title: IBD Daily
    - frequency: daily
    - first_study_day: 2
    - question_type: numerical, pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The IBD Daily survey corresponds to the daily PRO-2 style symptom questions for stool frequency and abdominal pain.
  Questions:
    1. How many liquid or very soft stools did you have today?
    2. Please rate your abdominal pain today.
"""

sinc_surveys_life_events_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Life Events
    - frequency: once
    - first_study_day: 90
    - question_type: pick-many, pick-one, text-input
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Life Events survey captures major stressful or meaningful life events during the prior 12 months and may include appraisal or follow-up context questions.
  Questions:
    1. Did someone you were close to die during the last 12 months?
    2. Has your house been broken into and/or burgled during the last 12 months?
    3. Have you broken off an engagement to be married, ended an intimate relationship or separated or divorced from your spouse during the last 12 months?
    4. Have you had a pet (animal) to whom you were attached die, or get lost, or did you have to give away during the last 12 months?
    5. Have you had any important relationship, for example, with your spouse, a close friend, your boss, or a family member become significantly worse during the last 12 months?
    6. Have you moved during the last 12 months?
    7. Have you or your spouse/partner lost or changed jobs or been involuntarily unemployed during the last 12 months?
    8. Have you or your spouse/partner or other member of your immediate family been assaulted or mugged during the last 12 months?
    9. Have you, a very close friend, or close family member been hospitalized for a serious (life-threatening) illness during the last 12 months?
    10. How would you rate your feelings about breaking up?
    11. How would you rate your feelings about leaving your job? (or your spouse leaving his/her job)
    12. If yes to 10: How would you rate your feelings about this event?
    13. Other than the events we have already asked about, have any other important things happened to you or to a very close friend or close family member in the last 12 months that made that period significantly different from a typical year?
    14. Overall, would you say that your moving was a good or bad experience?
    15. Please list this event and include more than one if applicable
    16. Who?
"""

sinc_surveys_manitoba_ibd_index_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Manitoba IBD Index
    - frequency: once
    - first_study_day: 4
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Manitoba IBD Index survey captures a single self-reported summary of disease activity over the past 6 months.
  Questions:
    1. In the past 6 months my disease has been... (select one)
"""

sinc_surveys_nightly_stress_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Nightly Stress
    - frequency: daily
    - first_study_day: 2
    - question_type: pick-many, pick-one, range
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Nightly Stress survey captures whether the participant felt very stressed during the day, when stress occurred, how long it lasted, and current perceived control.
  Questions:
    1. At any time during today did you feel very stressed or extremely stressed?
    2. In total how long did these feelings of stress last?
    3. Right now do you feel in control?
    4. When did these feelings of stress occur? (select all that apply)
"""

sinc_surveys_pain_interference_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Pain Interference
    - frequency: weekly
    - first_study_day: 5
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Pain Interference survey is a PROMIS pain interference instrument about the impact of pain on activities, chores, social participation, and discouragement over the past 7 days.
  Questions:
    1. In the past 7 days: How much did pain interfere with work around the home?
    2. In the past 7 days: How much did pain interfere with your ability to participate in social activities?
    3. In the past 7 days: How much did pain interfere with your day to day activities?
    4. In the past 7 days: How much did pain interfere with your household chores?
    5. In the past 7 days: How often did pain make you feel discouraged?
"""

sinc_pain_interference_score_calculation = """
To calculate the Pain Interference score in SinC:
  - Use the `sinc_surveys` table with `title = 'Pain Interference'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Score the four activity interference items from low interference to high interference:
    - `Not at all` = 1
    - `A little bit` = 2
    - `Somewhat` = 3
    - `Quite a bit` = 4
    - `Very much` = 5
  - Score the discouraged item from low frequency to high frequency:
    - `Never` = 1
    - `Rarely` = 2
    - `Sometimes` = 3
    - `Often` or `Usually` = 4
    - `Always` = 5
  - Sum the five item scores for a total pain interference score.
  - Higher scores indicate greater pain interference.
"""

sinc_surveys_perceived_stress_scale_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Perceived Stress Scale
    - frequency: weekly
    - first_study_day: 8
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Perceived Stress Scale survey is a short weekly stress instrument about control, coping, and overload during the last week. This corresponds to the PSS-4.
  Questions:
    1. In the last week, how often have you felt confident about your ability to handle your personal problems?
    2. In the last week, how often have you felt difficulties were piling up so high that you could not overcome them?
    3. In the last week, how often have you felt that things were going your way?
    4. In the last week, how often have you felt that you were unable to control the important things in your life?
"""

sinc_perceived_stress_score_calculation = """
To calculate the Perceived Stress Scale (PSS-4) score in SinC:
  - Use the `sinc_surveys` table with `title = 'Perceived Stress Scale'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Score the two positively worded / reverse-coded items:
    - `confident about your ability to handle your personal problems`
    - `things were going your way`
  - Reverse-code those positive items using:
    - `Never` = 4
    - `Almost Never` = 3
    - `Sometimes` = 2
    - `Fairly Often` = 1
    - `Very Often` = 0
  - Score the two negatively worded items:
    - `difficulties were piling up so high that you could not overcome them`
    - `unable to control the important things in your life`
  - Score those negative items using:
    - `Never` = 0
    - `Almost Never` = 1
    - `Sometimes` = 2
    - `Fairly Often` = 3
    - `Very Often` = 4
  - Sum the four item scores for a total perceived stress score.
  - Do not use `pro2_score` as the perceived stress score.
"""

sinc_surveys_personality_inventory_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Personality Inventory
    - frequency: once
    - first_study_day: 90
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Personality Inventory survey corresponds to the Ten-Item Personality Inventory (TIPI) and captures the Big Five personality dimensions.
  Questions:
    1. I see myself as anxious, easily upset
    2. I see myself as calm, emotionally stable
    3. I see myself as conventional, uncreative
    4. I see myself as critical, quarrelsome
    5. I see myself as dependable, self-disciplined
    6. I see myself as disorganized, careless
    7. I see myself as extroverted, enthusiastic
    8. I see myself as open to new experiences, complex
    9. I see myself as reserved, quiet
    10. I see myself as sympathetic, warm
"""

sinc_tipi_score_calculation = """
To calculate TIPI personality trait scores in SinC:
  - Use the `sinc_surveys` table with `title = 'Personality Inventory'`.
  - Group by `user_id` because this instrument is collected once.
  - SinC Personality Inventory answers are agreement text labels. Always map `answer` to the 1-7 agreement scale before computing scores:
    - `Disagree strongly` = 1
    - `Disagree moderately` = 2
    - `Disagree a little` = 3
    - `Neither agree nor disagree` = 4
    - `Agree a little` = 5
    - `Agree moderately` = 6
    - `Agree strongly` = 7
  - Compute the Big Five traits using reverse coding where required:
    - `Extraversion = (Q7 + (8 - Q9)) / 2`
    - `Agreeableness = ((8 - Q4) + Q10) / 2`
    - `Conscientiousness = (Q5 + (8 - Q6)) / 2`
    - `Emotional Stability = ((8 - Q1) + Q2) / 2`
    - `Openness to Experience = (Q8 + (8 - Q3)) / 2`
  - Higher trait scores indicate stronger presence of that personality dimension.
"""

sinc_surveys_sleep_related_impairment_info = """
  The `sinc_surveys` table stores survey responses:
    - title: Sleep Related Impairment
    - frequency: weekly
    - first_study_day: 7
    - question_type: pick-one
    - `question`: the exact survey question text
    - `answer`: the submitted answer

  The Sleep Related Impairment survey corresponds to a PROMIS sleep-related impairment instrument about daytime sleepiness, fatigue, concentration, irritability, and sleep quality over the past 7 days.
  Questions:
    1. In the past 7 days: I felt alert when I woke up.
    2. In the past 7 days: I felt irritable because of poor sleep.
    3. In the past 7 days: I felt tired.
    4. In the past 7 days: I had a hard time concentrating because of poor sleep.
    5. In the past 7 days: I had a hard time getting things done because I was sleepy.
    6. In the past 7 days: I had problems during the day because of poor sleep.
    7. In the past 7 days: I had trouble staying awake during the day.
    8. In the past 7 days: I was sleepy during the daytime.
    9. In the past 7 days: My sleep quality was...
"""

sinc_sleep_related_impairment_score_calculation = """
To calculate the Sleep Related Impairment score in SinC:
  - Use the `sinc_surveys` table with `title = 'Sleep Related Impairment'`.
  - Group by both `user_id` and `event_date` to calculate one score per assessment.
  - Score the impairment/frequency items on the 1-5 response scale:
    - `Not at all` or `Never` = 1
    - `A little bit` or `Rarely` = 2
    - `Somewhat` or `Sometimes` = 3
    - `Quite a bit` or `Often` = 4
    - `Very much` or `Always` = 5
  - If answers are stored as numeric strings from 1 to 5, cast the answer directly to a number.
  - Reverse-score the positively worded alertness item `I felt alert when I woke up.`:
    - text response score becomes `6 - score`
    - numeric response score becomes `6 - CAST(answer AS INTEGER)`
  - Sum all item scores for a total sleep-related impairment score.
  - Higher scores indicate greater impairment.
"""

# App Activities - Trail Making
sinc_trail_making_info = """
  The `sinc_trail_making` table stores app-based Trail Making Test data.
  - Each row represents a task event or tap with:
    - `start_time` and `end_time`
    - `timestamp`
    - `index` for the target sequence
    - `incorrect` and `number_of_errors` for performance tracking
    - `key` for the task event
  - Trail Making was scheduled every other day.
  - The study design references the standard ResearchKit Trail Making active task.
  - The task is tied to the EMA release time and expires after 2 hours, so summarize at the user-date or assessment-session level for most analyses.
"""

# Bodyport
sinc_bodyport_info = """
  The `sinc_bodyport` table contains measurements from the Bodyport cardiac scale.
  - Important measures include:
    - `weight` / `weight_kg`
    - `bmi_kg_m2`
    - `heart_rate` / `hr`
    - `hrv`
    - `peripheral_fluid` / `pfc`
    - impedance measures (`impedance_mag_1_ohms`, `impedance_phase_1_degs`, `impedance_high_freq_mag`, `impedance_high_freq_phase`)
    - balance and sway measures (`balance_score`, `backend_sway_area_mm2`, `sway_velocity_mm_sec`, `sway_area`)
    - cardiac timing features such as `pep`, `pat`, `ptt`, and `lvet`
  - Bodyport measurements were intended daily, with additional measurements before and after the 2-minute walk test.
  - Prefer the normalized or explicitly named columns when duplicates exist, for example `heart_rate` over `hr`, `peripheral_fluid` over `pfc`, and `bmi_kg_m2` for BMI.
"""

# CamCog - EBT
sinc_camcog_ebt_info = """
  The `sinc_camcog_ebt` table stores SinC Emotional Bias Task (EBT) results from the app-based cognition battery.
  - The main derived metrics include:
    - `EBTRTMDT`: median reaction time across all assessed trials
    - `EBTRTMNT`: mean reaction time across all assessed trials
    - `EBTRTSDT`: standard deviation of reaction time across all assessed trials
    - `EBTRTMDH`: median reaction time for `Happy` selections
    - `EBTRTMNH`: mean reaction time for `Happy` selections
    - `EBTRTSDH`: standard deviation of reaction time for `Happy` selections
    - `EBTRTMDS`: median reaction time for `Sad` selections
    - `EBTRTMNS`: mean reaction time for `Sad` selections
    - `EBTRTSDS`: standard deviation of reaction time for `Sad` selections
    - `EBTRCH`: count of `Happy` responses
    - `EBTRCS`: count of `Sad` responses
    - `EBTBP`: bias point
  - The table also includes session timing fields such as `general_start_time`, `start_time`, `end_time`, plus `subject_id`, `visit_id`, and `task`.
  - In the SinC app schedule, EBT was planned weekly.
"""

# IBDOC
sinc_ibdoc_info = """
  The `sinc_ibdoc` table stores IBDoc fecal calprotectin test results.
  - This table represents home fecal calprotectin measurements collected using IBDoc take-home kits.
  - Frequency: kits were planned every 6 weeks.
  - Key fields:
    - `date`: test date
    - `test_name`: assay or test label
    - `result_value`: measured fecal calprotectin value
    - `result_classification`: reported interpretation category
    - `batch`: kit or processing batch identifier
    - `patient_id`: source patient identifier from the test record
  - Use `result_value` when the question asks for calprotectin level, and `result_classification` when the question asks for the reported category.
"""

# Metadata
sinc_metadata_info = """
  The `sinc_user_site_mapping` table is a metadata lookup that maps each `user_id` to a study `site`.
  - Use this table when a question asks for site-level comparisons such as Mount Sinai vs Oxford.
  - This table should usually be joined on `user_id`.
"""

# Oura - Activity
sinc_oura_activity_info = """
  The `sinc_oura_activity` table contains daily Oura activity summaries.
  - `score` is the overall Oura activity score.
  - Useful field descriptions include:
    - `daily_movement`: walking-equivalent distance representing total daily activity
    - `non_wear`: minutes the ring was not worn; useful as a data-quality proxy
    - `rest`: minutes spent resting, such as sleeping or lying down
    - `inactivity_alerts`: count of continuous inactive periods of 60 minutes or more
    - `low`, `medium`, `high`: minutes spent in low-, medium-, or high-intensity activity
    - `total`: total low + medium + high activity minutes
    - `cal_active`: activity-related energy expenditure in kilocalories
    - `met_1min`: average MET level for each minute starting from 4 AM local time
    - `met_min_low`, `met_min_medium`, `met_min_high`: total MET-minutes accumulated within each intensity band
    - `class_5min`: 5-minute activity-state string where `0` = non-wear, `1` = rest, `2` = inactive, `3` = low, `4` = medium, `5` = high
  - Score contributors include:
    - `score_stay_active`
    - `score_move_every_hour`
    - `score_meet_daily_targets`
    - `score_training_frequency`
    - `score_training_volume`
    - `score_recovery_time`
  - These contributor scores reflect inactivity avoidance, hourly movement, target attainment, exercise frequency, exercise volume, and recovery time.
  - Use `summary_date` or `event_date` as the daily grain.
"""

# Oura - Readiness
sinc_oura_readiness_info = """
  The `sinc_oura_readiness` table contains daily Oura readiness summaries.
  - `score` is the overall readiness score.
  - Scores above about 85 indicate strong recovery, while scores below about 70 suggest that one or more readiness contributors are outside the user's normal or recommended range.
  - Component scores include:
    - `score_previous_night`
    - `score_sleep_balance`
    - `score_previous_day`
    - `score_activity_balance`
    - `score_resting_hr`
    - `score_recovery_index`
    - `score_temperature`
    - `score_hrv_balance`
  - `period_id` identifies which sleep period the readiness calculation is tied to.
  - `score_hrv_balance` may be missing for earlier dates before HRV balance was included in readiness scoring.
  - Use `summary_date` or `event_date` as the daily grain.
"""

# Oura - Sleep
sinc_oura_sleep_info = """
  The `sinc_oura_sleep` table contains nightly Oura sleep summaries.
  - `score` is the overall sleep score.
  - Main sleep-stage and duration fields include `awake`, `light`, `deep`, `rem`, `duration`, and `total`.
  - Useful field descriptions include:
    - `bedtime_start`, `bedtime_end`: start and end of the sleep period
    - `duration`: total sleep-period duration (`bedtime_end - bedtime_start`)
    - `hr_average`: average heart rate during sleep
    - `hr_lowest`: lowest 5-minute sliding-average heart rate during sleep
    - `efficiency`: percentage of the sleep period spent asleep
    - `onset_latency`: time from bedtime start to first sustained sleep
    - `midpoint_time`: sleep midpoint timing used in alignment scoring
    - `restless`: percentage of sleep time with movement
    - `period_id`: index of the sleep period within the summary date
    - `hypnogram_5min`: 5-minute sleep-stage string where `1` = deep, `2` = light, `3` = REM, `4` = awake
    - `rmssd_5min`: 5-minute HRV series during the sleep period
  - Sleep periods can cross midnight; `summary_date` follows Oura's sleep-summary convention rather than simply the wake date shown in the mobile app.
  - Component scores include `score_total`, `score_rem`, `score_deep`, `score_efficiency`, `score_latency`, `score_disturbances`, and `score_alignment`.
  - `score_disturbances` reflects wake-up count, got-up count, and restless sleep together.
  - Use `summary_date` or `event_date` as the nightly grain.
"""

# Pooply - Events
sinc_pooply_events_info = """
  The `sinc_pooply_events` table stores stool event entries from the Pooply by HealthMode app.
  - Each row is one stool event with timestamp `time` and daily anchor `event_date`.
  - Symptom and stool-characteristic fields include:
    - binary flags: `blood`, `fat`, `mucus`, `flatulence`
    - ordinal or coded attributes: `shape`, `color`, `size`, `float`, `pain`, `urgency`
  - Pooply was designed as patient-reported per-bowel-movement data, so each row is one BM-level record.
  - Count stool events by counting rows in this table, typically grouped by `user_id` and `event_date`.
"""

# Pooply - No Stool Days
sinc_pooply_no_stool_days_info = """
  The `sinc_pooply_no_stool_days` table records days when a participant explicitly reported no stool events.
  - Each row indicates a no-stool day for a participant on `date` / `event_date`.
  - Do not infer a no-stool day from the absence of rows in `sinc_pooply_events`; use this table when the question is specifically about reported no-stool days.
"""

# Survey Monkey - Chart Review Medical History
sinc_chart_review_medical_history_info = """
  The `sinc_chart_review_medical_history` table stores chart-review question and answer pairs in long format.
  - Each row contains `question` and `answer` text collected during chart review.
  - This table includes Crohn's phenotype and dysplasia questions with structured answer options.
  - Key structured questions include:
    - `Crohn's disease behavior: (select one)` with answers:
      - `B1 (nonstricturing, nonpenetrating)`
      - `B2 (stricturing)`
      - `B3 (penetrating)`
    - `Crohn's disease location (select one)` with answers:
      - `L1 Ileum`
      - `L2 colon`
      - `L3 ileocolon`
      - `L4 upper GI`
    - `Does the patient have perianal and rectovaginal fistula?` with `Yes` / `No`
    - `Has the patient ever been diagnosed with colonic dysplasia?` with `Yes` / `No`
    - `If yes to colonic dysplasia: select one` with:
      - `Low grade`
      - `High grade`
      - `Indefinite`
  - There is also a multi-select disease extent / phenotype question whose answer choices include small bowel involvement, isolated upper GI involvement, colonic involvement, and complex perianal disease.
  - For phenotype questions, filter using distinctive `question` text and return the raw `answer`.
"""

# Survey Monkey - Chart Review Medications
sinc_chart_review_medications_info = """
  The `sinc_chart_review_medications` table stores chart-reviewed medication question and answer pairs.
  - Each row is a medication-related item captured as `question` plus `answer`.
  - This table uses a repeating medication template:
    - `Has the patient ever taken <medication>` with answers: `Yes`, `No`, `Don't know`
    - `If yes ... Is the patient currently taking this medication?` with answers: `Yes`, `No`
    - `If no ... What was the reason for stopping this medication?` with answers:
      - `Side effect`
      - `Loss of response`
      - `Non responsive`
      - `Completed`
      - `Other/unknown`
  - Medication families represented include corticosteroids, budesonide, 5-ASA therapies, antibiotics, immunomodulators, biologics, nutritional therapies, supplements, hormonal therapies, pain medications, and complementary therapies.
  - Some side-effect follow-up questions also use `Yes` / `No`, such as whether 6-MP / azathioprine was stopped due to low white blood cell count or pancreatitis.
  - Use raw `answer` values to identify whether a participant was receiving a medication or medication class at the reviewed visit.
"""

# Survey Monkey - Chart Review Vitalalabs
sinc_chart_review_vitalslabs_info = """
  The `sinc_chart_review_vitalslabs` table stores chart-reviewed vitals and lab values in long format.
  - Each row contains one `question` / `answer` pair for a study visit or clinically proximate lab.
  - The main question types are:
    - open-ended numeric entries for:
      - weight
      - systolic and diastolic blood pressure
      - temperature
      - heart rate
      - hemoglobin / hematocrit result
      - CRP result
      - fecal calprotectin result
    - `Yes` / `No` collection flags for whether hemoglobin / hematocrit, CRP, or fecal calprotectin were collected within +/- 6 weeks of the clinic visit
  - `date_of_study_visit` anchors the visit date, while some labs may be opportunistic around the visit window.
"""

# Survey Monkey - Check In Survey
sinc_check_in_survey_info = """
  The `sinc_check_in_survey` table stores staff-administered check-in survey responses in long format.
  - Each row contains a `question` and free-text or categorical `answer`.
  - Key structured questions and answers include:
    - `Reason for Contact: (select all that apply)` with:
      - `Check in #1 / Onboarding Call`
      - `Follow-up check-in`
      - `Participant Initiated (e.g. tech or study participation support)`
      - `Other (please specify)`
    - `Was a scheduled check in missed?` with `Yes` / `No`
    - `Since I last spoke to you, have you noticed any changes in your Crohn's related symptoms?` with `Yes` / `No`
    - `Did your symptoms improve or get worse?` with `Improved` / `Worsened`
    - `What do you think was main the reason for this change in symptoms?` with:
      - `Change in medication`
      - `Change in diet`
      - `Change in lifestyle`
      - `Change in stress`
      - `I don't know`
      - `Other (please specify)`
    - stress-source follow-up with categories such as family / relationships, work, financial, significant event, multiple reasons, or other
    - event intensity with `Low`, `Moderate`, `Severe`, `Very severe`
    - several free-text or open-ended date / duration responses
  - This table is useful for operational or qualitative participant support questions rather than symptom scoring.
"""

# Survey Monkey - Extraintestinal Manifestations
extraintestinal_manifestations_info = """
    In the `sinc_extraintestinal_manifestations` table:
    - The `question` column only contains one prompt: "Have you had a diagnosis of... (select all that apply)".
    - The `answer` column records patient-reported diagnoses related to extraintestinal manifestations (EIMs). Each answer corresponds to one condition per row.
    - Selectable EIM options include:
      - Pyoderma Gangrenosum
      - Erythema Nodosum
      - Metastatic Crohn's Disease
      - Uveitis/iritis
      - Episcleritis / scleritis
      - Primary Sclerosing Cholangitis
      - Arthritis - small joints (hot swollen joints)
      - Arthritis - large joints (hot swollen joints)
      - Ankylosing Spondylitis
      - Sarcoiliitis
      - IBD - related mouth ulcers
      - Venous thrombosis
      - Arterial thrombosis
      - Kidney stones
      - Amyloidosis
    - Each patient may have multiple rows with different answers.
    - To find patients diagnosed with a specific condition, filter rows where `answer` matches the diagnosis.
"""

# Survey Monkey - Medications Monthly
sinc_medications_monthly_info = """
  The `sinc_medications_monthly` table stores monthly medication survey responses in long format.
  - Each row contains a medication-related `question` and `answer`.
  - This table uses the same medication template as chart-review medications:
    - `Has the patient ever taken <medication>` with answers: `Yes`, `No`, `Don't know`
    - `If yes ... Is the patient currently taking this medication?` with answers: `Yes`, `No`
    - `If no ... What was the reason for stopping this medication?` with answers:
      - `Side effect`
      - `Loss of response`
      - `Non responsive`
      - `Completed`
      - `Other/unknown`
  - Medication families represented include corticosteroids, budesonide, 5-ASA therapies, antibiotics, immunomodulators, biologics, nutritional therapies, supplements, hormonal therapies, pain medications, and complementary therapies.
  - Use raw answers to identify medication exposure, and group by `user_id` and `date` when summarizing a monthly medication assessment.
"""

# Survey Monkey - PHQ8
sinc_phq8_info = """
  The `sinc_phq_8` table stores PHQ-8 SurveyMonkey responses in long format.
  - Each row contains one `question` and one `answer`.
  - It contains 8 symptom items plus 1 functional difficulty item.
  - The 8 symptom items all use the same answer options:
    - `Not at all (0)`
    - `Several days (1)`
    - `More than half the days (2)`
    - `Nearly every day (3)`
  - Symptom questions include:
    - `Little interest or pleasure in doing things`
    - `Feeling down, depressed, or hopeless`
    - `Trouble falling or staying sleep, or sleeping too much`
    - `Feeling tired or having little energy`
    - `Poor appetite or overeating`
    - `Feeling bad about yourself...`
    - `Trouble concentrating on things...`
    - `Moving or speaking so slowly... or being so fidgety or restless...`
  - The functional difficulty question uses:
    - `Not difficult at all (0)`
    - `Somewhat difficult (1)`
    - `Very difficult (2)`
    - `Extremely difficult (3)`
"""

# Survey Monkey - Study Visit 1 Crohns Specifics
sinc_study_visit_1_crohns_specifics_info = """
  The `sinc_study_visit_1_crohns_specifics` table stores baseline Study Visit 1 Crohn's-specific question and answer pairs.
  - Each row contains one `question` and one `answer`.
  - This table includes:
    - date fields captured as `Date / Time` for diagnosis date, prior diagnosis date, last clinic visit, and diagnostic procedure dates
    - `Did patient ever have a different diagnosis?` with `Yes` / `No`
    - `If yes, what was the prior diagnosis?` with:
      - `Ulcerative Colitis`
      - `Indeterminate`
    - `How was the Crohn's diagnosis confirmed?` with:
      - `Endoscopy`
      - `Radiology`
      - `Other test (please specify)`
  - Use raw `answer` values and filter by distinctive `question` text.
"""

# Survey Monkey - Study Visit 1 Medical History
sinc_study_visit_1_medical_history_info = """
  The `sinc_study_visit_1_medical_history` table stores baseline Study Visit 1 medical history responses in long format.
  - Each row contains one `question` and one `answer`.
  - Structured questions include:
    - `Are you female?` with `Yes` / `No`
    - menstrual-period questions with `Yes` / `No`, open-ended age, and `Date / Time`
    - `Did your period stop because of...` with choices including natural menopause, surgery, chemotherapy / radiation, other medication / treatment / illness, birth control / HRT, never had a period, other, refused, and don't know
    - `Have you taken antibiotics at any time in the last 3 months?` with `Yes` / `No`
    - `Have you ever been told by a doctor that you had...` as a multi-select condition list including diabetes type 1, asthma, multiple sclerosis, systemic lupus erythematosus, delayed growth, celiac disease, rheumatoid arthritis, psoriasis, ankylosing spondylitis, osteopenia / osteoporosis, cancer, colon / rectal cancer, and pancreatitis
    - 6-MP / azathioprine adverse-event questions with `Yes` / `No`
  - Use raw answers and filter on distinctive `question` text for conditions, family history, or other baseline history items.
"""

# Survey Monkey - Study Visit 1 Surgical History
sinc_study_visit_1_surgical_history_info = """
  The `sinc_study_visit_1_surgical_history` table stores baseline Study Visit 1 surgical history responses.
  - Each row contains one `question` and one `answer`.
  - This table includes:
    - surgery count questions use `Open-Ended Response`
    - multiple surgery-date fields use `Date / Time`
    - `Was any perianal procedure ever performed?` and `Have you ever had a seton placed?` use `Yes` / `No`
    - `If yes: Select all that apply` includes:
      - `Abcess`
      - `Fistula`
      - `Stricture`
      - `Other (please specify)`
    - `Surgical Indication` uses:
      - `Medical Refractory`
      - `Complication of Disease`
      - `Dysplasia/ Cancer`
      - `Other (please specify)`
  - Answers are often numeric, date, or categorical; use raw `answer` values and filter by `question`.
"""

# PHQ-8 Scoring Questions and Classification
phq8_score_calculation = """
    The PHQ-8 (Patient Health Questionnaire-8) consists of 8 questions assessing depression symptoms.
    Depression score is equivalent to PHQ-8 score.
    - Extract the numeric scores from the `answer` column of the dataset-qualified `sinc_phq_8` table from schema, which contains responses in the format:
      - `"Not at all (0)"`
      - `"Several days (1)"`
      - `"More than half the days (2)"`
      - `"Nearly every day (3)"`
      Each question is scored as follows:
      - "Not at all (0)" = 0
      - "Several days (1)" = 1
      - "More than half the days (2)" = 2
      - "Nearly every day (3)" = 3
      - Any other value = 0
    - MUST always group by both user_id AND date to calculate one score per patient per assessment date.
    - Compute the total PHQ-8 score (sum of all 8 responses for each user-date combination).
    - Use HAVING COUNT(*) = 8 to ensure complete assessments with all 8 questions answered.
"""

phq8_score_classification = """
  - Classification of PHQ-8 score:
    - <5: Minimal depression
    - 5–9: Mild depression
    - 10–14: Moderate depression
    - 15–19: Moderately severe depression
    - >=20: Severe depression
  - If score does not exist in the table, calculate the phq8 score first.
"""

# Pro2-score Classification
pro2_score_classification = """
To classify the PRO-2 score:
  - <8: Remission
  - 8–13: Mild
  - 14–34: Moderate
  - >34: Severe
"""

pro2_score_calculation = """
To calculate the PRO-2 score in SinC:
  - Use the `sinc_surveys` table with `title = 'IBD Daily'`.
  - Use these two daily questions:
    - `How many liquid or very soft stools did you have today?`
    - `Please rate your abdominal pain today.`
  - MUST group by both `user_id` and `event_date` to calculate one PRO-2 score per patient per day.
  - Because `sinc_surveys` is long-format and may contain repeated rows, DO NOT use `SUM(CASE...)` across all survey rows.
  - First pivot the two components into separate columns with conditional aggregation:
    - `MAX(CASE WHEN question LIKE '%liquid or very soft stools%' THEN CAST(answer AS INTEGER) END) AS stool_score`
    - `MAX(CASE WHEN question LIKE '%rate your abdominal pain%' THEN mapped pain score END) AS pain_score`
  - Then calculate `pro2_score = stool_score + pain_score`.
  - Exclude incomplete daily records: require both `stool_score IS NOT NULL` and `pain_score IS NOT NULL`.
  - When listing PRO-2 scores, include `user_id`, `event_date`, `pro2_score`, and any requested classification.
  - For highest/lowest PRO-2 queries, use deterministic tie-breaking such as `ORDER BY pro2_score DESC, user_id, event_date`.
  - Abdominal pain score mapping:
    - `None` = 0
    - `Mild` = 1
    - `Moderate` = 2
    - `Severe` = 3
    - any other value = 0
"""

pro2_score_info = """
The PRO-2 concept in SinC should be treated as a derived daily symptom summary from the `sinc_surveys` table rather than a stored table column.

- Use the `IBD Daily` survey title and its two daily questions:
  - `How many liquid or very soft stools did you have today?`
  - `Please rate your abdominal pain today.`
- Derive one record per `user_id` and `event_date`.
- Do not assume a physical `pro2_score` column exists in `sinc_surveys`.
"""

abdominal_pain_score_calculation = """
    To calculate the abdominal pain score:
    - Select only rows from the `sinc_surveys` table where the `question` column exactly matches: "Please rate your abdominal pain today." Do not include rows with other formats in the `question` column.
    - Extract the value from the `answer` column. Valid responses include:
      - "None"
      - "Mild"
      - "Moderate"
      - "Severe"
    - Assign a specific numeric score based on the response:
      - "None" = 0
      - "Mild" = 1
      - "Moderate" = 2
      - "Severe" = 3
      - Any other value = 0
"""

stool_frequency_score = """
    To calculate the stool frequency score:
    - Select only rows from `sinc_surveys` table where the `question` column exactly matches: "How many liquid or very soft stools did you have today?" Do not include rows with other formats in the `question` column.
    - Return the integer value from the `answer` column.
"""
