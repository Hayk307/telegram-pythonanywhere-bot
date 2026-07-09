import os
from unittest.mock import patch, MagicMock

from bot.fileconvert import ConversionError


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


# ── /convertfile ────────────────────────────────────────────────────────────


def make_file_message(
    content_type="document",
    file_name="clip.mp4",
    file_size=1000,
    caption="mp3",
    user_id=123,
    chat_id=456,
):
    msg = MagicMock()
    msg.content_type = content_type
    msg.caption = caption
    msg.from_user.id = user_id
    msg.chat.id = chat_id
    doc = MagicMock()
    doc.file_id = "FILEID"
    doc.file_name = file_name
    doc.file_size = file_size
    msg.document = doc
    return msg


def test_cmd_convertfile_shows_instructions():
    with patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_convertfile

        cmd_convertfile(make_message(text="/convertfile"))
        assert "caption" in mock_bot.send_message.call_args[0][1].lower()


def test_handle_file_without_caption_prompts():
    with patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import handle_file

        handle_file(make_file_message(caption=None))
        mock_bot.get_file.assert_not_called()
        assert "caption" in mock_bot.send_message.call_args[0][1].lower()


def test_handle_file_unsupported_target():
    with patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import handle_file

        handle_file(make_file_message(caption="exe"))
        mock_bot.get_file.assert_not_called()
        assert "can't produce" in mock_bot.send_message.call_args[0][1].lower()


def test_handle_file_too_big():
    with patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import handle_file

        handle_file(make_file_message(caption="mp3", file_size=999_999_999))
        mock_bot.get_file.assert_not_called()
        assert "too big" in mock_bot.send_message.call_args[0][1].lower()


def test_handle_file_happy_path_sends_document():
    def fake_convert(in_path, source_ext, target):
        out = os.path.splitext(in_path)[0] + "." + target
        with open(out, "wb") as f:
            f.write(b"converted-bytes")
        return out

    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.fileconvert.convert", side_effect=fake_convert),
        patch("bot.handlers.keep_typing") as mock_keep,
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        mock_bot.get_file.return_value = MagicMock(file_path="path/on/tg")
        mock_bot.download_file.return_value = b"input-bytes"

        from bot.handlers import handle_file

        handle_file(make_file_message(file_name="clip.mp4", caption="mp3"))

        mock_bot.get_file.assert_called_once_with("FILEID")
        mock_bot.download_file.assert_called_once()
        assert mock_bot.send_document.called
        assert mock_bot.send_document.call_args.kwargs["visible_file_name"].endswith(
            ".mp3"
        )


def test_handle_file_conversion_error_is_reported():
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch(
            "bot.handlers.fileconvert.convert",
            side_effect=ConversionError("can't convert that"),
        ),
        patch("bot.handlers.keep_typing") as mock_keep,
    ):
        mock_keep.return_value.__enter__ = MagicMock(return_value=None)
        mock_keep.return_value.__exit__ = MagicMock(return_value=None)
        mock_bot.get_file.return_value = MagicMock(file_path="p")
        mock_bot.download_file.return_value = b"data"

        from bot.handlers import handle_file

        handle_file(make_file_message(caption="mp3"))
        mock_bot.send_document.assert_not_called()
        assert "can't convert that" in mock_bot.send_message.call_args[0][1]


# ── /image (Pollinations text-to-image generation) ───────────


def _patch_image_typing(stack_keep):
    stack_keep.return_value.__enter__ = MagicMock(return_value=None)
    stack_keep.return_value.__exit__ = MagicMock(return_value=None)


def test_cmd_image_no_prompt_shows_usage():
    """/image with no description shows usage and never generates."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers._generate_image") as mock_gen,
    ):
        from bot.handlers import cmd_image

        cmd_image(make_message(text="/image"))
        mock_gen.assert_not_called()
        assert "Usage" in mock_bot.send_message.call_args[0][1]


def test_cmd_image_generates_and_sends_photo():
    """/image <prompt> generates via Pollinations and sends the image back."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch(
            "bot.handlers._generate_image", return_value=b"generated-bytes"
        ) as mock_gen,
    ):
        _patch_image_typing(mock_keep)
        from bot.handlers import cmd_image

        cmd_image(make_message(text="/image a dragon on a skateboard"))
        mock_gen.assert_called_once_with("a dragon on a skateboard")
        mock_bot.send_photo.assert_called_once()
        assert mock_bot.send_photo.call_args[0][1] == b"generated-bytes"
        assert "dragon" in mock_bot.send_photo.call_args.kwargs["caption"]


