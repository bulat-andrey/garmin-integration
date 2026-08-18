from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import date
from os import environ
from pathlib import Path

from .analysis import DEFAULT_RPE_PATH
from .cli import (
    DEFAULT_PROFILE_DIR,
    _collect_recovery_result,
    _format_recovery_telegram_message,
    _load_profile_environment,
)
from .telegram import (
    TelegramNotificationError,
    get_telegram_bot_username,
    get_telegram_updates,
    send_telegram_message,
)


DEFAULT_OFFSET_PATH = Path.home() / ".config" / "garmin-recovery" / "telegram-listener.offset"
DEFAULT_RETRY_SECONDS = 5
DEFAULT_POLL_TIMEOUT_SECONDS = 50


@dataclass(slots=True)
class TelegramCommandRequest:
    update_id: int
    chat_id: int
    user_id: int | None
    command: str
    update_kind: str


def telegram_command_listener_main() -> None:
    parser = argparse.ArgumentParser(
        description="Listen for Telegram bot commands like /andrei or /vika and return on-demand recovery messages."
    )
    parser.add_argument(
        "--offset-file",
        default=environ.get("TELEGRAM_COMMAND_OFFSET_PATH", str(DEFAULT_OFFSET_PATH)),
        help="Path to the Telegram update offset file.",
    )
    parser.add_argument(
        "--profile-dir",
        default=environ.get("TELEGRAM_COMMAND_PROFILE_DIR", str(DEFAULT_PROFILE_DIR)),
        help="Directory containing profile env files.",
    )
    parser.add_argument(
        "--rpe-file",
        default=environ.get("TELEGRAM_COMMAND_RPE_FILE", str(DEFAULT_RPE_PATH)),
        help="Path to the manual RPE CSV.",
    )
    args = parser.parse_args()

    token = (environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required for telegram-command-listener.")

    allowed_user_ids = _parse_id_set(environ.get("TELEGRAM_COMMAND_ALLOWED_USER_IDS"))
    allowed_chat_ids = _parse_id_set(environ.get("TELEGRAM_COMMAND_ALLOWED_CHAT_IDS"))
    if not allowed_user_ids and not allowed_chat_ids:
        raise SystemExit(
            "At least one of TELEGRAM_COMMAND_ALLOWED_USER_IDS or TELEGRAM_COMMAND_ALLOWED_CHAT_IDS must be configured."
        )

    offset_path = Path(args.offset_file)
    profile_dir = Path(args.profile_dir)
    rpe_file = Path(args.rpe_file)
    listener = TelegramCommandListener(
        token=token,
        allowed_user_ids=allowed_user_ids,
        allowed_chat_ids=allowed_chat_ids,
        offset_path=offset_path,
        profile_dir=profile_dir,
        rpe_file=rpe_file,
        skip_existing_updates=_env_flag("TELEGRAM_COMMAND_SKIP_EXISTING_UPDATES", default=True),
    )
    listener.run_forever()


class TelegramCommandListener:
    def __init__(
        self,
        token: str,
        allowed_user_ids: set[int],
        allowed_chat_ids: set[int],
        offset_path: Path,
        profile_dir: Path,
        rpe_file: Path,
        skip_existing_updates: bool,
    ) -> None:
        self.token = token
        self.allowed_user_ids = allowed_user_ids
        self.allowed_chat_ids = allowed_chat_ids
        self.offset_path = offset_path
        self.profile_dir = profile_dir
        self.rpe_file = rpe_file
        self.skip_existing_updates = skip_existing_updates
        self.bot_username = get_telegram_bot_username(token)
        self.available_profiles = _available_profiles(profile_dir)

    def run_forever(self) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        offset = _load_offset(self.offset_path)

        if offset is None and self.skip_existing_updates:
            updates = get_telegram_updates(self.token, timeout=0)
            if updates:
                offset = max(int(update["update_id"]) for update in updates) + 1
                _save_offset(self.offset_path, offset)

        while True:
            try:
                updates = get_telegram_updates(
                    self.token,
                    offset=offset,
                    timeout=DEFAULT_POLL_TIMEOUT_SECONDS,
                )
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    _save_offset(self.offset_path, offset)
                    request = _extract_command_request(update, self.bot_username)
                    if request is None:
                        continue
                    if not _is_authorized(
                        request,
                        allowed_user_ids=self.allowed_user_ids,
                        allowed_chat_ids=self.allowed_chat_ids,
                    ):
                        continue
                    self._handle_request(request)
            except Exception as exc:
                print(f"Telegram listener error: {exc}", flush=True)
                time.sleep(DEFAULT_RETRY_SECONDS)

    def _handle_request(self, request: TelegramCommandRequest) -> None:
        if request.command in {"start", "help"}:
            send_telegram_message(self.token, str(request.chat_id), self._help_text())
            return

        if request.command not in self.available_profiles:
            send_telegram_message(self.token, str(request.chat_id), self._help_text())
            return

        try:
            message = _build_profile_recovery_message(
                profile=request.command,
                target_date=date.today().isoformat(),
                rpe_file=self.rpe_file,
                profile_dir=self.profile_dir,
            )
        except Exception as exc:
            message = f"Could not generate recovery report for {request.command}: {exc}"

        send_telegram_message(self.token, str(request.chat_id), message)

    def _help_text(self) -> str:
        commands = ", ".join(f"/{profile}" for profile in sorted(self.available_profiles))
        return (
            "Available commands:\n"
            f"{commands}\n\n"
            "Recommended use: send commands to the bot in a direct chat."
        )


def _build_profile_recovery_message(
    profile: str,
    target_date: str,
    rpe_file: Path,
    profile_dir: Path,
) -> str:
    with _profile_environment(profile, profile_dir):
        result = asyncio.run(_collect_recovery_result(target_date, rpe_file))
        athlete_name = environ.get("ATHLETE_NAME")
        lang = (environ.get("MESSAGE_LANGUAGE") or "en").lower()
        return _format_recovery_telegram_message(target_date, result, athlete_name, lang)


class _profile_environment:
    def __init__(self, profile: str, profile_dir: Path) -> None:
        self.profile = profile
        self.profile_dir = profile_dir
        self.original = dict(environ)

    def __enter__(self) -> None:
        _load_profile_environment(self.profile, self.profile_dir)

    def __exit__(self, exc_type, exc, tb) -> None:
        current_keys = list(environ.keys())
        for key in current_keys:
            if key not in self.original:
                del environ[key]
        for key, value in self.original.items():
            environ[key] = value


def _available_profiles(profile_dir: Path) -> set[str]:
    if not profile_dir.exists():
        return set()
    return {path.stem for path in profile_dir.glob("*.env") if path.is_file()}


def _extract_command_request(update: dict, bot_username: str) -> TelegramCommandRequest | None:
    for update_kind in ("message", "channel_post"):
        payload = update.get(update_kind)
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if not isinstance(text, str):
            continue
        command = _normalize_command_text(text, bot_username)
        if command is None:
            continue

        chat = payload.get("chat") or {}
        from_user = payload.get("from") or {}
        try:
            update_id = int(update["update_id"])
            chat_id = int(chat["id"])
        except (KeyError, TypeError, ValueError):
            return None

        user_id = None
        if from_user.get("id") is not None:
            try:
                user_id = int(from_user["id"])
            except (TypeError, ValueError):
                user_id = None

        return TelegramCommandRequest(
            update_id=update_id,
            chat_id=chat_id,
            user_id=user_id,
            command=command,
            update_kind=update_kind,
        )
    return None


def _normalize_command_text(text: str, bot_username: str) -> str | None:
    first_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not first_token.startswith("/"):
        return None

    command_token = first_token[1:]
    if not command_token:
        return None

    command_name, _, mentioned_bot = command_token.partition("@")
    if mentioned_bot and mentioned_bot.lower() != bot_username.lower():
        return None

    return command_name.lower()


def _is_authorized(
    request: TelegramCommandRequest,
    allowed_user_ids: set[int],
    allowed_chat_ids: set[int],
) -> bool:
    if allowed_chat_ids and request.chat_id not in allowed_chat_ids:
        return False

    if request.update_kind == "channel_post":
        return True

    if allowed_user_ids and request.user_id not in allowed_user_ids:
        return False

    return True


def _parse_id_set(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()
    result: set[int] = set()
    for part in raw_value.split(","):
        token = part.strip()
        if not token:
            continue
        result.add(int(token))
    return result


def _load_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    raw_value = path.read_text(encoding="utf-8").strip()
    if not raw_value:
        return None
    return int(raw_value)


def _save_offset(path: Path, offset: int) -> None:
    path.write_text(f"{offset}\n", encoding="utf-8")


def _env_flag(name: str, default: bool = False) -> bool:
    value = (environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}
