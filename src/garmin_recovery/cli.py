from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date, timedelta
from os import environ
from pathlib import Path

from .analysis import (
    DEFAULT_RPE_PATH,
    analyze_recovery,
    ensure_rpe_file,
    find_activity,
    kite_history_lines,
    load_rpe_entries,
    upsert_rpe_entry,
    weekly_summary_lines,
)
from .client import GarminMcpClient, GarminMcpError, GarminMcpToolError, format_date
from .telegram import TelegramNotificationError, send_telegram_message


DEFAULT_PROFILE_DIR = Path.home() / ".config" / "garmin-recovery" / "profiles"
WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def analyze_recovery_main() -> None:
    parser = argparse.ArgumentParser(description="Analyze today's recovery from Garmin + manual RPE.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional profile name. Loads ~/.config/garmin-recovery/profiles/<name>.env before analyzing.",
    )
    args = parser.parse_args()
    asyncio.run(_run_analyze_recovery(args.date, Path(args.rpe_file), args.profile))


def analyze_week_main() -> None:
    parser = argparse.ArgumentParser(description="Summarize the last 7 days of training and recovery.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to summarize.")
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    args = parser.parse_args()
    asyncio.run(_run_analyze_week(args.days, Path(args.rpe_file)))


def add_rpe_main() -> None:
    parser = argparse.ArgumentParser(description="Add or update manual RPE for a Garmin activity.")
    parser.add_argument("activity", help="Activity ID, 'latest', or a recent name/type match.")
    parser.add_argument("rpe", type=float, help="Session RPE, usually 1-10.")
    parser.add_argument("--notes", default="", help="Optional notes stored in the CSV.")
    parser.add_argument(
        "--search-days",
        type=int,
        default=30,
        help="How many days of recent activities to search for a text match.",
    )
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    args = parser.parse_args()
    asyncio.run(_run_add_rpe(args.activity, args.rpe, args.notes, args.search_days, Path(args.rpe_file)))


def analyze_kite_history_main() -> None:
    parser = argparse.ArgumentParser(description="Compare recent kite sessions with Garmin load and manual sRPE.")
    parser.add_argument("--days", type=int, default=120, help="How many days of history to inspect.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum kite sessions to display.")
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    args = parser.parse_args()
    asyncio.run(_run_analyze_kite_history(args.days, args.limit, Path(args.rpe_file)))


