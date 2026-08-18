from datetime import datetime

from garmin_recovery.analysis import (
    RecoveryResult,
    RpeEntry,
    analyze_recovery,
    calculate_srpe,
    kite_history_lines,
    summarize_loads,
)
from garmin_recovery.cli import _format_recovery_telegram_message, _select_context_lines_for_message
from garmin_recovery.client import GarminActivity


def test_calculate_srpe_matches_user_examples() -> None:
    assert calculate_srpe(161, 5) == 805
    assert calculate_srpe(141, 7) == 987


def test_calculate_srpe_keeps_decimal_when_needed() -> None:
    assert calculate_srpe(45, 6.5) == 292.5


def test_garmin_app_rpe_is_used_when_csv_is_missing() -> None:
    activity = GarminActivity(
        activity_id=24000211128,
        name="Choczewo Kiteboarding",
        activity_type="kiteboarding_v2",
        date="2026-08-16",
        start_local="2026-08-16 10:00:00",
        duration_min=196,
        moving_min=180,
        distance_km=52.0,
        calories=1200.0,
        description="",
        average_hr=98.0,
        garmin_load=8.2,
        garmin_rpe=7.0,
        moderate_intensity_minutes=21,
        vigorous_intensity_minutes=20,
        has_hr_data=True,
    )

    loads = summarize_loads([activity], {}, now=datetime(2026, 8, 16, 23, 0, 0))
    lines = kite_history_lines([activity], {})

    assert loads["srpe_24h"] == 1372.0
    assert loads["srpe_48h"] == 1372.0
    assert loads["srpe_72h"] == 1372.0
    assert any("RPE 7 (Garmin)" in line for line in lines)


def test_csv_rpe_overrides_garmin_app_rpe() -> None:
    activity = GarminActivity(
        activity_id=24024100396,
        name="Kosakowo Kiteboarding",
        activity_type="kiteboarding_v2",
        date="2026-08-18",
        start_local="2026-08-18 09:00:00",
        duration_min=83,
        moving_min=80,
        distance_km=22.1,
        calories=600.0,
        description="",
        average_hr=104.0,
        garmin_load=14.7,
        garmin_rpe=3.0,
        moderate_intensity_minutes=25,
        vigorous_intensity_minutes=20,
        has_hr_data=True,
    )
    csv_entry = RpeEntry(
        date="2026-08-18",
        activity_id=24024100396,
        activity_type="kiteboarding_v2",
        duration_min=83,
        rpe=6.0,
        srpe=498.0,
        notes="override",
    )

    loads = summarize_loads([activity], {activity.activity_id: csv_entry}, now=datetime(2026, 8, 18, 23, 0, 0))
    lines = kite_history_lines([activity], {activity.activity_id: csv_entry})

    assert loads["srpe_24h"] == 498.0
    assert any("RPE 6 (CSV override)" in line for line in lines)
    assert any("sRPE 498" in line for line in lines)


