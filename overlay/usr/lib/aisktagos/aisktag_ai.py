"""Клиентская библиотека Mind (встроенный ИИ AIsktagOS). Только стандартная библиотека Python.

Её используют команда `ai`, окно-ассистент aisktag-mind и Центр AIsktagOS. Работает на Linux и Windows.

Провайдеры (cfg['provider']):
  'auto'     — маршрутизатор: сам выбирает модель под задачу, бесплатные и дешёвые первыми,
               при лимите или сбое переключается на следующую (по умолчанию)
  'local'    — локальный llama.cpp через systemd-socket (http://127.0.0.1:6573)
  'nvidia', 'groq', 'gemini', 'openrouter', 'deepseek', 'kimi', 'openai' — OpenAI-совместимые API
  'claude'   — Anthropic Messages API
  'external' — любой OpenAI-совместимый сервер из base_url

Ключи берутся из переменных окружения (NVIDIA_API_KEY, GROQ_API_KEY, …) или из ~/.config/aisktagos/ai.json
(на Windows %APPDATA%\\aisktagos\\ai.json), поле "keys": {"nvidia": "…"}. Ключи никогда не пишутся в журнал.

Экономия квоты в режиме auto:
  • задача классифицируется (fast / code / deep), простые вопросы идут в быструю бесплатную модель;
  • платные модели (Claude, OpenAI) стоят в конце очереди и используются, только если остальные недоступны;
  • одинаковый вопрос в течение суток отдаётся из кэша без обращения к API;
  • для быстрых вопросов история урезается — меньше токенов на запрос;
  • провайдер, упёршийся в лимит (429) или с неверным ключом, временно пропускается.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

IS_WINDOWS = sys.platform == "win32"


def _config_home() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"])
    if IS_WINDOWS and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"])
    return Path.home() / ".config"


def _cache_home() -> Path:
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"])
    if IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"])
    return Path.home() / ".cache"


LOCAL_URL      = "http://127.0.0.1:6573/v1"
BACKEND_HEALTH = "http://127.0.0.1:6574/health"
OLLAMA_URL     = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if "://" not in OLLAMA_URL:
    OLLAMA_URL = "http://" + OLLAMA_URL
OLLAMA_URL = OLLAMA_URL.replace("://0.0.0.0", "://127.0.0.1")
USER_CONF      = _config_home() / "aisktagos" / "ai.json"
CACHE_DIR      = _cache_home() / "aisktagos" / "mind"
CATALOG        = Path(os.environ.get("AISKTAG_CATALOG", "/usr/share/aisktagos/ai/models.json"))
MODEL_HELPER   = "/usr/lib/aisktagos/ai/aisktag-ai-model"
READ_TIMEOUT   = 900   # секунд; первая загрузка локальной модели может занять несколько минут
# Облачный API, который молчит дольше (очередь на бесплатном тарифе), считаем недоступным и идём к следующему
CLOUD_TIMEOUT  = {"fast": 25, "code": 45, "deep": 90}
MODELS_TTL     = 24 * 3600   # как часто перечитывать список моделей провайдера
CACHE_TTL      = 24 * 3600

TIERS = ("fast", "code", "deep")
TIER_NAMES = {"fast": "быстрый ответ", "code": "код", "deep": "сложная задача"}

# ---------------------------------------------------------------------------
# Провайдеры. models: модель на каждый уровень задачи; free: бесплатный тариф (идёт в очереди первым)
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, dict] = {
    "local": {
        "title": "Локальная модель", "kind": "openai", "base_url": LOCAL_URL, "env": [], "free": True,
        "models": {"fast": ["aisktag-mind"], "code": ["aisktag-mind"], "deep": ["aisktag-mind"]},
        "description": "llama.cpp на этом компьютере, работает без интернета",
    },
    "ollama": {
        "title": "Ollama", "kind": "openai", "base_url": OLLAMA_URL + "/v1", "env": [], "free": True,
        "models": {"fast": [], "code": [], "deep": []},   # берутся из установленных в Ollama моделей
        "description": "модели Ollama на этом компьютере, без интернета и ключей",
    },
    "groq": {
        "title": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
        "env": ["GROQ_API_KEY"], "free": True,
        "models": {"fast": ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"],
                   "code": ["openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b"],
                   "deep": ["moonshotai/kimi-k2-instruct", "openai/gpt-oss-120b"]},
        "description": "очень быстрые открытые модели, бесплатный тариф",
    },
    "nvidia": {
        "title": "NVIDIA NIM", "kind": "openai", "base_url": "https://integrate.api.nvidia.com/v1",
        "env": ["NVIDIA_API_KEY"], "free": True,
        "models": {"fast": ["nvidia/nemotron-3-super-120b-a12b", "poolside/laguna-xs-2.1", "openai/gpt-oss-20b"],
                   "code": ["moonshotai/kimi-k3", "nvidia/nemotron-3-super-120b-a12b", "poolside/laguna-xs-2.1"],
                   "deep": ["nvidia/nemotron-3-ultra-550b-a55b", "moonshotai/kimi-k3", "z-ai/glm-5.3"]},
        "description": "бесплатный API, 80+ моделей вплоть до 671B",
    },
    "gemini": {
        "title": "Google Gemini", "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "free": True,
        "models": {"fast": ["gemini-2.5-flash", "gemini-flash-latest"], "code": ["gemini-2.5-pro", "gemini-2.5-flash"],
                   "deep": ["gemini-2.5-pro", "gemini-pro-latest"]},
        "description": "модели Gemini (их же использует Antigravity), бесплатный тариф",
    },
    "openrouter": {
        "title": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
        "env": ["OPENROUTER_API_KEY"], "free": True,
        "models": {"fast": ["openrouter/auto"], "code": ["openrouter/auto"], "deep": ["openrouter/auto"]},
        "description": "сотни моделей через один ключ, есть бесплатные",
    },
    "deepseek": {
        "title": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1",
        "env": ["DEEPSEEK_API_KEY"], "free": False,
        "models": {"fast": ["deepseek-chat"], "code": ["deepseek-chat"], "deep": ["deepseek-reasoner", "deepseek-chat"]},
        "description": "сильные и очень дешёвые модели для кода",
    },
    "kimi": {
        "title": "Kimi (Moonshot)", "kind": "openai", "base_url": "https://api.moonshot.ai/v1",
        "env": ["MOONSHOT_API_KEY", "KIMI_API_KEY"], "free": False,
        "models": {"fast": ["kimi-k3", "kimi-k2-turbo-preview"], "code": ["kimi-k3", "kimi-k2-turbo-preview"],
                   "deep": ["kimi-k3-thinking", "kimi-k3", "kimi-k2-thinking"]},
        "description": "Kimi K2: агентные задачи и длинный контекст",
    },
    "openai": {
        "title": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1",
        "env": ["OPENAI_API_KEY"], "free": False,
        "models": {"fast": ["gpt-5-mini", "gpt-4.1-mini"], "code": ["gpt-5", "gpt-4.1"], "deep": ["gpt-5", "gpt-4.1"]},
        "description": "модели OpenAI (платно)",
    },
    "claude": {
        "title": "Anthropic Claude", "kind": "claude", "base_url": "https://api.anthropic.com",
        "env": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"], "free": False,
        "models": {"fast": ["claude-haiku-5-5"], "code": ["claude-sonnet-5-5"], "deep": ["claude-opus-5-5", "claude-sonnet-5-5"]},
        "description": "самые сильные модели для кода (платно, тратит квоту — идёт последним)",
    },
}


def _keys_from_mindkit() -> None:
    """Ключи из связки ключей Mind (MindKit, экосистема MindTagSystem) становятся переменными окружения.

    Ключ, введённый один раз в связку (mindkit keychain set GROQ_API_KEY), видят Mind, шлюз AI Duo
    и остальные программы. Уже заданные переменные окружения важнее. Без MindKit ничего не делает.
    """
    try:
        from mindkit import keychain
    except ImportError:
        return
    try:
        keychain.load_env(sorted({env for p in PROVIDERS.values() for env in p["env"]}))
    except Exception:  # noqa: BLE001 — связка недоступна (нет KWallet и т. п.): работаем без неё
        pass


_keys_from_mindkit()

# Очередь по умолчанию: бесплатные и быстрые первыми, платные — только если больше некому ответить
DEFAULT_ROUTE: dict[str, list[str]] = {
    "fast": ["local", "ollama", "groq", "gemini", "nvidia", "openrouter", "deepseek", "kimi", "openai", "claude"],
    "code": ["groq", "nvidia", "gemini", "deepseek", "openrouter", "kimi", "local", "ollama", "openai", "claude"],
    "deep": ["nvidia", "gemini", "kimi", "deepseek", "groq", "openrouter", "claude", "openai", "local", "ollama"],
}

DEFAULTS = {
    "provider":    "auto",
    "base_url":    LOCAL_URL,
    "model":       "aisktag-mind",
    "api_key":     "",
    "temperature": 0.3,
}

# Роли ассистента: (название, системный промпт)
PERSONAS = {
    "general": (
        "Универсальный",
        "Ты Mind — встроенный ассистент AIsktagOS для программистов. "
        "Отвечай коротко, по делу и по-русски (если пользователь пишет на другом языке — на нём). "
        "Код давай в блоках с указанием языка. Если не уверен — так и скажи, не выдумывай.",
    ),
    "code": (
        "Программист",
        "Ты опытный инженер-программист. Пиши рабочий, идиоматичный и безопасный код, объясняй решение в 2–3 фразах. "
        "Отвечай на языке пользователя, код — в блоках.",
    ),
    "review": (
        "Ревьюер кода",
        "Ты строгий, но доброжелательный ревьюер кода. Найди ошибки, уязвимости, гонки, утечки и неочевидные проблемы, "
        "затем предложи улучшения. Формат: список замечаний по убыванию важности, у каждого — почему и как исправить.",
    ),
    "explain": (
        "Объясняет код",
        "Ты объясняешь код новичку: что делает, как устроено, где подводные камни. Просто, с примерами.",
    ),
    "admin": (
        "Системный администратор",
        "Ты системный администратор Linux (Ubuntu 24.04, KDE Plasma) и Windows. Давай проверенные команды, "
        "предупреждай об опасных действиях и объясняй, что команда сделает.",
    ),
    "translate": (
        "Переводчик",
        "Ты технический переводчик. Переводи между русским и английским, сохраняя код, термины и форматирование.",
    ),
    "design": (
        "UI/UX дизайнер",
        "Ты опытный UI/UX дизайнер и фронтенд-разработчик. Помогаешь с дизайном интерфейсов, цветовыми схемами, "
        "типографикой, компоновкой. Предлагаешь конкретные значения (hex, pt, px). Говоришь по-русски.",
    ),
}

JUDGE_PROMPT = (
    "Ты главный инженер. Несколько моделей независимо ответили на вопрос пользователя. "
    "Сведи их в один лучший ответ: возьми верное и сильное из каждого, исправь ошибки, убери повторы. "
    "Если модели расходятся — выбери правильный вариант и коротко скажи почему. Не упоминай, что ответов было несколько, "
    "если это не важно для пользователя. Отвечай на языке пользователя."
)


class AIError(Exception):
    """Понятная пользователю ошибка (текст на русском)."""

    def __init__(self, msg: str, *, retry: bool = False, cooldown: int = 0, model_only: bool = False):
        super().__init__(msg)
        self.retry = retry            # имеет смысл попробовать другую модель или провайдера
        self.cooldown = cooldown      # сколько секунд не трогать этого провайдера (или модель)
        self.model_only = model_only  # беда с конкретной моделью, а не со всем провайдером


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

def _read_user_conf() -> dict:
    try:
        data = json.loads(USER_CONF.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config() -> dict:
    """Читает конфиг из файла, затем перекрывает переменными окружения."""
    cfg = dict(DEFAULTS)
    cfg["keys"] = {}
    cfg.update(_read_user_conf())
    if not isinstance(cfg.get("keys"), dict):
        cfg["keys"] = {}

    for env, key in {"AI_PROVIDER": "provider", "AI_BASE_URL": "base_url",
                     "AI_MODEL": "model", "AI_API_KEY": "api_key"}.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]

    provider = cfg.get("provider") or "auto"
    cfg["provider"] = provider
    # Старый формат: один ключ для выбранного провайдера
    if cfg.get("api_key") and provider in PROVIDERS and provider not in cfg["keys"]:
        cfg["keys"][provider] = cfg["api_key"]
    if provider in PROVIDERS and provider != "local":
        if cfg["base_url"] == LOCAL_URL:
            cfg["base_url"] = PROVIDERS[provider]["base_url"]
        if not cfg.get("api_key"):
            cfg["api_key"] = provider_key(provider, cfg)
        if cfg.get("model") == DEFAULTS["model"]:
            cfg["model"] = PROVIDERS[provider]["models"]["code"][0]
    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")
    return cfg


def save_config(cfg: dict) -> None:
    """Сохраняет конфиг (без пустых полей). Файл может содержать ключи — доступ только владельцу."""
    USER_CONF.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: cfg[k] for k in ("provider", "base_url", "model", "api_key", "temperature", "route", "cache")
            if k in cfg and cfg[k] not in ("", None)}
    keys = {k: v for k, v in (cfg.get("keys") or {}).items() if v}
    if keys:
        keep["keys"] = keys
    USER_CONF.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        USER_CONF.chmod(0o600)
    except OSError:
        pass


def provider_key(name: str, cfg: dict | None = None) -> str:
    """Ключ провайдера: переменная окружения важнее файла настроек."""
    for env in PROVIDERS.get(name, {}).get("env", []):
        if os.environ.get(env):
            return os.environ[env]
    cfg = cfg if cfg is not None else load_config()
    return (cfg.get("keys") or {}).get(name, "")


_ollama_seen: tuple[float, list[dict]] = (0.0, [])


def ollama_models() -> list[dict]:
    """Модели, установленные в Ollama (GET /api/tags, кэш 30 с). Пусто — Ollama не запущен или моделей нет."""
    global _ollama_seen
    if time.time() - _ollama_seen[0] < 30:
        return _ollama_seen[1]
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=0.6) as r:
            found = [m for m in json.load(r).get("models", []) if isinstance(m, dict) and m.get("name")
                     and "embed" not in m["name"]]
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        found = []
    _ollama_seen = (time.time(), found)
    return found


def _ollama_order(tier: str) -> list[str]:
    """Модели Ollama под уровень задачи: для кода — кодеры, для быстрых ответов — самые маленькие."""
    ms = ollama_models()
    size = {m["name"]: m.get("size", 0) for m in ms}
    names = sorted(size, key=size.get)
    if tier == "code":
        names.sort(key=lambda n: ("coder" not in n.lower(), -size[n]))
    elif tier == "deep":
        names.reverse()
    return names


def model_candidates(name: str, tier: str) -> list[str]:
    """Модели провайдера для уровня задачи по убыванию предпочтения (каталог route_models важнее таблицы)."""
    over = load_catalog().get("route_models", {}).get(name, {}).get(tier)
    items = over if isinstance(over, list) else ([over] if over else [])
    found = _ollama_order(tier) if name == "ollama" else []
    return list(dict.fromkeys(items + found + PROVIDERS[name]["models"][tier]))


def _model_list(name: str, cfg: dict | None = None) -> set[str] | None:
    """Модели, которые провайдер отдаёт сейчас (GET /models, кэш на сутки). None — узнать не удалось."""
    if PROVIDERS[name]["kind"] != "openai" or not PROVIDERS[name]["env"]:
        return None
    path = CACHE_DIR / f"models-{name}.json"
    try:
        if time.time() - path.stat().st_mtime < MODELS_TTL:
            return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    key = provider_key(name, cfg)
    if not key:
        return None
    req = urllib.request.Request(PROVIDERS[name]["base_url"] + "/models",
                                 headers=_headers_openai({"api_key": key}))
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            ids = sorted(m["id"] for m in json.load(r).get("data", []) if isinstance(m, dict) and m.get("id"))
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ids), encoding="utf-8")
    except OSError:
        pass
    return set(ids) or None


def provider_model(name: str, tier: str, cfg: dict | None = None, *, discover: bool = False) -> str:
    """Первая модель из списка, которая не помечена как недоступная (и есть у провайдера, если discover)."""
    cands = model_candidates(name, tier)
    paused = _cooldowns()
    listed = _model_list(name, cfg) if discover else None
    for m in cands:
        if f"{name}|{m}" not in paused and (listed is None or m in listed):
            return m
    for m in cands:
        if f"{name}|{m}" not in paused:
            return m
    return cands[0]


def available_providers(cfg: dict | None = None, *, check_local: bool = True) -> list[str]:
    """Провайдеры, к которым можно обратиться прямо сейчас: есть ключ (или локальная модель запущена)."""
    cfg = cfg if cfg is not None else load_config()
    out = []
    for name in PROVIDERS:
        if name == "local":
            if check_local and backend_state() != "missing":
                out.append(name)
        elif name == "ollama":
            if check_local and ollama_models():
                out.append(name)
        elif provider_key(name, cfg):
            out.append(name)
    return out


def is_local(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    if cfg.get("provider") == "auto":
        return not available_providers(cfg, check_local=False)
    return cfg.get("provider", "local") == "local" or cfg["base_url"].startswith(
        ("http://127.0.0.1", "http://localhost")
    )


# ---------------------------------------------------------------------------
# Классификация задачи: от неё зависит, какую (и насколько дорогую) модель звать
# ---------------------------------------------------------------------------
_CODE_HINTS = ("```", "def ", "class ", "function", "import ", "#include", "код", "code", "скрипт", "script",
               "regex", "регуляр", "sql", "тест", "test", "python", "javascript", "typescript", "react", "bash",
               "powershell", "git ", "docker", "api", "баг", "bug", "ошибк", "error", "traceback", "exception",
               "компил", "функци", "метод", "алгоритм")
_DEEP_HINTS = ("архитектур", "спроектир", "проектир", "design a", "architecture", "докажи", "проанализируй",
               "анализ", "сравни", "compare", "оптимизир", "optimiz", "отлад", "debug", "рефактор", "refactor",
               "план ", "стратег", "уязвим", "security", "почему не работает", "разбер", "с нуля", "полностью",
               "step by step", "пошагово", "миграц", "масштаб")
_PERSONA_TIER = {"code": "code", "review": "deep", "design": "code"}


def classify(messages: list[dict], persona: str = "general") -> str:
    """fast | code | deep — грубая, но дешёвая оценка сложности последнего запроса."""
    user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
    low = text.lower()
    size = len(text)
    tier = "fast"
    if any(h in low for h in _CODE_HINTS) or size > 1500:
        tier = "code"
    if (any(h in low for h in _DEEP_HINTS) and size > 20) or size > 20000:
        tier = "deep"
    floor = _PERSONA_TIER.get(persona)
    if floor and TIERS.index(floor) > TIERS.index(tier):
        tier = floor
    return tier


def _trim(messages: list[dict], tier: str) -> list[dict]:
    """Для быстрых вопросов длинная история не нужна: меньше токенов — меньше расход квоты."""
    keep = {"fast": 6, "code": 14, "deep": 24}[tier]
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return system + rest[-keep:]


# ---------------------------------------------------------------------------
# Состояние маршрутизатора: временно отключённые провайдеры и кэш ответов
# ---------------------------------------------------------------------------
_STATE_LOCK = threading.Lock()


def _state_file() -> Path:
    return CACHE_DIR / "cooldown.json"


def _cooldowns() -> dict[str, float]:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        now = time.time()
        return {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > now}
    except (OSError, ValueError, AttributeError):
        return {}


def _cool(name: str, seconds: int) -> None:
    if seconds <= 0:
        return
    with _STATE_LOCK:
        data = _cooldowns()
        data[name] = time.time() + seconds
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _state_file().write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass


def reset_cooldowns() -> None:
    try:
        _state_file().unlink()
    except OSError:
        pass


def _cache_key(messages: list[dict], tier: str, mode: str) -> str:
    raw = json.dumps([mode, tier, messages], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    try:
        path = CACHE_DIR / "answers" / f"{key}.json"
        if time.time() - path.stat().st_mtime > CACHE_TTL:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _cache_put(key: str, text: str, route: dict) -> None:
    if not text.strip():
        return
    try:
        d = CACHE_DIR / "answers"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(json.dumps({"text": text, "provider": route.get("provider"),
                                                   "model": route.get("model")}, ensure_ascii=False),
                                       encoding="utf-8")
    except OSError:
        pass


def clear_cache() -> int:
    n = 0
    for p in (CACHE_DIR / "answers").glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


# ---------------------------------------------------------------------------
# HTTP: заголовки и потоковое чтение
# ---------------------------------------------------------------------------

def _headers_openai(cfg: dict) -> dict:
    h = {"Content-Type": "application/json", "User-Agent": "aisktagos-mind/2.1"}
    if cfg.get("api_key"):
        h["Authorization"] = "Bearer " + cfg["api_key"]
    return h


def _headers_claude(cfg: dict) -> dict:
    return {"Content-Type": "application/json", "User-Agent": "aisktagos-mind/2.1",
            "x-api-key": cfg.get("api_key", ""), "anthropic-version": "2023-06-01"}


def _http_error(e: urllib.error.HTTPError, who: str) -> AIError:
    try:
        detail = e.read().decode(errors="replace")[:300]
    except OSError:
        detail = ""
    if e.code in (401, 403):
        return AIError(f"{who}: ключ доступа отклонён. Проверьте ключ в настройках Mind.", retry=True, cooldown=6 * 3600)
    if e.code == 429:
        return AIError(f"{who}: исчерпан лимит запросов.", retry=True, cooldown=15 * 60)
    if e.code in (404, 410):
        return AIError(f"{who}: модель недоступна ({e.code}).", retry=True, cooldown=24 * 3600, model_only=True)
    if e.code >= 500 or e.code in (408, 409, 413, 422, 400):
        return AIError(f"{who}: ошибка сервера {e.code}: {detail}", retry=True, cooldown=120 if e.code >= 500 else 0)
    return AIError(f"{who}: ошибка {e.code}: {detail}")


def _open(req: urllib.request.Request, cfg: dict, who: str):
    timeout = READ_TIMEOUT if _is_local_url(cfg["base_url"]) else CLOUD_TIMEOUT.get(cfg.get("_tier", "code"), 45)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise _http_error(e, who) from e
    except (urllib.error.URLError, ConnectionError, socket.timeout, OSError) as e:
        if _is_local_url(cfg["base_url"]):
            raise AIError(
                "Локальный ИИ недоступен. Проверьте:\n"
                "  systemctl status aisktag-llm.socket\n"
                "  journalctl -u aisktag-llm-backend\n"
                "(возможно, модель не установлена: запустите ai model)", retry=True, cooldown=60) from e
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason):
            raise AIError(f"{who}: модель слишком долго не отвечает", retry=True, cooldown=600,
                          model_only=True) from e
        raise AIError(f"{who}: не удалось подключиться ({reason})", retry=True, cooldown=120) from e


def _is_local_url(url: str) -> bool:
    return url.startswith(("http://127.0.0.1", "http://localhost"))


def _stream_openai(messages: list[dict], cfg: dict, max_tokens: int | None,
                   temperature: float, should_stop: Callable[[], bool] | None) -> Iterator[str]:
    body = {"model": cfg["model"], "messages": messages, "stream": True, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(cfg["base_url"] + "/chat/completions", data=json.dumps(body).encode(),
                                 headers=_headers_openai(cfg), method="POST")
    who = cfg.get("_title") or cfg["base_url"]
    with _open(req, cfg, who) as resp:
        for raw in resp:
            if should_stop and should_stop():
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("error"):
                err = event["error"]
                raise AIError(f"{who}: {err.get('message', err) if isinstance(err, dict) else err}",
                              retry=True, cooldown=300, model_only=True)
            try:
                delta = event["choices"][0].get("delta", {})
            except (KeyError, IndexError, TypeError, AttributeError):
                continue
            if delta.get("content"):
                yield delta["content"]


def _stream_claude(messages: list[dict], cfg: dict, max_tokens: int | None,
                   temperature: float, should_stop: Callable[[], bool] | None) -> Iterator[str]:
    """Anthropic SSE: события content_block_delta с delta.type == text_delta."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    body: dict = {"model": cfg["model"], "messages": [m for m in messages if m["role"] != "system"],
                  "max_tokens": max_tokens or 4096, "stream": True, "temperature": temperature}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/v1/messages", data=json.dumps(body).encode(),
                                 headers=_headers_claude(cfg), method="POST")
    with _open(req, cfg, "Claude") as resp:
        for raw in resp:
            if should_stop and should_stop():
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if event.get("type") == "error":
                raise AIError(f"Claude: {event.get('error', {}).get('message', 'ошибка')}", retry=True, cooldown=300)
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]


