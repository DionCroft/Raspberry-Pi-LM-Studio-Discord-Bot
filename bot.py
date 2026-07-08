"""Discord bot that talks directly to LM Studio."""

from __future__ import annotations

import asyncio
import fcntl
import html
from html.parser import HTMLParser
import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import discord
import requests


DISCORD_MESSAGE_LIMIT = 1900
PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"
STATE_PATH = PROJECT_DIR / "state.json"
LOCK_PATH = PROJECT_DIR / "bot.lock"
DEFAULT_LOG_PATH = PROJECT_DIR / "bot.log"
DEFAULT_SYSTEM_PROMPT = (
    "You are a concise, helpful assistant in Discord. "
    "Answer naturally and keep replies practical. "
    "Discord does not render LaTeX, so never use LaTeX or math delimiters like $...$. "
    "Write equations in plain text, such as epsilon_r, epsilon_0, >=, <=, x 10^-12, and F/m."
)
LATEX_REPLACEMENTS = {
    r"\alpha": "alpha",
    r"\beta": "beta",
    r"\gamma": "gamma",
    r"\delta": "delta",
    r"\epsilon": "epsilon",
    r"\varepsilon": "epsilon",
    r"\theta": "theta",
    r"\lambda": "lambda",
    r"\mu": "mu",
    r"\pi": "pi",
    r"\rho": "rho",
    r"\sigma": "sigma",
    r"\omega": "omega",
    r"\Omega": "ohm",
    r"\approx": "approx",
    r"\times": "x",
    r"\cdot": "*",
    r"\leq": "<=",
    r"\le": "<=",
    r"\geq": ">=",
    r"\ge": ">=",
    r"\ll": "<<",
    r"\gg": ">>",
    r"\infty": "infinity",
}


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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    discord_channel_id: str | None
    discord_lm_prefix: str
    lms_binary: str
    lms_default_context_length: int
    lms_default_ttl_seconds: int
    lms_min_free_memory_mb: int
    lms_ps_timeout_seconds: int
    lm_studio_base_url: str
    lm_studio_model: str
    lm_studio_timeout_seconds: int
    lm_studio_system_prompt: str
    bot_log_path: Path
    bot_log_max_bytes: int
    bot_log_backup_count: int
    bot_max_queue_size: int
    bot_user_cooldown_seconds: int
    web_search_enabled: bool
    web_search_provider: str
    web_search_max_results: int
    web_search_timeout_seconds: int
    web_search_user_agent: str
    searxng_base_url: str | None
    wiki_search_enabled: bool
    wiki_base_url: str
    wiki_max_results: int
    wiki_timeout_seconds: int
    wiki_article_chars: int


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


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
        lms_min_free_memory_mb=_env_int("LMS_MIN_FREE_MEMORY_MB", 700),
        lms_ps_timeout_seconds=_env_int("LMS_PS_TIMEOUT_SECONDS", 10),
        lm_studio_base_url=os.getenv("LM_STUDIO_BASE_URL") or "http://127.0.0.1:1234/v1",
        lm_studio_model=os.getenv("LM_STUDIO_MODEL") or "qwen3.5-0.8b",
        lm_studio_timeout_seconds=_env_int("LM_STUDIO_TIMEOUT_SECONDS", 180),
        lm_studio_system_prompt=os.getenv("LM_STUDIO_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT,
        bot_log_path=Path(os.getenv("BOT_LOG_PATH") or DEFAULT_LOG_PATH),
        bot_log_max_bytes=_env_int("BOT_LOG_MAX_BYTES", 1_000_000),
        bot_log_backup_count=_env_int("BOT_LOG_BACKUP_COUNT", 3),
        bot_max_queue_size=_env_int("BOT_MAX_QUEUE_SIZE", 3),
        bot_user_cooldown_seconds=_env_int("BOT_USER_COOLDOWN_SECONDS", 2),
        web_search_enabled=_env_bool("WEB_SEARCH_ENABLED", True),
        web_search_provider=(os.getenv("WEB_SEARCH_PROVIDER") or "bing").strip().lower(),
        web_search_max_results=_env_int("WEB_SEARCH_MAX_RESULTS", 4),
        web_search_timeout_seconds=_env_int("WEB_SEARCH_TIMEOUT_SECONDS", 12),
        web_search_user_agent=(
            os.getenv("WEB_SEARCH_USER_AGENT")
            or "Mozilla/5.0 (compatible; lm-studio-discord-bot/1.0)"
        ),
        searxng_base_url=os.getenv("SEARXNG_BASE_URL") or None,
        wiki_search_enabled=_env_bool("WIKI_SEARCH_ENABLED", True),
        wiki_base_url=(os.getenv("WIKI_BASE_URL") or "http://127.0.0.1:8090").rstrip("/"),
        wiki_max_results=_env_int("WIKI_MAX_RESULTS", 3),
        wiki_timeout_seconds=_env_int("WIKI_TIMEOUT_SECONDS", 12),
        wiki_article_chars=_env_int("WIKI_ARTICLE_CHARS", 700),
    )


