from __future__ import annotations

import json
import os
import tomllib
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ENV_CODEX_CONFIG = "GARMIN_CODEX_CONFIG"
DEFAULT_CODEX_CONFIG = Path.home() / ".codex" / "config.toml"


class GarminMcpError(RuntimeError):
    """Base Garmin MCP client error."""


class GarminMcpConfigError(GarminMcpError):
    """Raised when the Garmin MCP server config cannot be found."""


class GarminMcpToolError(GarminMcpError):
    """Raised when the Garmin MCP server returns an error payload."""


@dataclass(slots=True)
class GarminServerConfig:
    command: str
    args: list[str]
    env: dict[str, str]


@dataclass(slots=True)
class GarminActivity:
    activity_id: int
    name: str
    activity_type: str
    date: str
    start_local: str
    duration_min: int
    moving_min: int | None
    distance_km: float | None
    calories: float | None
    description: str
    average_hr: float | None = None
    max_hr: float | None = None
    garmin_load: float | None = None
    training_effect_label: str | None = None
    moderate_intensity_minutes: int | None = None
    vigorous_intensity_minutes: int | None = None
    has_hr_data: bool = False

    @property
    def total_intensity_minutes(self) -> int:
        return (self.moderate_intensity_minutes or 0) + (self.vigorous_intensity_minutes or 0)


def load_garmin_server_config(
    config_path: Path | None = None, server_name: str = "garmin"
) -> GarminServerConfig:
    resolved_config_path = config_path
    if resolved_config_path is None:
        override = os.environ.get(ENV_CODEX_CONFIG)
        resolved_config_path = Path(override) if override else DEFAULT_CODEX_CONFIG

    if not resolved_config_path.exists():
        raise GarminMcpConfigError(f"Codex config not found: {resolved_config_path}")

    config = tomllib.loads(resolved_config_path.read_text(encoding="utf-8"))
    servers = config.get("mcp_servers", {})
    server = servers.get(server_name)
    if not isinstance(server, dict):
        raise GarminMcpConfigError(
            f"MCP server '{server_name}' was not found in {resolved_config_path}"
        )

    command = server.get("command")
    args = server.get("args")
    env = server.get("env", {})
    if not isinstance(command, str) or not isinstance(args, list):
        raise GarminMcpConfigError(f"MCP server '{server_name}' is missing command or args")

    normalized_env = {str(key): str(value) for key, value in env.items()}
    return GarminServerConfig(command=command, args=[str(arg) for arg in args], env=normalized_env)


def parse_garmin_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _minutes_from_seconds(value: Any) -> int:
    try:
        return int(round(float(value) / 60))
    except (TypeError, ValueError):
        return 0


def _distance_km_from_summary(summary: dict[str, Any]) -> float | None:
    distance = summary.get("distance")
    if isinstance(distance, dict):
        meters = distance.get("meters")
        if meters is None:
            return None
        return round(float(meters) / 1000, 2)
    if distance is None:
        return None
    return round(float(distance) / 1000, 2)


def activity_from_summary(summary: dict[str, Any]) -> GarminActivity:
    start_local = summary.get("startTimeLocal", {})
    activity_type = summary.get("activityType", {})
    duration = summary.get("duration", {})
    distance = summary.get("distance", {})

    return GarminActivity(
        activity_id=int(summary["activityId"]),
        name=str(summary.get("activityName") or ""),
        activity_type=str(activity_type.get("typeKey") or "unknown"),
        date=str(start_local.get("date") or ""),
        start_local=str(start_local.get("datetime") or ""),
        duration_min=_minutes_from_seconds(duration.get("seconds")),
        moving_min=_minutes_from_seconds(summary.get("movingDuration")) or None,
        distance_km=round(float(distance.get("meters", 0.0)) / 1000, 2) if distance else None,
        calories=float(summary["calories"]) if summary.get("calories") is not None else None,
        description=str(summary.get("description") or ""),
    )


def enrich_activity_from_detail(
    activity: GarminActivity, detail_payload: dict[str, Any]
) -> GarminActivity:
    detail = detail_payload.get("data", {}).get("activity", {})
    summary = detail.get("summaryDTO", {})

    return replace(
        activity,
        average_hr=float(summary["averageHR"]) if summary.get("averageHR") is not None else None,
        max_hr=float(summary["maxHR"]) if summary.get("maxHR") is not None else None,
        garmin_load=float(summary["activityTrainingLoad"])
        if summary.get("activityTrainingLoad") is not None
        else None,
        training_effect_label=str(summary.get("trainingEffectLabel") or "") or None,
        moderate_intensity_minutes=int(summary["moderateIntensityMinutes"])
        if summary.get("moderateIntensityMinutes") is not None
        else None,
        vigorous_intensity_minutes=int(summary["vigorousIntensityMinutes"])
        if summary.get("vigorousIntensityMinutes") is not None
        else None,
        has_hr_data=summary.get("averageHR") is not None,
        moving_min=_minutes_from_seconds(summary.get("movingDuration")) or activity.moving_min,
        distance_km=_distance_km_from_summary(summary) or activity.distance_km,
        calories=float(summary["calories"]) if summary.get("calories") is not None else activity.calories,
        start_local=str(summary.get("startTimeLocal") or activity.start_local),
        date=str(summary.get("startTimeLocal", "")[:10] or activity.date),
    )


