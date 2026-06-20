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

## Model Commands

The bot can manage LM Studio models through the local `lms` CLI.

```text
!lm status
!lm models
!lm loaded
!lm use qwen3-1.7b
!lm load qwen/qwen3-1.7b as qwen3-1.7b
```

`!lm models` lists downloaded LM Studio models. `!lm loaded` shows currently loaded model instances. `!lm load ...` unloads the previously active model when switching identifiers, loads the new model with one parallel slot and the context length from `LMS_DEFAULT_CONTEXT_LENGTH`, then makes the bot use that identifier for future replies.