def _stream_direct(messages, cfg, max_tokens, temperature, should_stop) -> Iterator[str]:
    kind = PROVIDERS.get(cfg.get("provider", ""), {}).get("kind", "openai")
    if kind == "claude":
        yield from _stream_claude(messages, cfg, max_tokens, temperature, should_stop)
    else:
        yield from _stream_openai(messages, cfg, max_tokens, temperature, should_stop)


def _provider_cfg(name: str, tier: str, base: dict, model: str | None = None) -> dict:
    p = PROVIDERS[name]
    return {**base, "provider": name, "base_url": p["base_url"],
            "model": model or provider_model(name, tier, base, discover=True),
            "api_key": provider_key(name, base), "_title": p["title"], "_tier": tier}


def _penalize(name: str, model: str, e: AIError) -> None:
    _cool(f"{name}|{model}" if e.model_only else name, e.cooldown)


# ---------------------------------------------------------------------------
# Маршрутизатор
# ---------------------------------------------------------------------------

def plan_route(tier: str, cfg: dict | None = None, *, only: list[str] | None = None) -> list[str]:
    """Очередь провайдеров для уровня задачи: доступные, не на паузе, в порядке «бесплатные → платные»."""
    cfg = cfg if cfg is not None else load_config()
    order = (cfg.get("route") or {}).get(tier) or DEFAULT_ROUTE[tier]
    have = set(available_providers(cfg))
    paused = _cooldowns()
    queue = [p for p in order if p in have and p in PROVIDERS and (only is None or p in only)]
    ready = [p for p in queue if p not in paused]
    # Если все на паузе — всё равно пробуем (лимит мог уже сброситься), начиная с того, кто освободится раньше
    return ready or sorted(queue, key=lambda p: paused.get(p, 0))


