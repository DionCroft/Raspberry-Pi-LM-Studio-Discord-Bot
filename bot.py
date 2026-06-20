"""Discord bot that talks directly to LM Studio."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord
import requests


DISCORD_MESSAGE_LIMIT = 1900
PROJECT_DIR = Path(__file__).resolve().parent
STATE_PATH = PROJECT_DIR / "state.json"
LOCK_PATH = PROJECT_DIR / "bot.lock"
DEFAULT_SYSTEM_PROMPT = (
    "You are a concise, helpful assistant in Discord. "
    "Answer naturally and keep replies practical."
)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    discord_channel_id: str | None
    discord_lm_prefix: str
    lms_binary: str
    lms_default_context_length: int
    lms_default_ttl_seconds: int
    lm_studio_base_url: str
    lm_studio_model: str
    lm_studio_timeout_seconds: int
    lm_studio_system_prompt: str


def load_config() -> Config:
    _load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is required.")

    return Config(
        discord_bot_token=token,
        discord_channel_id=os.getenv("DISCORD_CHANNEL_ID") or None,
        discord_lm_prefix=os.getenv("DISCORD_LM_PREFIX") or "!lm",
        lms_binary=os.getenv("LMS_BINARY") or "/home/cadmus/.lmstudio/bin/lms",
        lms_default_context_length=_env_int("LMS_DEFAULT_CONTEXT_LENGTH", 16384),
        lms_default_ttl_seconds=_env_int("LMS_DEFAULT_TTL_SECONDS", 14400),
        lm_studio_base_url=os.getenv("LM_STUDIO_BASE_URL") or "http://127.0.0.1:1234/v1",
        lm_studio_model=os.getenv("LM_STUDIO_MODEL") or "qwen3-1.7b",
        lm_studio_timeout_seconds=_env_int("LM_STUDIO_TIMEOUT_SECONDS", 180),
        lm_studio_system_prompt=os.getenv("LM_STUDIO_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT,
    )


def _load_state(default_model: str) -> dict[str, str]:
    if not STATE_PATH.exists():
        return {"active_model": default_model}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_model": default_model}
    active_model = data.get("active_model")
    if not isinstance(active_model, str) or not active_model.strip():
        active_model = default_model
    return {"active_model": active_model}


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _safe_identifier(value: str) -> str:
    identifier = value.strip().split("/")[-1].lower()
    identifier = re.sub(r"[^a-z0-9_.:-]+", "-", identifier).strip("-")
    if not identifier:
        raise ValueError("Model identifier cannot be empty.")
    return identifier


def _run_lms(config: Config, args: list[str], timeout: int = 240) -> str:
    result = subprocess.run(
        [config.lms_binary, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"lms {' '.join(args)} failed with exit code {result.returncode}")
    return output or "Done."


def _format_error(title: str, error: Exception) -> str:
    detail = str(error).strip() or error.__class__.__name__
    if len(detail) > 1200:
        detail = f"{detail[:1200].rstrip()}..."
    return f"{title}\n```text\n{detail}\n```"


def ask_lm_studio(prompt: str, config: Config, model: str) -> str:
    response = requests.post(
        f"{config.lm_studio_base_url.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": config.lm_studio_system_prompt},
                {"role": "user", "content": f"/no_think {prompt}"},
            ],
            "temperature": 0.7,
            "max_tokens": 700,
        },
        timeout=config.lm_studio_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    return (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
        or "LM Studio returned an empty response."
    )


def handle_model_command(command: str, config: Config, state: dict[str, str]) -> str:
    parts = command.split()
    subcommand = parts[0].lower() if parts else "status"

    if subcommand in {"status", "current"}:
        return f"Current chat model: `{state['active_model']}`"

    if subcommand in {"models", "list"}:
        output = _run_lms(config, ["ls"])
        return f"Models command completed.\n```text\n{output}\n```"

    if subcommand in {"loaded", "ps"}:
        output = _run_lms(config, ["ps"])
        return f"Loaded-models command completed.\n```text\n{output}\n```"

    if subcommand == "use":
        if len(parts) < 2:
            return "Usage: `!lm use <loaded-model-identifier>`"
        identifier = _safe_identifier(parts[1])
        state["active_model"] = identifier
        _save_state(state)
        return f"Use command completed. Using `{identifier}` for new replies."

    if subcommand == "load":
        if len(parts) < 2:
            return "Usage: `!lm load <model> [as <identifier>]`"

        model_spec = parts[1]
        identifier = _safe_identifier(model_spec)
        if len(parts) >= 4 and parts[2].lower() == "as":
            identifier = _safe_identifier(parts[3])

        previous_identifier = state["active_model"]
        unload_note = ""
        if previous_identifier != identifier:
            try:
                _run_lms(config, ["unload", previous_identifier], timeout=120)
                unload_note = f"Unloaded previous model `{previous_identifier}`.\n"
            except Exception as exc:
                unload_note = (
                    f"Tried to unload previous model `{previous_identifier}`, "
                    f"but LM Studio reported: `{str(exc).strip() or exc.__class__.__name__}`\n"
                )

        output = _run_lms(
            config,
            [
                "load",
                model_spec,
                "--context-length",
                str(config.lms_default_context_length),
                "--parallel",
                "1",
                "--identifier",
                identifier,
                "--ttl",
                str(config.lms_default_ttl_seconds),
                "-y",
            ],
            timeout=420,
        )
        state["active_model"] = identifier
        _save_state(state)
        return f"{unload_note}Load command completed. Loaded and using `{identifier}`.\n```text\n{output[-1500:]}\n```"

    return (
        "Model commands: `!lm status`, `!lm models`, `!lm loaded`, "
        "`!lm use <identifier>`, `!lm load <model> [as <identifier>]`"
    )


def _discord_chunks(text: str) -> list[str]:
    if len(text) <= DISCORD_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines() or [text]:
        line_length = len(line) + 1
        if current and current_length + line_length > DISCORD_MESSAGE_LIMIT:
            chunks.append("\n".join(current).rstrip())
            current = []
            current_length = 0
        if line_length > DISCORD_MESSAGE_LIMIT:
            chunks.extend(
                line[start : start + DISCORD_MESSAGE_LIMIT]
                for start in range(0, len(line), DISCORD_MESSAGE_LIMIT)
            )
            continue
        current.append(line)
        current_length += line_length
    if current:
        chunks.append("\n".join(current).rstrip())
    return chunks


def _extract_prompt(
    message: discord.Message,
    prefix: str,
    bot_user: discord.ClientUser | None,
) -> str | None:
    content = message.content.strip()
    if message.guild is None:
        return content

    if bot_user is not None:
        for token in (f"<@{bot_user.id}>", f"<@!{bot_user.id}>"):
            if content.startswith(token):
                return content.removeprefix(token).strip()

    if content.startswith(prefix):
        return content.removeprefix(prefix).strip()

    return None


class LMStudioDiscordClient(discord.Client):
    def __init__(self, config: Config, **options: Any) -> None:
        super().__init__(**options)
        self.config = config
        self.state = _load_state(config.lm_studio_model)

    async def on_ready(self) -> None:
        logging.info("Logged in as %s. Listening for %s prompts.", self.user, self.config.discord_lm_prefix)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self.config.discord_channel_id and str(message.channel.id) != self.config.discord_channel_id:
            return

        prompt = _extract_prompt(message, self.config.discord_lm_prefix, self.user)
        if not prompt:
            return

        async with message.channel.typing():
            try:
                if prompt.lower() in {"help", "commands"}:
                    reply = (
                        f"Use `{self.config.discord_lm_prefix} <message>` to chat. "
                        f"Model commands: `{self.config.discord_lm_prefix} status`, "
                        f"`{self.config.discord_lm_prefix} models`, "
                        f"`{self.config.discord_lm_prefix} loaded`, "
                        f"`{self.config.discord_lm_prefix} use <identifier>`, "
                        f"`{self.config.discord_lm_prefix} load <model> [as <identifier>]`."
                    )
                elif prompt.lower().startswith(("model ", "models", "loaded", "status", "use ", "load ")):
                    model_command = prompt[6:].strip() if prompt.lower().startswith("model ") else prompt
                    reply = await asyncio.to_thread(handle_model_command, model_command, self.config, self.state)
                else:
                    reply = await asyncio.to_thread(
                        ask_lm_studio,
                        prompt,
                        self.config,
                        self.state["active_model"],
                    )
            except requests.RequestException as exc:
                logging.exception("LM Studio request failed.")
                reply = _format_error("LM Studio request failed.", exc)
            except subprocess.TimeoutExpired as exc:
                logging.exception("Command timed out.")
                reply = _format_error("Command timed out before finishing.", exc)
            except (RuntimeError, ValueError) as exc:
                logging.exception("Command failed.")
                reply = _format_error("Command failed.", exc)
            except Exception as exc:
                logging.exception("Unexpected bot error.")
                reply = _format_error("Unexpected bot error.", exc)

        for chunk in _discord_chunks(reply):
            await message.reply(chunk, mention_author=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    lock_file = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another lm-studio-discord-bot process is already running.", file=sys.stderr)
        raise SystemExit(1)
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    config = load_config()
    intents = discord.Intents.default()
    intents.message_content = True
    client = LMStudioDiscordClient(config=config, intents=intents)
    client.run(config.discord_bot_token)


if __name__ == "__main__":
    main()
