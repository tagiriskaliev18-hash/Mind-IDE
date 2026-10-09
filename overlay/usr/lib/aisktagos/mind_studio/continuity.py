"""Handoff для Mind Studio через MindKit (экосистема MindTagSystem).

Разговор отправляется на другие устройства владельца по MindLink и там сам
появляется в списке разговоров Mind Studio с пометкой «⇄». Если MindKit не
установлен (pip install git+https://github.com/tagiriskaliev18-hash/MindTagSystem),
функция просто выключена.
"""
from __future__ import annotations

import json

from . import store

try:
    from mindkit import link
except ImportError:  # MindKit не обязателен
    link = None

APP = "mind-studio"
MAX_MESSAGES = 200
_SEEN = store.DATA / "handoff-imported.json"


def available() -> bool:
    return link is not None and link.account_key() is not None


def send_chat(chat: dict, to: str | None = None) -> dict[str, str]:
    if link is None:
        raise RuntimeError("MindKit не установлен")
    # Сообщения переносятся целиком: роль, текст, агент, маршрут модели
    msgs = [m for m in chat.get("messages", [])[-MAX_MESSAGES:] if isinstance(m, dict)]
    act = link.make_activity("chat", chat.get("title") or "Разговор Mind Studio", app=APP,
                             chat={"title": chat.get("title"), "project": chat.get("project", ""),
                                   "agent": chat.get("agent", "mind"), "messages": msgs})
    return link.handoff(act, to=to)


def _seen() -> set[str]:
    try:
        return set(json.loads(_SEEN.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def import_incoming() -> int:
    """Пришедшие разговоры становятся обычными разговорами Mind Studio."""
    if link is None:
        return 0
    seen = _seen()
    added = 0
    for act in reversed(link.inbox()):
        key = f"{act.get('from')}:{act.get('time')}"
        if act.get("kind") != "chat" or act.get("app") != APP or key in seen or not act.get("chat"):
            continue
        src = act["chat"]
        chat = store.new_chat(project=src.get("project", ""), agent=src.get("agent", "mind"))
        chat["title"] = f"⇄ {src.get('title') or 'Разговор'}"
        chat["messages"] = [m for m in src.get("messages", []) if isinstance(m, dict)]
        chat["handoff_from"] = act.get("from", "")
        store.save_chat(chat)
        seen.add(key)
        added += 1
    if added:
        _SEEN.parent.mkdir(parents=True, exist_ok=True)
        _SEEN.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    return added
