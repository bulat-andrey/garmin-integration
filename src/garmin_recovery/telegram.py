from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import parse, request
from urllib.error import HTTPError, URLError


class TelegramNotificationError(RuntimeError):
    """Raised when Telegram delivery fails."""


@dataclass(slots=True)
class TelegramResponse:
    ok: bool
    description: str | None = None


def get_telegram_bot_username(token: str, timeout: int = 30) -> str:
    parsed = _telegram_api_request(token, "getMe", {}, timeout=timeout)
    username = ((parsed.get("result") or {}).get("username") or "").strip()
    if not username:
        raise TelegramNotificationError("Telegram getMe did not return a bot username")
    return username


def get_telegram_updates(
    token: str,
    offset: int | None = None,
    timeout: int = 60,
) -> list[dict]:
    payload: dict[str, str | int] = {
        "timeout": timeout,
        "allowed_updates": json.dumps(["message", "channel_post"]),
    }
    if offset is not None:
        payload["offset"] = offset
    parsed = _telegram_api_request(token, "getUpdates", payload, timeout=timeout + 10)
    result = parsed.get("result")
    return result if isinstance(result, list) else []


def send_telegram_message(token: str, chat_id: str, text: str, timeout: int = 30) -> TelegramResponse:
    _telegram_api_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        },
        timeout=timeout,
    )
    return TelegramResponse(ok=True, description=None)


def _telegram_api_request(
    token: str,
    method: str,
    payload: dict[str, str | int],
    timeout: int,
) -> dict:
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    encoded = parse.urlencode(payload).encode("utf-8")
    req = request.Request(endpoint, data=encoded, method="POST")

    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TelegramNotificationError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise TelegramNotificationError(str(exc.reason)) from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TelegramNotificationError("Telegram returned non-JSON response") from exc

    if not parsed.get("ok"):
        raise TelegramNotificationError(parsed.get("description", "Unknown Telegram API error"))

    return parsed
