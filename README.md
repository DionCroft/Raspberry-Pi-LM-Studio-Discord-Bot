# LM Studio Discord Bot

A small Discord bot that sends messages directly to LM Studio's OpenAI-compatible API.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_CHANNEL_ID=your-channel-id
DISCORD_LM_PREFIX=!lm
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_MODEL=qwen3-1.7b
```

In the Discord Developer Portal, enable the bot's **Message Content Intent**.

Make sure LM Studio's local server is running and your model is loaded, then start the bot:

```bash
./run.sh
```

Use it in Discord with `!lm hello`, by mentioning the bot, or by sending it a DM.

`LM_STUDIO_MODEL` is the runtime active model identifier. The bot updates it in `.env` when `!lm use` or a successful `!lm load` switches models, so restarts keep using the same active model.

## Model Commands

The bot can manage LM Studio models through the local `lms` CLI.

```text
!lm health
!lm status
!lm models
!lm loaded
!lm use qwen3-1.7b
!lm load qwen/qwen3-1.7b as qwen3-1.7b
```

`!lm models` lists downloaded LM Studio models. `!lm loaded` shows currently loaded model instances. `!lm load ...` unloads the previously active model when switching identifiers, loads the new model with one parallel slot and the context length from `LMS_DEFAULT_CONTEXT_LENGTH`, then makes the bot use that identifier for future replies.

## Self-Healing

- `!lm health` checks Discord readiness, LM Studio's API, the active model, loaded models, and available RAM.
- If chat fails because the active model is not loaded, the bot tries to load it once and then retries the chat.
- The bot processes one LM/model request at a time and keeps only a small queue so the Pi is not flooded.
- Loading a new model switches back to the previous model if the new load fails.
- Loading is refused when another non-active model is already loaded or available RAM is below `LMS_MIN_FREE_MEMORY_MB`.
- `bot.log` records command name, model, duration, and success/failure without logging Discord message text or tokens.
