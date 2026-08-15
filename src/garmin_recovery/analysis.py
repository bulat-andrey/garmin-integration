from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable

from .client import GarminActivity, parse_garmin_datetime


CSV_HEADERS = ["date", "activity_id", "activity_type", "duration_min", "rpe", "srpe", "notes"]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RPE_PATH = PROJECT_ROOT / "data" / "manual_rpe.csv"


@dataclass(slots=True)
class RpeEntry:
    date: str
    activity_id: int
    activity_type: str
    duration_min: int
    rpe: float
    srpe: float
    notes: str


@dataclass(slots=True)
class RecoveryResult:
    color: str
    recommendation: str
    score: int
    reasons: list[str]
    context_lines: list[str]
    training_focus: str | None = None
    training_status: str | None = None
    menstrual_cycle_context: str | None = None


@dataclass(slots=True)
class RecentStrengthContext:
    had_strength_in_last_24h: bool
    had_strength_in_last_48h: bool
    latest_strength_date: str | None


WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def calculate_srpe(duration_min: int, rpe: float) -> int | float:
    value = duration_min * rpe
    rounded = round(value, 1)
    return int(rounded) if rounded.is_integer() else rounded


def ensure_rpe_file(path: Path = DEFAULT_RPE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(",".join(CSV_HEADERS) + "\n", encoding="utf-8")
    return path


def load_rpe_entries(path: Path = DEFAULT_RPE_PATH) -> dict[int, RpeEntry]:
    file_path = ensure_rpe_file(path)
    entries: dict[int, RpeEntry] = {}
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            activity_id = int(row["activity_id"])
            entries[activity_id] = RpeEntry(
                date=row["date"],
                activity_id=activity_id,
                activity_type=row["activity_type"],
                duration_min=int(row["duration_min"]),
                rpe=float(row["rpe"]),
                srpe=float(row["srpe"]),
                notes=row.get("notes", ""),
            )
    return entries


def save_rpe_entries(entries: Iterable[RpeEntry], path: Path = DEFAULT_RPE_PATH) -> Path:
    file_path = ensure_rpe_file(path)
    sorted_entries = sorted(entries, key=lambda item: (item.date, item.activity_id))
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for entry in sorted_entries:
            writer.writerow(
                {
                    "date": entry.date,
                    "activity_id": entry.activity_id,
                    "activity_type": entry.activity_type,
                    "duration_min": entry.duration_min,
                    "rpe": _format_number(entry.rpe),
                    "srpe": _format_number(entry.srpe),
                    "notes": entry.notes,
                }
            )
    return file_path


def upsert_rpe_entry(
    activity: GarminActivity, rpe: float, notes: str = "", path: Path = DEFAULT_RPE_PATH
) -> tuple[RpeEntry, Path]:
    entries = load_rpe_entries(path)
    srpe = calculate_srpe(activity.duration_min, rpe)
    entry = RpeEntry(
        date=activity.date,
        activity_id=activity.activity_id,
        activity_type=activity.activity_type,
        duration_min=activity.duration_min,
        rpe=rpe,
        srpe=float(srpe),
        notes=notes,
    )
    entries[activity.activity_id] = entry
    file_path = save_rpe_entries(entries.values(), path)
    return entry, file_path


def find_activity(
    activities: list[GarminActivity], selector: str
) -> tuple[GarminActivity, str]:
    selector = selector.strip()
    if selector.isdigit():
        activity_id = int(selector)
        for activity in activities:
            if activity.activity_id == activity_id:
                return activity, "exact id"
        raise ValueError(f"Activity ID {selector} was not found in the recent search window")

    if selector.lower() == "latest":
        if not activities:
            raise ValueError("No recent activities were found")
        return activities[0], "latest"

    lowered = selector.lower()
    matches = [
        activity
        for activity in activities
        if lowered in activity.name.lower() or lowered in activity.activity_type.lower()
    ]
    if not matches:
        raise ValueError(f"No recent activity matched '{selector}'")
    return matches[0], "best recent text match"


def summarize_loads(
    activities: list[GarminActivity], rpe_entries: dict[int, RpeEntry], now: datetime | None = None
) -> dict[str, float | int]:
    current_time = now or datetime.now()
    windows = {
        "24h": current_time - timedelta(hours=24),
        "48h": current_time - timedelta(hours=48),
        "72h": current_time - timedelta(hours=72),
    }
    totals: dict[str, float | int] = {
        "garmin_24h": 0.0,
        "garmin_48h": 0.0,
        "garmin_72h": 0.0,
        "srpe_24h": 0.0,
        "srpe_48h": 0.0,
        "srpe_72h": 0.0,
    }

    for activity in activities:
        start_time = parse_garmin_datetime(activity.start_local)
        if start_time is None:
            continue
        srpe = rpe_entries.get(activity.activity_id).srpe if activity.activity_id in rpe_entries else 0.0
        garmin_load = activity.garmin_load or 0.0
        for label, cutoff in windows.items():
            if start_time >= cutoff:
                totals[f"garmin_{label}"] += garmin_load
                totals[f"srpe_{label}"] += srpe

    return totals


def consecutive_training_days(
    activities: list[GarminActivity], rpe_entries: dict[int, RpeEntry], today: date | None = None
) -> int:
    current_date = today or date.today()
    meaningful_dates = {
        activity.date
        for activity in activities
        if is_meaningful_training_session(activity, rpe_entries.get(activity.activity_id))
    }

    streak = 0
    day_cursor = current_date
    if day_cursor.isoformat() not in meaningful_dates:
        day_cursor -= timedelta(days=1)

    while day_cursor.isoformat() in meaningful_dates:
        streak += 1
        day_cursor -= timedelta(days=1)
    return streak


def is_meaningful_training_session(activity: GarminActivity, rpe_entry: RpeEntry | None) -> bool:
    if rpe_entry is not None and rpe_entry.srpe > 0:
        return True
    if (activity.garmin_load or 0.0) >= 10:
        return True
    if activity.total_intensity_minutes >= 20:
        return True
    if activity.duration_min >= 30 and "walk" not in activity.activity_type:
        return True
    return False


def recent_strength_context(
    activities: list[GarminActivity], now: datetime | None = None
) -> RecentStrengthContext:
    current_time = now or datetime.now()
    latest_strength_time: datetime | None = None
    latest_strength_date: str | None = None

    for activity in activities:
        if activity.activity_type.lower() != "strength_training":
            continue
        start_time = parse_garmin_datetime(activity.start_local)
        if start_time is None or start_time > current_time:
            continue
        if latest_strength_time is None or start_time > latest_strength_time:
            latest_strength_time = start_time
            latest_strength_date = activity.date

    if latest_strength_time is None:
        return RecentStrengthContext(
            had_strength_in_last_24h=False,
            had_strength_in_last_48h=False,
            latest_strength_date=None,
        )

    age = current_time - latest_strength_time
    return RecentStrengthContext(
        had_strength_in_last_24h=age <= timedelta(hours=30),
        had_strength_in_last_48h=age <= timedelta(hours=54),
        latest_strength_date=latest_strength_date,
    )


def analyze_recovery(
    target_date: str,
    health_today: dict,
    sleep_today: dict,
    health_range: list[dict],
    sleep_range: list[dict],
    activities: list[GarminActivity],
    rpe_entries: dict[int, RpeEntry],
    womens_health_today: dict | None = None,
    preferred_strength_days: set[int] | None = None,
) -> RecoveryResult:
    reference_now = datetime.combine(date.fromisoformat(target_date), datetime.max.time())
    target_day = date.fromisoformat(target_date)
    stats = _dict_or_empty(health_today.get("stats") if isinstance(health_today, dict) else {})
    sleep = _dict_or_empty(sleep_today.get("sleep") if isinstance(sleep_today, dict) else {})
    daily_sleep = _dict_or_empty(sleep.get("dailySleepDTO"))
    sleep_scores = _dict_or_empty(daily_sleep.get("sleepScores"))

    sleep_hours = _hours(daily_sleep.get("sleepTimeSeconds"))
    sleep_score = _number_or_none(_dict_or_empty(sleep_scores.get("overall")).get("value"))
    overnight_hrv = _number_or_none(sleep.get("avgOvernightHrv"))
    hrv_status = str(sleep.get("hrvStatus") or "")
    resting_hr = _number_or_none(sleep.get("restingHeartRate")) or _number_or_none(stats.get("restingHeartRate"))
    body_battery_wake = _number_or_none(stats.get("bodyBatteryAtWakeTime"))
    body_battery_current = _number_or_none(stats.get("bodyBatteryMostRecentValue"))
    average_stress = _number_or_none(stats.get("averageStressLevel"))
    training_focus = _extract_training_focus(health_today.get("training_status", {}))
    training_status = _extract_training_status(health_today.get("training_status", {}))
    menstrual_cycle_context = _extract_menstrual_cycle_context(womens_health_today)

    prior_sleep_hours = [
        _hours(_dict_or_empty(_dict_or_empty(item).get("sleep")).get("dailySleepDTO", {}).get("sleepTimeSeconds"))
        for item in sleep_range
        if _dict_or_empty(_dict_or_empty(item).get("date")).get("date") != target_date
    ]
    prior_sleep_hours = [value for value in prior_sleep_hours if value is not None]

    prior_hrv = [
        _number_or_none(_dict_or_empty(_dict_or_empty(item).get("sleep")).get("avgOvernightHrv"))
        for item in sleep_range
        if _dict_or_empty(_dict_or_empty(item).get("date")).get("date") != target_date
    ]
    prior_hrv = [value for value in prior_hrv if value is not None]

    prior_rhr = [
        _number_or_none(_dict_or_empty(_dict_or_empty(item).get("stats")).get("restingHeartRate"))
        for item in health_range
        if _dict_or_empty(_dict_or_empty(item).get("date")).get("date") != target_date
    ]
    prior_rhr = [value for value in prior_rhr if value is not None]

    sleep_baseline = _safe_mean(prior_sleep_hours)
    sleep_baseline_nights = len(prior_sleep_hours)
    hrv_baseline = _safe_mean(prior_hrv)
    rhr_baseline = _number_or_none(stats.get("lastSevenDaysAvgRestingHeartRate")) or _safe_mean(prior_rhr)

    loads = summarize_loads(activities, rpe_entries, now=reference_now)
    streak = consecutive_training_days(activities, rpe_entries, date.fromisoformat(target_date))
    missing_hr_kite = [
        activity
        for activity in activities
        if "kite" in activity.activity_type.lower() and not activity.has_hr_data
    ]
    recent_missing_hr_kite = [
        activity
        for activity in missing_hr_kite
        if (parse_garmin_datetime(activity.start_local) or datetime.min)
        >= reference_now - timedelta(hours=72)
    ]
    strength_context = recent_strength_context(activities, now=reference_now)
    strength_day_preferred = (
        preferred_strength_days is None or target_day.weekday() in preferred_strength_days
    )

    score = 0
    reasons: list[str] = []

    if sleep_hours is not None and sleep_baseline is not None:
        ratio = sleep_hours / sleep_baseline if sleep_baseline else 1.0
        if sleep_hours < 6.0 or ratio < 0.85:
            score += 2
            reasons.append(
                f"Sleep was {sleep_hours:.1f} h versus a recent baseline of {sleep_baseline:.1f} h from {sleep_baseline_nights} prior nights."
            )
        elif ratio < 0.95:
            score += 1
            reasons.append(
                f"Sleep was slightly below baseline at {sleep_hours:.1f} h versus {sleep_baseline:.1f} h from {sleep_baseline_nights} prior nights."
            )

    if sleep_score is not None:
        if sleep_score < 55:
            score += 2
            reasons.append(f"Sleep score was low at {int(sleep_score)}/100.")
        elif sleep_score < 70:
            score += 1
            reasons.append(f"Sleep score was fair at {int(sleep_score)}/100.")

    if overnight_hrv is not None and hrv_baseline is not None:
        ratio = overnight_hrv / hrv_baseline if hrv_baseline else 1.0
        if ratio < 0.85:
            score += 2
            reasons.append(
                f"Overnight HRV was suppressed at {overnight_hrv:.0f} ms versus {hrv_baseline:.0f} ms baseline."
            )
        elif ratio < 0.95:
            score += 1
            reasons.append(
                f"Overnight HRV was a bit below baseline at {overnight_hrv:.0f} ms versus {hrv_baseline:.0f} ms."
            )

    if resting_hr is not None and rhr_baseline is not None:
        delta = resting_hr - rhr_baseline
        if delta >= 5:
            score += 2
            reasons.append(
                f"Resting HR was elevated at {resting_hr:.0f} bpm versus {rhr_baseline:.0f} bpm baseline."
            )
        elif delta >= 3:
            score += 1
            reasons.append(
                f"Resting HR was mildly elevated at {resting_hr:.0f} bpm versus {rhr_baseline:.0f} bpm."
            )

    if body_battery_wake is not None:
        if body_battery_wake < 50:
            score += 2
            reasons.append(f"Body Battery at wake-up was low at {body_battery_wake:.0f}.")
        elif body_battery_wake < 70:
            score += 1
            reasons.append(f"Body Battery at wake-up was moderate at {body_battery_wake:.0f}.")

    if average_stress is not None:
        if average_stress >= 35:
            score += 2
            reasons.append(f"Average stress was high at {average_stress:.0f}.")
        elif average_stress >= 25:
            score += 1
            reasons.append(f"Average stress was somewhat elevated at {average_stress:.0f}.")

    srpe_24h = float(loads["srpe_24h"])
    srpe_48h = float(loads["srpe_48h"])
    srpe_72h = float(loads["srpe_72h"])
    if srpe_24h >= 800 or srpe_48h >= 1400 or srpe_72h >= 1800:
        score += 2
        reasons.append(
            f"Manual sRPE load is high: 24h {srpe_24h:.0f}, 48h {srpe_48h:.0f}, 72h {srpe_72h:.0f}."
        )
    elif srpe_24h >= 500 or srpe_48h >= 900 or srpe_72h >= 1200:
        score += 1
        reasons.append(
            f"Manual sRPE load is building: 24h {srpe_24h:.0f}, 48h {srpe_48h:.0f}, 72h {srpe_72h:.0f}."
        )

    if streak >= 4:
        score += 2
        reasons.append(f"You are on a {streak}-day meaningful training streak.")
    elif streak >= 3:
        score += 1
        reasons.append(f"You are on a {streak}-day meaningful training streak.")

    if recent_missing_hr_kite and srpe_72h <= 0:
        score += 1
        reasons.append(
            "Recent kite sessions are missing HR and have no manual RPE yet, so Garmin load is likely underestimated."
        )

    if score >= 6:
        color = "RED"
        recommendation = "A) recovery/rest"
    elif score >= 3:
        color = "YELLOW"
        recommendation = "B) easy Zone 2"
    else:
        ready_for_hard = (
            (sleep_score or 0) >= 75
            and (body_battery_wake or 0) >= 75
            and srpe_24h < 350
            and (overnight_hrv is None or hrv_baseline is None or overnight_hrv >= hrv_baseline * 0.95)
        )
        color = "GREEN"
        if strength_context.had_strength_in_last_24h:
            recommendation = "C) normal aerobic training"
            reasons.append(
                "A recent strength session was detected, so back-to-back strength is deprioritized."
            )
        elif ready_for_hard:
            recommendation = "E) hard training"
        elif not strength_context.had_strength_in_last_48h and (body_battery_wake or 0) >= 65:
            if strength_day_preferred:
                recommendation = "D) strength training"
            else:
                recommendation = "C) normal aerobic training"
                reasons.append(
                    "Today is not a preferred strength day for this profile, so aerobic work is favored instead."
                )
        else:
            recommendation = "C) normal aerobic training"

    context_lines = [
        _format_context_line(
            "Sleep",
            sleep_hours,
            sleep_baseline,
            "h",
            digits=1,
            baseline_detail=f"{sleep_baseline_nights} prior nights" if sleep_baseline_nights else None,
        ),
        _format_context_line("Sleep score", sleep_score, None, "/100", digits=0),
        _format_context_line("Overnight HRV", overnight_hrv, hrv_baseline, " ms", digits=0),
        _format_context_line("Resting HR", resting_hr, rhr_baseline, " bpm", digits=0),
        _format_pair("Body Battery", body_battery_wake, body_battery_current, "wake/current"),
        _format_context_line("Average stress", average_stress, None, "", digits=0),
        (
            "Recent load: "
            f"Garmin 24h {float(loads['garmin_24h']):.1f}, 48h {float(loads['garmin_48h']):.1f}, 72h {float(loads['garmin_72h']):.1f}; "
            f"sRPE 24h {srpe_24h:.0f}, 48h {srpe_48h:.0f}, 72h {srpe_72h:.0f}"
        ),
        f"Consecutive meaningful training days: {streak}",
    ]
    if strength_context.latest_strength_date:
        context_lines.append(
            f"Recent strength session: {strength_context.latest_strength_date}"
        )
    if preferred_strength_days is not None:
        preferred_days_text = ", ".join(
            WEEKDAY_NAMES[day] for day in sorted(preferred_strength_days)
        )
        context_lines.append(
            "Preferred strength days: "
            f"{preferred_days_text}; today is {WEEKDAY_NAMES[target_day.weekday()]}"
        )
    if recent_missing_hr_kite:
        context_lines.append(
            f"Recent kite sessions without HR: {len(recent_missing_hr_kite)}; sRPE was used as the more trusted load signal."
        )
    if hrv_status:
        context_lines.append(f"Garmin HRV status: {hrv_status}")
    if training_focus:
        context_lines.append(f"Training focus: {training_focus}")
    if training_status:
        context_lines.append(f"Garmin training status: {training_status}")
    if menstrual_cycle_context:
        context_lines.append(f"Menstrual cycle context: {menstrual_cycle_context}")

    return RecoveryResult(
        color=color,
        recommendation=recommendation,
        score=score,
        reasons=reasons,
        context_lines=[line for line in context_lines if line],
        training_focus=training_focus,
        training_status=training_status,
        menstrual_cycle_context=menstrual_cycle_context,
    )


def weekly_summary_lines(
    start_date: str,
    end_date: str,
    activities: list[GarminActivity],
    health_range: list[dict],
    sleep_range: list[dict],
    rpe_entries: dict[int, RpeEntry],
) -> list[str]:
    total_duration = sum(activity.duration_min for activity in activities)
    total_distance = sum(activity.distance_km or 0.0 for activity in activities)
    total_garmin_load = sum(activity.garmin_load or 0.0 for activity in activities)
    total_srpe = sum(rpe_entries.get(activity.activity_id).srpe for activity in activities if activity.activity_id in rpe_entries)
    meaningful_days = len(
        {
            activity.date
            for activity in activities
            if is_meaningful_training_session(activity, rpe_entries.get(activity.activity_id))
        }
    )
    missing_hr_kite = [
        activity for activity in activities if "kite" in activity.activity_type.lower() and not activity.has_hr_data
    ]

    sleep_hours = [
        _hours(item.get("sleep", {}).get("dailySleepDTO", {}).get("sleepTimeSeconds"))
        for item in sleep_range
    ]
    sleep_scores = [
        _number_or_none(item.get("sleep", {}).get("dailySleepDTO", {}).get("sleepScores", {}).get("overall", {}).get("value"))
        for item in sleep_range
    ]
    overnight_hrv = [_number_or_none(item.get("sleep", {}).get("avgOvernightHrv")) for item in sleep_range]
    resting_hr = [_number_or_none(item.get("stats", {}).get("restingHeartRate")) for item in health_range]

    lines = [
        f"7-day window: {start_date} to {end_date}",
        f"Activities: {len(activities)} across {meaningful_days} meaningful training day(s)",
        f"Total duration: {_format_duration_minutes(total_duration)}",
        f"Total distance: {total_distance:.1f} km",
        f"Garmin activity load total: {total_garmin_load:.1f}",
        f"Manual sRPE total: {total_srpe:.0f}",
        f"Kite sessions without HR: {len(missing_hr_kite)}",
    ]

    avg_sleep = _safe_mean([value for value in sleep_hours if value is not None])
    avg_sleep_score = _safe_mean([value for value in sleep_scores if value is not None])
    avg_hrv = _safe_mean([value for value in overnight_hrv if value is not None])
    avg_rhr = _safe_mean([value for value in resting_hr if value is not None])
    if avg_sleep is not None:
        lines.append(f"Average sleep: {avg_sleep:.1f} h")
    if avg_sleep_score is not None:
        lines.append(f"Average sleep score: {avg_sleep_score:.0f}/100")
    if avg_hrv is not None:
        lines.append(f"Average overnight HRV: {avg_hrv:.0f} ms")
    if avg_rhr is not None:
        lines.append(f"Average resting HR: {avg_rhr:.0f} bpm")

    lines.append("")
    lines.append("Recent sessions:")
    for activity in activities:
        rpe_entry = rpe_entries.get(activity.activity_id)
        hr_text = f"HR {activity.average_hr:.0f}" if activity.average_hr is not None else "HR missing"
        srpe_text = f"sRPE {rpe_entry.srpe:.0f}" if rpe_entry is not None else "sRPE -"
        lines.append(
            f"- {activity.date} {activity.activity_type} {activity.duration_min} min | "
            f"{activity.distance_km or 0.0:.1f} km | Garmin load {(activity.garmin_load or 0.0):.1f} | "
            f"{hr_text} | {srpe_text}"
        )
    return lines


def kite_history_lines(
    activities: list[GarminActivity], rpe_entries: dict[int, RpeEntry]
) -> list[str]:
    kite_activities = [activity for activity in activities if "kite" in activity.activity_type.lower()]
    lines = [f"Kite sessions found: {len(kite_activities)}", ""]
    for activity in kite_activities:
        rpe_entry = rpe_entries.get(activity.activity_id)
        notes = f" | notes: {rpe_entry.notes}" if rpe_entry and rpe_entry.notes else ""
        lines.append(
            f"- {activity.date} {activity.duration_min} min | {activity.distance_km or 0.0:.1f} km | "
            f"Garmin load {(activity.garmin_load or 0.0):.1f} | intensity {activity.total_intensity_minutes} | "
            f"{'HR missing' if not activity.has_hr_data else f'HR {activity.average_hr:.0f}'} | "
            f"RPE {_format_number(rpe_entry.rpe) if rpe_entry else '-'} | "
            f"sRPE {_format_number(rpe_entry.srpe) if rpe_entry else '-'}{notes}"
        )
    return lines


def _hours(seconds: object) -> float | None:
    value = _number_or_none(seconds)
    if value is None:
        return None
    return round(value / 3600, 1)


def _number_or_none(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_mean(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return mean(usable)


def _dict_or_empty(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _format_context_line(
    label: str,
    today_value: float | None,
    baseline_value: float | None,
    suffix: str,
    digits: int = 1,
    baseline_detail: str | None = None,
) -> str:
    if today_value is None:
        return ""
    if digits == 0:
        today_text = f"{today_value:.0f}"
        baseline_text = f"{baseline_value:.0f}" if baseline_value is not None else None
    else:
        today_text = f"{today_value:.{digits}f}"
        baseline_text = f"{baseline_value:.{digits}f}" if baseline_value is not None else None

    if baseline_text is None:
        return f"{label}: {today_text}{suffix}"
    detail_suffix = f" ({baseline_detail})" if baseline_detail else ""
    return f"{label}: {today_text}{suffix} vs baseline {baseline_text}{suffix}{detail_suffix}"


def _format_pair(label: str, first: float | None, second: float | None, pair_label: str) -> str:
    if first is None and second is None:
        return ""
    first_text = "-" if first is None else f"{first:.0f}"
    second_text = "-" if second is None else f"{second:.0f}"
    return f"{label} ({pair_label}): {first_text} / {second_text}"


def _format_duration_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def _format_number(value: float) -> str:
    rounded = round(value, 1)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


def _extract_training_focus(training_status: dict | None) -> str | None:
    if not isinstance(training_status, dict):
        return None
    load_balance = training_status.get("mostRecentTrainingLoadBalance") or {}
    if not isinstance(load_balance, dict):
        return None
    metrics_map = load_balance.get("metricsTrainingLoadBalanceDTOMap") or {}
    if not isinstance(metrics_map, dict) or not metrics_map:
        return None

    primary_metric = None
    for metric in metrics_map.values():
        if isinstance(metric, dict) and metric.get("primaryTrainingDevice"):
            primary_metric = metric
            break
    if primary_metric is None:
        primary_metric = next(
            (metric for metric in metrics_map.values() if isinstance(metric, dict)),
            None,
        )
    if primary_metric is None:
        return None

    phrase = primary_metric.get("trainingBalanceFeedbackPhrase")
    if not phrase:
        return None
    return _normalize_garmin_phrase(str(phrase))


def _extract_training_status(training_status: dict | None) -> str | None:
    if not isinstance(training_status, dict):
        return None
    recent_status = training_status.get("mostRecentTrainingStatus") or {}
    if not isinstance(recent_status, dict):
        return None
    latest_data = recent_status.get("latestTrainingStatusData") or {}
    if not isinstance(latest_data, dict) or not latest_data:
        return None

    primary_status = None
    for item in latest_data.values():
        if isinstance(item, dict) and item.get("primaryTrainingDevice"):
            primary_status = item
            break
    if primary_status is None:
        primary_status = next(
            (item for item in latest_data.values() if isinstance(item, dict)),
            None,
        )
    if primary_status is None:
        return None

    phrase = primary_status.get("trainingStatusFeedbackPhrase")
    if not phrase:
        return None
    return _normalize_garmin_phrase(str(phrase))


def _extract_menstrual_cycle_context(womens_health_today: dict | None) -> str | None:
    if not womens_health_today:
        return None

    data = womens_health_today.get("data", {})
    menstrual = data.get("menstrual_data")
    if not isinstance(menstrual, dict) or not menstrual:
        return None

    parts: list[str] = []

    cycle_day = next(
        (
            menstrual.get(key)
            for key in ("cycleDay", "dayOfCycle", "cycle_day", "day_in_cycle")
            if menstrual.get(key) is not None
        ),
        None,
    )
    phase = next(
        (
            menstrual.get(key)
            for key in ("phase", "cyclePhase", "currentPhase", "menstrualPhase")
            if menstrual.get(key)
        ),
        None,
    )
    if cycle_day is not None:
        parts.append(f"cycle day {cycle_day}")
    if phase:
        parts.append(f"phase {str(phase).replace('_', ' ').lower()}")

    active_period_keys = (
        "isPeriodDay",
        "onPeriod",
        "currentlyMenstruating",
        "isBleeding",
    )
    if any(bool(menstrual.get(key)) for key in active_period_keys):
        parts.append("active period")

    symptoms = next(
        (
            menstrual.get(key)
            for key in ("symptoms", "symptomCategories", "loggedSymptoms")
            if menstrual.get(key)
        ),
        None,
    )
    if isinstance(symptoms, list) and symptoms:
        normalized = ", ".join(str(item).replace("_", " ").lower() for item in symptoms[:3])
        parts.append(f"symptoms: {normalized}")

    if not parts:
        return "data available"
    return "; ".join(parts)


def _normalize_garmin_phrase(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return trimmed

    parts = [part for part in trimmed.split("_") if part and not part.isdigit()]
    normalized = " ".join(part.lower() for part in parts)
    replacements = {
        "aerobic high shortage": "high aerobic shortage",
        "aerobic low shortage": "low aerobic shortage",
        "anaerobic shortage": "anaerobic shortage",
        "productive": "productive",
        "maintaining": "maintaining",
        "recovery": "recovery",
        "peaking": "peaking",
        "detraining": "detraining",
        "strained": "strained",
        "unproductive": "unproductive",
        "overreaching": "overreaching",
    }
    return replacements.get(normalized, normalized)