def _load_state(default_model: str) -> dict[str, str]:
    default_state = {
        "active_model": default_model,
        "active_model_spec": default_model,
        "last_good_model": default_model,
        "last_good_model_spec": default_model,
    }
    if not STATE_PATH.exists():
        return default_state
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_state
    active_model = data.get("active_model")
    if not isinstance(active_model, str) or not active_model.strip():
        active_model = default_model
    active_model_spec = data.get("active_model_spec")
    if not isinstance(active_model_spec, str) or not active_model_spec.strip():
        active_model_spec = active_model
    last_good_model = data.get("last_good_model")
    if not isinstance(last_good_model, str) or not last_good_model.strip():
        last_good_model = active_model
    last_good_model_spec = data.get("last_good_model_spec")
    if not isinstance(last_good_model_spec, str) or not last_good_model_spec.strip():
        last_good_model_spec = active_model_spec
    return {
        "active_model": active_model,
        "active_model_spec": active_model_spec,
        "last_good_model": last_good_model,
        "last_good_model_spec": last_good_model_spec,
    }


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _set_dotenv_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replacement = f"{key}={value}"

    for index, line in enumerate(lines):
        if pattern.match(line) and not line.lstrip().startswith("#"):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def _sync_env_active_model(identifier: str) -> None:
    _set_dotenv_value(ENV_PATH, "LM_STUDIO_MODEL", identifier)


def configure_logging(config: Config) -> None:
    config.bot_log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        config.bot_log_path,
        maxBytes=config.bot_log_max_bytes,
        backupCount=config.bot_log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream_handler, file_handler], force=True)


def _safe_identifier(value: str) -> str:
    identifier = value.strip()
    if not identifier:
        raise ValueError("Model identifier cannot be empty.")
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]+", identifier):
        raise ValueError(
            "Model identifier can only contain letters, numbers, slash, dot, underscore, colon, at-sign, and dash."
        )
    return identifier


def _default_identifier_for_spec(model_spec: str) -> str:
    return _safe_identifier(model_spec.strip().split("/")[-1])


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


def _discord_safe_response(text: str) -> str:
    text = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", text)

    for latex, replacement in LATEX_REPLACEMENTS.items():
        text = text.replace(latex, replacement)

    text = text.replace(r"\(", "").replace(r"\)", "")
    text = text.replace(r"\[", "").replace(r"\]", "")
    text = re.sub(r"\$\s*([^$\n]{1,200}?)\s*\$", r"\1", text)
    text = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", text)
    text = re.sub(r"_\{([^{}]+)\}", r"_\1", text)
    text = re.sub(r"\\([{}_])", r"\1", text)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    return text


def _format_lm_studio_error(config: Config, error: requests.RequestException) -> str:
    if isinstance(error, requests.Timeout):
        return (
            f"LM Studio timed out after {config.lm_studio_timeout_seconds} seconds.\n"
            "The Pi is probably overloaded, LM Studio is wedged, or the active model is too heavy. "
            "Try `!lm health`, unload extra models in LM Studio, or switch to the smaller fast model "
            "`qwen3.5-0.8b`."
        )
    return _format_error("LM Studio request failed.", error)


def _api_model_identifiers(config: Config, timeout: int = 12) -> list[str]:
    response = requests.get(f"{config.lm_studio_base_url.rstrip('/')}/models", timeout=timeout)
    response.raise_for_status()
    data = response.json()
    identifiers: list[str] = []
    for item in data.get("data", []):
        identifier = item.get("id") if isinstance(item, dict) else None
        if isinstance(identifier, str) and identifier.strip():
            identifiers.append(identifier.strip())
    return identifiers


def _loaded_model_identifiers_from_lms_ls(config: Config) -> list[str]:
    output = _run_lms(config, ["ls"], timeout=45)
    identifiers: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "LOADED" not in line:
            continue
        columns = re.split(r"\s{2,}", line)
        if not columns:
            continue
        identifier = columns[0].removesuffix("(1 variant)").strip()
        if identifier:
            identifiers.append(identifier)
    return identifiers


def _loaded_model_identifiers(config: Config) -> list[str]:
    lms_ls_error: Exception | None = None
    try:
        return _loaded_model_identifiers_from_lms_ls(config)
    except Exception as exc:
        lms_ls_error = exc
        logging.warning("lms ls loaded-marker check failed; falling back to lms ps: %s", exc)

    try:
        output = _run_lms(config, ["ps"], timeout=config.lms_ps_timeout_seconds)
        identifiers: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("IDENTIFIER", "No models", "To load")):
                continue
            first = line.split()[0]
            if first and first not in {"MODEL", "STATUS"}:
                identifiers.append(first)
        return identifiers
    except Exception as ps_exc:
        raise RuntimeError(
            "Could not inspect loaded models through `lms ps` or `lms ls`. "
            f"lms ls error: {lms_ls_error}; lms ps error: {ps_exc}"
        ) from ps_exc