def test_cmd_image_generation_failure_reports_error():
    """When generation returns None, send a friendly error and no photo."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch("bot.handlers._generate_image", return_value=None),
    ):
        _patch_image_typing(mock_keep)
        from bot.handlers import cmd_image

        cmd_image(make_message(text="/image something"))
        mock_bot.send_photo.assert_not_called()
        assert "Couldn't generate" in mock_bot.send_message.call_args[0][1]


# ── /image helpers ───────────


def test_generate_image_calls_pollinations_and_returns_bytes():
    """_generate_image builds the Pollinations URL and returns the image bytes."""
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.headers = {"Content-Type": "image/jpeg"}
    fake.content = b"img-bytes"
    with patch("bot.handlers.requests.get", return_value=fake) as mock_get:
        from bot.handlers import _generate_image

        assert _generate_image("a red fox") == b"img-bytes"
        url = mock_get.call_args[0][0]
        assert url.startswith("https://image.pollinations.ai/prompt/")
        assert "a%20red%20fox" in url


def test_generate_image_returns_none_on_error():
    """A network/HTTP error yields None (caller shows a friendly message)."""
    with patch("bot.handlers.requests.get", side_effect=Exception("boom")):
        from bot.handlers import _generate_image

        assert _generate_image("x") is None


def test_generate_image_rejects_non_image_response():
    """A non-image content-type (e.g. an error page) yields None."""
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.headers = {"Content-Type": "application/json"}
    fake.content = b'{"error":"busy"}'
    with patch("bot.handlers.requests.get", return_value=fake):
        from bot.handlers import _generate_image

        assert _generate_image("x") is None


def test_download_image_returns_bytes_and_rejects_svg():
    """_download_image (used by the /edit result path) accepts rasters, rejects SVG."""
    ok = MagicMock()
    ok.raise_for_status = MagicMock()
    ok.headers = {"Content-Type": "image/png"}
    ok.content = b"PNGDATA"
    svg = MagicMock()
    svg.raise_for_status = MagicMock()
    svg.headers = {"Content-Type": "image/svg+xml"}
    svg.content = b"<svg/>"
    with patch("bot.handlers.requests.get", return_value=ok):
        from bot.handlers import _download_image

        assert _download_image("http://x/a.png") == b"PNGDATA"
    with patch("bot.handlers.requests.get", return_value=svg):
        from bot.handlers import _download_image

        assert _download_image("http://x/a.svg") is None


# ── /edit (free Hugging Face Space image editing) ────────────────────────────


def make_photo_message(caption=None, text=None, reply_photo=False, user_id=123):
    """A photo message. `caption` is the text sent WITH the photo; when
    `reply_photo` is set, the message instead replies to a separate photo."""
    msg = MagicMock()
    msg.content_type = "photo"
    msg.caption = caption
    msg.text = text
    msg.from_user.id = user_id
    msg.chat.id = 456
    msg.chat.type = "private"
    size = MagicMock()
    size.file_id = "PHOTOID"
    msg.photo = [size]
    msg.document = None
    if reply_photo:
        reply = MagicMock()
        reply.photo = [size]
        reply.document = None
        msg.reply_to_message = reply
    else:
        msg.reply_to_message = None
    return msg


def _patch_edit_typing(stack_keep):
    stack_keep.return_value.__enter__ = MagicMock(return_value=None)
    stack_keep.return_value.__exit__ = MagicMock(return_value=None)


def test_cmd_edit_reply_path_edits_and_sends_photo():
    """Replying to a photo with /edit <prompt> downloads it, edits it, sends it."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch(
            "bot.handlers._edit_image",
            return_value=(b"\xff\xd8edited", "image/png"),
        ) as mock_edit,
    ):
        _patch_edit_typing(mock_keep)
        mock_bot.get_file.return_value = MagicMock(file_path="photos/f.jpg")
        mock_bot.download_file.return_value = b"src-bytes"

        from bot.handlers import cmd_edit

        msg = make_photo_message(text="/edit make it snowy", reply_photo=True)
        msg.content_type = "text"  # the reply command itself is a text message
        cmd_edit(msg)

        mock_bot.get_file.assert_called_once_with("PHOTOID")
        assert mock_edit.call_args[0][0] == "make it snowy"
        assert mock_edit.call_args[0][1] == b"src-bytes"
        mock_bot.send_photo.assert_called_once()
        assert mock_bot.send_photo.call_args[0][1] == b"\xff\xd8edited"