def stream_chat(
    messages: list[dict],
    cfg: dict | None = None,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    should_stop: Callable[[], bool] | None = None,
    tier: str | None = None,
    persona: str = "general",
    mode: str = "auto",
    on_route: Callable[[dict], None] | None = None,
    use_cache: bool | None = None,
) -> Iterator[str]:
    """Потоково отдаёт куски ответа модели. Бросает AIError с понятным текстом.

    mode: 'auto' — одна лучшая по цене модель с резервом; 'council' — консилиум нескольких моделей.
    on_route(info) вызывается, когда известно, кто отвечает: {provider, model, tier, cached, tried}.
    """
    cfg = cfg or load_config()
    temp = cfg.get("temperature", 0.3) if temperature is None else temperature
    provider = cfg.get("provider", "auto")

    if provider != "auto" and mode != "council":
        if on_route:
            on_route({"provider": provider, "model": cfg.get("model"), "tier": tier or "", "cached": False,
                      "tried": []})
        yield from _stream_direct(messages, cfg, max_tokens, temp, should_stop)
        return

    tier = tier if tier in TIERS else classify(messages, persona)
    caching = cfg.get("cache", True) if use_cache is None else use_cache
    key = _cache_key(messages, tier, mode)
    if caching:
        hit = _cache_get(key)
        if hit:
            if on_route:
                on_route({"provider": hit.get("provider"), "model": hit.get("model"), "tier": tier,
                          "cached": True, "tried": []})
            yield hit["text"]
            return

    if mode == "council":
        route: dict = {}
        out: list[str] = []
        for piece in _council(messages, cfg, temp, should_stop, on_route, route):
            out.append(piece)
            yield piece
        if caching and not (should_stop and should_stop()):
            _cache_put(key, "".join(out), route)
        return

    if not plan_route(tier, cfg):
        raise AIError("Нет ни одного доступного ИИ. Запустите локальную модель или добавьте ключ API "
                      "(NVIDIA, Groq, Gemini…) в настройках Mind.")
    msgs = _trim(messages, tier)
    errors: list[str] = []
    # Если все модели уровня заняты — лучше ответ модели попроще, чем никакого
    steps = [(t, n) for t in TIERS[:TIERS.index(tier) + 1][::-1] for n in plan_route(t, cfg)]
    dead: set[str] = set()                    # службы, отказавшие целиком в этом запросе
    tried: dict[str, set[str]] = {}           # уже опробованные модели каждой службы
    for tier, name in steps:
        if name in dead:
            continue
        tried_models = tried.setdefault(name, set())
        for _attempt in range(3):
            pcfg = _provider_cfg(name, tier, cfg)
            if pcfg["model"] in tried_models:
                break
            tried_models.add(pcfg["model"])
            route = {"provider": name, "model": pcfg["model"], "tier": tier, "cached": False, "tried": list(errors)}
            started = False
            out = []
            try:
                for piece in _stream_direct(msgs, pcfg, max_tokens, temp, should_stop):
                    if not started:
                        started = True
                        if on_route:
                            on_route(route)
                    out.append(piece)
                    yield piece
            except AIError as e:
                if started or not e.retry:
                    raise
                _penalize(name, pcfg["model"], e)
                errors.append(f"{PROVIDERS[name]['title']} ({pcfg['model']}): {e}")
                if e.model_only:
                    continue      # та же служба, следующая модель из списка
                dead.add(name)    # беда со всей службой — к следующему провайдеру
                break
            if not started:
                if should_stop and should_stop():
                    return
                # Пустой ответ (бывает у «думающих» моделей) — эту модель ненадолго откладываем
                _cool(f"{name}|{pcfg['model']}", 600)
                errors.append(f"{PROVIDERS[name]['title']} ({pcfg['model']}): пустой ответ")
                continue
            if caching and not (should_stop and should_stop()):
                _cache_put(key, "".join(out), route)
            return
    raise AIError("Ни одна модель не ответила:\n  " + "\n  ".join(errors))


