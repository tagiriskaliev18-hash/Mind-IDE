"""HTTP-сервер Mind Studio: статический интерфейс web/ и JSON/SSE API. Только 127.0.0.1.

Защита: каждый запуск получает случайный токен; API принимает запросы только с заголовком X-Studio-Token
и Host 127.0.0.1/localhost — чужая веб-страница в браузере не сможет управлять агентами.
"""
from __future__ import annotations

import json
import os
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import aisktag_ai as ai

from . import agents, antigravity, continuity, store

WEB = Path(__file__).resolve().parent / "web"
TOKEN = secrets.token_urlsafe(24)
_STOPS: dict[str, threading.Event] = {}

PERSONAS = [{"id": k, "name": v[0]} for k, v in ai.PERSONAS.items()]
MODES = [
    {"id": "auto", "name": "Авто", "hint": "модель под задачу, экономно"},
    {"id": "fast", "name": "Быстро", "hint": "мгновенный ответ"},
    {"id": "code", "name": "Код", "hint": "сильная модель для кода"},
    {"id": "deep", "name": "Максимум", "hint": "самые сильные модели"},
    {"id": "council", "name": "Консилиум", "hint": "несколько моделей, лучший ответ"},
]


def _state() -> dict:
    cfg = ai.load_config()
    return {
        "agents": [{"id": k, **v} for k, v in agents.AGENTS.items()],
        "status": agents.status(),
        "providers": [{"id": p, "title": ai.PROVIDERS[p]["title"], "models": ai.list_provider_models(p)}
                      for p in ai.available_providers(cfg)],
        "modes": MODES,
        "personas": PERSONAS,
        "skills": store.list_skills(),
        "projects": store.projects(),
        "agy_models": [{"id": k, "name": v} for k, v in antigravity.MODELS.items()],
        "link_command": os.environ.get("AISKTAG_STUDIO_LINK_CMD", "aisktag-studio --link-antigravity"),
        "continuity": continuity.available(),
    }


def _settings_view() -> dict:
    cfg = ai.load_config()
    keys = {}
    for name, p in ai.PROVIDERS.items():
        if not p["env"]:      # локальные службы без ключа
            continue
        keys[name] = {"title": p["title"], "free": p["free"], "description": p["description"],
                      "env": any(os.environ.get(e) for e in p["env"]),
                      "saved": bool((cfg.get("keys") or {}).get(name)), "var": p["env"][0]}
    st = store.settings()
    return {"keys": keys, "auto": cfg.get("provider") in (None, "auto"), "cache": bool(cfg.get("cache", True)),
            "projects": store.projects(), "claude_edit": bool(st.get("claude_edit")),
            "claude_model": st.get("claude_model", "sonnet"), "theme": st.get("theme", "light"),
            "config_path": str(ai.USER_CONF)}


def _save_settings(data: dict) -> None:
    cfg = ai._read_user_conf()
    keys = dict(cfg.get("keys") or {})
    for name, val in (data.get("keys") or {}).items():
        if name in ai.PROVIDERS and isinstance(val, str) and val.strip():
            keys[name] = val.strip()
    for name in data.get("forget_keys") or []:
        keys.pop(name, None)
    cfg["keys"] = keys
    if "auto" in data and data["auto"]:
        cfg.update(provider="auto", base_url=ai.LOCAL_URL, model="aisktag-mind", api_key="")
    if "cache" in data:
        cfg["cache"] = bool(data["cache"])
    ai.save_config(cfg)
    extra = {k: data[k] for k in ("claude_edit", "claude_model", "theme") if k in data}
    if isinstance(data.get("projects"), list):
        extra["projects"] = [{"name": str(p.get("name") or Path(p["path"]).name), "path": str(p["path"])}
                             for p in data["projects"] if isinstance(p, dict) and p.get("path")
                             and Path(p["path"]).is_dir()]
    if extra:
        store.save_settings(extra)


