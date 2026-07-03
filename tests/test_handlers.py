from unittest.mock import patch, MagicMock


def make_message(text="hello", user_id=123, chat_id=456, chat_type="private"):
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.reply_to_message = None
    return msg


HANDLER_PATCHES = {
    "bot.handlers.should_respond": True,
    "bot.handlers.is_rate_limited": False,
    "bot.handlers.BOT_INFO": MagicMock(id=42, username="testbot"),
}


def test_handle_message_calls_ask_ai():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", return_value="AI reply") as mock_ask,
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message(text="hello")
        handle_message(msg)
        mock_ask.assert_called_once_with(123, "hello")
        mock_send.assert_called_once_with(msg, "AI reply")


def test_handle_message_skips_when_not_responding():
    with (
        patch("bot.handlers.should_respond", return_value=False),
        patch("bot.handlers.ask_ai") as mock_ask,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        mock_ask.assert_not_called()


def test_handle_message_rate_limited():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=True),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        mock_ask.assert_not_called()
        mock_bot.send_message.assert_called_once()
        assert "daily limit" in mock_bot.send_message.call_args[0][1]


def test_handle_message_sends_generic_error():
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", side_effect=Exception("API key invalid")),
        patch("bot.handlers.bot") as mock_bot,
    ):
        from bot.handlers import handle_message

        handle_message(make_message())
        error_msg = mock_bot.send_message.call_args[0][1]
        assert "Something went wrong" in error_msg
        assert "API key" not in error_msg


def test_handle_message_none_text_skipped():
    """Stickers/photos/edits arriving with text=None must NOT call ask_ai
    (would burn rate limit and AI quota for no reason)."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.send_reply") as mock_send,
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message()
        msg.text = None
        handle_message(msg)
        mock_ask.assert_not_called()
        mock_send.assert_not_called()


def test_handle_message_mention_only_skipped():
    """In a group, '@testbot' alone strips to empty — don't call ask_ai."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai") as mock_ask,
        patch("bot.handlers.send_reply"),
        patch("bot.handlers.bot"),
    ):
        from bot.handlers import handle_message

        msg = make_message(text="@testbot")
        handle_message(msg)
        mock_ask.assert_not_called()


# ── /about ────────────────────────────────────────────────────────────────────


def test_cmd_about_with_sqlite():
    """When SQLite is configured, /about should reference SQLite."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.generate", return_value="I'm a friendly helper."),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "SQLite" in sent
        assert "stateless" not in sent


def test_cmd_about_includes_commit_sha_when_set():
    """When COMMIT_SHA is populated (worker booted inside a git repo),
    /about exposes a Version line so users can validate which commit is
    live."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.COMMIT_SHA", "abc1234"),
        patch("bot.handlers.generate", return_value="I'm a friendly helper."),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Version: abc1234" in sent


def test_cmd_about_omits_version_line_when_sha_unknown():
    """If git rev-parse failed at boot, the Version line is dropped
    entirely rather than showing 'unknown' — clearer for the user."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.COMMIT_SHA", ""),
        patch("bot.handlers.generate", return_value="I'm a friendly helper."),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Version" not in sent


def test_cmd_about_without_store():
    """When no backend is configured, /about must say stateless. Regression
    guard for the NameError that occurred when `store` was missing from
    bot.handlers' imports."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", None),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.generate", return_value="I'm a friendly helper."),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "stateless" in sent


def test_cmd_about_includes_ai_personality():
    """/about asks the AI to introduce itself and shows the blurb above the
    technical info."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch(
            "bot.handlers.generate", return_value="I'm a witty, concise helper."
        ) as mock_gen,
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "I'm a witty, concise helper." in sent
        # personality appears before the technical block
        assert sent.index("witty") < sent.index("Model")
        mock_gen.assert_called_once()


def test_cmd_about_survives_ai_failure():
    """If the personality call raises, /about still returns the technical
    info rather than erroring out."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.store", MagicMock()),
        patch("bot.handlers.HF_SPACE_ID", ""),
        patch("bot.handlers.generate", side_effect=Exception("provider down")),
    ):
        from bot.handlers import cmd_about

        cmd_about(make_message())
        sent = mock_bot.send_message.call_args[0][1]
        assert "Model" in sent
        assert "SQLite" in sent


# ── /sha ─────────────────────────────────────────────────────────────────────


def test_cmd_sha_reports_live_commit_sha():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.COMMIT_SHA", "abc1234"),
    ):
        from bot.handlers import cmd_sha

        cmd_sha(make_message())
        mock_bot.send_message.assert_called_once_with(456, "#️⃣ Live SHA: abc1234")


def test_cmd_sha_reports_unknown_when_git_sha_unavailable():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.COMMIT_SHA", ""),
    ):
        from bot.handlers import cmd_sha

        cmd_sha(make_message())
        mock_bot.send_message.assert_called_once_with(456, "#️⃣ Live SHA: unknown")


# ── /clear ─────────────────────────────────────────────────────────────────────


