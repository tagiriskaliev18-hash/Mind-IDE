"""Хранилище Mind Studio: разговоры, проекты и скиллы. Всё — JSON-файлы в папке настроек пользователя."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import aisktag_ai as ai

DATA = ai.USER_CONF.parent / "studio"
CHATS = DATA / "chats"
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Разговоры
# ---------------------------------------------------------------------------

def _chat_path(cid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", cid or ""):
        raise KeyError(cid)
    return CHATS / f"{cid}.json"


def new_chat(project: str = "", agent: str = "mind") -> dict:
    chat = {"id": uuid.uuid4().hex, "title": "Новый разговор", "project": project, "agent": agent,
            "created": _now(), "updated": _now(), "messages": [], "claude_session": ""}
    save_chat(chat)
    return chat


def save_chat(chat: dict) -> None:
    with _LOCK:
        CHATS.mkdir(parents=True, exist_ok=True)
        chat["updated"] = _now()
        tmp = _chat_path(chat["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _chat_path(chat["id"]))


def get_chat(cid: str) -> dict:
    return json.loads(_chat_path(cid).read_text(encoding="utf-8"))


def delete_chat(cid: str) -> None:
    try:
        _chat_path(cid).unlink()
    except OSError:
        pass


def list_chats() -> list[dict]:
    out = []
    for p in CHATS.glob("*.json"):
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: c.get(k) for k in ("id", "title", "project", "agent", "updated")})
    return sorted(out, key=lambda c: c["updated"] or 0, reverse=True)


def title_from(text: str) -> str:
    line = " ".join(text.strip().split())
    return (line[:60] + "…") if len(line) > 60 else (line or "Новый разговор")


# ---------------------------------------------------------------------------
# Проекты (папки, в которых работает Claude Code)
# ---------------------------------------------------------------------------

def _settings_path() -> Path:
    return DATA / "settings.json"


def settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    cur = settings()
    cur.update(data)
    _settings_path().write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def projects() -> list[dict]:
    items = settings().get("projects")
    if items is None:
        home = Path.home()
        guess = [home / "Desktop" / "AIskTagOS", home / "projects", home / "Desktop"]
        items = [{"name": p.name, "path": str(p)} for p in guess if p.is_dir()][:3]
    return [p for p in items if Path(p.get("path", "")).is_dir()]


# ---------------------------------------------------------------------------
# Скиллы: SKILL.md из OpenCode, Claude Code, Antigravity и самой системы
# ---------------------------------------------------------------------------

def skill_dirs() -> list[tuple[str, Path]]:
    home = Path.home()
    dirs = [("Mind", Path(__file__).resolve().parent / "skills"),
            ("Mind", Path("/usr/share/aisktagos/skills")),
            ("OpenCode", home / ".config" / "opencode" / "skills"),
            ("Claude", home / ".claude" / "skills"),
            ("Antigravity", home / ".gemini" / "antigravity" / "skills"),
            ("Antigravity", home / ".gemini" / "antigravity" / "builtin" / "skills")]
    extra = os.environ.get("AISKTAG_SKILLS", "")
    dirs += [("Свои", Path(p)) for p in extra.split(os.pathsep) if p]
    return [(src, d) for src, d in dirs if d.is_dir()]


def _front_matter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            text = text[end + 4:]
    return meta, text.strip()


def list_skills() -> list[dict]:
    seen, out = set(), []
    for src, d in skill_dirs():
        for f in sorted(d.glob("*/SKILL.md")):
            try:
                meta, _body = _front_matter(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            name = meta.get("name") or f.parent.name
            if name in seen:
                continue
            seen.add(name)
            out.append({"id": name, "name": name, "source": src,
                        "description": meta.get("description", "")[:300], "path": str(f)})
    return out


def skill_text(name: str, limit: int = 12000) -> str:
    for s in list_skills():
        if s["id"] == name:
            _meta, body = _front_matter(Path(s["path"]).read_text(encoding="utf-8"))
            return body[:limit]
    raise KeyError(name)
