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

The Pi uses four user services:

```bash
systemctl --user enable kiwix-wikipedia.service
systemctl --user enable lm-studio-app.service
systemctl --user enable lm-studio-qwen35-08b.service
systemctl --user enable lm-studio-discord-bot.service
systemctl --user enable pi-service-maintenance.timer
systemctl --user enable pi-service-healthcheck.timer
systemctl --user enable pi-runtime-backup.timer
```

`kiwix-wikipedia.service` serves the local Wikipedia ZIM on `127.0.0.1:8090`. `lm-studio-app.service` keeps the LM Studio AppImage running. `lm-studio-qwen35-08b.service` starts LM Studio's local server, unloads every model except `qwen3.5-0.8b`, and loads `qwen3.5-0.8b` if needed. The Discord bot service depends on LM Studio and starts after Kiwix when it is present.

`pi-service-maintenance.timer` runs once a day around 04:30, with up to 10 minutes of random delay. It stops the Discord bot, restarts the research funding signup service if it is installed, restarts Kiwix, restarts LM Studio, re-runs the model loader, and starts the bot again. This is a lighter 24/7 maintenance cycle than rebooting the whole Pi.

`pi-service-healthcheck.timer` runs every 15 minutes. It checks the Discord bot service, LM Studio API, Kiwix API, available RAM, and swap pressure. After repeated LM Studio or Kiwix failures it restarts the affected stack.

`pi-runtime-backup.timer` writes private runtime backups under `backups/`, including `.env`, `state.json`, scripts, and systemd templates. The backup directory is gitignored and mode `700`.

If you change the model in `.env`, restart the services:

```bash
systemctl --user restart kiwix-wikipedia.service
systemctl --user restart lm-studio-app.service
systemctl --user restart lm-studio-qwen35-08b.service
systemctl --user restart lm-studio-discord-bot.service
```

Service templates live in `systemd/`; install them to `~/.config/systemd/user/`.

```bash
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pi-service-maintenance.timer pi-service-healthcheck.timer pi-runtime-backup.timer
```

For persistent service history, Raspberry Pi OS currently forces volatile journald storage. Run this once with sudo if you want `journalctl --user` logs to survive reboots:

```bash
sudo ./scripts/configure_pi_24x7_system.sh
```

The Pi already has zram swap active. If weekly reboot checks are desired, set `WEEKLY_REBOOT_ENABLED=true` in `.env`, install `pi-conditional-weekly-reboot.timer`, and enable it:

```bash
systemctl --user enable --now pi-conditional-weekly-reboot.timer
```

## Service Commands

Check whether the bot is running:

```bash
systemctl --user status lm-studio-discord-bot.service
```

Check whether local Wikipedia is running:

```bash
systemctl --user status kiwix-wikipedia.service
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

Follow the daily maintenance log:

```bash
tail -f logs/daily-service-maintenance.log
```

Follow the health-check log:

```bash
tail -f logs/service-health.log
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
!lm web latest Raspberry Pi news
!lm wikifind Albert Einstein
!lm wiki who was Albert Einstein?
!lm use qwen3.5-0.8b
!lm load qwen3.5-0.8b
```

`!lm models` lists downloaded LM Studio models. `!lm loaded` shows currently loaded model instances. `!lm web <query>` searches the web, asks the active local model to answer from the search snippets, and includes source links. `!lm wikifind <query>` returns fast local Wikipedia excerpts without using LM Studio. `!lm wiki <query>` searches local Wikipedia, asks the active local model to answer from the excerpts, and includes local source links. `!lm clean` unloads everything except the active bot model, loading the active model first if needed. `!lm load ...` unloads every other loaded model, loads the new model with one parallel slot and the context length from `LMS_DEFAULT_CONTEXT_LENGTH`, then makes the bot use that identifier for future replies.

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

## Web Search

`!lm web <query>` and `!lm search <query>` use web search before calling LM Studio. By default the bot uses Bing RSS search results, which requires no API key and works from the headless Pi. DuckDuckGo HTML search is also supported, but it may return bot challenges from server environments. For a self-hosted or private search backend, set:

```env
WEB_SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=https://your-searxng.example
```

Set `WEB_SEARCH_PROVIDER=duckduckgo` if you want to try DuckDuckGo instead.

Search results are sent to the active local model as context, so `qwen3.5-0.8b` stays the fast default while still being able to answer from fresh web snippets.

## Local Wikipedia

Local Wikipedia uses Kiwix ZIM files served by `kiwix-serve` on `127.0.0.1:8090`. The current first-pass corpus is Simple English Wikipedia without pictures:

```text
data/wikipedia/wikipedia_en-simple_all_nopic_2026-06.zim
```

The Kiwix command-line tools are unpacked locally in `vendor/kiwix-tools`, so sudo is not required. To rebuild that local bundle:

```bash
./scripts/install_local_kiwix_tools.sh
```

To download or resume the default Simple English ZIM:

```bash
./scripts/download_wikipedia_zim.sh
```

To download a different ZIM, pass the file name and optional URL:

```bash
./scripts/download_wikipedia_zim.sh wikipedia_en_all_mini_2026-06.zim
```

`!lm wikifind <query>` is the fast local lookup command. `!lm wiki <query>` asks the local model to synthesize an answer from those excerpts, which is more useful but slower on the Pi.