def _format_loaded_models(config: Config) -> str:
    loaded = _loaded_model_identifiers(config)
    if not loaded:
        return "Loaded-models command completed. No models are currently loaded."
    return "Loaded-models command completed.\n" + "\n".join(f"- `{identifier}`" for identifier in loaded)


def _unload_model(config: Config, identifier: str) -> None:
    _run_lms(config, ["unload", identifier], timeout=90)


def _unload_other_models(
    config: Config,
    target_identifier: str,
    loaded: list[str] | None = None,
) -> list[str]:
    loaded = loaded if loaded is not None else _loaded_model_identifiers(config)
    unloaded: list[str] = []
    failures: list[str] = []
    for identifier in loaded:
        if identifier == target_identifier:
            continue
        try:
            _unload_model(config, identifier)
            unloaded.append(identifier)
        except Exception as exc:
            failures.append(f"{identifier}: {str(exc).strip() or exc.__class__.__name__}")

    if failures:
        raise RuntimeError(
            "Could not enforce single-model mode because these models would not unload: "
            + "; ".join(failures)
        )
    return unloaded


def _ensure_single_active_model(config: Config, state: dict[str, str]) -> list[str]:
    active_model = state["active_model"]
    active_spec = state.get("active_model_spec") or active_model
    loaded = _loaded_model_identifiers(config)
    unloaded = _unload_other_models(config, active_model, loaded)
    if active_model not in loaded:
        _check_memory_guard(config)
        _load_model(config, active_spec, active_model)
    return unloaded


def _runtime_warnings(config: Config, state: dict[str, str]) -> list[str]:
    loaded = _loaded_model_identifiers(config)
    active_model = state["active_model"]
    warnings: list[str] = []
    if len(loaded) > 1:
        warnings.append(
            "Multiple models are loaded: "
            + ", ".join(loaded)
            + ". Single-model mode should unload extras on the next request."
        )
    if active_model not in loaded:
        warnings.append(f"Active model `{active_model}` is not currently loaded.")
    return warnings


def _format_runtime_warning(config: Config, state: dict[str, str]) -> str:
    try:
        warnings = _runtime_warnings(config, state)
    except Exception:
        return ""
    if not warnings:
        return ""
    return "\n\nRuntime warning:\n" + "\n".join(f"- {warning}" for warning in warnings)


def _is_model_loaded(config: Config, identifier: str) -> bool:
    return identifier in _loaded_model_identifiers(config)


def _memory_available_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _check_memory_guard(config: Config) -> None:
    available_mb = _memory_available_mb()
    if available_mb is not None and available_mb < config.lms_min_free_memory_mb:
        raise RuntimeError(
            f"Refusing to load because only {available_mb} MB RAM is available. "
            f"Minimum configured free RAM is {config.lms_min_free_memory_mb} MB."
        )


def _load_model(config: Config, model_spec: str, identifier: str) -> str:
    args = [
        "load",
        model_spec,
        "--context-length",
        str(config.lms_default_context_length),
        "--parallel",
        "1",
        "--identifier",
        identifier,
        "-y",
    ]
    if config.lms_default_ttl_seconds > 0:
        args.extend(["--ttl", str(config.lms_default_ttl_seconds)])

    return _run_lms(
        config,
        args,
        timeout=420,
    )


def _mark_active_model(state: dict[str, str], identifier: str, model_spec: str) -> None:
    state["active_model"] = identifier
    state["active_model_spec"] = model_spec
    state["last_good_model"] = identifier
    state["last_good_model_spec"] = model_spec
    _save_state(state)
    _sync_env_active_model(identifier)


def _looks_like_model_not_loaded(error: requests.HTTPError) -> bool:
    response = error.response
    status_code = response.status_code if response is not None else None
    body = response.text.lower() if response is not None else str(error).lower()
    if status_code in {400, 404, 422} and "model" in body:
        return any(fragment in body for fragment in ("not loaded", "not found", "not exist", "unknown"))
    return False


def ask_lm_studio(
    prompt: str,
    config: Config,
    model: str,
    max_tokens: int = 700,
    temperature: float = 0.7,
) -> str:
    response = requests.post(
        f"{config.lm_studio_base_url.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": config.lm_studio_system_prompt},
                {"role": "user", "content": f"/no_think {prompt}"},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=config.lm_studio_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
        or "LM Studio returned an empty response."
    )
    return _discord_safe_response(content)


def ask_lm_studio_with_reload(
    prompt: str,
    config: Config,
    state: dict[str, str],
    max_tokens: int = 700,
    temperature: float = 0.7,
) -> str:
    model = state["active_model"]
    try:
        unloaded = _ensure_single_active_model(config, state)
        if unloaded:
            logging.info("event=single_model_enforced active_model=%s unloaded=%s", model, ",".join(unloaded))
        return ask_lm_studio(prompt, config, model, max_tokens=max_tokens, temperature=temperature)
    except requests.HTTPError as exc:
        if not _looks_like_model_not_loaded(exc):
            raise

    model_spec = state.get("active_model_spec") or model
    logging.info("event=auto_reload model=%s spec=%s", model, model_spec)
    _unload_other_models(config, model)
    _check_memory_guard(config)
    _load_model(config, model_spec, model)
    return ask_lm_studio(prompt, config, model, max_tokens=max_tokens, temperature=temperature)