def test_handle_file_caption_edit_path_edits_photo():
    """Sending a photo captioned /edit <prompt> routes to the edit flow."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch(
            "bot.handlers._edit_image",
            return_value=(b"\xff\xd8edited", "image/png"),
        ) as mock_edit,
        patch("bot.handlers.fileconvert.convert") as mock_convert,
    ):
        _patch_edit_typing(mock_keep)
        mock_bot.get_file.return_value = MagicMock(file_path="photos/f.jpg")
        mock_bot.download_file.return_value = b"src-bytes"

        from bot.handlers import handle_file

        handle_file(make_photo_message(caption="/edit turn it into a painting"))

        mock_convert.assert_not_called()  # not treated as a file conversion
        assert mock_edit.call_args[0][0] == "turn it into a painting"
        mock_bot.send_photo.assert_called_once()


def test_cmd_edit_without_reply_asks_for_photo():
    """A bare /edit (no photo to work on) prompts the user for one."""
    with patch("bot.handlers.bot") as mock_bot:
        from bot.handlers import cmd_edit

        msg = make_message(text="/edit make it snowy")  # no reply_to_message
        cmd_edit(msg)
        mock_bot.get_file.assert_not_called()
        assert "photo" in mock_bot.send_message.call_args[0][1].lower()


def test_handle_file_caption_edit_without_prompt_shows_usage():
    """A photo captioned just /edit (no instruction) shows usage, doesn't edit."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers._edit_image") as mock_edit,
    ):
        from bot.handlers import handle_file

        handle_file(make_photo_message(caption="/edit"))
        mock_edit.assert_not_called()
        assert "Usage" in mock_bot.send_message.call_args[0][1]


def test_edit_image_calls_hf_space_and_returns_bytes(tmp_path):
    """_edit_image sends the photo + prompt to the HF Space and returns its bytes."""
    result_file = tmp_path / "out.png"
    result_file.write_bytes(b"\x89PNG-edited-bytes")
    fake_client = MagicMock()
    fake_client.predict.return_value = (str(result_file), 12345)

    with patch("gradio_client.Client", return_value=fake_client):
        from bot.handlers import _edit_image

        out_bytes, out_mime = _edit_image("make it snowy", b"src-bytes", "image/jpeg")
        assert out_bytes == b"\x89PNG-edited-bytes"
        assert out_mime == "image/png"
        kwargs = fake_client.predict.call_args.kwargs
        assert kwargs["prompt"] == "make it snowy"
        assert kwargs["api_name"] == "/infer"


def test_edit_image_raises_editerror_when_all_spaces_fail():
    """A generic (non-quota) failure across the chain yields the 'busy' reason."""
    from bot.handlers import _EditError

    fake_client = MagicMock()
    fake_client.predict.side_effect = Exception("Queue full (50/50)")
    with patch("gradio_client.Client", return_value=fake_client):
        from bot.handlers import _edit_image

        try:
            _edit_image("x", b"src")
            assert False, "expected _EditError"
        except _EditError as e:
            assert "busy" in str(e)


def test_edit_image_quota_error_points_to_hf_token():
    """A ZeroGPU quota failure surfaces the actionable HF_TOKEN guidance
    instead of the misleading 'all busy' message (HF_TOKEN unset in tests)."""
    from bot.handlers import _EditError

    fake_client = MagicMock()
    fake_client.predict.side_effect = Exception(
        "You have exceeded your ZeroGPU quota (90s requested vs. 0s left)."
    )
    with patch("gradio_client.Client", return_value=fake_client):
        from bot.handlers import _edit_image

        try:
            _edit_image("x", b"src")
            assert False, "expected _EditError"
        except _EditError as e:
            assert "quota" in str(e).lower()
            assert "HF_TOKEN" in str(e)