def _system_prompt(chat: dict, req: dict) -> str:
    parts = [ai.PERSONAS.get(req.get("persona") or "general", ai.PERSONAS["general"])[1]]
    if chat.get("project"):
        parts.append(f"Пользователь работает в проекте: {chat['project']}.")
    for sk in req.get("skills") or []:
        try:
            parts.append(f"## Скилл «{sk}»\n{store.skill_text(sk)}")
        except (KeyError, OSError):
            pass
    return "\n\n".join(parts)


def _run(chat: dict, req: dict, stop: threading.Event, emit) -> None:
    text = str(req.get("text", "")).strip()
    agent = req.get("agent") if req.get("agent") in agents.AGENTS else "mind"
    attachments = req.get("attachments") or []
    content = text
    for a in attachments[:5]:
        content += f"\n\n[{a.get('name', 'вложение')}]\n```\n{str(a.get('text', ''))[:200_000]}\n```"
    if chat["title"] == "Новый разговор" and text:
        chat["title"] = store.title_from(text)
    chat["messages"].append({"role": "user", "content": content, "shown": text,
                             "attachments": [a.get("name") for a in attachments]})
    chat["agent"] = agent
    store.save_chat(chat)
    emit("chat", {"id": chat["id"], "title": chat["title"]})

    system = _system_prompt(chat, req)
    history = [{"role": m["role"], "content": m["content"]} for m in chat["messages"][-24:]
               if m["role"] in ("user", "assistant") and m.get("content")]
    settings = store.settings()
    opts = {"tier": None, "mode": "auto", "persona": req.get("persona") or "general",
            "claude_session": chat.get("claude_session", ""), "agy_conversation": chat.get("agy_conversation", ""),
            "claude_model": req.get("claude_model") or settings.get("claude_model", "sonnet"),
            "claude_edit": bool(settings.get("claude_edit")), "cwd": chat.get("project_path", ""),
            "agy_model": req.get("agy_model") or "flash", "title": chat["title"], "system": system,
            "provider": req.get("provider") or ""}
    mode = req.get("mode") or "auto"
    if mode == "council":
        opts["mode"] = "council"
    elif mode in ai.TIERS:
        opts["tier"] = mode

    should_stop = stop.is_set
    if agent == "mind":
        gen = agents.run_mind([{"role": "system", "content": system}] + history, opts, should_stop)
    elif agent == "claude":
        gen = agents.run_claude(content, opts, should_stop)
    elif agent == "antigravity":
        prompt = content if not req.get("skills") else f"{system}\n\n---\n\n{content}"
        gen = agents.run_antigravity(prompt, opts, should_stop)
    else:
        gen = agents.run_all([{"role": "system", "content": system}] + history, content, opts, should_stop)

    answer = {"role": "assistant", "content": "", "agent": agent, "route": {}, "activity": []}
    try:
        for kind, data in gen:
            if kind == "text":
                answer["content"] += str(data)
            elif kind == "route":
                answer["route"] = data
            elif kind == "activity":
                answer["activity"].append(str(data))
            elif kind == "meta":
                chat.update(data)
                continue
            emit(kind, data)
    except ai.AIError as e:
        answer["error"] = str(e)
        emit("error", str(e))
    except Exception as e:  # noqa: BLE001 — любая ошибка агента показывается в чате, сервер живёт дальше
        answer["error"] = f"Неожиданная ошибка: {e}"
        emit("error", answer["error"])
    if stop.is_set():
        answer["stopped"] = True
    chat["messages"].append(answer)
    store.save_chat(chat)
    emit("done", {"id": chat["id"]})