def test_recent_strength_session_avoids_back_to_back_strength_recommendation() -> None:
    result = analyze_recovery(
        target_date="2026-08-14",
        health_today={
            "stats": {
                "restingHeartRate": 43,
                "lastSevenDaysAvgRestingHeartRate": 44,
                "bodyBatteryAtWakeTime": 96,
                "bodyBatteryMostRecentValue": 96,
                "averageStressLevel": 13,
            }
        },
        sleep_today={
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 32040,
                    "sleepScores": {"overall": {"value": 84}},
                },
                "avgOvernightHrv": 55,
                "hrvStatus": "BALANCED",
                "restingHeartRate": 43,
            }
        },
        health_range=[
            {"date": {"date": "2026-08-13"}, "stats": {"restingHeartRate": 44}},
            {"date": {"date": "2026-08-12"}, "stats": {"restingHeartRate": 44}},
        ],
        sleep_range=[
            {
                "date": {"date": "2026-08-13"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 30960, "sleepScores": {"overall": {"value": 82}}},
                    "avgOvernightHrv": 50,
                },
            },
            {
                "date": {"date": "2026-08-12"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 30960, "sleepScores": {"overall": {"value": 80}}},
                    "avgOvernightHrv": 52,
                },
            },
        ],
        activities=[
            GarminActivity(
                activity_id=1,
                name="Strength",
                activity_type="strength_training",
                date="2026-08-13",
                start_local="2026-08-13 18:00:00",
                duration_min=50,
                moving_min=40,
                distance_km=0.0,
                calories=250.0,
                description="",
                average_hr=110,
                max_hr=135,
                garmin_load=17.5,
                moderate_intensity_minutes=20,
                vigorous_intensity_minutes=0,
                has_hr_data=True,
            )
        ],
        rpe_entries={},
    )

    assert result.color == "GREEN"
    assert result.recommendation == "C) normal aerobic training"
    assert any("back-to-back strength" in reason for reason in result.reasons)
    assert any(
        line.startswith('Sleep: 8.9h vs baseline 8.6h (2 prior nights) "above baseline, generally positive"')
        for line in result.context_lines
    )


def test_non_preferred_strength_day_favors_aerobic_work() -> None:
    result = analyze_recovery(
        target_date="2026-08-14",
        health_today={
            "stats": {
                "restingHeartRate": 43,
                "lastSevenDaysAvgRestingHeartRate": 44,
                "bodyBatteryAtWakeTime": 72,
                "bodyBatteryMostRecentValue": 72,
                "averageStressLevel": 13,
            }
        },
        sleep_today={
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 29040,
                    "sleepScores": {"overall": {"value": 72}},
                },
                "avgOvernightHrv": 52,
                "hrvStatus": "BALANCED",
                "restingHeartRate": 43,
            }
        },
        health_range=[
            {"date": {"date": "2026-08-13"}, "stats": {"restingHeartRate": 44}},
            {"date": {"date": "2026-08-12"}, "stats": {"restingHeartRate": 44}},
        ],
        sleep_range=[
            {
                "date": {"date": "2026-08-13"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 28800, "sleepScores": {"overall": {"value": 71}}},
                    "avgOvernightHrv": 51,
                },
            },
            {
                "date": {"date": "2026-08-12"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 29160, "sleepScores": {"overall": {"value": 70}}},
                    "avgOvernightHrv": 52,
                },
            },
        ],
        activities=[],
        rpe_entries={},
        preferred_strength_days={0, 3, 5, 6},
    )

    assert result.color == "GREEN"
    assert result.recommendation == "C) normal aerobic training"
    assert any("not a preferred strength day" in reason for reason in result.reasons)
    assert any("Preferred strength days:" in line for line in result.context_lines)


def test_telegram_message_includes_athlete_name_when_provided() -> None:
    result = RecoveryResult(
        color="GREEN",
        recommendation="C) normal aerobic training",
        score=0,
        reasons=["Recovered well."],
        context_lines=["Sleep: 8.0h"],
    )

    message = _format_recovery_telegram_message("2026-08-14", result, "Vika")

    assert "Garmin recovery for Vika - 2026-08-14" in message


def test_russian_telegram_message_translates_core_fields() -> None:
    result = RecoveryResult(
        color="GREEN",
        recommendation="C) normal aerobic training",
        score=0,
        reasons=["A recent strength session was detected, so back-to-back strength is deprioritized."],
        context_lines=[
            'Sleep: 8.0h vs baseline 7.5h (6 prior nights) "above baseline, generally positive"',
            "Training focus: high aerobic shortage",
            "Garmin training status: productive",
        ],
    )

    message = _format_recovery_telegram_message("2026-08-15", result, "Andrei", "ru")

    assert "Andrei - 2026-08-15" in message
    assert "C) normal aerobic training" not in message
    assert "high aerobic shortage" not in message
    assert "productive" not in message
    assert "6 предыдущих ночей" in message
    assert '"выше базы, это хорошо"' in message
    assert "Фокус тренинга: не хватает высокоаэробной нагрузки" in message


