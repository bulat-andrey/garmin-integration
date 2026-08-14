from __future__ import annotations

import argparse
import asyncio
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


def analyze_recovery_main() -> None:
    parser = argparse.ArgumentParser(description="Analyze today's recovery from Garmin + manual RPE.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--rpe-file", default=str(DEFAULT_RPE_PATH), help="Path to the manual RPE CSV.")
    args = parser.parse_args()
    asyncio.run(_run_analyze_recovery(args.date, Path(args.rpe_file)))


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
            athlete_name=args.athlete_name,
            dry_run=args.dry_run,
        )
    )


async def _run_analyze_recovery(target_date: str, rpe_file: Path) -> None:
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
    athlete_name: str | None,
    dry_run: bool,
) -> None:
    if profile:
        _load_profile_environment(profile)

    result = await _collect_recovery_result(target_date, rpe_file)
    resolved_athlete_name = athlete_name or environ.get("ATHLETE_NAME")
    message = _format_recovery_telegram_message(target_date, result, resolved_athlete_name)

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

    try:
        async with GarminMcpClient() as garmin:
            health_today = await garmin.get_health_summary(target_date)
            sleep_today = await garmin.get_sleep(target_date)
            health_range = await garmin.get_health_range(start_date, end_date)
            sleep_range = await garmin.get_sleep_range(start_date, end_date)
            activities = await garmin.get_recent_activities_with_details(start_date, end_date)
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
) -> str:
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
    for line in result.context_lines[:6]:
        lines.append(f"- {line}")
    lines.append("")
    lines.append("Heuristic only, not medical advice.")
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