def test_edit_image_falls_back_to_next_space():
    """If the first Space raises, the bot transparently tries the next one."""
    import bot.handlers as h

    good = MagicMock()
    good.predict.return_value = ({"path": None, "url": "http://x/out.png"}, 0)

    def client_factory(space_id, **kw):
        if space_id == h.HF_EDIT_SPACE_IDS[0]:
            raise Exception("Queue full (50/50)")
        return good

    with (
        patch.object(h, "HF_EDIT_SPACE_IDS", ["space/one", "space/two"]),
        patch("gradio_client.Client", side_effect=client_factory),
        patch("bot.handlers._download_image", return_value=b"\x89PNG-from-fallback"),
    ):
        out_bytes, out_mime = h._edit_image("x", b"src")
        assert out_bytes == b"\x89PNG-from-fallback"


def test_run_edit_surfaces_edit_error_reason():
    """A _EditError from the backend is shown to the user, not a vague message."""
    from bot.handlers import _EditError

    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch(
            "bot.handlers._edit_image",
            side_effect=_EditError("the free image editor is busy or unavailable."),
        ),
    ):
        _patch_edit_typing(mock_keep)
        mock_bot.get_file.return_value = MagicMock(file_path="photos/f.jpg")
        mock_bot.download_file.return_value = b"src"

        from bot.handlers import _run_edit

        _run_edit(make_photo_message(), "PHOTOID", "make it snowy")
        sent = mock_bot.send_message.call_args[0][1]
        assert "busy or unavailable" in sent
        mock_bot.send_photo.assert_not_called()


def test_run_edit_falls_back_to_document_when_photo_rejected():
    """If Telegram rejects the edited bytes as a photo, we still deliver it as a file."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers.keep_typing") as mock_keep,
        patch(
            "bot.handlers._edit_image",
            return_value=(b"\x89PNG-edited", "image/png"),
        ),
    ):
        _patch_edit_typing(mock_keep)
        mock_bot.get_file.return_value = MagicMock(file_path="photos/f.jpg")
        mock_bot.download_file.return_value = b"src"
        mock_bot.send_photo.side_effect = Exception("PHOTO_INVALID_DIMENSIONS")

        from bot.handlers import _run_edit

        _run_edit(make_photo_message(), "PHOTOID", "make it snowy")
        mock_bot.send_photo.assert_called_once()
        mock_bot.send_document.assert_called_once()
        assert mock_bot.send_document.call_args[0][1] == b"\x89PNG-edited"


def test_run_edit_reports_download_failure():
    """A failed Telegram download tells the user to resend, never edits."""
    with (
        patch("bot.handlers.bot") as mock_bot,
        patch("bot.handlers._edit_image") as mock_edit,
    ):
        mock_bot.get_file.side_effect = Exception("file too big")

        from bot.handlers import _run_edit

        _run_edit(make_photo_message(), "PHOTOID", "make it snowy")
        mock_edit.assert_not_called()
        assert "resend" in mock_bot.send_message.call_args[0][1].lower()


def test_normalize_for_telegram_converts_webp_to_jpeg():
    """The Space returns WebP; we convert to JPEG so send_photo renders it inline."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="WEBP")

    from bot.handlers import _normalize_for_telegram

    data, mime = _normalize_for_telegram(buf.getvalue(), "image/webp")
    assert mime == "image/jpeg"
    assert data[:3] == b"\xff\xd8\xff"  # valid JPEG magic


def test_normalize_for_telegram_passes_jpeg_png_through():
    """JPEG/PNG are already Telegram-friendly and are returned untouched."""
    from bot.handlers import _normalize_for_telegram

    assert _normalize_for_telegram(b"rawjpeg", "image/jpeg") == (b"rawjpeg", "image/jpeg")
    assert _normalize_for_telegram(b"rawpng", "image/png") == (b"rawpng", "image/png")

