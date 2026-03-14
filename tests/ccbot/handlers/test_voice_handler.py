"""Unit tests for voice message handler — mocks transcriber and Telegram API."""

from unittest.mock import AsyncMock, MagicMock, patch

from ccbot.handlers.user_state import VOICE_PENDING
from ccbot.whisper.base import TranscriptionResult

_VH = "ccbot.handlers.voice_handler"
_VC = "ccbot.handlers.voice_callbacks"


def _make_update(
    user_id: int = 100,
    thread_id: int | None = 42,
    message_id: int = 1,
    voice_file_id: str = "voice123",
    voice_file_size: int | None = 1000,
) -> MagicMock:
    """Create a mock Update with voice message."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.message_id = message_id
    update.message.voice = MagicMock()
    update.message.voice.file_id = voice_file_id
    update.message.voice.file_size = voice_file_size
    update.message.chat = MagicMock()
    update.message.chat.id = 999
    update.message.get_bot = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.effective_message = update.message

    # Mock message_thread_id for thread support
    update.message.message_thread_id = thread_id

    return update


def _make_callback_query(
    data: str,
    user_id: int = 100,
    message_id: int = 42,
) -> MagicMock:
    """Create a mock callback query."""
    from telegram import CallbackQuery, Message

    # Create a proper mock that passes isinstance checks
    query = MagicMock(spec=CallbackQuery)
    query.data = data
    query.from_user = MagicMock()
    query.from_user.id = user_id
    # Make message an actual Message instance for isinstance check
    query.message = MagicMock(spec=Message)
    query.message.message_id = message_id
    query.message.delete = AsyncMock()
    query.answer = AsyncMock()
    return query


class TestHandleVoiceMessage:
    """Tests for handle_voice_message."""

    @patch(f"{_VH}._download_voice", new_callable=AsyncMock)
    @patch(f"{_VH}.session_manager")
    @patch(f"{_VH}.config")
    @patch(f"{_VH}.get_transcriber")
    @patch(f"{_VH}.safe_reply", new_callable=AsyncMock)
    async def test_voice_handler_no_transcriber(
        self,
        mock_reply: AsyncMock,
        mock_get_transcriber: MagicMock,
        mock_config: MagicMock,
        mock_session_manager: MagicMock,
        mock_download: AsyncMock,
    ) -> None:
        """Test handler replies with not configured message when transcriber is None.

        The transcriber check happens inside _transcribe_audio, after the session is
        resolved, so we need a bound session to reach that code path.
        """
        from ccbot.handlers import voice_handler

        mock_config.is_user_allowed.return_value = True
        mock_get_transcriber.return_value = None
        mock_session_manager.resolve_window_for_thread.return_value = "@0"
        mock_download.return_value = b"fake audio bytes"
        update = _make_update()

        context = MagicMock()
        context.user_data = {}

        await voice_handler.handle_voice_message(update, context)

        mock_reply.assert_called_once()
        assert "not configured" in mock_reply.call_args.args[1]

    @patch(f"{_VH}.session_manager")
    @patch(f"{_VH}.config")
    @patch(f"{_VH}.get_transcriber")
    @patch(f"{_VH}.safe_reply", new_callable=AsyncMock)
    async def test_voice_handler_topic_not_bound(
        self,
        mock_reply: AsyncMock,
        mock_get_transcriber: MagicMock,
        mock_config: MagicMock,
        mock_session_manager: MagicMock,
    ) -> None:
        """Test handler replies with bind topic message when window is not bound."""
        from ccbot.handlers import voice_handler

        mock_config.is_user_allowed.return_value = True
        mock_transcriber = MagicMock()
        mock_get_transcriber.return_value = mock_transcriber
        mock_session_manager.resolve_window_for_thread.return_value = None

        update = _make_update()

        context = MagicMock()
        context.user_data = {}

        await voice_handler.handle_voice_message(update, context)

        mock_reply.assert_called_once()
        assert "Bind this topic" in mock_reply.call_args.args[1]

    @patch(f"{_VH}.session_manager")
    @patch(f"{_VH}.config")
    @patch(f"{_VH}.get_transcriber")
    @patch(f"{_VH}.safe_reply", new_callable=AsyncMock)
    async def test_voice_handler_file_too_large(
        self,
        mock_reply: AsyncMock,
        mock_get_transcriber: MagicMock,
        mock_config: MagicMock,
        mock_session_manager: MagicMock,
    ) -> None:
        """Test handler rejects files larger than 25 MB."""
        from ccbot.handlers import voice_handler

        mock_config.is_user_allowed.return_value = True
        mock_transcriber = MagicMock()
        mock_get_transcriber.return_value = mock_transcriber
        # Need to return a window so we get past the bind check
        mock_session_manager.resolve_window_for_thread.return_value = "@0"

        # Create update with file_size > 25MB
        update = _make_update(voice_file_size=26 * 1024 * 1024)

        context = MagicMock()
        context.user_data = {}

        await voice_handler.handle_voice_message(update, context)

        mock_reply.assert_called_once()
        assert "too large" in mock_reply.call_args.args[1]
        # Transcriber should not be called
        mock_transcriber.transcribe.assert_not_called()

    @patch(f"{_VH}._download_voice", new_callable=AsyncMock)
    @patch(f"{_VH}.session_manager")
    @patch(f"{_VH}.config")
    @patch(f"{_VH}.get_transcriber")
    @patch(f"{_VH}.safe_reply", new_callable=AsyncMock)
    async def test_voice_handler_transcription_success(
        self,
        mock_reply: AsyncMock,
        mock_get_transcriber: MagicMock,
        mock_config: MagicMock,
        mock_session_manager: MagicMock,
        mock_download: AsyncMock,
    ) -> None:
        """Test successful transcription stores in VOICE_PENDING."""
        from ccbot.handlers import voice_handler

        mock_config.is_user_allowed.return_value = True
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe = AsyncMock(
            return_value=TranscriptionResult(text="do the thing", language="en")
        )
        mock_get_transcriber.return_value = mock_transcriber
        mock_session_manager.resolve_window_for_thread.return_value = "@0"
        mock_download.return_value = b"fake audio bytes"

        # Mock reply_text to return a message with message_id
        mock_reply_msg = MagicMock()
        mock_reply_msg.message_id = 42
        mock_reply_msg.edit_reply_markup = AsyncMock()
        mock_reply.return_value = mock_reply_msg
        # Also need to mock the message.reply_text method used in _send_confirm_message
        update = _make_update()
        update.message.reply_text = AsyncMock(return_value=mock_reply_msg)

        context = MagicMock()
        context.user_data = {}

        await voice_handler.handle_voice_message(update, context)

        # Verify transcription was stored
        assert VOICE_PENDING in context.user_data
        assert 42 in context.user_data[VOICE_PENDING]

        # Verify the transcribed text was stored correctly
        assert context.user_data[VOICE_PENDING][42] == "do the thing"

    @patch(f"{_VH}._download_voice", new_callable=AsyncMock)
    @patch(f"{_VH}.session_manager")
    @patch(f"{_VH}.config")
    @patch(f"{_VH}.get_transcriber")
    @patch(f"{_VH}.safe_reply", new_callable=AsyncMock)
    async def test_voice_handler_transcription_runtime_error(
        self,
        mock_reply: AsyncMock,
        mock_get_transcriber: MagicMock,
        mock_config: MagicMock,
        mock_session_manager: MagicMock,
        mock_download: AsyncMock,
    ) -> None:
        """Test RuntimeError from transcriber shows error message."""
        from ccbot.handlers import voice_handler

        mock_config.is_user_allowed.return_value = True
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe = AsyncMock(
            side_effect=RuntimeError("Transcription failed: 401")
        )
        mock_get_transcriber.return_value = mock_transcriber
        mock_session_manager.resolve_window_for_thread.return_value = "@0"
        mock_download.return_value = b"fake audio bytes"

        update = _make_update()

        context = MagicMock()
        context.user_data = {}

        await voice_handler.handle_voice_message(update, context)

        mock_reply.assert_called()
        reply_text = mock_reply.call_args.args[1]
        assert "❌" in reply_text


class TestHandleVoiceCallback:
    """Tests for handle_voice_callback."""

    @patch(f"{_VC}.session_manager")
    @patch(f"{_VC}.get_thread_id")
    async def test_voice_callback_send_success(
        self, mock_get_thread_id: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Test vc:send sends text to window and deletes message."""
        from ccbot.handlers import voice_callbacks

        mock_session_manager.resolve_window_for_thread.return_value = "@0"
        mock_session_manager.send_to_window = AsyncMock(return_value=(True, None))
        mock_get_thread_id.return_value = 42

        # Create update with callback query
        update = MagicMock()
        update.callback_query = _make_callback_query("vc:send:42", message_id=42)
        update.effective_user = MagicMock()
        update.effective_user.id = 100

        context = MagicMock()
        context.user_data = {VOICE_PENDING: {42: "hello"}}

        await voice_callbacks.handle_voice_callback(update, context)

        # Verify send_to_window was called
        mock_session_manager.send_to_window.assert_called_once_with("@0", "hello")

        # Verify message was deleted
        update.callback_query.message.delete.assert_called_once()

        # Verify pending entry was cleaned up
        assert 42 not in context.user_data.get(VOICE_PENDING, {})

    @patch(f"{_VC}.session_manager")
    @patch(f"{_VC}.get_thread_id")
    async def test_voice_callback_drop(
        self, mock_get_thread_id: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Test vc:drop deletes message and cleans up."""
        from ccbot.handlers import voice_callbacks

        mock_get_thread_id.return_value = 42

        update = MagicMock()
        update.callback_query = _make_callback_query("vc:drop:42", message_id=42)
        update.effective_user = MagicMock()
        update.effective_user.id = 100

        context = MagicMock()
        context.user_data = {VOICE_PENDING: {42: "hello"}}

        await voice_callbacks.handle_voice_callback(update, context)

        # Verify message was deleted
        update.callback_query.message.delete.assert_called_once()

        # Verify pending entry was cleaned up
        assert 42 not in context.user_data.get(VOICE_PENDING, {})

        # Verify answer was called
        update.callback_query.answer.assert_called_once_with("Discarded")

    @patch(f"{_VC}.session_manager")
    @patch(f"{_VC}.get_thread_id")
    async def test_voice_callback_expired(
        self, mock_get_thread_id: MagicMock, mock_session_manager: MagicMock
    ) -> None:
        """Test vc:send with expired entry shows alert."""
        from ccbot.handlers import voice_callbacks

        mock_get_thread_id.return_value = 42

        update = MagicMock()
        update.callback_query = _make_callback_query("vc:send:99", message_id=99)
        update.effective_user = MagicMock()
        update.effective_user.id = 100

        context = MagicMock()
        context.user_data = {VOICE_PENDING: {}}  # Empty - no entry for 99

        await voice_callbacks.handle_voice_callback(update, context)

        # Verify answer was called with expired message
        update.callback_query.answer.assert_called_once()
        # The handler should answer with "Session expired, resend voice message"
        call_args = str(update.callback_query.answer.call_args)
        assert "expired" in call_args.lower() or "resend" in call_args.lower()