def test_cmd_clear_deletes_and_resets():
    """In a private chat, /clear deletes recent messages (walking back from its
    own id) and resets the AI's conversation memory."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.clear_history") as mock_clear,
    ):
        from bot.handlers import cmd_clear

        # First few deletes succeed, then Telegram refuses (too old) — the
        # handler should stop after a run of consecutive failures.
        mock_bot.delete_message.side_effect = [None, None, None] + [
            Exception("message can't be deleted")
        ] * 20
        msg = make_message(text="/clear")
        msg.message_id = 100
        cmd_clear(msg)

        assert mock_bot.delete_message.call_count >= 3
        # AI memory is reset for this user
        mock_clear.assert_called_once_with(123)
        # a confirmation is sent
        assert mock_bot.send_message.called


def test_cmd_clear_preserves_notes():
    """/clear resets conversation memory via clear_history but must never touch
    the store directly, so /remember notes (note:{id}) survive."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.clear_history") as mock_clear,
        patch("bot.handlers.store") as mock_store,
    ):
        from bot.handlers import cmd_clear

        mock_bot.delete_message.side_effect = [None] + [Exception("old")] * 20
        msg = make_message(text="/clear")
        msg.message_id = 50
        cmd_clear(msg)

        mock_clear.assert_called_once_with(123)
        # cmd_clear does no direct store I/O, so saved notes are never deleted
        mock_store.delete.assert_not_called()


def test_cmd_clear_only_in_private_chat():
    """In a group /clear must not delete anything or reset memory — it would be
    deleting other people's messages and needs admin rights."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.clear_history") as mock_clear,
    ):
        from bot.handlers import cmd_clear

        msg = make_message(text="/clear", chat_type="group")
        msg.message_id = 100
        cmd_clear(msg)

        mock_bot.delete_message.assert_not_called()
        mock_clear.assert_not_called()
        assert mock_bot.send_message.called  # sent the "private only" notice


# ── /model command ────────────────────────────────────────────────────────────


def _import_cmd_model_with_hf_enabled():
    """Re-import handlers module with HF_SPACE_ID set so cmd_model exists."""
    import importlib
    import bot.config
    import bot.handlers

    original = bot.config.HF_SPACE_ID
    bot.config.HF_SPACE_ID = "fake/space"
    # Also patch the import in handlers module (already imported via `from ... import HF_SPACE_ID`)
    bot.handlers.HF_SPACE_ID = "fake/space"
    importlib.reload(bot.handlers)
    cmd_model = getattr(bot.handlers, "cmd_model", None)
    # Restore
    bot.config.HF_SPACE_ID = original
    bot.handlers.HF_SPACE_ID = original
    return cmd_model


def test_cmd_model_no_args_shows_current():
    cmd_model = _import_cmd_model_with_hf_enabled()
    assert cmd_model is not None
    with (
        patch("bot.handlers.get_provider", return_value="main"),
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model")
        cmd_model(msg)
        sent = mock_bot.send_message.call_args[0][1]
        assert "Current provider: main" in sent
        assert "/model main" in sent
        assert "/model hf" in sent


def test_cmd_model_switch_to_hf():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=True) as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model hf")
        cmd_model(msg)
        mock_set.assert_called_once_with(123, "hf")
        sent = mock_bot.send_message.call_args[0][1]
        assert "hf" in sent
        assert "Armenian" in sent


def test_cmd_model_switch_to_main():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=True) as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model main")
        cmd_model(msg)
        mock_set.assert_called_once_with(123, "main")
        sent = mock_bot.send_message.call_args[0][1]
        assert "Main" in sent


def test_cmd_model_invalid_choice():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider") as mock_set,
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model bogus")
        cmd_model(msg)
        mock_set.assert_not_called()
        assert "Invalid" in mock_bot.send_message.call_args[0][1]


def test_cmd_model_redis_error_reports_failure():
    cmd_model = _import_cmd_model_with_hf_enabled()
    with (
        patch("bot.handlers.set_provider", return_value=False),
        patch("bot.handlers.bot") as mock_bot,
    ):
        msg = make_message(text="/model hf")
        cmd_model(msg)
        assert "Could not save" in mock_bot.send_message.call_args[0][1]


def test_cmd_model_not_registered_without_hf_space_id():
    """When HF_SPACE_ID is empty, cmd_model should not exist."""
    import importlib
    import bot.config
    import bot.handlers

    bot.config.HF_SPACE_ID = ""
    bot.handlers.HF_SPACE_ID = ""
    # reload() doesn't delete existing attributes, so clear it first
    if hasattr(bot.handlers, "cmd_model"):
        delattr(bot.handlers, "cmd_model")
    importlib.reload(bot.handlers)
    assert not hasattr(bot.handlers, "cmd_model")


def test_handle_message_uses_keep_typing():
    """handle_message should wrap ask_ai in the keep_typing context."""
    with (
        patch("bot.handlers.should_respond", return_value=True),
        patch("bot.handlers.is_rate_limited", return_value=False),
        patch("bot.handlers.BOT_INFO", MagicMock(username="testbot")),
        patch("bot.handlers.ask_ai", return_value="reply"),
        patch("bot.handlers.send_reply"),
        patch("bot.handlers.keep_typing") as mock_keep,
        patch("bot.handlers.bot"),
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        from bot.handlers import handle_message

        msg = make_message()
        handle_message(msg)
        mock_keep.assert_called_once_with(456)