def send_telegram_recovery_main() -> None:
    parser = argparse.ArgumentParser(
        description="Send today's recovery recommendation to Telegram."
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    parser.add_argument("--token", default=None, help="Telegram bot token. Defaults to TELEGRAM_BOT_TOKEN.")
    parser.add_argument("--chat-id", default=None, help="Telegram chat ID. Defaults to TELEGRAM_CHAT_ID.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional profile name. Loads ~/.config/garmin-recovery/profiles/<name>.env before sending.",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Message language, for example 'ru' or 'en'. Defaults to MESSAGE_LANGUAGE or 'en'.",
    )
    parser.add_argument(
        "--athlete-name",
        default=None,
        help="Optional athlete name for the Telegram title. Defaults to ATHLETE_NAME.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the message instead of sending it.")
    args = parser.parse_args()
    asyncio.run(
        _run_send_telegram_recovery(
            target_date=args.date,
            rpe_file=Path(args.rpe_file),
            token=args.token,
            chat_id=args.chat_id,
            profile=args.profile,
            lang=args.lang,
            athlete_name=args.athlete_name,
            dry_run=args.dry_run,
        )
    )


async def _run_analyze_recovery(target_date: str, rpe_file: Path, profile: str | None = None) -> None:
    if profile:
        _load_profile_environment(profile)
    result = await _collect_recovery_result(target_date, rpe_file)
    print("\n".join(_format_recovery_output(result)))


async def _run_analyze_week(days: int, rpe_file: Path) -> None:
    ensure_rpe_file(rpe_file)
    end = date.today()
    start = end - timedelta(days=days - 1)
    start_date = format_date(start)
    end_date = format_date(end)

    try:
        async with GarminMcpClient() as garmin:
            activities = await garmin.get_recent_activities_with_details(start_date, end_date)
            health_range = await garmin.get_health_range(start_date, end_date)
            sleep_range = await garmin.get_sleep_range(start_date, end_date)
    except (GarminMcpError, GarminMcpToolError) as exc:
        raise SystemExit(f"Garmin MCP error: {exc}") from exc

    rpe_entries = load_rpe_entries(rpe_file)
    print("\n".join(weekly_summary_lines(start_date, end_date, activities, health_range, sleep_range, rpe_entries)))


async def _run_add_rpe(
    selector: str, rpe: float, notes: str, search_days: int, rpe_file: Path
) -> None:
    ensure_rpe_file(rpe_file)
    end = date.today()
    start = end - timedelta(days=search_days - 1)
    start_date = format_date(start)
    end_date = format_date(end)

    try:
        async with GarminMcpClient() as garmin:
            activities = await garmin.get_recent_activities_with_details(start_date, end_date)
    except (GarminMcpError, GarminMcpToolError) as exc:
        raise SystemExit(f"Garmin MCP error: {exc}") from exc

    try:
        activity, resolution = find_activity(activities, selector)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    entry, file_path = upsert_rpe_entry(activity, rpe, notes, path=rpe_file)
    print(f"Saved RPE for activity {activity.activity_id} ({resolution})")
    print(f"- Date: {entry.date}")
    print(f"- Activity: {activity.name or activity.activity_type}")
    print(f"- Type: {entry.activity_type}")
    print(f"- Duration: {entry.duration_min} min")
    print(f"- RPE: {entry.rpe:g}")
    print(f"- sRPE: {entry.srpe:g}")
    if entry.notes:
        print(f"- Notes: {entry.notes}")
    print(f"- File: {file_path.resolve()}")


async def _run_analyze_kite_history(days: int, limit: int, rpe_file: Path) -> None:
    ensure_rpe_file(rpe_file)
    end = date.today()
    start = end - timedelta(days=days - 1)
    start_date = format_date(start)
    end_date = format_date(end)

    try:
        async with GarminMcpClient() as garmin:
            summaries = await garmin.list_activity_summaries(start_date, end_date)
            kite_summaries = [
                activity for activity in summaries if "kite" in activity.activity_type.lower()
            ][:limit]
            activities = []
            for activity in kite_summaries:
                activities.append(await garmin.enrich_activity(activity))
    except (GarminMcpError, GarminMcpToolError) as exc:
        raise SystemExit(f"Garmin MCP error: {exc}") from exc

    rpe_entries = load_rpe_entries(rpe_file)
    print("\n".join(kite_history_lines(activities, rpe_entries)))


async def _run_send_telegram_recovery(
    target_date: str,
    rpe_file: Path,
    token: str | None,
    chat_id: str | None,
    profile: str | None,
    lang: str | None,
    athlete_name: str | None,
    dry_run: bool,
) -> None:
    if profile:
        _load_profile_environment(profile)

    result = await _collect_recovery_result(target_date, rpe_file)
    resolved_athlete_name = athlete_name or environ.get("ATHLETE_NAME")
    resolved_lang = (lang or environ.get("MESSAGE_LANGUAGE") or "en").lower()
    message = _format_recovery_telegram_message(
        target_date,
        result,
        resolved_athlete_name,
        resolved_lang,
    )

    if dry_run:
        print(message)
        return

    resolved_token = token or environ.get("TELEGRAM_BOT_TOKEN")
    resolved_chat_id = chat_id or environ.get("TELEGRAM_CHAT_ID")
    if not resolved_token or not resolved_chat_id:
        raise SystemExit(
            "Telegram config missing. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or pass --token and --chat-id."
        )

    try:
        send_telegram_message(token=resolved_token, chat_id=resolved_chat_id, text=message)
    except TelegramNotificationError as exc:
        raise SystemExit(f"Telegram send failed: {exc}") from exc

    print(f"Sent Telegram recovery report for {target_date} to chat {resolved_chat_id}.")


async def _collect_recovery_result(target_date: str, rpe_file: Path):
    ensure_rpe_file(rpe_file)
    target = date.fromisoformat(target_date)
    start = target - timedelta(days=6)
    start_date = format_date(start)
    end_date = format_date(target)
    womens_health_today = None

    try:
        async with GarminMcpClient() as garmin:
            health_today = await garmin.get_health_summary(target_date)
            sleep_today = await garmin.get_sleep(target_date)
            health_range = await garmin.get_health_range(start_date, end_date)
            sleep_range = await garmin.get_sleep_range(start_date, end_date)
            activities = await garmin.get_recent_activities_with_details(start_date, end_date)
            if _env_flag("ENABLE_WOMENS_HEALTH"):
                try:
                    womens_health_today = await garmin.call_json(
                        "query_womens_health",
                        {"data_type": "menstrual", "date": target_date},
                    )
                except (GarminMcpError, GarminMcpToolError):
                    womens_health_today = None
    except (GarminMcpError, GarminMcpToolError) as exc:
        raise SystemExit(f"Garmin MCP error: {exc}") from exc

    rpe_entries = load_rpe_entries(rpe_file)
    return analyze_recovery(
        target_date=target_date,
        health_today=health_today,
        sleep_today=sleep_today,
        health_range=health_range,
        sleep_range=sleep_range,
        activities=activities,
        rpe_entries=rpe_entries,
        womens_health_today=womens_health_today,
        preferred_strength_days=_preferred_strength_days_from_env(),
    )


def _format_recovery_output(result) -> list[str]:
    lines = [
        f"Recovery status: {result.color}",
        f"Recommended today: {result.recommendation}",
        "",
    ]
    if result.reasons:
        lines.append("Main drivers:")
        for reason in result.reasons:
            lines.append(f"- {reason}")
        lines.append("")
    lines.append("Context:")
    for line in result.context_lines:
        lines.append(f"- {line}")
    lines.append("")
    lines.append("Note: This is a coaching heuristic, not a medical decision rule.")
    return lines


def _format_recovery_telegram_message(
    target_date: str,
    result,
    athlete_name: str | None = None,
    lang: str = "en",
) -> str:
    if lang == "ru":
        return _format_recovery_telegram_message_ru(target_date, result, athlete_name)

    title = f"Garmin recovery for {athlete_name}" if athlete_name else "Garmin recovery"
    lines = [
        f"{title} - {target_date}",
        f"Status: {result.color}",
        f"Today: {result.recommendation}",
    ]
    if result.reasons:
        lines.append("")
        lines.append("Drivers:")
        for reason in result.reasons[:4]:
            lines.append(f"- {reason}")
    lines.append("")
    lines.append("Context:")
    for line in _select_context_lines_for_message(result.context_lines, limit=8):
        lines.append(f"- {line}")
    lines.append("")
    lines.append("Heuristic only, not medical advice.")
    return "\n".join(lines)


def _format_recovery_telegram_message_ru(
    target_date: str,
    result,
    athlete_name: str | None = None,
) -> str:
    title = f"Garmin: восстановление для {athlete_name}" if athlete_name else "Garmin: восстановление"
    lines = [
        f"{title} - {target_date}",
        f"Статус: {_translate_color_ru(result.color)}",
        f"Сегодня: {_translate_recommendation_ru(result.recommendation)}",
    ]
    if result.reasons:
        lines.append("")
        lines.append("Почему:")
        for reason in result.reasons[:4]:
            lines.append(f"- {_postprocess_ru_text(_translate_reason_ru(reason))}")
    lines.append("")
    lines.append("Контекст:")
    for line in _select_context_lines_for_message(result.context_lines, limit=12):
        lines.append(f"- {_postprocess_ru_text(_translate_context_line_ru(line))}")
    lines.append("")
    lines.append("Это не медицинская рекомендация, а тренировочная эвристика.")
    return "\n".join(lines)


def _load_profile_environment(profile: str, profile_dir: Path = DEFAULT_PROFILE_DIR) -> None:
    profile_path = profile_dir / f"{profile}.env"
    if not profile_path.exists():
        raise SystemExit(f"Profile env file not found: {profile_path}")

    for raw_line in profile_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environ[key.strip()] = value.strip()


def _env_flag(name: str) -> bool:
    value = (environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _preferred_strength_days_from_env() -> set[int] | None:
    raw_value = (environ.get("PREFERRED_STRENGTH_DAYS") or "").strip()
    if not raw_value:
        return None

    normalized = raw_value.lower()
    if normalized in {"any", "all", "*"}:
        return None

    days: set[int] = set()
    for item in raw_value.split(","):
        token = item.strip().lower()
        if not token:
            continue
        weekday = WEEKDAY_ALIASES.get(token)
        if weekday is None:
            raise SystemExit(
                "Invalid PREFERRED_STRENGTH_DAYS value. Use comma-separated weekdays like mon,thu,sat,sun or 'any'."
            )
        days.add(weekday)

    return days or None


def _select_context_lines_for_message(lines: list[str], limit: int) -> list[str]:
    priority_prefixes = (
        "Training focus: ",
        "Garmin training status: ",
        "Menstrual cycle context: ",
        "Garmin HRV status: ",
    )
    prioritized: list[str] = []
    regular: list[str] = []
    for line in lines:
        if any(line.startswith(prefix) for prefix in priority_prefixes):
            prioritized.append(line)
        else:
            regular.append(line)

    selected_regular = regular[: max(limit - len(prioritized), 0)]
    return selected_regular + prioritized[:limit]


def _postprocess_ru_text(value: str) -> str:
    translated = (
        value.replace(
            "Today is not a preferred strength day for this profile, so aerobic work is favored instead.",
            "\u0421\u0435\u0433\u043e\u0434\u043d\u044f \u043d\u0435\u043f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0438\u0442"
            "\u0435\u043b\u044c\u043d\u044b\u0439 \u0434\u0435\u043d\u044c \u0434\u043b\u044f \u0441\u0438\u043b\u043e\u0432\u043e"
            "\u0439 \u0443 \u044d\u0442\u043e\u0433\u043e \u043f\u0440\u043e\u0444\u0438\u043b\u044f, \u043f\u043e\u044d"
            "\u0442\u043e\u043c\u0443 \u043b\u0443\u0447\u0448\u0435 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u0430\u044d"
            "\u0440\u043e\u0431\u043d\u0443\u044e \u0440\u0430\u0431\u043e\u0442\u0443.",
        )
        .replace(
            "Preferred strength days: ",
            "\u041f\u0440\u0435\u0434\u043f\u043e\u0447\u0442\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 "
            "\u0434\u043d\u0438 \u0434\u043b\u044f \u0441\u0438\u043b\u043e\u0432\u043e\u0439: ",
        )
        .replace("today is", "\u0441\u0435\u0433\u043e\u0434\u043d\u044f")
        .replace(
            "Early-cycle menstrual context was detected, so hard training is deprioritized unless subjective feel is clearly excellent.",
            "\u0420\u0430\u043d\u043d\u0438\u0439 \u044d\u0442\u0430\u043f \u0446\u0438\u043a\u043b\u0430 \u0443\u0447\u0442\u0435\u043d, "
            "\u043f\u043e\u044d\u0442\u043e\u043c\u0443 \u0442\u044f\u0436\u0435\u043b\u0430\u044f \u0442\u0440\u0435\u043d\u0438\u0440"
            "\u043e\u0432\u043a\u0430 \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u0434\u0435\u043f\u0440\u0438\u043e\u0440\u0438\u0442"
            "\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043d\u0430, \u0435\u0441\u043b\u0438 \u0441\u0430\u043c\u043e\u0447\u0443\u0432"
            "\u0441\u0442\u0432\u0438\u0435 \u043d\u0435 \u043e\u0442\u043b\u0438\u0447\u043d\u043e\u0435.",
        )
    )
    translated = re.sub(
        r"from (\d+) prior nights",
        lambda match: f"\u0438\u0437 {match.group(1)} \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0438\u0445 \u043d\u043e\u0447\u0435\u0439",
        translated,
    )
    translated = re.sub(
        r"\((\d+) prior nights\)",
        lambda match: f"({match.group(1)} \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0438\u0445 \u043d\u043e\u0447\u0435\u0439)",
        translated,
    )
    return translated


def _translate_color_ru(value: str) -> str:
    return {
        "GREEN": "ЗЕЛЕНЫЙ",
        "YELLOW": "ЖЕЛТЫЙ",
        "RED": "КРАСНЫЙ",
    }.get(value, value)


def _translate_recommendation_ru(value: str) -> str:
    return {
        "A) recovery/rest": "A) восстановление / отдых",
        "B) easy Zone 2": "B) легкая Zone 2",
        "C) normal aerobic training": "C) обычная аэробная тренировка",
        "D) strength training": "D) силовая тренировка",
        "E) hard training": "E) тяжелая тренировка",
    }.get(value, value)


def _translate_reason_ru(value: str) -> str:
    if value.startswith("Sleep was "):
        return value.replace("Sleep was", "Сон был").replace("versus a recent baseline of", "по сравнению с недавней базой")
    if value.startswith("Sleep score was low at "):
        return value.replace("Sleep score was low at", "Оценка сна низкая:").replace("/100.", "/100.")
    if value.startswith("Sleep score was fair at "):
        return value.replace("Sleep score was fair at", "Оценка сна средняя:").replace("/100.", "/100.")
    if value.startswith("Overnight HRV was suppressed at "):
        return value.replace("Overnight HRV was suppressed at", "Ночная HRV снижена:").replace("versus", "против").replace("baseline.", "базы.")
    if value.startswith("Overnight HRV was a bit below baseline at "):
        return value.replace("Overnight HRV was a bit below baseline at", "Ночная HRV немного ниже базы:").replace("versus", "против").replace("ms.", "мс.")
    if value.startswith("Resting HR was elevated at "):
        return value.replace("Resting HR was elevated at", "Пульс покоя повышен:").replace("versus", "против").replace("baseline.", "базы.")
    if value.startswith("Resting HR was mildly elevated at "):
        return value.replace("Resting HR was mildly elevated at", "Пульс покоя слегка повышен:").replace("versus", "против").replace("bpm.", "уд/мин.")
    if value.startswith("Body Battery at wake-up was low at "):
        return value.replace("Body Battery at wake-up was low at", "Body Battery утром низкий:")
    if value.startswith("Body Battery at wake-up was moderate at "):
        return value.replace("Body Battery at wake-up was moderate at", "Body Battery утром средний:")
    if value.startswith("Average stress was high at "):
        return value.replace("Average stress was high at", "Средний стресс высокий:")
    if value.startswith("Average stress was somewhat elevated at "):
        return value.replace("Average stress was somewhat elevated at", "Средний стресс немного повышен:")
    if value.startswith("Session RPE load is high: "):
        return value.replace("Session RPE load is high:", "Нагрузка по session RPE высокая:")
    if value.startswith("Session RPE load is building: "):
        return value.replace("Session RPE load is building:", "Нагрузка по session RPE накапливается:")
    if value.startswith("You are on a "):
        return value.replace("You are on a", "Сейчас серия из").replace("-day meaningful training streak.", " дней значимых тренировок подряд.")
    if value.startswith("Recent kite sessions are missing HR"):
        return "В последних кайт-сессиях нет HR, поэтому Garmin, вероятно, занижает нагрузку."
    if value.startswith("A recent strength session was detected"):
        return "Недавно была силовая тренировка, поэтому подряд еще одну силовую сегодня лучше не ставить."
    if value.startswith("Today's Garmin recovery data is not yet available or synced"):
        return (
            "Сегодняшние recovery-данные Garmin пока недоступны или еще не "
            "синхронизированы, поэтому рекомендация больше опирается "
            "на недавнюю нагрузку и контекст профиля."
        )
    return value


def _translate_context_line_ru(value: str) -> str:
    if value.startswith("Training focus: "):
        return f"Фокус тренинга: {_translate_focus_phrase_ru(value.removeprefix('Training focus: '))}"
    if value.startswith("Garmin training status: "):
        return f"Статус тренинга Garmin: {_translate_training_status_phrase_ru(value.removeprefix('Garmin training status: '))}"
    if value.startswith("Menstrual cycle context: "):
        return f"Контекст цикла: {_translate_menstrual_context_ru(value.removeprefix('Menstrual cycle context: '))}"

    replacements = {
        "Sleep: ": "Сон: ",
        "Sleep score: ": "Оценка сна: ",
        "Overnight HRV: ": "Ночная HRV: ",
        "Resting HR: ": "Пульс покоя: ",
        "Body Battery (wake/current): ": "Body Battery (утро/сейчас): ",
        "Average stress: ": "Средний стресс: ",
        "Recent load: ": "Недавняя нагрузка: ",
        "Consecutive meaningful training days: ": "Дней значимых тренировок подряд: ",
        "Recent strength session: ": "Последняя силовая: ",
        "Recent kite sessions without HR: ": "Последние кайт-сессии без HR: ",
        "Garmin HRV status: ": "Статус HRV Garmin: ",
    }
    for source, target in replacements.items():
        if value.startswith(source):
            translated = value.replace(source, target, 1)
            return (
                translated.replace("vs baseline", "vs база")
                .replace("wake/current", "утро/сейчас")
                .replace('"above baseline, generally positive"', '"выше базы, это хорошо"')
                .replace('"below baseline, generally positive"', '"ниже базы, это хорошо"')
                .replace('"below baseline, a bit worse than usual"', '"ниже базы, это хуже обычного"')
                .replace('"above baseline, a bit worse than usual"', '"выше базы, это хуже обычного"')
                .replace('"around baseline"', '"примерно как обычно"')
            )
    return value


def _translate_focus_phrase_ru(value: str) -> str:
    return {
        "high aerobic shortage": "не хватает высокоаэробной нагрузки",
        "low aerobic shortage": "не хватает низкоаэробной нагрузки",
        "anaerobic shortage": "не хватает анаэробной нагрузки",
        "balanced": "баланс нормальный",
    }.get(value, value)


def _translate_training_status_phrase_ru(value: str) -> str:
    return {
        "productive": "продуктивно",
        "maintaining": "поддержание формы",
        "recovery": "восстановление",
        "peaking": "выход на пик",
        "strained": "перегрузка",
        "unproductive": "непродуктивно",
        "detraining": "детренированность",
        "overreaching": "перенапряжение",
    }.get(value, value)


def _translate_menstrual_context_ru(value: str) -> str:
    translated = value.replace("cycle day", "день цикла")
    translated = translated.replace("active period", "активная менструация")
    translated = translated.replace("symptoms:", "симптомы:")
    translated = translated.replace("data available", "данные доступны")
    translated = translated.replace("phase menstrual", "менструальная фаза")
    translated = translated.replace("phase follicular", "фолликулярная фаза")
    translated = translated.replace("phase ovulatory", "овуляторная фаза")
    translated = translated.replace("phase luteal", "лютеиновая фаза")
    translated = translated.replace("cycle type irregular", "нерегулярный цикл")
    translated = translated.replace("cycle type regular", "регулярный цикл")
    translated = re.sub(r"\bphase\b", "фаза", translated)
    translated = re.sub(r"\bcycle type\b", "тип цикла", translated)
    return translated