def _clean_search_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _search_terms(query: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "before",
        "best",
        "find",
        "latest",
        "news",
        "recent",
        "search",
        "today",
        "update",
        "updates",
        "what",
        "when",
        "where",
        "with",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9]+", query.lower()):
        if term in stopwords:
            continue
        if len(term) >= 3 or term in {"ai", "pi", "vr"}:
            terms.append(term)
    return list(dict.fromkeys(terms))


def _rank_search_results(query: str, results: list[WebSearchResult], max_results: int) -> list[WebSearchResult]:
    terms = _search_terms(query)
    if not terms:
        return results[:max_results]

    scored: list[tuple[int, WebSearchResult]] = []
    for result in results:
        tokens = set(re.findall(r"[a-z0-9]+", f"{result.title} {result.snippet} {result.url}".lower()))
        score = sum(1 for term in terms if term in tokens)
        scored.append((score, result))

    threshold = min(2, len(terms))
    relevant = [(score, result) for score, result in scored if score >= threshold]
    if not relevant:
        relevant = [(score, result) for score, result in scored if score > 0]
    if not relevant:
        return results[:max_results]

    relevant.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in relevant[:max_results]]


def _normalize_duckduckgo_url(value: str) -> str:
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    redirect = query.get("uddg", [""])[0]
    if redirect:
        return unquote(redirect)
    return value


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max_results
        self.results: list[WebSearchResult] = []
        self._current_url = ""
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._flush_current()
            self._current_url = _normalize_duckduckgo_url(attrs_dict.get("href", ""))
            self._current_title = []
            self._current_snippet = []
            self._in_title = True
        elif "result__snippet" in classes:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        elif tag in {"a", "div"} and self._in_snippet:
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._current_title.append(data)
        elif self._in_snippet:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        if len(self.results) >= self.max_results or not self._current_url:
            return
        title = _clean_search_text(" ".join(self._current_title))
        snippet = _clean_search_text(" ".join(self._current_snippet))
        if title and self._current_url.startswith(("http://", "https://")):
            self.results.append(WebSearchResult(title=title, url=self._current_url, snippet=snippet))
        self._current_url = ""
        self._current_title = []
        self._current_snippet = []


class KiwixSearchHTMLParser(HTMLParser):
    def __init__(self, base_url: str, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_results = max_results
        self.results: list[WebSearchResult] = []
        self._in_results = False
        self._results_depth = 0
        self._in_link = False
        self._in_cite = False
        self._current_url = ""
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if self._in_results:
            self._results_depth += 1
        if tag == "div" and "results" in classes and not self._in_results:
            self._in_results = True
            self._results_depth = 1
        elif self._in_results and tag == "li":
            self._flush_current()
            self._current_url = ""
            self._current_title = []
            self._current_snippet = []
        elif self._in_results and tag == "a" and not self._current_url:
            self._current_url = urljoin(f"{self.base_url}/", attrs_dict.get("href", ""))
            self._in_link = True
        elif self._in_results and tag == "cite":
            self._in_cite = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
        elif tag == "cite" and self._in_cite:
            self._in_cite = False
        elif tag == "li" and self._in_results:
            self._flush_current()
        if self._in_results:
            self._results_depth -= 1
        if self._in_results and self._results_depth <= 0:
            self._in_results = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._current_title.append(data)
        elif self._in_cite:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        if len(self.results) >= self.max_results or not self._current_url:
            return
        title = _clean_search_text(" ".join(self._current_title))
        snippet = _clean_search_text(" ".join(self._current_snippet))
        if title and self._current_url.startswith(("http://", "https://")):
            self.results.append(WebSearchResult(title=title, url=self._current_url, snippet=snippet))
        self._current_url = ""
        self._current_title = []
        self._current_snippet = []


class KiwixArticleHTMLParser(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self.paragraphs: list[str] = []
        self._current: list[str] = []
        self._in_paragraph = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "sup", "table"}:
            self._skip_depth += 1
        elif tag == "p" and self._skip_depth == 0:
            self._current = []
            self._in_paragraph = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "sup", "table"} and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "p" and self._in_paragraph:
            paragraph = _clean_search_text(" ".join(self._current))
            if len(paragraph) >= 80:
                self.paragraphs.append(paragraph)
            self._current = []
            self._in_paragraph = False

    def handle_data(self, data: str) -> None:
        if self._in_paragraph and self._skip_depth == 0:
            self._current.append(data)

    def text(self) -> str:
        text = "\n".join(self.paragraphs)
        if len(text) <= self.max_chars:
            return text
        return f"{text[: self.max_chars].rsplit(' ', 1)[0].rstrip()}..."


def _search_duckduckgo(query: str, config: Config) -> list[WebSearchResult]:
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": config.web_search_user_agent},
        timeout=config.web_search_timeout_seconds,
    )
    response.raise_for_status()
    parser = DuckDuckGoHTMLParser(config.web_search_max_results)
    parser.feed(response.text)
    parser.close()
    return _rank_search_results(query, parser.results, config.web_search_max_results)