class GarminMcpClient:
    def __init__(self, config: GarminServerConfig | None = None) -> None:
        self._config = config or load_garmin_server_config()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._errlog: TextIO | None = None

    async def __aenter__(self) -> "GarminMcpClient":
        server = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env={**os.environ, **self._config.env},
        )

        self._stack = AsyncExitStack()
        # Suppress noisy FastMCP banners and auth chatter during normal CLI use.
        self._errlog = open(os.devnull, "w", encoding="utf-8")
        read, write = await self._stack.enter_async_context(stdio_client(server, errlog=self._errlog))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        if self._errlog is not None:
            self._errlog.close()
        self._stack = None
        self._session = None
        self._errlog = None

    async def call_json(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise GarminMcpError("Garmin MCP session is not initialized")

        result = await self._session.call_tool(tool_name, arguments)
        text_parts = [item.text for item in result.content if hasattr(item, "text")]
        text = "\n".join(text_parts).strip()
        payload = json.loads(text)
        if "error" in payload:
            message = payload["error"].get("message", "Unknown Garmin MCP error")
            raise GarminMcpToolError(f"{tool_name} failed: {message}")
        return payload

    async def list_activities(
        self, start_date: str, end_date: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            arguments: dict[str, Any] = {
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
                "unit": "metric",
            }
            if cursor:
                arguments["cursor"] = cursor

            payload = await self.call_json("query_activities", arguments)
            activities.extend(payload.get("data", {}).get("activities", []))
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            cursor = pagination.get("cursor")
            if not cursor:
                break

        return activities

    async def get_activity_summary(self, activity_id: int) -> dict[str, Any]:
        payload = await self.call_json("query_activities", {"activity_id": activity_id, "unit": "metric"})
        return payload.get("data", {}).get("activity", {})

    async def get_activity_details(self, activity_id: int) -> dict[str, Any]:
        return await self.call_json("get_activity_details", {"activity_id": activity_id, "unit": "metric"})

    async def get_health_summary(self, target_date: str) -> dict[str, Any]:
        payload = await self.call_json("query_health_summary", {"date": target_date, "unit": "metric"})
        return payload.get("data", {})

    async def get_health_range(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            arguments: dict[str, Any] = {
                "start_date": start_date,
                "end_date": end_date,
                "limit": 10,
                "unit": "metric",
            }
            if cursor:
                arguments["cursor"] = cursor

            payload = await self.call_json("query_health_summary", arguments)
            summaries.extend(payload.get("data", {}).get("summaries", []))
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_more"):
                break
            cursor = pagination.get("cursor")
            if not cursor:
                break

        return summaries

    async def get_sleep(self, target_date: str) -> dict[str, Any]:
        payload = await self.call_json("query_sleep_data", {"date": target_date})
        return payload.get("data", {})

    async def get_sleep_range(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        payload = await self.call_json(
            "query_sleep_data",
            {"start_date": start_date, "end_date": end_date},
        )
        return payload.get("data", {}).get("sleep_data", [])

    async def get_recent_activities_with_details(
        self, start_date: str, end_date: str
    ) -> list[GarminActivity]:
        activities = await self.list_activity_summaries(start_date, end_date)
        detailed_activities: list[GarminActivity] = []
        for activity in activities:
            detailed_activities.append(await self.enrich_activity(activity))
        return detailed_activities

    async def list_activity_summaries(
        self, start_date: str, end_date: str
    ) -> list[GarminActivity]:
        activities = [activity_from_summary(item) for item in await self.list_activities(start_date, end_date)]
        activities.sort(
            key=lambda item: parse_garmin_datetime(item.start_local) or datetime.min,
            reverse=True,
        )
        return activities

    async def enrich_activity(self, activity: GarminActivity) -> GarminActivity:
        try:
            detail_payload = await self.get_activity_details(activity.activity_id)
        except GarminMcpToolError:
            return activity
        return enrich_activity_from_detail(activity, detail_payload)


def format_date(value: date) -> str:
    return value.isoformat()