def test_russian_telegram_message_translates_menstrual_context_fully() -> None:
    result = RecoveryResult(
        color="GREEN",
        recommendation="C) normal aerobic training",
        score=0,
        reasons=[],
        context_lines=[
            "Menstrual cycle context: cycle day 2; phase menstrual; cycle type irregular; active period",
        ],
    )

    message = _format_recovery_telegram_message("2026-08-15", result, "Vika", "ru")

    assert "Контекст цикла: день цикла 2; менструальная фаза; нерегулярный цикл; активная менструация" in message


def test_context_lines_add_short_baseline_interpretation() -> None:
    result = analyze_recovery(
        target_date="2026-08-15",
        health_today={
            "stats": {
                "restingHeartRate": 49,
                "lastSevenDaysAvgRestingHeartRate": 53,
                "bodyBatteryAtWakeTime": 90,
                "bodyBatteryMostRecentValue": 70,
                "averageStressLevel": 16,
            }
        },
        sleep_today={
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 30960,
                    "sleepScores": {"overall": {"value": 88}},
                },
                "avgOvernightHrv": 58,
                "restingHeartRate": 49,
            }
        },
        health_range=[
            {"date": {"date": "2026-08-14"}, "stats": {"restingHeartRate": 53}},
            {"date": {"date": "2026-08-13"}, "stats": {"restingHeartRate": 53}},
        ],
        sleep_range=[
            {
                "date": {"date": "2026-08-14"},
                "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 27360}, "avgOvernightHrv": 53},
            },
            {
                "date": {"date": "2026-08-13"},
                "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 27360}, "avgOvernightHrv": 53},
            },
        ],
        activities=[],
        rpe_entries={},
    )

    assert 'Sleep: 8.6h vs baseline 7.6h (2 prior nights) "above baseline, generally positive"' in result.context_lines
    assert 'Overnight HRV: 58 ms vs baseline 53 ms "above baseline, generally positive"' in result.context_lines
    assert 'Resting HR: 49 bpm vs baseline 53 bpm "below baseline, generally positive"' in result.context_lines


def test_context_selection_keeps_priority_lines_visible() -> None:
    lines = [
        "Sleep: 8.0h",
        "Sleep score: 80/100",
        "Overnight HRV: 50 ms",
        "Resting HR: 44 bpm",
        "Body Battery (wake/current): 85 / 80",
        "Average stress: 14",
        "Recent load: Garmin 24h 10.0, 48h 20.0, 72h 30.0; sRPE 24h 0, 48h 0, 72h 0",
        "Consecutive meaningful training days: 2",
        "Training focus: high aerobic shortage",
        "Garmin training status: productive",
        "Menstrual cycle context: luteal phase",
        "Garmin HRV status: balanced",
    ]

    selected = _select_context_lines_for_message(lines, limit=8)

    assert "Training focus: high aerobic shortage" in selected
    assert "Garmin training status: productive" in selected
    assert "Menstrual cycle context: luteal phase" in selected
    assert "Garmin HRV status: balanced" in selected


