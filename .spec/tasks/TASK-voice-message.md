# TASK: Voice Message Transcription — Multi-Provider Whisper Support

**Branch**: `feat/voice-message-support`
**Issue**: https://github.com/alexei-led/ccbot/issues/5

---

## Overview

Add support for Telegram voice messages: download the OGG audio, transcribe via a
Whisper-compatible API (OpenAI or Groq), show the transcription to the user for
confirmation, and forward it to the bound agent as a text message.

---

## Architecture

### New package: `src/ccbot/whisper/`

```
src/ccbot/whisper/
├── __init__.py           # get_transcriber() → WhisperTranscriber | None
├── base.py               # WhisperTranscriber protocol + TranscriptionResult
└── openai_compat.py      # Single impl for OpenAI + Groq (same SDK, different base_url)
```

**Why a single implementation?**
Both OpenAI Whisper and Groq Whisper are OpenAI-compatible APIs. The `openai` Python
SDK accepts a `base_url` parameter, so one class handles both providers. This avoids
code duplication while keeping the abstraction clean.

#### `base.py`

```python
@dataclass
class TranscriptionResult:
    text: str
    language: str | None   # detected language (ISO-639-1), None if unknown
    duration: float | None # audio duration in seconds, None if not returned

class WhisperTranscriber(Protocol):
    @property
    def provider_name(self) -> str: ...
    async def transcribe(
        self, audio_path: Path, language: str | None = None
    ) -> TranscriptionResult: ...
```

#### `openai_compat.py`

```python
class OpenAICompatTranscriber:
    """Transcriber for any OpenAI-compatible Whisper endpoint (OpenAI, Groq, custom)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,   # None = official OpenAI endpoint
        provider_name: str = "openai",
    ) -> None: ...

    async def transcribe(self, audio_path: Path, language: str | None = None) -> TranscriptionResult:
        # Uses openai.AsyncOpenAI(api_key=..., base_url=...)
        # Calls client.audio.transcriptions.create(
        #     model=self.model,
        #     file=open(audio_path, "rb"),
        #     response_format="verbose_json",  # returns language + duration
        #     language=language,               # None = auto-detect
        # )
        ...
```

#### `__init__.py`  — `get_transcriber()`

Reads config and returns a configured transcriber, or `None` if not configured.

Provider configs built-in:
| Provider | `base_url`                          | Default model          | API key env var   |
|----------|-------------------------------------|------------------------|-------------------|
| `openai` | `None` (SDK default)                | `whisper-1`            | `OPENAI_API_KEY`  |
| `groq`   | `https://api.groq.com/openai/v1`    | `whisper-large-v3`     | `GROQ_API_KEY`    |
| `custom` | `CCBOT_WHISPER_BASE_URL` (required) | `CCBOT_WHISPER_MODEL`  | `CCBOT_WHISPER_API_KEY` |

---

### New handler: `src/ccbot/handlers/voice_handler.py`

Handles `filters.VOICE` messages.

**Flow**:
1. Auth check (`config.is_user_allowed(user_id)`)
2. If no transcriber configured → reply with setup instructions
3. Check topic is bound → if not, reply "Bind this topic first"
4. Send typing action + "🎙️ Transcribing…" status message
5. Download OGG from Telegram to temp file (`tempfile.NamedTemporaryFile`)
6. Call `transcriber.transcribe(path, language=config.whisper_language)`
7. Edit status message → show transcription with confirm keyboard
8. Store pending transcription in `context.user_data[VOICE_PENDING][msg_id]`

**Message format**:
```
🎙️ Voice transcribed (12s · en):

"Your transcribed text here"

[✓ Send to agent]  [✗ Discard]
```

### New handler: `src/ccbot/handlers/voice_callbacks.py`

Handles callback queries for voice confirmation.

Callback data format (≤ 64 bytes):
- `vc:send:{msg_id}` — confirm, forward text to agent
- `vc:drop:{msg_id}` — discard, delete message

**`handle_voice_callback`**:
- `vc:send` → retrieve text from `user_data[VOICE_PENDING][msg_id]` → call
  `session_manager.send_to_window(window_id, transcribed_text)` → edit message
  to "✅ Sent to agent" → cleanup state
- `vc:drop` → delete message → cleanup state
- Both: call `query.answer()` for instant feedback

---

## Configuration Changes

### `src/ccbot/config.py` — new fields

```python
# Voice transcription (Whisper API)
# CCBOT_WHISPER_PROVIDER: "openai" | "groq" | "" (empty = disabled)
self.whisper_provider: str = os.getenv("CCBOT_WHISPER_PROVIDER", "")
# API keys (env-only — never pass via CLI to avoid ps exposure)
self.whisper_openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
self.whisper_groq_api_key: str = os.getenv("GROQ_API_KEY", "")
# Custom endpoint overrides (optional — override built-in provider defaults)
self.whisper_api_key: str = os.getenv("CCBOT_WHISPER_API_KEY", "")
self.whisper_base_url: str | None = os.getenv("CCBOT_WHISPER_BASE_URL") or None
self.whisper_model: str | None = os.getenv("CCBOT_WHISPER_MODEL") or None
# Language hint — None = auto-detect (pass ISO-639-1 code to force a language)
self.whisper_language: str | None = os.getenv("CCBOT_WHISPER_LANGUAGE") or None
```

**Key resolution**:
- `CCBOT_WHISPER_API_KEY` overrides provider-specific key
- Otherwise: `openai` uses `OPENAI_API_KEY`, `groq` uses `GROQ_API_KEY`

