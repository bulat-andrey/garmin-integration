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


def send_telegram_message(token: str, chat_id: str, text: str, timeout: int = 30) -> TelegramResponse:
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = request.Request(endpoint, data=payload, method="POST")

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

    return TelegramResponse(ok=True, description=parsed.get("description"))
