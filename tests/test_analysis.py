from garmin_recovery.analysis import RecoveryResult, analyze_recovery, calculate_srpe
from garmin_recovery.cli import _format_recovery_telegram_message
from garmin_recovery.client import GarminActivity


def test_calculate_srpe_matches_user_examples() -> None:
    assert calculate_srpe(161, 5) == 805
    assert calculate_srpe(141, 7) == 987


def test_calculate_srpe_keeps_decimal_when_needed() -> None:
    assert calculate_srpe(45, 6.5) == 292.5


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
            "Sleep: 8.0h vs baseline 7.5h",
            "Training focus: high aerobic shortage",
            "Garmin training status: productive",
        ],
    )

    message = _format_recovery_telegram_message("2026-08-15", result, "Andrei", "ru")

    assert "Garmin: восстановление для Andrei - 2026-08-15" in message
    assert "Сегодня: C) обычная аэробная тренировка" in message
    assert "Training focus: не хватает high aerobic нагрузки" in message
    assert "Статус тренинга Garmin: продуктивно" in message