### `src/ccbot/handlers/user_state.py` — new key

```python
VOICE_PENDING = "voice_pending"  # dict[int, str]: msg_id → transcribed_text
```

### `src/ccbot/handlers/callback_data.py` — new constant

```python
CB_VOICE = "vc"
```

---

## `src/ccbot/bot.py` Changes

1. **Import**: `from .handlers.voice_handler import handle_voice_message`
2. **Import**: `from .handlers.voice_callbacks import handle_voice_callback`
3. **Register handler** (before catch-all):
   ```python
   application.add_handler(
       MessageHandler(filters.VOICE & _group_filter, handle_voice_message)
   )
   ```
4. **Remove VOICE from catch-all** — update the `unsupported_content_handler` message
   to no longer mention "voice" (or keep it if transcription is disabled)
5. **Callback dispatch** — add branch in `callback_handler`:
   ```python
   elif data.startswith(_CB_VOICE):
       await handle_voice_callback(query, user.id, data, update, context)
   ```

---

## `pyproject.toml` Changes

```toml
[project]
dependencies = [
    ...
    "openai>=1.58.0",   # Whisper transcription (openai + groq compatible)
]
```

---

## `src/ccbot/cli.py` Changes

Add CLI flag:
```
--whisper-provider TEXT   Whisper provider: openai, groq, or empty to disable
                          [env: CCBOT_WHISPER_PROVIDER]
```

---

## Error Handling

| Scenario                              | Response                                      |
|---------------------------------------|-----------------------------------------------|
| Provider not configured               | "🎙️ Voice transcription is not configured. Set `CCBOT_WHISPER_PROVIDER`…" |
| Topic not bound                       | "⚠️ This topic has no active session. Send a message first to start one." |
| Audio file too large (> 25 MB)        | "⚠️ Voice message too large for transcription (max 25 MB)." |
| API key missing                       | Log error, reply "❌ Whisper API key not configured." |
| Transcription API error               | Log error with details, reply "❌ Transcription failed: {message}" |
| Network timeout                       | Retry once, then reply with error |
| Pending state expired (bot restart)   | On callback, reply "⚠️ Session expired. Please resend the voice message." |

---

## File Summary

| File                                     | Action   | Description                                        |
|------------------------------------------|----------|----------------------------------------------------|
| `src/ccbot/whisper/__init__.py`          | **New**  | `get_transcriber()` factory                        |
| `src/ccbot/whisper/base.py`              | **New**  | Protocol + `TranscriptionResult` dataclass         |
| `src/ccbot/whisper/openai_compat.py`     | **New**  | OpenAI-compatible transcriber (OpenAI + Groq)      |
| `src/ccbot/handlers/voice_handler.py`    | **New**  | Telegram voice message handler                     |
| `src/ccbot/handlers/voice_callbacks.py`  | **New**  | Confirm/discard callbacks                          |
| `src/ccbot/config.py`                    | **Edit** | Add whisper_* config fields                        |
| `src/ccbot/handlers/user_state.py`       | **Edit** | Add `VOICE_PENDING` key                            |
| `src/ccbot/handlers/callback_data.py`    | **Edit** | Add `CB_VOICE = "vc"`                              |
| `src/ccbot/bot.py`                       | **Edit** | Register voice handler + callback dispatch         |
| `src/ccbot/cli.py`                       | **Edit** | Add `--whisper-provider` flag                      |
| `pyproject.toml`                         | **Edit** | Add `openai>=1.58.0` dependency                    |
| `tests/ccbot/test_voice_handler.py`      | **New**  | Unit tests for voice handler                       |
| `tests/ccbot/whisper/test_transcriber.py`| **New**  | Unit tests for transcriber                         |

---

## Environment Variable Reference

| Variable                  | Required | Default      | Description                                        |
|---------------------------|----------|--------------|----------------------------------------------------|
| `CCBOT_WHISPER_PROVIDER`  | No       | `""` (off)   | `openai` or `groq` to enable voice transcription  |
| `OPENAI_API_KEY`          | If openai| —            | OpenAI API key                                     |
| `GROQ_API_KEY`            | If groq  | —            | Groq API key                                       |
| `CCBOT_WHISPER_API_KEY`   | No       | —            | Override API key (takes precedence over above)     |
| `CCBOT_WHISPER_BASE_URL`  | No       | provider default | Override API base URL (for custom endpoints)  |
| `CCBOT_WHISPER_MODEL`     | No       | provider default | Override model name                           |
| `CCBOT_WHISPER_LANGUAGE`  | No       | `""` (auto)  | ISO-639-1 language code hint (e.g. `en`, `zh`)    |

---

## Implementation Order

1. `pyproject.toml` — add openai dependency
2. `src/ccbot/whisper/` — base + openai_compat + __init__
3. `src/ccbot/config.py` — add whisper fields
4. `src/ccbot/handlers/user_state.py` — add VOICE_PENDING
5. `src/ccbot/handlers/callback_data.py` — add CB_VOICE
6. `src/ccbot/handlers/voice_handler.py` — voice message handler
7. `src/ccbot/handlers/voice_callbacks.py` — confirm/discard callbacks
8. `src/ccbot/bot.py` — register handlers
9. `src/ccbot/cli.py` — add --whisper-provider flag
10. Tests
11. `make check` — must pass (fmt + lint + typecheck + test)
