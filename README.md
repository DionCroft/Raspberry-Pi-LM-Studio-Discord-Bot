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
LM_STUDIO_MODEL=qwen3.5-0.8b
LMS_DEFAULT_TTL_SECONDS=0
```

In the Discord Developer Portal, enable the bot's **Message Content Intent**.

Make sure LM Studio's local server is running and your model is loaded, then start the bot manually:

```bash
./run.sh
```

If the bot is installed as a user service, do not also run `./run.sh` in another terminal. The service is the normal way to keep the bot online.

## Boot Setup

The Pi uses two user services:

```bash
systemctl --user enable lm-studio-qwen35-08b.service
systemctl --user enable lm-studio-discord-bot.service
```

`lm-studio-qwen35-08b.service` starts LM Studio's local server, unloads every model except `qwen3.5-0.8b`, and loads `qwen3.5-0.8b` if needed. The Discord bot service depends on it, so the model is ready before the bot starts after a reboot.

If you change the model in `.env`, restart both services:

```bash
systemctl --user restart lm-studio-qwen35-08b.service
systemctl --user restart lm-studio-discord-bot.service
```

Service templates live in `systemd/`; install them to `~/.config/systemd/user/`.

## Service Commands

Check whether the bot is running:

```bash
systemctl --user status lm-studio-discord-bot.service
```

Check whether LM Studio/model boot prep completed:

```bash
systemctl --user status lm-studio-qwen35-08b.service
```

Restart the bot after editing `.env` or `bot.py`:

```bash
systemctl --user restart lm-studio-discord-bot.service
```

Stop and start it manually:

```bash
systemctl --user stop lm-studio-discord-bot.service
systemctl --user start lm-studio-discord-bot.service
```

Follow the bot log:

```bash
tail -f bot.log
```

Follow the LM Studio model loader log:

```bash
tail -f logs/lm-studio-model.log
```

For foreground testing in a terminal, stop the service first, run the script, then press `Ctrl+C` when finished and start the service again:

```bash
systemctl --user stop lm-studio-discord-bot.service
./run.sh
systemctl --user start lm-studio-discord-bot.service
```

Use it in Discord with `!lm hello`, by mentioning the bot, or by sending it a DM.

`LM_STUDIO_MODEL` is the runtime active model identifier. The bot updates it in `.env` when `!lm use` or a successful `!lm load` switches models, so restarts keep using the same active model.

## Model Commands

The bot can manage LM Studio models through the local `lms` CLI.

```text
!lm health
!lm clean
!lm status
!lm models
!lm loaded
!lm use qwen3.5-0.8b
!lm load qwen3.5-0.8b
```

`!lm models` lists downloaded LM Studio models. `!lm loaded` shows currently loaded model instances. `!lm clean` unloads everything except the active bot model, loading the active model first if needed. `!lm load ...` unloads every other loaded model, loads the new model with one parallel slot and the context length from `LMS_DEFAULT_CONTEXT_LENGTH`, then makes the bot use that identifier for future replies.

Model identifiers are matched exactly against LM Studio's loaded identifiers, including names such as `qwen3.5-0.8b` or `qwen3.5-0.8b@q4_k_m`. `!lm use ...` only succeeds for a currently loaded identifier.

## Timeout Notes

If Discord says LM Studio timed out, the most common cause on the Pi is too many chat models loaded at once or a model that is too large for the available RAM. Run:

```text
!lm health
!lm loaded
```

Keep only one model loaded for the bot. The smaller `qwen3.5-0.8b` model should be the fast option; larger 4B+ models can work, but they may take several minutes or time out under load.

## Discord Formatting

Discord does not render LaTeX math. The bot prompt asks models to write equations in plain text, such as `epsilon_r`, `>=`, and `x 10^-12`, and the bot also cleans common LaTeX fragments before replying.

## Self-Healing

- `!lm health` checks Discord readiness, LM Studio's API, the active model, loaded models, and available RAM.
- `!lm clean` enforces single-model mode by unloading everything except the active model.
- If chat fails because the active model is not loaded, the bot tries to load it once and then retries the chat.
- The bot processes one LM/model request at a time and keeps only a small queue so the Pi is not flooded.
- Chat, `!lm use`, and `!lm load` enforce single-model mode before using LM Studio.
- Loading a new model switches back to the previous bot model if the new load fails.
- Loading is refused when available RAM is below `LMS_MIN_FREE_MEMORY_MB`.
- `bot.log` records command name, model, duration, and success/failure without logging Discord message text or tokens. It rotates according to `BOT_LOG_MAX_BYTES` and `BOT_LOG_BACKUP_COUNT`.
