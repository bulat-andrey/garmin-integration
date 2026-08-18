from pathlib import Path

from garmin_recovery.telegram_listener import (
    TelegramCommandRequest,
    _available_profiles,
    _extract_command_request,
    _is_authorized,
    _normalize_command_text,
)

DIRECT_CHAT_ID = 123456789
GROUP_CHAT_ID = -1001234567890
USER_ID = 123456789


def test_normalize_command_text_accepts_plain_command() -> None:
    assert _normalize_command_text("/andrei", "pager_bot") == "andrei"


def test_normalize_command_text_accepts_matching_bot_mention() -> None:
    assert _normalize_command_text("/vika@pager_bot", "pager_bot") == "vika"


def test_normalize_command_text_ignores_other_bot_mentions() -> None:
    assert _normalize_command_text("/vika@other_bot", "pager_bot") is None


def test_extract_command_request_reads_private_message() -> None:
    update = {
        "update_id": 123,
        "message": {
            "text": "/andrei",
            "chat": {"id": DIRECT_CHAT_ID},
            "from": {"id": USER_ID},
        },
    }

    request = _extract_command_request(update, "pager_bot")

    assert request == TelegramCommandRequest(
        update_id=123,
        chat_id=DIRECT_CHAT_ID,
        user_id=USER_ID,
        command="andrei",
        update_kind="message",
    )


def test_is_authorized_requires_allowed_user_and_chat_for_messages() -> None:
    request = TelegramCommandRequest(
        update_id=1,
        chat_id=GROUP_CHAT_ID,
        user_id=USER_ID,
        command="vika",
        update_kind="message",
    )

    assert _is_authorized(
        request,
        allowed_user_ids={USER_ID},
        allowed_chat_ids={GROUP_CHAT_ID},
    )
    assert not _is_authorized(
        request,
        allowed_user_ids={999},
        allowed_chat_ids={GROUP_CHAT_ID},
    )


def test_is_authorized_allows_channel_post_by_chat_only() -> None:
    request = TelegramCommandRequest(
        update_id=1,
        chat_id=GROUP_CHAT_ID,
        user_id=None,
        command="andrei",
        update_kind="channel_post",
    )

    assert _is_authorized(
        request,
        allowed_user_ids={USER_ID},
        allowed_chat_ids={GROUP_CHAT_ID},
    )


def test_available_profiles_reads_env_stems(tmp_path: Path) -> None:
    (tmp_path / "andrei.env").write_text("", encoding="utf-8")
    (tmp_path / "vika.env").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")

    assert _available_profiles(tmp_path) == {"andrei", "vika"}