def _search_bing(query: str, config: Config) -> list[WebSearchResult]:
    response = requests.get(
        "https://www.bing.com/search",
        params={"format": "rss", "q": query},
        headers={"User-Agent": config.web_search_user_agent},
        timeout=config.web_search_timeout_seconds,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    results: list[WebSearchResult] = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        url = item.findtext("link", default="")
        snippet = item.findtext("description", default="")
        if url.startswith(("http://", "https://")):
            results.append(
                WebSearchResult(
                    title=_clean_search_text(title),
                    url=url.strip(),
                    snippet=_clean_search_text(snippet),
                )
            )
    return _rank_search_results(query, results, config.web_search_max_results)


def _search_searxng(query: str, config: Config) -> list[WebSearchResult]:
    if not config.searxng_base_url:
        raise RuntimeError("SEARXNG_BASE_URL is required when WEB_SEARCH_PROVIDER=searxng.")
    response = requests.get(
        f"{config.searxng_base_url.rstrip('/')}/search",
        params={"q": query, "format": "json"},
        headers={"User-Agent": config.web_search_user_agent},
        timeout=config.web_search_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    results: list[WebSearchResult] = []
    for item in data.get("results", []):
        title = item.get("title")
        url = item.get("url")
        snippet = item.get("content") or item.get("snippet") or ""
        if isinstance(title, str) and isinstance(url, str) and url.startswith(("http://", "https://")):
            results.append(
                WebSearchResult(
                    title=_clean_search_text(title),
                    url=url.strip(),
                    snippet=_clean_search_text(str(snippet)),
                )
            )
    return _rank_search_results(query, results, config.web_search_max_results)


def search_web(query: str, config: Config) -> list[WebSearchResult]:
    if not config.web_search_enabled:
        raise RuntimeError("Web search is disabled. Set WEB_SEARCH_ENABLED=true to enable it.")
    query = query.strip()
    if not query:
        raise ValueError("Usage: `!lm web <search query>`")
    if config.web_search_provider == "bing":
        return _search_bing(query, config)
    if config.web_search_provider == "searxng":
        return _search_searxng(query, config)
    if config.web_search_provider == "duckduckgo":
        return _search_duckduckgo(query, config)
    raise RuntimeError(f"Unsupported WEB_SEARCH_PROVIDER: {config.web_search_provider}")


def answer_with_web_search(prompt: str, config: Config, state: dict[str, str]) -> str:
    query = prompt.split(maxsplit=1)[1].strip() if " " in prompt else ""
    results = search_web(query, config)
    if not results:
        return "Web search completed, but no usable results came back."

    source_lines = []
    for index, result in enumerate(results, start=1):
        snippet = result.snippet or "No snippet available."
        source_lines.append(f"[{index}] {result.title}\nURL: {result.url}\nSnippet: {snippet}")

    grounded_prompt = (
        "Use the web search results below to answer the user's question. "
        "Keep the answer to 4 short bullets or fewer and cite sources inline like [1] or [2]. "
        "If the results are insufficient, say what is missing.\n\n"
        f"Question: {query}\n\n"
        "Web search results:\n"
        + "\n\n".join(source_lines)
    )
    answer = ask_lm_studio_with_reload(grounded_prompt, config, state, max_tokens=300, temperature=0.2)
    sources = "\n".join(f"[{index}] {result.title} - {result.url}" for index, result in enumerate(results, start=1))
    return f"{answer}\n\nSources:\n{sources}"


def _fetch_wiki_article_text(url: str, config: Config) -> str:
    response = requests.get(url, timeout=config.wiki_timeout_seconds)
    response.raise_for_status()
    response.encoding = "utf-8"
    parser = KiwixArticleHTMLParser(config.wiki_article_chars)
    parser.feed(response.text)
    parser.close()
    return parser.text()


def _wiki_search_pattern(query: str) -> str:
    stopwords = {
        "a",
        "an",
        "are",
        "can",
        "did",
        "do",
        "does",
        "explain",
        "for",
        "is",
        "me",
        "of",
        "on",
        "please",
        "tell",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
    }
    terms = [term for term in re.findall(r"[A-Za-z0-9]+", query) if term.lower() not in stopwords]
    return " ".join(terms) or query


def _normalize_wiki_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def search_wiki(query: str, config: Config) -> list[WebSearchResult]:
    if not config.wiki_search_enabled:
        raise RuntimeError("Local Wikipedia search is disabled. Set WIKI_SEARCH_ENABLED=true to enable it.")
    query = query.strip()
    if not query:
        raise ValueError("Usage: `!lm wiki <search query>`")

    search_pattern = _wiki_search_pattern(query)
    response = requests.get(
        f"{config.wiki_base_url}/search",
        params={"pattern": search_pattern},
        timeout=config.wiki_timeout_seconds,
    )
    response.raise_for_status()
    response.encoding = "utf-8"

    parser = KiwixSearchHTMLParser(config.wiki_base_url, config.wiki_max_results)
    parser.feed(response.text)
    parser.close()

    parser_results = parser.results
    normalized_pattern = _normalize_wiki_title(search_pattern)
    exact_matches = [result for result in parser_results if _normalize_wiki_title(result.title) == normalized_pattern]
    if exact_matches:
        parser_results = exact_matches[:1]

    results: list[WebSearchResult] = []
    for result in parser_results:
        article_text = ""
        try:
            article_text = _fetch_wiki_article_text(result.url, config)
        except requests.RequestException as exc:
            logging.warning("event=wiki_article_fetch_failed url=%s error=%s", result.url, exc)
        snippet = article_text or result.snippet or "No snippet available."
        results.append(WebSearchResult(title=result.title, url=result.url, snippet=snippet))
    return results


def format_wiki_results(prompt: str, config: Config) -> str:
    query = prompt.split(maxsplit=1)[1].strip() if " " in prompt else ""
    results = search_wiki(query, config)
    if not results:
        return "Local Wikipedia search completed, but no usable results came back."

    lines = ["Local Wikipedia results:"]
    for index, result in enumerate(results, start=1):
        snippet = result.snippet[:500].rsplit(" ", 1)[0].rstrip()
        if len(result.snippet) > len(snippet):
            snippet = f"{snippet}..."
        lines.append(f"[W{index}] {result.title}\n{snippet}\n{result.url}")
    return "\n\n".join(lines)


def answer_with_wiki_search(prompt: str, config: Config, state: dict[str, str]) -> str:
    query = prompt.split(maxsplit=1)[1].strip() if " " in prompt else ""
    results = search_wiki(query, config)
    if not results:
        return "Local Wikipedia search completed, but no usable results came back."

    source_lines = []
    for index, result in enumerate(results, start=1):
        source_lines.append(f"[W{index}] {result.title}\nLocal URL: {result.url}\nExcerpt: {result.snippet}")

    grounded_prompt = (
        "Use the local Wikipedia excerpts below to answer the user's question. "
        "Keep the answer to 4 short bullets or fewer and cite sources inline like [W1] or [W2]. "
        "If the excerpts are insufficient, say what is missing.\n\n"
        f"Question: {query}\n\n"
        "Local Wikipedia excerpts:\n"
        + "\n\n".join(source_lines)
    )
    answer = ask_lm_studio_with_reload(grounded_prompt, config, state, max_tokens=220, temperature=0.2)
    sources = "\n".join(f"[W{index}] {result.title} - {result.url}" for index, result in enumerate(results, start=1))
    return f"{answer}\n\nLocal Wikipedia sources:\n{sources}"


def build_health_report(config: Config, state: dict[str, str], discord_ready: bool) -> str:
    lines = ["Health check completed."]
    broken: list[str] = []

    if discord_ready:
        lines.append("- Discord gateway: ok")
    else:
        lines.append("- Discord gateway: not ready")
        broken.append("Discord gateway is not ready")

    try:
        response = requests.get(f"{config.lm_studio_base_url.rstrip('/')}/models", timeout=10)
        response.raise_for_status()
        lines.append("- LM Studio API: ok")
    except requests.RequestException as exc:
        lines.append(f"- LM Studio API: failed ({exc})")
        broken.append("LM Studio API is not reachable")

    if config.wiki_search_enabled:
        try:
            response = requests.get(f"{config.wiki_base_url}/catalog/v2/entries", timeout=5)
            response.raise_for_status()
            lines.append("- Local Wikipedia: ok")
        except requests.RequestException as exc:
            lines.append(f"- Local Wikipedia: failed ({exc})")
            broken.append("Local Wikipedia/Kiwix is not reachable")
    else:
        lines.append("- Local Wikipedia: disabled")

    active_model = state["active_model"]
    active_spec = state.get("active_model_spec") or active_model
    lines.append(f"- Active model: `{active_model}` (spec: `{active_spec}`)")

    try:
        loaded = _loaded_model_identifiers(config)
        if loaded:
            lines.append("- Loaded models: " + ", ".join(f"`{identifier}`" for identifier in loaded))
        else:
            lines.append("- Loaded models: none")
        if len(loaded) > 1:
            lines.append("- Single-model mode: violated")
            broken.append("More than one LM Studio model is loaded, which can overload the Pi")
        else:
            lines.append("- Single-model mode: ok")
        if active_model in loaded:
            lines.append("- Active model loaded: yes")
        else:
            lines.append("- Active model loaded: no")
            broken.append(f"Active model `{active_model}` is not loaded")
    except Exception as exc:
        lines.append(f"- Loaded models: failed ({exc})")
        broken.append("Could not inspect loaded models")

    available_mb = _memory_available_mb()
    if available_mb is None:
        lines.append("- RAM: unknown")
    elif available_mb < config.lms_min_free_memory_mb:
        lines.append(f"- RAM: low ({available_mb} MB available)")
        broken.append("Available RAM is below the load guard")
    else:
        lines.append(f"- RAM: ok ({available_mb} MB available)")

    if broken:
        lines.append("")
        lines.append("Broken:")
        lines.extend(f"- {item}" for item in broken)
    else:
        lines.append("")
        lines.append("Nothing looks broken.")

    return "\n".join(lines)


def handle_model_command(command: str, config: Config, state: dict[str, str]) -> str:
    parts = command.split()
    subcommand = parts[0].lower() if parts else "status"

    if subcommand in {"status", "current"}:
        return (
            f"Current chat model: `{state['active_model']}`\n"
            f"Load spec: `{state.get('active_model_spec') or state['active_model']}`\n"
            f"Last good model: `{state.get('last_good_model') or state['active_model']}`"
        )

    if subcommand in {"models", "list"}:
        output = _run_lms(config, ["ls"])
        return f"Models command completed.\n```text\n{output}\n```"

    if subcommand in {"loaded", "ps"}:
        return _format_loaded_models(config)

    if subcommand == "clean":
        unloaded = _ensure_single_active_model(config, state)
        if unloaded:
            return (
                f"Clean command completed. Active model `{state['active_model']}` is now the only loaded model.\n"
                "Unloaded: " + ", ".join(f"`{identifier}`" for identifier in unloaded)
            )
        return f"Clean command completed. Active model `{state['active_model']}` was already the only loaded model."

    if subcommand == "use":
        if len(parts) < 2:
            return "Usage: `!lm use <loaded-model-identifier>`"
        identifier = _safe_identifier(parts[1])
        loaded = _loaded_model_identifiers(config)
        if identifier not in loaded:
            return (
                f"Use command failed. `{identifier}` is not currently loaded in LM Studio.\n"
                "Run `!lm loaded` to see valid identifiers, or `!lm load <model> as <identifier>`."
            )
        unloaded = _unload_other_models(config, identifier, loaded)
        state["active_model"] = identifier
        state["active_model_spec"] = identifier
        state["last_good_model"] = identifier
        state["last_good_model_spec"] = identifier
        _save_state(state)
        _sync_env_active_model(identifier)
        note = f"\nSingle-model mode unloaded: {', '.join(unloaded)}." if unloaded else ""
        return f"Use command completed. Using `{identifier}` for new replies.{note}"

    if subcommand == "load":
        if len(parts) < 2:
            return "Usage: `!lm load <model> [as <identifier>]`"

        model_spec = parts[1]
        identifier = _default_identifier_for_spec(model_spec)
        if len(parts) >= 4 and parts[2].lower() == "as":
            identifier = _safe_identifier(parts[3])

        previous_identifier = state["active_model"]
        previous_spec = state.get("active_model_spec") or previous_identifier
        unload_note = ""
        try:
            loaded = _loaded_model_identifiers(config)
            unloaded = _unload_other_models(config, identifier, loaded)
            if unloaded:
                unload_note = "Single-model mode unloaded: " + ", ".join(f"`{item}`" for item in unloaded) + ".\n"

            if identifier in loaded:
                output = f"Model `{identifier}` was already loaded."
            else:
                _check_memory_guard(config)
                output = _load_model(config, model_spec, identifier)
        except Exception as exc:
            state["active_model"] = previous_identifier
            state["active_model_spec"] = previous_spec
            _save_state(state)
            _sync_env_active_model(previous_identifier)
            fallback_note = ""
            try:
                if previous_identifier and not _is_model_loaded(config, previous_identifier):
                    _load_model(config, previous_spec, previous_identifier)
                fallback_note = f"Switched back to previous model `{previous_identifier}`."
            except Exception as fallback_exc:
                fallback_note = (
                    f"Could not reload previous model `{previous_identifier}`: "
                    f"{str(fallback_exc).strip() or fallback_exc.__class__.__name__}"
                )
            return _format_error(
                f"Load command failed for `{identifier}`. {fallback_note}",
                exc,
            )

        _mark_active_model(state, identifier, model_spec)
        return f"{unload_note}Load command completed. Loaded and using `{identifier}`.\n```text\n{output[-1500:]}\n```"

    return (
        "Model commands: `!lm health`, `!lm clean`, `!lm status`, `!lm models`, `!lm loaded`, "
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


def _command_name(prompt: str) -> str:
    lower = prompt.strip().lower()
    if lower in {"help", "commands"}:
        return "help"
    if lower.startswith("model "):
        lower = lower.removeprefix("model ").strip()
    if not lower:
        return "empty"
    first = lower.split()[0]
    if first in {
        "health",
        "status",
        "current",
        "models",
        "list",
        "loaded",
        "ps",
        "use",
        "load",
        "clean",
        "web",
        "search",
        "wiki",
        "wikipedia",
        "wikifind",
        "wikisearch",
    }:
        return first
    return "chat"


class LMStudioDiscordClient(discord.Client):
    def __init__(self, config: Config, **options: Any) -> None:
        super().__init__(**options)
        self.config = config
        self.state = _load_state(config.lm_studio_model)
        self.request_semaphore = asyncio.Semaphore(1)
        self.pending_requests = 0
        self.last_user_request_at: dict[int, float] = {}

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

        command = _command_name(prompt)
        now = time.monotonic()
        last_request_at = self.last_user_request_at.get(message.author.id, 0.0)
        if command != "help" and now - last_request_at < self.config.bot_user_cooldown_seconds:
            await message.reply(
                f"Please wait {self.config.bot_user_cooldown_seconds} seconds between bot requests.",
                mention_author=False,
            )
            return

        if self.pending_requests >= self.config.bot_max_queue_size:
            await message.reply(
                "The Pi is busy right now. Please try again when the current model request finishes.",
                mention_author=False,
            )
            logging.warning(
                "event=request_rejected reason=queue_full user_id=%s channel_id=%s command=%s pending=%s",
                message.author.id,
                message.channel.id,
                command,
                self.pending_requests,
            )
            return

        self.pending_requests += 1
        self.last_user_request_at[message.author.id] = now
        start_time = time.monotonic()
        outcome = "success"
        error_name = ""
        model = self.state["active_model"]
        reply = "Unexpected bot error before a reply was generated."

        try:
            async with self.request_semaphore:
                async with message.channel.typing():
                    try:
                        if command == "help":
                            reply = (
                                f"Use `{self.config.discord_lm_prefix} <message>` to chat. "
                                f"Model commands: `{self.config.discord_lm_prefix} health`, "
                                f"`{self.config.discord_lm_prefix} clean`, "
                                f"`{self.config.discord_lm_prefix} status`, "
                                f"`{self.config.discord_lm_prefix} models`, "
                                f"`{self.config.discord_lm_prefix} loaded`, "
                                f"`{self.config.discord_lm_prefix} web <query>`, "
                                f"`{self.config.discord_lm_prefix} wiki <query>`, "
                                f"`{self.config.discord_lm_prefix} wikifind <query>`, "
                                f"`{self.config.discord_lm_prefix} use <identifier>`, "
                                f"`{self.config.discord_lm_prefix} load <model> [as <identifier>]`."
                            )
                        elif command == "health":
                            reply = await asyncio.to_thread(
                                build_health_report,
                                self.config,
                                self.state,
                                self.is_ready(),
                            )
                        elif command in {"models", "list", "loaded", "ps", "status", "current", "use", "load", "clean"}:
                            model_command = prompt[6:].strip() if prompt.lower().startswith("model ") else prompt
                            reply = await asyncio.to_thread(handle_model_command, model_command, self.config, self.state)
                        elif command in {"web", "search"}:
                            reply = await asyncio.to_thread(answer_with_web_search, prompt, self.config, self.state)
                        elif command in {"wiki", "wikipedia"}:
                            reply = await asyncio.to_thread(answer_with_wiki_search, prompt, self.config, self.state)
                        elif command in {"wikifind", "wikisearch"}:
                            reply = await asyncio.to_thread(format_wiki_results, prompt, self.config)
                        else:
                            reply = await asyncio.to_thread(
                                ask_lm_studio_with_reload,
                                prompt,
                                self.config,
                                self.state,
                            )
                    except requests.RequestException as exc:
                        outcome = "failure"
                        error_name = exc.__class__.__name__
                        logging.exception("LM Studio request failed.")
                        reply = _format_lm_studio_error(self.config, exc)
                    except subprocess.TimeoutExpired as exc:
                        outcome = "failure"
                        error_name = exc.__class__.__name__
                        logging.exception("Command timed out.")
                        reply = _format_error("Command timed out before finishing.", exc)
                    except (RuntimeError, ValueError) as exc:
                        outcome = "failure"
                        error_name = exc.__class__.__name__
                        logging.exception("Command failed.")
                        reply = _format_error("Command failed.", exc)
                    except Exception as exc:
                        outcome = "failure"
                        error_name = exc.__class__.__name__
                        logging.exception("Unexpected bot error.")
                        reply = _format_error("Unexpected bot error.", exc)
        except Exception as exc:
            outcome = "failure"
            error_name = exc.__class__.__name__
            logging.exception("Bot request processing failed.")
            reply = _format_error("Bot request processing failed.", exc)
        finally:
            self.pending_requests -= 1
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logging.info(
                "event=request command=%s outcome=%s model=%s duration_ms=%s user_id=%s channel_id=%s error=%s",
                command,
                outcome,
                model,
                duration_ms,
                message.author.id,
                message.channel.id,
                error_name or "-",
            )

        try:
            for chunk in _discord_chunks(reply):
                await message.reply(chunk, mention_author=False)
        except discord.DiscordException:
            logging.exception("Discord reply failed.")


def main() -> None:
    lock_file = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another lm-studio-discord-bot process is already running.", file=sys.stderr)
        raise SystemExit(1)
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    config = load_config()
    configure_logging(config)
    intents = discord.Intents.default()
    intents.message_content = True
    client = LMStudioDiscordClient(config=config, intents=intents)
    client.run(config.discord_bot_token)


if __name__ == "__main__":
    main()