def council_voters(cfg: dict, size: int = 3) -> list[tuple[str, str]]:
    """Участники консилиума: сначала разные службы, затем другие модели тех же служб (хватит и одного ключа)."""
    providers = [p for p in plan_route("code", cfg) if p != "local"] or plan_route("code", cfg)[:1]
    paused = _cooldowns()
    pool = {p: [m for m in model_candidates(p, "code") + model_candidates(p, "deep") if f"{p}|{m}" not in paused]
            for p in providers}
    voters: list[tuple[str, str]] = []
    while len(voters) < size and any(pool.values()):
        for p in providers:
            if pool[p] and len(voters) < size:
                m = pool[p].pop(0)
                if (p, m) not in voters:
                    voters.append((p, m))
    return voters


def _council(messages, cfg, temp, should_stop, on_route, route: dict) -> Iterator[str]:
    """Консилиум: до трёх разных моделей отвечают параллельно, судья сводит лучший ответ (потоково)."""
    voters = council_voters(cfg)
    if not voters:
        raise AIError("Для консилиума нужен хотя бы один ключ API или локальная модель.")
    msgs = _trim(messages, "deep")

    def ask(voter: tuple[str, str]) -> tuple[str, str, str]:
        name, model = voter
        pcfg = _provider_cfg(name, "code", cfg, model)
        try:
            return name, model, "".join(_stream_direct(msgs, pcfg, 2048, temp, should_stop))
        except AIError as e:
            _penalize(name, model, e)
            return name, model, ""

    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        answers = [(n, m, t) for n, m, t in pool.map(ask, voters) if t.strip()]
    if should_stop and should_stop():
        return
    if not answers:
        raise AIError("Консилиум: ни одна модель не ответила (лимиты или нет сети).")
    if len(answers) == 1:
        name, model, text = answers[0]
        route.update(provider=name, model=model, tier="council", cached=False, tried=[])
        if on_route:
            on_route(dict(route))
        yield text
        return

    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    bundle = "\n\n".join(f"### Ответ {i + 1} ({m})\n{t}" for i, (n, m, t) in enumerate(answers))
    judge_msgs = [{"role": "system", "content": JUDGE_PROMPT},
                  {"role": "user", "content": f"Вопрос пользователя:\n{user}\n\n{bundle}"}]
    judges = [p for p in plan_route("deep", cfg) if p != "local"] or [answers[0][0]]
    voters_names = ", ".join(m.split("/")[-1] for _, m, _ in answers)
    for name in judges:
        jcfg = _provider_cfg(name, "deep", cfg)
        started = False
        try:
            for piece in _stream_direct(judge_msgs, jcfg, None, 0.2, should_stop):
                if not started:
                    started = True
                    route.update(provider=name, model=jcfg["model"], tier="council", cached=False,
                                 tried=[], voters=voters_names)
                    if on_route:
                        on_route(dict(route))
                yield piece
        except AIError as e:
            if started or not e.retry:
                raise
            _penalize(name, jcfg["model"], e)
            continue
        if started:
            return
    # Судья не ответил — отдаём лучший из ответов (самый подробный)
    name, model, text = max(answers, key=lambda a: len(a[2]))
    route.update(provider=name, model=model, tier="council", cached=False, tried=[])
    if on_route:
        on_route(dict(route))
    yield text