def test_menstrual_day_summary_is_rendered_and_deprioritizes_hard_training() -> None:
    result = analyze_recovery(
        target_date="2026-08-15",
        health_today={
            "stats": {
                "restingHeartRate": 49,
                "lastSevenDaysAvgRestingHeartRate": 53,
                "bodyBatteryAtWakeTime": 100,
                "bodyBatteryMostRecentValue": 40,
                "averageStressLevel": 27,
            },
            "training_status": {
                "mostRecentTrainingLoadBalance": {
                    "metricsTrainingLoadBalanceDTOMap": {
                        "device": {
                            "trainingBalanceFeedbackPhrase": "AEROBIC_HIGH_SHORTAGE",
                            "primaryTrainingDevice": True,
                        }
                    }
                },
                "mostRecentTrainingStatus": {
                    "latestTrainingStatusData": {
                        "device": {
                            "trainingStatusFeedbackPhrase": "RECOVERY_2",
                            "primaryTrainingDevice": True,
                        }
                    }
                },
            },
        },
        sleep_today={
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 31020,
                    "sleepScores": {"overall": {"value": 94}},
                },
                "avgOvernightHrv": 58,
                "hrvStatus": "BALANCED",
                "restingHeartRate": 49,
            }
        },
        health_range=[
            {"date": {"date": "2026-08-14"}, "stats": {"restingHeartRate": 53}},
            {"date": {"date": "2026-08-13"}, "stats": {"restingHeartRate": 53}},
        ],
        sleep_range=[
            {
                "date": {"date": "2026-08-14"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 80}}},
                    "avgOvernightHrv": 53,
                },
            },
            {
                "date": {"date": "2026-08-13"},
                "sleep": {
                    "dailySleepDTO": {"sleepTimeSeconds": 28080, "sleepScores": {"overall": {"value": 81}}},
                    "avgOvernightHrv": 52,
                },
            },
        ],
        activities=[],
        rpe_entries={},
        womens_health_today={
            "data": {
                "menstrual_data": {
                    "daySummary": {
                        "dayInCycle": 2,
                        "currentPhase": 1,
                        "cycleType": "IRREGULAR",
                    }
                }
            }
        },
    )

    assert result.color == "GREEN"
    assert result.recommendation == "C) normal aerobic training"
    assert any("Early-cycle menstrual context" in reason for reason in result.reasons)
    assert any(
        line == "Menstrual cycle context: cycle day 2; phase menstrual; cycle type irregular; active period"
        for line in result.context_lines
    )


def test_missing_today_recovery_data_adds_explanatory_reason() -> None:
    result = analyze_recovery(
        target_date="2026-08-18",
        health_today={
            "stats": {
                "includesWellnessData": False,
                "restingHeartRate": None,
                "lastSevenDaysAvgRestingHeartRate": None,
                "bodyBatteryAtWakeTime": None,
                "bodyBatteryMostRecentValue": None,
                "averageStressLevel": None,
            },
            "training_status": {},
        },
        sleep_today={
            "sleep": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": None,
                }
            }
        },
        health_range=[
            {"date": {"date": "2026-08-17"}, "stats": {"restingHeartRate": 52}},
            {"date": {"date": "2026-08-16"}, "stats": {"restingHeartRate": 53}},
        ],
        sleep_range=[
            {
                "date": {"date": "2026-08-17"},
                "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 29580}, "avgOvernightHrv": 54},
            },
            {
                "date": {"date": "2026-08-16"},
                "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 28800}, "avgOvernightHrv": 53},
            },
        ],
        activities=[],
        rpe_entries={},
        womens_health_today={
            "data": {
                "menstrual_data": {
                    "daySummary": {
                        "dayInCycle": 5,
                        "currentPhase": 2,
                        "cycleType": "IRREGULAR",
                    }
                }
            }
        },
        preferred_strength_days={0, 3, 5, 6},
    )

    assert any(
        "Today's Garmin recovery data is not yet available or synced" in reason
        for reason in result.reasons
    )
    assert "Garmin sync status: today's recovery data is not yet available" in result.context_lines


def test_russian_telegram_message_explains_missing_garmin_recovery_data() -> None:
    result = RecoveryResult(
        color="GREEN",
        recommendation="C) normal aerobic training",
        score=0,
        reasons=[
            "Today's Garmin recovery data is not yet available or synced, so the recommendation leans more on recent load and profile context."
        ],
        context_lines=["Recent load: Garmin 24h 0.0, 48h 0.0, 72h 15.8; sRPE 24h 0, 48h 0, 72h 0"],
    )

    message = _format_recovery_telegram_message("2026-08-18", result, "Vika", "ru")

    assert "Сегодняшние recovery-данные Garmin пока недоступны" in message