class Handler(BaseHTTPRequestHandler):
    server_version = "MindStudio/1.0"

    def log_message(self, *a):
        pass

    # --- служебное
    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 5_000_000:
            raise ValueError("слишком большой запрос")
        data = json.loads(self.rfile.read(n) or b"{}")
        return data if isinstance(data, dict) else {}

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _authed(self) -> bool:
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Studio-Token", ""), TOKEN)

    # --- статика
    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        f = (WEB / rel).resolve()
        if WEB not in f.parents or not f.is_file():
            self.send_error(404)
            return
        body = f.read_bytes()
        self.send_response(200)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if not self._host_ok():
                return self.send_error(403)
            return self._static(url.path)
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        parts = [unquote(p) for p in url.path.split("/")[2:]]
        try:
            if parts == ["state"]:
                return self._json(_state())
            if parts == ["chats"]:
                continuity.import_incoming()
                return self._json(store.list_chats())
            if len(parts) == 2 and parts[0] == "chats":
                return self._json(store.get_chat(parts[1]))
            if parts == ["settings"]:
                return self._json(_settings_view())
            if parts == ["antigravity", "history"]:
                return self._json(antigravity.history())
            if len(parts) == 3 and parts[:2] == ["antigravity", "conversation"]:
                return self._json(antigravity.conversation(parts[2]))
            if len(parts) == 2 and parts[0] == "skills":
                return self._json({"id": parts[1], "text": store.skill_text(parts[1])})
        except (KeyError, OSError, antigravity.AntigravityError):
            return self._json({"error": "не найдено"}, 404)
        self._json({"error": "нет такого метода"}, 404)

    def do_DELETE(self):
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        parts = urlparse(self.path).path.split("/")[2:]
        if len(parts) == 2 and parts[0] == "chats":
            store.delete_chat(parts[1])
            return self._json({"ok": True})
        self._json({"error": "нет такого метода"}, 404)

    def do_POST(self):
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        parts = urlparse(self.path).path.split("/")[2:]
        try:
            req = self._body()
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        if parts == ["chats"]:
            chat = store.new_chat(project=str(req.get("project", "")), agent=str(req.get("agent", "mind")))
            if req.get("project_path") and Path(req["project_path"]).is_dir():
                chat["project_path"] = req["project_path"]
                store.save_chat(chat)
            return self._json(chat)
        if parts == ["settings"]:
            _save_settings(req)
            return self._json(_settings_view())
        if parts == ["antigravity", "autostart"]:
            cmd = json.loads(os.environ.get("AISKTAG_STUDIO_SELF") or '["aisktag-studio"]')
            try:
                path = antigravity.install_autostart(cmd + ["--link-antigravity", "--mcp"])
            except (antigravity.AntigravityError, OSError) as e:
                return self._json({"error": str(e)}, 500)
            return self._json({"ok": True, "path": path})
        if parts == ["cache", "clear"]:
            ai.reset_cooldowns()
            return self._json({"cleared": ai.clear_cache()})
        if len(parts) == 3 and parts[0] == "chats" and parts[2] == "handoff":
            try:
                res = continuity.send_chat(store.get_chat(parts[1]), to=req.get("to") or None)
            except (KeyError, OSError):
                return self._json({"error": "разговор не найден"}, 404)
            except Exception as e:  # noqa: BLE001 — MindKit сообщает понятным текстом
                return self._json({"error": str(e)}, 500)
            return self._json({"devices": res})
        if len(parts) == 3 and parts[0] == "chats" and parts[2] == "stop":
            ev = _STOPS.get(parts[1])
            if ev:
                ev.set()
            return self._json({"ok": True})
        if len(parts) == 3 and parts[0] == "chats" and parts[2] == "send":
            try:
                chat = store.get_chat(parts[1])
            except (KeyError, OSError):
                return self._json({"error": "разговор не найден"}, 404)
            return self._stream(chat, req)
        self._json({"error": "нет такого метода"}, 404)

    def _stream(self, chat: dict, req: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        stop = threading.Event()
        _STOPS[chat["id"]] = stop

        def emit(kind: str, data) -> None:
            try:
                self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except OSError:
                stop.set()          # окно закрыли — агента останавливаем

        try:
            _run(chat, req, stop, emit)
        finally:
            _STOPS.pop(chat["id"], None)
        self.close_connection = True


def start(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def url(srv: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}/#token={TOKEN}"