def complete(messages: list[dict], cfg: dict | None = None, **kw) -> str:
    """Не-стриминговый вариант: возвращает весь ответ строкой."""
    return "".join(stream_chat(messages, cfg, **kw))


# ---------------------------------------------------------------------------
# Состояние локального бэкенда
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 0.6) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def backend_state() -> str:
    """ready | loading | idle | missing — актуально только для локального провайдера."""
    code = _get(BACKEND_HEALTH)
    if code == 200:
        return "ready"
    if code == 503:
        return "loading"
    try:
        with socket.create_connection(("127.0.0.1", 6573), timeout=0.4):
            return "idle"
    except OSError:
        return "missing"


# ---------------------------------------------------------------------------
# Каталог моделей и железо
# ---------------------------------------------------------------------------

def ram_mb() -> int:
    """Объём оперативной памяти в МБ (Linux: /proc/meminfo, Windows: GlobalMemoryStatusEx)."""
    if IS_WINDOWS:
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys // (1024 * 1024))
        except (OSError, AttributeError):
            return 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def load_catalog() -> dict:
    """Читает models.json: models — локальные GGUF-модели, cloud — облачные (справочно)."""
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        data.setdefault("models", [])
        return data
    except (OSError, ValueError):
        return {"default": "lite", "models": []}


def local_models() -> list[dict]:
    """Только локальные модели (у облачных нет файла и требований к памяти)."""
    return [m for m in load_catalog()["models"] if "ram_mb" in m and "file" in m]


def cloud_models() -> list[dict]:
    return load_catalog().get("cloud", [])


def recommend_model(total_mb: int | None = None) -> str:
    """Самая сильная локальная модель каталога, которая комфортно помещается в RAM."""
    total_mb = ram_mb() if total_mb is None else total_mb
    best = None
    for m in local_models():
        if total_mb >= m["ram_mb"] and (best is None or m["size_mb"] > best["size_mb"]):
            best = m
    return best["id"] if best else load_catalog().get("default", "lite")


def list_models() -> dict:
    """Каталог локальных моделей с отметками «установлена» и активной моделью."""
    try:
        out = subprocess.run(
            [MODEL_HELPER, "list"], capture_output=True, text=True, timeout=10, check=True
        ).stdout
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"active": "lite", "models": local_models()}


def list_provider_models(provider: str) -> list[str]:
    """Все модели провайдера из таблицы маршрутизатора без повторов."""
    if provider not in PROVIDERS:
        return []
    return list(dict.fromkeys(m for t in TIERS for m in model_candidates(provider, t)))
